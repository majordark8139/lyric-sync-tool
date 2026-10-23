"""Audio tagging engine: detects sync state and embeds/exports synchronized lyrics."""

import os
import pathlib
import re
from typing import Any, Dict, List, Optional

import mutagen
from mutagen._vorbis import VCommentDict
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3FileType, SYLT, TXXX, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggflac import OggFLAC
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from core.models import SongLyrics, SyncState, TaggingConfig
from core.sync_engine import to_enhanced_lrc, to_plain_text, to_ttml

# Regex to detect word/syllable-level timing tags:
# - Kugou KRC style: <offset,duration,flag>
# - Enhanced LRC style: <mm:ss.xx>
_WORD_TAG_RE = re.compile(r"<\d+,\d+,-?\d+>|<\d+,\d+,\d+>|<\d{2}:\d{2}\.\d{2}>")
_TTML_WORD_RE = re.compile(r'itunes:timing=["\']Word["\']|<span\s+[^>]*begin=', re.IGNORECASE)

# Regex to detect standard line-level timing tags:
# - Standard LRC style: [mm:ss.xx] or [mm:ss.xxx]
_LINE_TAG_RE = re.compile(r"^\s*\[\d{2}:\d{2}\.\d{2,3}\]", re.MULTILINE)
_TTML_LINE_RE = re.compile(r"<p\s+[^>]*begin=", re.IGNORECASE)


