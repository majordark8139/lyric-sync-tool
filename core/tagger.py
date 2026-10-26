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

from core.models import LyricLine, SongLyrics, SyllableSpan, SyncState, TaggingConfig
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


# Regex for parsing LRC/ELRC files
_LRC_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)$")
_ELRC_WORD_RE = re.compile(r"<(\d{2}):(\d{2})\.(\d{2,3})>([^<]*)")
# Regex for KRC-style syllable tokens (offset,dur,flag)
_KRC_HEADER_RE = re.compile(r"^\[(\d+),(\d+)\]")
_KRC_SYLLABLE_RE = re.compile(r"<(\d+),(\d+),-?\d+>([^<\r\n]*)")


def _ms_from_lrc(m: str, s: str, cs_str: str) -> int:
    """Convert mm:ss.xx (or xxx) to milliseconds."""
    cs = int(cs_str)
    millis = cs * 10 if len(cs_str) == 2 else cs
    return int(m) * 60000 + int(s) * 1000 + millis


def _is_background_candidate_text(text: str) -> bool:
    """True when text is fully parenthesized and contains no inner parentheses."""
    t = text.strip()
    if len(t) < 2:
        return False
    open_p = t[0] in ("(", "（")
    close_p = t[-1] in (")", "）")
    if not open_p or not close_p:
        return False
    inner = t[1:-1]
    return not any(c in ("(", ")", "（", "）") for c in inner)


def _extract_inline_background_words(
    syllables: List[SyllableSpan],
) -> tuple[List[SyllableSpan], List[SyllableSpan]]:
    """Splits parenthesized word runs out of a syllable list into a background layer.

    Returns (main_syllables, bg_syllables). If no valid run or unbalanced, returns (syllables, []).
    """
    main: List[SyllableSpan] = []
    bg: List[SyllableSpan] = []
    in_run = False
    started = False

    for s in syllables:
        vis = s.text.strip()
        if not in_run and vis and vis[0] in ("(", "（"):
            started = True
            in_run = True

        if in_run:
            bg.append(s)
            if vis and vis[-1] in (")", "）"):
                in_run = False
        else:
            main.append(s)

    if not started or in_run or not main or not bg:
        return syllables, []

    return main, bg


