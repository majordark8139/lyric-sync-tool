"""TTML parser — converts Apple Music / TTML timed XML documents into SongLyrics."""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from core.models import LyricLine, SongLyrics, Syllable


def parse_time(val: Optional[str]) -> int:
    """Convert timestamp strings into integer milliseconds.

    Handles formats such as:
    - ``00:00:27.780`` (hours:minutes:seconds.fraction)
    - ``00:27.78`` or ``0:30.025`` (minutes:seconds.fraction)
    - ``27.78s`` or ``1.000`` (seconds with optional suffix)
    - integer or float strings
    """
    if not val:
        return 0
    val = val.strip()
    if not val:
        return 0
    if val.endswith("s"):
        val = val[:-1].strip()

    if ":" in val:
        parts = val.split(":")
        if len(parts) == 3:
            try:
                h = float(parts[0])
                m = float(parts[1])
                s = float(parts[2])
                return int(round((h * 3600.0 + m * 60.0 + s) * 1000.0))
            except ValueError:
                return 0
        elif len(parts) == 2:
            try:
                m = float(parts[0])
                s = float(parts[1])
                return int(round((m * 60.0 + s) * 1000.0))
            except ValueError:
                return 0

    try:
        return int(round(float(val) * 1000.0))
    except ValueError:
        return 0


def _local_tag(elem: ET.Element) -> str:
    """Return the local tag name stripped of XML namespace prefix/URI."""
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


def _is_bg_role(elem: ET.Element) -> bool:
    """Return True if element role attribute designates background vocals ('x-bg')."""
    for k, v in elem.attrib.items():
        local_name = k.split("}")[-1] if "}" in k else k
        if local_name.lower() == "role" and v.strip().lower() == "x-bg":
            return True
    return False


def parse_ttml(xml_content: str) -> SongLyrics:
    """Parse TTML XML content into a populated SongLyrics instance.

    Handles root namespaces (``http://www.w3.org/ns/ttml``,
    ``http://music.apple.com/lyric-ttml-internal``,
    ``http://www.w3.org/ns/ttml#metadata``), extracting line timings,
    discrete syllable timestamps, and background vocal ad-libs.

    Args:
        xml_content: XML string conforming to TTML specification.

    Returns:
        SongLyrics populated with LyricLine and Syllable objects.
    """
    root = ET.fromstring(xml_content)
    lines: List[LyricLine] = []
    metadata: Dict[str, str] = {}

    # Extract all <p> elements across body/div hierarchy
    p_elements: List[ET.Element] = [
        elem for elem in root.iter() if _local_tag(elem) == "p"
    ]

    for p in p_elements:
        line_start = parse_time(p.attrib.get("begin", "0"))
        line_end = parse_time(p.attrib.get("end", "0"))

        syllables: List[Syllable] = []
        bg_syllables: List[Syllable] = []
        bg_text_parts: List[str] = []

        child_spans = [child for child in p if _local_tag(child) == "span"]

        if child_spans:
            for child in child_spans:
                if _is_bg_role(child):
                    sub_spans = [gc for gc in child if _local_tag(gc) == "span"]
                    if sub_spans:
                        for gc in sub_spans:
                            gc_text = gc.text or ""
                            gc_start = parse_time(gc.attrib.get("begin", str(line_start)))
                            gc_end = parse_time(gc.attrib.get("end", str(gc_start)))
                            gc_dur = max(0, gc_end - gc_start)
                            bg_syllables.append(
                                Syllable(text=gc_text, start_ms=gc_start, duration_ms=gc_dur)
                            )
                            bg_text_parts.append(gc_text)
                            if gc.tail:
                                bg_text_parts.append(gc.tail)
                    else:
                        txt = (child.text or "").strip()
                        if txt:
                            bg_text_parts.append(txt)
                        if "begin" in child.attrib:
                            s_start = parse_time(child.attrib.get("begin"))
                            s_end = parse_time(child.attrib.get("end", str(s_start)))
                            s_dur = max(0, s_end - s_start)
                            bg_syllables.append(
                                Syllable(text=txt, start_ms=s_start, duration_ms=s_dur)
                            )
                else:
                    text = child.text or ""
                    span_start = parse_time(child.attrib.get("begin", str(line_start)))
                    span_end = parse_time(child.attrib.get("end", str(span_start)))
                    duration = max(0, span_end - span_start)
                    syllables.append(
                        Syllable(text=text, start_ms=span_start, duration_ms=duration)
                    )

            bg_text = "".join(bg_text_parts).strip() if bg_text_parts else None

            if syllables:
                full_text = "".join(s.text for s in syllables)
                if not line_end:
                    line_end = max(s.abs_end_ms for s in syllables)
                lines.append(
                    LyricLine(
                        start_ms=line_start,
                        end_ms=line_end,
                        text=full_text,
                        syllables=syllables,
                        background_text=bg_text,
                        background_syllables=bg_syllables,
                    )
                )
            elif bg_text or bg_syllables:
                if not line_end:
                    line_end = (
                        max(s.abs_end_ms for s in bg_syllables)
                        if bg_syllables
                        else line_start
                    )
                lines.append(
                    LyricLine(
                        start_ms=line_start,
                        end_ms=line_end,
                        text="",
                        syllables=[],
                        background_text=bg_text,
                        background_syllables=bg_syllables,
                        is_background=True,
                    )
                )
            else:
                lines.append(
                    LyricLine(
                        start_ms=line_start,
                        end_ms=line_end or line_start,
                        text=(p.text or "").strip(),
                        syllables=[],
                    )
                )
        else:
            lines.append(
                LyricLine(
                    start_ms=line_start,
                    end_ms=line_end or line_start,
                    text=(p.text or "").strip(),
                    syllables=[],
                )
            )

    is_word_synced = bool(any(len(l.syllables) > 1 for l in lines))
    return SongLyrics(metadata=metadata, lines=lines, is_word_synced=is_word_synced)