def _atomic_write_text(target_path: pathlib.Path, content: str) -> None:
    """Atomically write string content to target_path using a temporary file."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = target_path.with_name(f".{target_path.name}.{os.getpid()}.tmp")
    try:
        temp_file.write_text(content, encoding="utf-8")
        temp_file.replace(target_path)
    except Exception:
        if temp_file.exists():
            temp_file.unlink(missing_ok=True)
        raise


def detect_sync_state(file_path: str) -> SyncState:
    """Detect whether an audio file or its sidecars contain synchronized lyrics.

    Inspection order:
      1. Sidecars: Reads matching ``.lrc``, ``.elrc``, and ``.ttml`` files in the same directory.
      2. Audio metadata via ``mutagen.File(file_path, easy=False)``:
         - Vorbis comments (FLAC/OGG): ``LYRICS``, ``UNSYNCEDLYRICS`` tags.
         - ID3 tags (MP3): ``SYLT`` frames, ``USLT`` frames, ``TXXX:LYRICS``.
         - MP4/M4A atoms: ``\xa9lyr`` atom.
      3. Classify sync level:
         - Word-level sync: Detected via syllable regexes (``<0,350,-1>``, ``<mm:ss.xx>``),
           TTML word spans / timing attributes, or word-level ``SYLT`` frames.
         - Line-level sync: Detected via standard line tags (``[mm:ss.xx]``), TTML ``<p begin=...>``,
           or line-level ``SYLT`` frames.
         - Otherwise: ``SyncState.NONE``.

    Args:
        file_path: Absolute or relative path to the audio file.

    Returns:
        SyncState indicating NONE, LINE, or WORD synchronization.
    """
    candidate_texts: List[str] = []
    has_sylt_word_sync = False
    has_sylt_line_sync = False

    path = pathlib.Path(file_path)

    # ── 1. Inspect sidecar files in the same directory ─────────────────────
    for ext in (".lrc", ".LRC", ".elrc", ".ELRC", ".ttml", ".TTML"):
        sidecar = path.with_suffix(ext)
        if sidecar.is_file():
            try:
                candidate_texts.append(sidecar.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    # ── 2. Inspect embedded audio metadata ─────────────────────────────────
    audio = None
    if path.is_file():
        try:
            audio = mutagen.File(file_path, easy=False)
        except Exception:
            audio = None

    if audio is not None:
        tags = getattr(audio, "tags", None)

        # Vorbis comments (FLAC, OGG, Opus) and dict-like tags
        for container in (tags, audio):
            if container is not None and hasattr(container, "get") and not isinstance(container, ID3):
                for key in ("LYRICS", "lyrics", "UNSYNCEDLYRICS", "unsyncedlyrics"):
                    try:
                        val = container.get(key)
                        if val:
                            if isinstance(val, (list, tuple)):
                                candidate_texts.extend(str(v) for v in val)
                            else:
                                candidate_texts.append(str(val))
                    except (KeyError, ValueError):
                        pass

        # ID3 frames (MP3, etc.)
        id3_tags = tags if isinstance(tags, ID3) else (audio if isinstance(audio, ID3) else None)
        if id3_tags is None and hasattr(tags, "getall"):
            id3_tags = tags

        if id3_tags is not None and hasattr(id3_tags, "getall"):
            # USLT frames (Unsynchronized lyrics/text)
            for frame in id3_tags.getall("USLT"):
                if getattr(frame, "text", None):
                    candidate_texts.append(str(frame.text))

            # TXXX frames (User defined text information, e.g. TXXX:LYRICS)
            for frame in id3_tags.getall("TXXX"):
                desc = getattr(frame, "desc", "")
                if desc.upper() in ("LYRICS", "UNSYNCEDLYRICS"):
                    text_val = getattr(frame, "text", None)
                    if isinstance(text_val, (list, tuple)):
                        candidate_texts.extend(str(t) for t in text_val)
                    elif text_val is not None:
                        candidate_texts.append(str(text_val))

            # SYLT frames (Synchronized lyrics/text)
            for frame in id3_tags.getall("SYLT"):
                entries = getattr(frame, "text", [])
                if entries:
                    has_sylt_line_sync = True
                    non_empty = [
                        e[0] for e in entries
                        if isinstance(e, (tuple, list)) and len(e) > 0 and e[0]
                    ]
                    if len(non_empty) > 1:
                        # In ID3 SYLT specification: if lines are synchronized on syllable basis,
                        # entries do not all end with newline characters.
                        if any(not (str(w).endswith("\n") or str(w).endswith("\r")) for w in non_empty[:-1]):
                            has_sylt_word_sync = True
                    candidate_texts.append(" ".join(str(w) for w in non_empty))

        # MP4/M4A atoms (e.g. \xa9lyr)
        for container in (tags, audio):
            if container is not None and hasattr(container, "get") and not isinstance(container, (VCommentDict, ID3)):
                for atom_key in ("\xa9lyr", "©lyr"):
                    try:
                        lyr = container.get(atom_key)
                        if lyr:
                            if isinstance(lyr, (list, tuple)):
                                candidate_texts.extend(str(l) for l in lyr)
                            else:
                                candidate_texts.append(str(lyr))
                    except (KeyError, ValueError):
                        pass

    # ── 3. Check for Word-level sync ───────────────────────────────────────
    if has_sylt_word_sync:
        return SyncState.WORD

    for text in candidate_texts:
        if _WORD_TAG_RE.search(text) or _TTML_WORD_RE.search(text):
            return SyncState.WORD

    # ── 4. Check for Line-level sync ───────────────────────────────────────
    if has_sylt_line_sync:
        return SyncState.LINE

    for text in candidate_texts:
        if _LINE_TAG_RE.search(text) or _TTML_LINE_RE.search(text) or re.search(r"\[\d{2}:\d{2}\.\d{2,3}\]", text):
            return SyncState.LINE

    # ── 5. Otherwise ───────────────────────────────────────────────────────
    return SyncState.NONE


def apply_lyrics(
    file_path: str,
    lyrics: SongLyrics,
    config: Optional[TaggingConfig] = None,
    embed: Optional[bool] = None,
    write_sidecars: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply synchronized lyrics by writing sidecars, purging stale files, and/or embedding.

    Args:
        file_path: Path to target audio file.
        lyrics: SongLyrics object to serialize and write.
        config: TaggingConfig controlling formats, sidecars, purge, and embedding options.
        embed: Backwards compatibility override for config.embed_metadata.
        write_sidecars: Backwards compatibility override for config.write_lrc and config.write_ttml.

    Returns:
        Dict detailing the actions performed: {"skipped": bool, "sidecars_written": list, ...}

    Raises:
        FileNotFoundError: If embed=True and audio file does not exist.
        ValueError: If embed=True and audio file format is unsupported by Mutagen.
    """
    if config is None:
        config = TaggingConfig()

    if embed is not None:
        config.embed_metadata = embed
    if write_sidecars is not None:
        config.write_lrc = write_sidecars
        config.write_ttml = write_sidecars

    results: Dict[str, Any] = {
        "skipped": False,
        "sidecars_written": [],
        "sidecars_purged": [],
        "embedded": False,
    }

    path = pathlib.Path(file_path)

    # ── Guard: Skip if existing word sync should not be overwritten ───────
    if not config.overwrite_existing_word_sync and detect_sync_state(file_path) == SyncState.WORD:
        results["skipped"] = True
        results["reason"] = "existing_word_sync"
        return results

    # ── 1. Write Sidecars ──────────────────────────────────────────────────
    written_sidecars: List[pathlib.Path] = []
    enhanced_lrc = to_enhanced_lrc(lyrics)
    ttml_content = to_ttml(lyrics)
    plain_text = to_plain_text(lyrics)

    if config.write_lrc:
        ext_mode = config.lrc_extension.lower()
        if ext_mode in ("lrc", "both"):
            p_lrc = path.with_suffix(".lrc")
            _atomic_write_text(p_lrc, enhanced_lrc)
            written_sidecars.append(p_lrc)
        if ext_mode in ("elrc", "both"):
            p_elrc = path.with_suffix(".elrc")
            _atomic_write_text(p_elrc, enhanced_lrc)
            written_sidecars.append(p_elrc)

    if config.write_ttml:
        p_ttml = path.with_suffix(".ttml")
        _atomic_write_text(p_ttml, ttml_content)
        written_sidecars.append(p_ttml)

    results["sidecars_written"] = [str(p) for p in written_sidecars]

    # ── 2. Stale Purge Pass ───────────────────────────────────────────────
    if config.purge_stale_line_sync:
        candidate_siblings = [
            path.with_suffix(".lrc"),
            path.with_suffix(".elrc"),
            path.with_suffix(".ttml"),
        ]
        for sibling in candidate_siblings:
            if sibling.is_file() and sibling not in written_sidecars:
                try:
                    content = sibling.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    content = ""

                # Check if sibling lacks word sync
                has_word_sync = bool(_WORD_TAG_RE.search(content) or _TTML_WORD_RE.search(content))
                if not has_word_sync:
                    if config.backup_stale_sidecars:
                        bak_path = sibling.with_name(sibling.name + ".bak")
                        sibling.replace(bak_path)
                        results["sidecars_purged"].append(str(bak_path))
                    else:
                        sibling.unlink()
                        results["sidecars_purged"].append(str(sibling))

    # ── 3. Embed into audio file tags ─────────────────────────────────────
    if config.embed_metadata:
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        audio = mutagen.File(file_path, easy=False)
        if audio is None:
            raise ValueError(f"Cannot embed lyrics: unsupported audio file format: {file_path}")

        # Vorbis comments: FLAC, OGG, Opus
        if isinstance(audio, (FLAC, OggVorbis, OggOpus, OggFLAC)) or (
            hasattr(audio, "tags") and isinstance(audio.tags, VCommentDict)
        ):
            if config.flac_embed_vorbis:
                if audio.tags is None:
                    audio.add_tags()
                audio["LYRICS"] = [enhanced_lrc]
                audio.save()
                results["embedded"] = True

        # ID3 frames: MP3, etc.
        elif isinstance(audio, (MP3, ID3FileType)) or (
            hasattr(audio, "tags") and isinstance(audio.tags, ID3)
        ) or file_path.lower().endswith(".mp3"):
            if audio.tags is None:
                audio.add_tags()
            if config.mp3_embed_uslt_fallback:
                audio.tags.delall("USLT")
                audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=plain_text))
            if config.mp3_embed_txxx:
                audio.tags.delall("TXXX:LYRICS")
                audio.tags.add(TXXX(encoding=3, desc="LYRICS", text=enhanced_lrc))
            if config.mp3_embed_sylt:
                sylt_entries = []
                for line in lyrics.lines:
                    for syl in line.syllables:
                        sylt_entries.append((syl.text, syl.abs_start_ms))
                if sylt_entries:
                    audio.tags.delall("SYLT")
                    audio.tags.add(SYLT(encoding=3, lang="eng", format=2, type=1, desc="", text=sylt_entries))
            audio.save(v2_version=3)
            results["embedded"] = True

        # MP4/M4A atoms
        elif isinstance(audio, MP4) or file_path.lower().endswith((".m4a", ".mp4")):
            if config.m4a_embed_atom:
                if audio.tags is None:
                    audio.add_tags()
                audio["\xa9lyr"] = [enhanced_lrc]
                audio.save()
                results["embedded"] = True

        else:
            # Generic fallback
            if audio.tags is None:
                try:
                    audio.add_tags()
                except Exception:
                    pass
            if hasattr(audio, "tags") and isinstance(audio.tags, ID3):
                if config.mp3_embed_uslt_fallback:
                    audio.tags.delall("USLT")
                    audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=plain_text))
                if config.mp3_embed_txxx:
                    audio.tags.delall("TXXX:LYRICS")
                    audio.tags.add(TXXX(encoding=3, desc="LYRICS", text=enhanced_lrc))
                try:
                    audio.save(v2_version=3)
                except TypeError:
                    audio.save()
                results["embedded"] = True
            else:
                try:
                    audio["LYRICS"] = [enhanced_lrc]
                    audio.save()
                    results["embedded"] = True
                except Exception:
                    pass

    return results