def _extract_inline_background_text(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract parenthesized background runs from plain line text.

    Returns (main_text, bg_text) if background vocal found, otherwise (None, None).
    """
    t = text.strip()
    if _is_background_candidate_text(t):
        return None, None

    pattern = re.compile(r"(\([^\(\)（）]+\)|（[^\(\)（）]+）)")
    matches = list(pattern.finditer(t))
    if not matches:
        return None, None

    # Check for unbalanced parentheses
    cleaned_check = pattern.sub("", t)
    if any(c in ("(", ")", "（", "）") for c in cleaned_check):
        return None, None

    main_text = pattern.sub("", t).strip()
    main_text = re.sub(r"\s+", " ", main_text).strip()
    if not main_text:
        return None, None

    bg_parts = [m.group(0).strip() for m in matches]
    bg_text = " ".join(bg_parts)
    return main_text, bg_text


def _append_background(
    target: LyricLine,
    bg_syllables: Optional[List[SyllableSpan]] = None,
    bg_text: Optional[str] = None,
    bg_end_ms: Optional[int] = None,
) -> None:
    """Attach background vocals/adlibs to target LyricLine."""
    if bg_syllables:
        if target.background_syllables:
            merged = list(target.background_syllables)
            last_syl = merged[-1]
            if not last_syl.text.endswith(" "):
                merged[-1] = SyllableSpan(
                    abs_start_ms=last_syl.abs_start_ms,
                    abs_end_ms=last_syl.abs_end_ms,
                    text=last_syl.text + " ",
                )
            merged.extend(bg_syllables)
            target.background_syllables = merged
        else:
            target.background_syllables = list(bg_syllables)

        if bg_end_ms is not None:
            target.background_end_ms = bg_end_ms
        elif target.background_syllables:
            target.background_end_ms = target.background_syllables[-1].abs_end_ms

        target.background_text = "".join(s.text for s in target.background_syllables).strip()
    elif bg_text:
        cleaned_bg = bg_text.strip()
        if target.background_text:
            target.background_text = f"{target.background_text} {cleaned_bg}"
        else:
            target.background_text = cleaned_bg
        if bg_end_ms is not None:
            target.background_end_ms = bg_end_ms


def _fold_background_lines(lines: List[LyricLine]) -> List[LyricLine]:
    """Fold parenthesized background vocal lines into preceding line, and extract inline adlibs."""
    result: List[LyricLine] = []
    last_main: Optional[LyricLine] = None

    for line in lines:
        if last_main is not None and _is_background_candidate_text(line.text):
            bg_syls = line.syllables if (line.syllables and len(line.syllables) > 1) else None
            bg_end = line.end_ms
            _append_background(
                last_main,
                bg_syllables=bg_syls,
                bg_text=line.text,
                bg_end_ms=bg_end,
            )
            continue

        # Extract inline background adlibs if present
        if line.syllables and len(line.syllables) > 1:
            main_syls, bg_syls = _extract_inline_background_words(line.syllables)
            if bg_syls:
                line.syllables = main_syls
                line.end_ms = main_syls[-1].abs_end_ms
                line.text = "".join(s.text for s in main_syls).strip()
                _append_background(
                    line,
                    bg_syllables=bg_syls,
                    bg_end_ms=bg_syls[-1].abs_end_ms,
                )
            else:
                main_text, bg_text = _extract_inline_background_text(line.text)
                if main_text and bg_text:
                    line.text = main_text
                    _append_background(line, bg_text=bg_text, bg_end_ms=line.end_ms)
        else:
            main_text, bg_text = _extract_inline_background_text(line.text)
            if main_text and bg_text:
                line.text = main_text
                line.syllables = [SyllableSpan(abs_start_ms=line.start_ms, abs_end_ms=line.end_ms, text=main_text)]
                _append_background(line, bg_text=bg_text, bg_end_ms=line.end_ms)

        result.append(line)
        last_main = line

    return result


def _parse_lrc_text(text: str) -> Optional[SongLyrics]:
    """Parse standard LRC or Enhanced LRC text into SongLyrics.

    Handles standard ``[mm:ss.xx]Line text``, Enhanced LRC
    ``[mm:ss.xx]<mm:ss.xx>word1 <mm:ss.xx>word2`` syllable markers,
    and Noctis ``[bg: ...]`` / inline parenthesized background vocals.

    Args:
        text: Raw LRC/ELRC file content.

    Returns:
        Populated :class:`~core.models.SongLyrics`, or *None* if no timed lines found.
    """
    if not text:
        return None

    metadata: dict = {}
    lines: List[LyricLine] = []
    has_word_sync = False

    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        # iTunes / Noctis background vocal line "[bg: ...]"
        if raw_line.lower().startswith("[bg:"):
            if not lines:
                continue
            body = raw_line[4:]
            if body.endswith("]"):
                body = body[:-1]
            body = body.strip()
            word_matches = list(_ELRC_WORD_RE.finditer(body))
            if word_matches:
                has_word_sync = True
                bg_syls: List[SyllableSpan] = []
                if word_matches[0].start() > 0:
                    leading = body[:word_matches[0].start()]
                    if leading.strip():
                        s_first = _ms_from_lrc(word_matches[0].group(1), word_matches[0].group(2), word_matches[0].group(3))
                        bg_syls.append(SyllableSpan(abs_start_ms=lines[-1].start_ms, abs_end_ms=s_first, text=leading))
                for j, wm in enumerate(word_matches):
                    s_start = _ms_from_lrc(wm.group(1), wm.group(2), wm.group(3))
                    s_text = wm.group(4)
                    if not s_text and j == len(word_matches) - 1:
                        if bg_syls:
                            last = bg_syls[-1]
                            bg_syls[-1] = SyllableSpan(abs_start_ms=last.abs_start_ms, abs_end_ms=s_start, text=last.text)
                        continue
                    if j + 1 < len(word_matches):
                        next_wm = word_matches[j + 1]
                        s_end = _ms_from_lrc(next_wm.group(1), next_wm.group(2), next_wm.group(3))
                    else:
                        s_end = s_start + 1000
                    bg_syls.append(SyllableSpan(abs_start_ms=s_start, abs_end_ms=s_end, text=s_text))
                _append_background(lines[-1], bg_syllables=bg_syls, bg_end_ms=bg_syls[-1].abs_end_ms)
            else:
                _append_background(lines[-1], bg_text=body, bg_end_ms=lines[-1].end_ms)
            continue

        # Metadata tag (non-digit first char after [)
        if raw_line.startswith("[") and len(raw_line) > 1 and not raw_line[1].isdigit():
            meta_m = re.match(r"^\[([a-zA-Z]+):(.*)\]$", raw_line)
            if meta_m:
                metadata[meta_m.group(1)] = meta_m.group(2).strip()
            continue

        lm = _LRC_LINE_RE.match(raw_line)
        if not lm:
            continue

        start_ms = _ms_from_lrc(lm.group(1), lm.group(2), lm.group(3))
        body = lm.group(4)

        syllables: List[SyllableSpan] = []
        word_matches = list(_ELRC_WORD_RE.finditer(body))

        if word_matches:
            has_word_sync = True
            if word_matches[0].start() > 0:
                leading = body[:word_matches[0].start()]
                if leading.strip():
                    s_first = _ms_from_lrc(word_matches[0].group(1), word_matches[0].group(2), word_matches[0].group(3))
                    syllables.append(SyllableSpan(abs_start_ms=start_ms, abs_end_ms=s_first, text=leading))
            for j, wm in enumerate(word_matches):
                s_start = _ms_from_lrc(wm.group(1), wm.group(2), wm.group(3))
                s_text = wm.group(4)
                if not s_text and j == len(word_matches) - 1:
                    if syllables:
                        last = syllables[-1]
                        syllables[-1] = SyllableSpan(abs_start_ms=last.abs_start_ms, abs_end_ms=s_start, text=last.text)
                    continue
                if j + 1 < len(word_matches):
                    next_wm = word_matches[j + 1]
                    s_end = _ms_from_lrc(next_wm.group(1), next_wm.group(2), next_wm.group(3))
                else:
                    s_end = s_start + 1000
                syllables.append(SyllableSpan(abs_start_ms=s_start, abs_end_ms=s_end, text=s_text))
            end_ms = syllables[-1].abs_end_ms
        else:
            body_stripped = body.strip()
            end_ms = start_ms + 3000
            syllables.append(SyllableSpan(abs_start_ms=start_ms, abs_end_ms=end_ms, text=body_stripped))

        lines.append(LyricLine(start_ms=start_ms, end_ms=end_ms, syllables=syllables))

    if not lines:
        return None

    lines = _fold_background_lines(lines)

    return SongLyrics(metadata=metadata, lines=lines, is_word_synced=has_word_sync)


def _parse_krc_text_tagger(krc_text: str) -> Optional[SongLyrics]:
    """Parse decrypted KRC text (offset,dur format) into SongLyrics.

    Args:
        krc_text: Decrypted KRC content with UNIX line endings.

    Returns:
        Populated :class:`~core.models.SongLyrics`, or *None* if no lines parsed.
    """
    metadata: dict = {}
    lines: List[LyricLine] = []
    _META_RE = re.compile(r"^\[([a-zA-Z]+):(.*)\]$")

    for raw_line in krc_text.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        meta_match = _META_RE.match(raw_line)
        if meta_match and not raw_line[1].isdigit():
            metadata[meta_match.group(1).strip()] = meta_match.group(2).strip()
            continue
        header_match = _KRC_HEADER_RE.match(raw_line)
        if not header_match:
            continue
        line_start_ms = int(header_match.group(1))
        line_dur_ms = int(header_match.group(2))
        line_end_ms = line_start_ms + line_dur_ms
        syllables: List[SyllableSpan] = []
        for syl_match in _KRC_SYLLABLE_RE.finditer(raw_line):
            rel_off = int(syl_match.group(1))
            syl_dur = int(syl_match.group(2))
            text = syl_match.group(3)
            abs_start = line_start_ms + rel_off
            abs_end = abs_start + syl_dur
            syllables.append(SyllableSpan(abs_start_ms=abs_start, abs_end_ms=abs_end, text=text))
        lines.append(LyricLine(start_ms=line_start_ms, end_ms=line_end_ms, syllables=syllables))

    if not lines:
        return None
    return SongLyrics(metadata=metadata, lines=lines, is_word_synced=bool(lines and lines[0].syllables))


def read_track_lyrics(file_path: str) -> Optional[SongLyrics]:
    """Deserialize existing synchronized lyrics for a track from disk.

    Inspection order:
      1. Sibling sidecars: ``{stem}.lrc``, ``{stem}.elrc``, ``{stem}.ttml``
         (ELRC / Enhanced LRC preferred over plain LRC).
      2. Embedded audio tags via Mutagen:
         - FLAC / OGG (Vorbis): ``LYRICS`` or ``UNSYNCEDLYRICS`` comments.
         - MP3 (ID3): ``TXXX:LYRICS`` → ``USLT`` → ``SYLT`` frames.
         - M4A / MP4: ``\\xa9lyr`` atom.

    Content classification:
      - If text contains ``<offset,duration>`` or ``<mm:ss.xx>`` syllable markers,
        the result has ``is_word_synced=True``.
      - If text only contains standard ``[mm:ss.xx]`` line markers,
        ``is_word_synced=False``.

    Args:
        file_path: Absolute or relative path to the audio file.

    Returns:
        Populated :class:`~core.models.SongLyrics` or *None* if no readable lyrics found.
    """
    path = pathlib.Path(file_path)

    # ── 1. Sidecar files (ELRC before LRC for word-sync priority) ─────────
    for ext in (".elrc", ".lrc"):
        sidecar = path.with_suffix(ext)
        if sidecar.is_file():
            try:
                content = sidecar.read_text(encoding="utf-8", errors="replace")
                parsed = _parse_lrc_text(content)
                if parsed and parsed.lines:
                    return parsed
            except Exception:
                pass

    # ── 2. Embedded tags via Mutagen ───────────────────────────────────────
    if not path.is_file():
        return None

    try:
        audio = mutagen.File(file_path, easy=False)
    except Exception:
        audio = None

    if audio is None:
        return None

    tags = getattr(audio, "tags", None)

    # Helper: parse any text candidate
    def _try_parse(text: str) -> Optional[SongLyrics]:
        if not text:
            return None
        # Try KRC-style syllable tokens first
        if _KRC_HEADER_RE.search(text) and _KRC_SYLLABLE_RE.search(text):
            result = _parse_krc_text_tagger(text)
            if result:
                return result
        # Try LRC / Enhanced LRC
        result = _parse_lrc_text(text)
        if result:
            return result
        return None

    # ID3 tags (MP3)
    id3_tags = tags if isinstance(tags, ID3) else (audio if isinstance(audio, ID3) else None)
    if id3_tags is None and hasattr(tags, "getall"):
        id3_tags = tags

    if id3_tags is not None and hasattr(id3_tags, "getall"):
        # TXXX:LYRICS (preferred: may contain Enhanced LRC)
        for frame in id3_tags.getall("TXXX"):
            desc = getattr(frame, "desc", "")
            if desc.upper() in ("LYRICS", "UNSYNCEDLYRICS"):
                text_val = getattr(frame, "text", None)
                raw = text_val[0] if isinstance(text_val, (list, tuple)) and text_val else (str(text_val) if text_val else "")
                result = _try_parse(raw)
                if result:
                    return result

        # USLT (plain or timestamped)
        for frame in id3_tags.getall("USLT"):
            raw = getattr(frame, "text", "")
            if raw:
                result = _try_parse(str(raw))
                if result:
                    return result

        # SYLT (synchronized text frames)
        for frame in id3_tags.getall("SYLT"):
            entries = getattr(frame, "text", [])
            if entries:
                parts = []
                for entry in entries:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        word, ts = entry[0], entry[1]
                        ts_ms = int(ts)
                        m_str = f"{ts_ms // 60000:02d}"
                        s_str = f"{(ts_ms // 1000) % 60:02d}"
                        cs_str = f"{(ts_ms % 1000) // 10:02d}"
                        parts.append(f"[{m_str}:{s_str}.{cs_str}]{word}")
                synth_lrc = "\n".join(parts)
                result = _parse_lrc_text(synth_lrc)
                if result:
                    return result

    # Vorbis comments (FLAC / OGG)
    for container in (tags, audio):
        if container is not None and hasattr(container, "get") and not isinstance(container, ID3):
            for key in ("LYRICS", "lyrics", "UNSYNCEDLYRICS", "unsyncedlyrics"):
                try:
                    val = container.get(key)
                    if val:
                        raw = val[0] if isinstance(val, (list, tuple)) else str(val)
                        result = _try_parse(str(raw))
                        if result:
                            return result
                except (KeyError, ValueError):
                    pass

    # MP4 / M4A atoms
    for container in (tags, audio):
        if container is not None and hasattr(container, "get") and not isinstance(container, (type(None),)):
            for atom_key in ("\xa9lyr", "©lyr"):
                try:
                    lyr = container.get(atom_key)
                    if lyr:
                        raw = lyr[0] if isinstance(lyr, (list, tuple)) else str(lyr)
                        result = _try_parse(str(raw))
                        if result:
                            return result
                except (KeyError, ValueError):
                    pass

    return None


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

    # ── Consolidation Pass ────────────────────────────────────────────────
    # If writing standard .lrc only, purge lingering .elrc
    if config.write_lrc and config.lrc_extension.lower() == "lrc":
        lingering_elrc = path.with_suffix(".elrc")
        if lingering_elrc.is_file() and lingering_elrc not in written_sidecars:
            if config.backup_stale_sidecars:
                bak_path = lingering_elrc.with_name(lingering_elrc.name + ".bak")
                lingering_elrc.replace(bak_path)
                results["sidecars_purged"].append(str(bak_path))
            else:
                lingering_elrc.unlink()
                results["sidecars_purged"].append(str(lingering_elrc))

    # ── 2. Stale Purge Pass ───────────────────────────────────────────────
    if config.purge_stale_line_sync:
        candidate_siblings = [
            path.with_suffix(".lrc"),
            path.with_suffix(".elrc"),
            path.with_suffix(".ttml"),
        ]
        for sibling in candidate_siblings:
            if sibling.is_file() and sibling not in written_sidecars:
                should_purge = False

                # If TTML writing is disabled, purge lingering TTML
                if sibling.suffix.lower() == ".ttml" and not config.write_ttml:
                    should_purge = True
                else:
                    try:
                        content = sibling.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        content = ""

                    # Check if sibling lacks word sync
                    has_word_sync = bool(_WORD_TAG_RE.search(content) or _TTML_WORD_RE.search(content))
                    if not has_word_sync:
                        should_purge = True

                if should_purge:
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

