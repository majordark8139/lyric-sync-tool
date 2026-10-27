"""Background worker threads for directory scanning and batch tagging."""

import os
import pathlib
import queue
import threading
import time
from typing import Any, List, Optional, Union

import mutagen
from PySide6.QtCore import QThread, Signal

from core.models import SyncState, TaggingConfig, TrackItem
from core.providers.auto import AutoLyricsProvider
from core.providers.base import BaseLyricsProvider
from core.providers.kugou import KugouProvider
from core.providers.lrc_red import LrcRedProvider
from core.providers.unison import UnisonProvider
from core.tagger import apply_lyrics, detect_sync_state, read_metadata

_AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg"}


def _extract_tag_string(audio: Any, keys: List[str], default: str = "") -> str:
    """Helper to extract the first non-empty tag from multiple possible keys."""
    if not audio:
        return default

    # Check directly on audio
    for k in keys:
        try:
            val = audio.get(k)
            if val:
                if isinstance(val, (list, tuple)) and len(val) > 0:
                    return str(val[0]).strip()
                return str(val).strip()
        except Exception:
            pass

    # Check on audio.tags
    tags = getattr(audio, "tags", None)
    if tags:
        for k in keys:
            try:
                val = tags.get(k)
                if val:
                    if isinstance(val, (list, tuple)) and len(val) > 0:
                        return str(val[0]).strip()
                    return str(val).strip()
            except Exception:
                pass

    return default


class ScannerWorker(QThread):
    """Recursively scans a directory for audio files and extracts track metadata."""

    track_found = Signal(TrackItem)
    progress = Signal(str)
    finished_scanning = Signal(int)

    def __init__(self, root_directory: str, parent=None) -> None:
        super().__init__(parent)
        self._root_directory = root_directory

    def run(self) -> None:
        count = 0
        root_path = pathlib.Path(self._root_directory)

        if not root_path.is_dir():
            self.finished_scanning.emit(0)
            return

        for dirpath, _, filenames in os.walk(self._root_directory):
            if self.isInterruptionRequested():
                break

            self.progress.emit(f"Scanning: {dirpath}")

            for fname in sorted(filenames):
                if self.isInterruptionRequested():
                    break

                ext = os.path.splitext(fname)[1].lower()
                if ext not in _AUDIO_EXTENSIONS:
                    continue

                full_path = os.path.join(dirpath, fname)
                meta = read_metadata(full_path)
                sync_state = detect_sync_state(full_path)

                item = TrackItem(
                    file_path=full_path,
                    title=meta["title"] or pathlib.Path(fname).stem,
                    artist=meta["artist"] or "Unknown Artist",
                    duration_ms=meta["duration_ms"],
                    sync_state=sync_state,
                    matched_lyrics=None,
                    album=meta["album"] or "Unknown Album",
                    album_artist=meta["album_artist"],
                )

                count += 1
                self.track_found.emit(item)

        self.finished_scanning.emit(count)