def strip_lyrics(file_path: str) -> None:
    """Permanently strip lyrics from an audio file across all metadata tags and sibling sidecars.

    Args:
        file_path: Path to the target audio file.
    """
    path = pathlib.Path(file_path)

    # ── 1. Remove sidecars ─────────────────────────────────────────────────
    for ext in (".lrc", ".elrc", ".ttml"):
        sidecar = path.with_suffix(ext)
        if sidecar.is_file():
            try:
                os.unlink(str(sidecar))
            except OSError:
                pass

    # ── 2. Remove embedded tags ───────────────────────────────────────────
    if not path.is_file():
        return

    try:
        audio = mutagen.File(file_path, easy=False)
    except Exception:
        audio = None

    if audio is None:
        return

    # FLAC / OGG / Vorbis comments
    if isinstance(audio, (FLAC, OggVorbis, OggOpus, OggFLAC)) or (
        hasattr(audio, "tags") and isinstance(audio.tags, VCommentDict)
    ):
        modified = False
        for key in ("LYRICS", "lyrics", "UNSYNCEDLYRICS", "unsyncedlyrics"):
            if key in audio:
                del audio[key]
                modified = True
            elif audio.tags is not None and key in audio.tags:
                del audio.tags[key]
                modified = True
        if modified:
            audio.save()

    # MP3 / ID3
    elif isinstance(audio, (MP3, ID3FileType)) or (
        hasattr(audio, "tags") and isinstance(audio.tags, ID3)
    ) or file_path.lower().endswith(".mp3"):
        if audio.tags is not None:
            audio.tags.delall("USLT")
            audio.tags.delall("TXXX:LYRICS")
            audio.tags.delall("SYLT")
            audio.save(v2_version=3)

    # MP4 / M4A
    elif isinstance(audio, MP4) or file_path.lower().endswith((".m4a", ".mp4")):
        modified = False
        if hasattr(audio, "pop"):
            if "\xa9lyr" in audio:
                audio.pop("\xa9lyr", None)
                modified = True
            if "©lyr" in audio:
                audio.pop("©lyr", None)
                modified = True
        if audio.tags is not None and hasattr(audio.tags, "pop"):
            if "\xa9lyr" in audio.tags:
                audio.tags.pop("\xa9lyr", None)
                modified = True
            if "©lyr" in audio.tags:
                audio.tags.pop("©lyr", None)
                modified = True
        if modified:
            audio.save()

    else:
        # Fallback for generic ID3 / dictionary tags
        if hasattr(audio, "tags") and isinstance(audio.tags, ID3):
            audio.tags.delall("USLT")
            audio.tags.delall("TXXX:LYRICS")
            audio.tags.delall("SYLT")
            try:
                audio.save(v2_version=3)
            except TypeError:
                audio.save()
        elif hasattr(audio, "__delitem__"):
            modified = False
            for key in ("LYRICS", "lyrics", "UNSYNCEDLYRICS", "unsyncedlyrics"):
                try:
                    del audio[key]
                    modified = True
                except Exception:
                    pass
            if modified:
                try:
                    audio.save()
                except Exception:
                    pass

