"""Background worker threads for directory scanning and batch tagging."""

import os
import pathlib
from typing import Any, List, Optional

import mutagen
from PySide6.QtCore import QThread, Signal

from core.models import SyncState, TaggingConfig, TrackItem
from core.providers.base import BaseLyricsProvider
from core.providers.kugou import KugouProvider
from core.tagger import apply_lyrics, detect_sync_state

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
                try:
                    audio = mutagen.File(full_path, easy=False)
                except Exception:
                    audio = None

                title = _extract_tag_string(audio, ["title", "TIT2", "\xa9nam", "©nam"], default=pathlib.Path(fname).stem)
                artist = _extract_tag_string(audio, ["artist", "TPE1", "\xa9ART", "©ART"], default="Unknown Artist")
                album = _extract_tag_string(audio, ["album", "TALB", "\xa9alb", "©alb"], default="Unknown Album")
                album_artist = _extract_tag_string(audio, ["albumartist", "album_artist", "TPE2", "aART"], default="")

                duration_ms = 0
                if audio and hasattr(audio, "info") and hasattr(audio.info, "length"):
                    duration_ms = int(audio.info.length * 1000)

                sync_state = detect_sync_state(full_path)

                item = TrackItem(
                    file_path=full_path,
                    title=title or pathlib.Path(fname).stem,
                    artist=artist or "Unknown Artist",
                    duration_ms=duration_ms,
                    sync_state=sync_state,
                    matched_lyrics=None,
                    album=album or "Unknown Album",
                    album_artist=album_artist,
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
        provider: Optional[BaseLyricsProvider] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tracks = tracks
        self._config = config
        self._provider = provider or KugouProvider()
        self._is_cancelled: bool = False

    def cancel(self) -> None:
        """Cancel ongoing batch tagging."""
        self._is_cancelled = True
        self.requestInterruption()

    def run(self) -> None:
        total = len(self._tracks)

        for idx, track in enumerate(self._tracks):
            if self.isInterruptionRequested() or self._is_cancelled:
                break

            self.track_progress.emit(idx, "Searching...", track)

            # Skip if already word synced and overwrite is disabled
            if not self._config.overwrite_existing_word_sync and track.sync_state == SyncState.WORD:
                self.track_progress.emit(idx, "Skipped (Existing Word Sync)", track)
                self.overall_progress.emit(idx + 1, total)
                if idx < total - 1 and not (self.isInterruptionRequested() or self._is_cancelled):
                    self.msleep(180)
                continue

            try:
                lyrics = self._provider.search_word_sync(
                    title=track.title,
                    artist=track.artist,
                    duration_ms=track.duration_ms,
                    config=self._config,
                )
            except Exception as e:
                self.track_progress.emit(idx, f"Error: {e}", track)
                self.overall_progress.emit(idx + 1, total)
                if idx < total - 1 and not (self.isInterruptionRequested() or self._is_cancelled):
                    self.msleep(180)
                continue

            if lyrics and lyrics.lines:
                try:
                    self.track_progress.emit(idx, "Applying tags...", track)
                    apply_lyrics(track.file_path, lyrics, config=self._config)
                    actual_type = "WORD" if lyrics.is_word_synced else "LINE"
                    track.sync_state = actual_type
                    track.status = f"Tagged ({actual_type})"
                    track.matched_lyrics = lyrics
                    self.track_progress.emit(idx, track.status, track)
                except Exception as e:
                    track.status = f"Apply Error: {e}"
                    self.track_progress.emit(idx, track.status, track)
            else:
                track.status = "No Word Sync Found"
                self.track_progress.emit(idx, track.status, track)

            self.overall_progress.emit(idx + 1, total)

            # Cooperative pause between tracks to prevent socket saturation and write stutter
            if idx < total - 1 and not (self.isInterruptionRequested() or self._is_cancelled):
                self.msleep(180)

        self.batch_finished.emit()