class BatchTaggerWorker(QThread):
    """Processes selected tracks, queries lyrics provider, and applies tags/sidecars."""

    track_progress = Signal(int, str, TrackItem)  # index, status_text, track
    overall_progress = Signal(int, int)           # completed, total
    batch_finished = Signal()

    def __init__(
        self,
        tracks: List[TrackItem],
        config: TaggingConfig,
        provider: Union[BaseLyricsProvider, str, None] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tracks = tracks
        self._config = config
        self._is_cancelled: bool = False

        if provider is None or provider == "Auto" or (isinstance(provider, str) and provider.lower() == "auto"):
            self._provider = AutoLyricsProvider()
            self._is_auto = True
        elif isinstance(provider, AutoLyricsProvider):
            self._provider = provider
            self._is_auto = True
        elif isinstance(provider, str):
            if provider.lower() in ("unison",):
                self._provider = UnisonProvider()
            elif provider.lower() in ("lrc.red", "lrcred"):
                self._provider = LrcRedProvider()
            else:
                self._provider = KugouProvider()
            self._is_auto = False
        else:
            self._provider = provider
            self._is_auto = (getattr(provider, "name", "").lower() == "auto")

    def cancel(self) -> None:
        """Cancel ongoing batch tagging."""
        self._is_cancelled = True
        self.requestInterruption()

    def _process_single_track(self, idx: int, track: TrackItem) -> None:
        if self.isInterruptionRequested() or self._is_cancelled:
            return

        # Skip if already word synced and overwrite is disabled
        if not self._config.overwrite_existing_word_sync and track.sync_state == SyncState.WORD:
            track.status = "Skipped (Existing Word Sync)"
            self.track_progress.emit(idx, track.status, track)
            return

        status = "No Word Sync Available"
        try:
            lyrics = None
            if self._is_auto:
                unison = getattr(self._provider, "unison", None) or UnisonProvider()
                lrc_red = getattr(self._provider, "lrc_red", None) or LrcRedProvider()
                kugou = getattr(self._provider, "kugou", None) or KugouProvider()

                # Stage 1: Unison
                self.track_progress.emit(idx, "Searching (Unison)...", track)
                lyrics = unison.search_word_sync(
                    artist=track.artist,
                    title=track.title,
                    duration_ms=track.duration_ms,
                    album_artist=getattr(track, "album_artist", ""),
                    config=self._config,
                )

                # Stage 2: lrc.red
                if not lyrics and not self.isInterruptionRequested() and not self._is_cancelled:
                    self.track_progress.emit(idx, "Searching (lrc.red)...", track)
                    lyrics = lrc_red.search_word_sync(
                        artist=track.artist,
                        title=track.title,
                        duration_ms=track.duration_ms,
                        album_artist=getattr(track, "album_artist", ""),
                        config=self._config,
                    )

                # Stage 3: Kugou
                if not lyrics and not self.isInterruptionRequested() and not self._is_cancelled:
                    self.track_progress.emit(idx, "Searching (Kugou)...", track)
                    lyrics = kugou.search_word_sync(
                        artist=track.artist,
                        title=track.title,
                        duration_ms=track.duration_ms,
                        album_artist=getattr(track, "album_artist", ""),
                        config=self._config,
                    )
            else:
                p_name = getattr(self._provider, "name", "Provider")
                self.track_progress.emit(idx, f"Searching ({p_name})...", track)
                lyrics = self._provider.search_word_sync(
                    artist=track.artist,
                    title=track.title,
                    duration_ms=track.duration_ms,
                    album_artist=getattr(track, "album_artist", ""),
                    config=self._config,
                )

            if self.isInterruptionRequested() or self._is_cancelled:
                return

            if lyrics and lyrics.lines:
                self.track_progress.emit(idx, "Applying tags...", track)
                apply_lyrics(track.file_path, lyrics, config=self._config)
                actual_type = "WORD" if lyrics.is_word_synced else "LINE"
                track.sync_state = actual_type
                status = f"Tagged ({actual_type})"
                track.matched_lyrics = lyrics
            else:
                status = "No Word Sync Available"
        except Exception as e:
            status = f"Error: {e}"
        finally:
            if not (self.isInterruptionRequested() or self._is_cancelled):
                track.status = status
                self.track_progress.emit(idx, track.status, track)

    def run(self) -> None:
        total = len(self._tracks)
        if total == 0:
            self.overall_progress.emit(0, 0)
            self.batch_finished.emit()
            return

        # Keep worker concurrency bounded to maximum 3 threads for dual-core / low RAM smoothness
        max_workers = min(3, total)

        task_queue: queue.Queue = queue.Queue()
        for idx, track in enumerate(self._tracks):
            task_queue.put((idx, track))

        done_count = 0
        done_lock = threading.Lock()

        def worker_loop() -> None:
            nonlocal done_count
            while not self.isInterruptionRequested() and not self._is_cancelled:
                try:
                    idx, track = task_queue.get(timeout=0.05)
                except queue.Empty:
                    break

                self._process_single_track(idx, track)

                with done_lock:
                    done_count += 1
                    curr_done = done_count
                    self.overall_progress.emit(curr_done, total)

                task_queue.task_done()
                time.sleep(0.02)

        threads = [threading.Thread(target=worker_loop, daemon=True) for _ in range(max_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.batch_finished.emit()
