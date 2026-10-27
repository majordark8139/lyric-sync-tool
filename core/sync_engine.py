"""Synchronisation engine: export, reconcile, and transform SongLyrics objects."""

import dataclasses
import re
from typing import List
from xml.sax.saxutils import escape as xml_escape

from core.models import LyricLine, SongLyrics, SyllableSpan


# ---------------------------------------------------------------------------
# Plain-text export
# ---------------------------------------------------------------------------

def to_plain_text(song: SongLyrics) -> str:
    """Strip all timestamps and return clean multiline lyrics.

    Each :class:`~core.models.LyricLine` is rendered as a single text line by
    concatenating the ``text`` fields of its syllables.  Lines are joined with
    ``\\n``.

    Args:
        song: Source :class:`~core.models.SongLyrics` to export.

    Returns:
        Plain string suitable for display in a plain-text editor widget.
    """
    out_lines: List[str] = []
    for line in song.lines:
        full_text = line.text or "".join(s.text for s in line.syllables).strip()
        if line.background_text and line.background_text not in full_text:
            full_text = f"{full_text} {line.background_text}".strip()
        out_lines.append(full_text)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Reconciliation helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """Return non-empty whitespace-split tokens from *text*."""
    return text.split()


def _token_diff(original_words: List[str], new_words: List[str]) -> int:
    """Count differing positions between two equal-length token lists.

    If the lists differ in length the result is the absolute length difference
    (every extra / missing token counts as one diff).

    Args:
        original_words: Tokens from the original line.
        new_words: Tokens from the edited line.

    Returns:
        Integer token-level distance.
    """
    if len(original_words) != len(new_words):
        return abs(len(original_words) - len(new_words))
    return sum(1 for a, b in zip(original_words, new_words) if a != b)


def reconcile_line(original_line: LyricLine, new_text: str) -> LyricLine:
    """Reconcile an edited text line against the original timed line.

    Two strategies are applied based on how much the text changed:

    **Rule A – Typo / Minor edit** (token diff ≤ 1 **and** same word count):
        Preserve every syllable's ``abs_start_ms`` and ``abs_end_ms`` exactly;
        only the ``text`` payload of each :class:`~core.models.SyllableSpan` is
        updated.  This keeps micro-timing intact for at-most-one-word edits.

    **Rule B – Rewrite / Misheard line** (token diff > 1 **or** word count
    changed):
        Discard per-syllable micro-timing.  Lock the line's ``start_ms`` /
        ``end_ms`` bounds and linearly interpolate new syllable durations
        weighted by character length::

            word_dur = int((len(word) / total_chars) * total_line_duration)

        The **last** word's ``abs_end_ms`` is snapped to ``original_line.end_ms``
        to prevent cumulative rounding drift.

    Args:
        original_line: The existing :class:`~core.models.LyricLine` with timing.
        new_text: The user-edited text for this line (words separated by spaces).

    Returns:
        A new :class:`~core.models.LyricLine` with reconciled syllables and
        updated ``start_ms`` / ``end_ms`` from the original.
    """
    new_words: List[str] = _tokenize(new_text)

    # ── Rule A eligibility ────────────────────────────────────────────────
    # Each original SyllableSpan is treated as exactly one "word slot".
    # Comparing span count vs new word count avoids re-tokenizing the
    # concatenated syllable text (which collapses spans that lack trailing
    # spaces, e.g. "Hel"+"lo"+"World" → one token instead of three).
    n_spans: int = len(original_line.syllables)
    same_count: bool = n_spans == len(new_words)

    if same_count and n_spans > 0:
        original_slot_texts: List[str] = [s.text.strip() for s in original_line.syllables]
        diff: int = _token_diff(original_slot_texts, new_words)
    else:
        # Counts differ → always Rule B.
        diff = abs(n_spans - len(new_words))

    # ── Rule A: Typo — preserve timing, patch text ────────────────────────
    if diff <= 1 and same_count and n_spans > 0:
        new_syllables: List[SyllableSpan] = [
            SyllableSpan(
                abs_start_ms=span.abs_start_ms,
                abs_end_ms=span.abs_end_ms,
                text=word,
            )
            for span, word in zip(original_line.syllables, new_words)
        ]
        return LyricLine(
            start_ms=original_line.start_ms,
            end_ms=original_line.end_ms,
            syllables=new_syllables,
        )

    # ── Rule B: Rewrite — linear interpolation ────────────────────────────
    return _rule_b(original_line, new_words)


def _rule_b(original_line: LyricLine, new_words: List[str]) -> LyricLine:
    """Apply Rule B linear interpolation to *new_words* within *original_line* bounds.

    Args:
        original_line: Source line providing ``start_ms`` and ``end_ms`` bounds.
        new_words: Tokenised replacement words.

    Returns:
        New :class:`~core.models.LyricLine` with interpolated syllable spans.
    """
    total_line_duration: int = original_line.end_ms - original_line.start_ms
    total_chars: int = sum(len(w) for w in new_words)

    if not new_words:
        return LyricLine(
            start_ms=original_line.start_ms,
            end_ms=original_line.end_ms,
            syllables=[],
        )

    new_syllables: List[SyllableSpan] = []
    cursor_ms: int = original_line.start_ms

    for idx, word in enumerate(new_words):
        is_last = idx == len(new_words) - 1

        if total_chars > 0:
            word_dur: int = int((len(word) / total_chars) * total_line_duration)
        else:
            word_dur = total_line_duration // len(new_words)

        abs_start: int = cursor_ms
        if is_last:
            abs_end: int = original_line.end_ms  # snap to prevent drift
        else:
            abs_end = cursor_ms + word_dur

        new_syllables.append(SyllableSpan(
            abs_start_ms=abs_start,
            abs_end_ms=abs_end,
            text=word,
        ))
        cursor_ms = abs_end

    return LyricLine(
        start_ms=original_line.start_ms,
        end_ms=original_line.end_ms,
        syllables=new_syllables,
    )


def reconcile_plain_text(original: SongLyrics, edited_text: str) -> SongLyrics:
    """Reconcile a full edited plain-text block against original timed lyrics.

    Splits *edited_text* by ``\\n`` and calls :func:`reconcile_line` for each
    pair of (original line, edited line) sequentially.  Excess edited lines
    (beyond ``len(original.lines)``) are silently discarded; if the edited text
    has fewer lines the corresponding original lines are kept unchanged.

    Args:
        original: The original timed :class:`~core.models.SongLyrics`.
        edited_text: Multi-line plain text produced by the text editor widget.

    Returns:
        New :class:`~core.models.SongLyrics` with updated syllables and the
        same metadata as *original*.
    """
    edited_lines: List[str] = edited_text.split("\n")
    new_lines: List[LyricLine] = []

    for idx, orig_line in enumerate(original.lines):
        if idx < len(edited_lines):
            new_lines.append(reconcile_line(orig_line, edited_lines[idx]))
        else:
            # Fewer edited lines than original — keep original timing.
            new_lines.append(orig_line)

    return SongLyrics(metadata=dict(original.metadata), lines=new_lines)


# ---------------------------------------------------------------------------
# Offset shift
# ---------------------------------------------------------------------------

def apply_offset(song: SongLyrics, offset_ms: int) -> SongLyrics:
    """Shift every timestamp in *song* by *offset_ms* milliseconds.

    All ``start_ms``, ``end_ms``, ``abs_start_ms``, and ``abs_end_ms`` values
    are adjusted and clamped to ``>= 0`` so that a negative offset cannot
    produce invalid negative timestamps.

    Args:
        song: Source :class:`~core.models.SongLyrics`.
        offset_ms: Milliseconds to add (positive → later, negative → earlier).

    Returns:
        A new :class:`~core.models.SongLyrics` with shifted timestamps.
    """
    new_lines: List[LyricLine] = []
    for line in song.lines:
        new_spans: List[SyllableSpan] = [
            SyllableSpan(
                abs_start_ms=max(0, s.abs_start_ms + offset_ms),
                abs_end_ms=max(0, s.abs_end_ms + offset_ms),
                text=s.text,
            )
            for s in line.syllables
        ]
        new_lines.append(LyricLine(
            start_ms=max(0, line.start_ms + offset_ms),
            end_ms=max(0, line.end_ms + offset_ms),
            syllables=new_spans,
        ))
    return SongLyrics(metadata=dict(song.metadata), lines=new_lines)


# ---------------------------------------------------------------------------
# Enhanced LRC export
# ---------------------------------------------------------------------------

def _ms_to_lrc(ms: int) -> str:
    """Format *ms* as LRC centisecond timestamp ``mm:ss.xx``.

    Args:
        ms: Time in milliseconds.

    Returns:
        String formatted as ``mm:ss.xx`` (centiseconds, zero-padded).
    """
    total_cs, _ = divmod(ms, 10)
    total_s, cs = divmod(total_cs, 100)
    minutes, seconds = divmod(total_s, 60)
    return f"{minutes:02d}:{seconds:02d}.{cs:02d}"


def to_enhanced_lrc(song: SongLyrics) -> str:
    """Export *song* as Enhanced LRC with per-word centisecond tags.

    Format per line::

        [mm:ss.xx]<mm:ss.xx>word1 <mm:ss.xx>word2 ...<mm:ss.xx>

    The trailing ``<mm:ss.xx>`` after the last word marks the line's end
    timestamp (equal to the final syllable's ``abs_end_ms``).

    Args:
        song: Source :class:`~core.models.SongLyrics`.

    Returns:
        Multi-line Enhanced LRC string.
    """
    out: List[str] = []

    # Header metadata
    for key, value in song.metadata.items():
        out.append(f"[{key}:{value}]")

    for line in song.lines:
        spans: List[SyllableSpan] = list(line.syllables)

        if line.background_syllables:
            spans = sorted(spans + list(line.background_syllables), key=lambda s: s.abs_start_ms)
        elif line.background_text:
            if spans:
                last = spans[-1]
                if line.background_text not in last.text and line.background_text not in (line.text or ""):
                    recombined = f"{last.text.strip()} {line.background_text.strip()}"
                    spans = spans[:-1] + [
                        SyllableSpan(
                            abs_start_ms=last.abs_start_ms,
                            abs_end_ms=line.background_end_ms or last.abs_end_ms,
                            text=recombined,
                        )
                    ]

        if not spans:
            full_text = line.text or ""
            if line.background_text and line.background_text not in full_text:
                full_text = f"{full_text} {line.background_text}".strip()
            if full_text:
                out.append(f"[{_ms_to_lrc(line.start_ms)}]{full_text}")
            else:
                out.append(f"[{_ms_to_lrc(line.start_ms)}]")
            continue

        line_tag = f"[{_ms_to_lrc(line.start_ms)}]"
        body = ""
        for idx, span in enumerate(spans):
            tag = f"<{_ms_to_lrc(span.abs_start_ms)}>"
            if idx > 0 and not body.endswith(" "):
                body += " "
            body += tag + span.text
        last_end = spans[-1].abs_end_ms
        if not body.endswith(" "):
            body += " "
        body += f"<{_ms_to_lrc(last_end)}>"
        out.append(line_tag + body)

    return "\n".join(out)


# ---------------------------------------------------------------------------
# TTML export
# ---------------------------------------------------------------------------

def _ms_to_ttml(ms: int) -> str:
    """Format *ms* as a TTML ``HH:MM:SS.mmm`` timestamp.

    Args:
        ms: Time in milliseconds.

    Returns:
        String formatted as ``HH:MM:SS.mmm``.
    """
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def to_ttml(song: SongLyrics) -> str:
    """Export *song* as Apple-style TTML XML with ``itunes:timing="Word"``.

    Syllable text values are escaped with :func:`xml.sax.saxutils.escape` to
    ensure the output is well-formed XML even if the lyrics contain ``<``,
    ``>``, or ``&``. Background vocals are serialized using Apple Music
    ``ttm:role="x-bg"`` spans.

    Args:
        song: Source :class:`~core.models.SongLyrics`.

    Returns:
        UTF-8 XML string conforming to the TTML 1.0 profile used by iTunes /
        Apple Music.
    """
    lines_xml: List[str] = []
    agents = dict(getattr(song, "agents", {}) or {})
    if not agents and any(getattr(line, "singer_id", None) for line in song.lines):
        seen: Dict[str, str] = {}
        for line in song.lines:
            sid = getattr(line, "singer_id", None)
            if sid:
                seen[sid] = sid
        if "v1" not in seen and "v2" not in seen:
            seen["v1"] = "v1"
            seen["v2"] = "v2"
        elif "v1" not in seen:
            seen = {"v1": "v1", **seen}
        elif "v2" not in seen:
            seen["v2"] = "v2"
        agents = seen

    head_xml = ""
    if agents:
        agent_tags = "".join(f'<ttm:agent type="person" xml:id="{aid}"/>' for aid in agents.keys())
        head_xml = (
            '  <head>\n'
            '    <metadata>\n'
            f'      {agent_tags}\n'
            '    </metadata>\n'
            '  </head>\n'
        )

    for line in song.lines:
        begin = _ms_to_ttml(line.start_ms)
        end = _ms_to_ttml(line.end_ms)
        agent_attr = f' ttm:agent="{line.singer_id}"' if getattr(line, "singer_id", None) else ""

        spans: List[str] = []
        for span in line.syllables:
            sb = _ms_to_ttml(span.abs_start_ms)
            se = _ms_to_ttml(span.abs_end_ms)
            escaped = xml_escape(span.text)
            spans.append(f'<span begin="{sb}" end="{se}">{escaped}</span>')

        if not spans and line.text:
            escaped = xml_escape(line.text)
            spans.append(f'<span begin="{begin}" end="{end}">{escaped}</span>')

        bg_spans: List[str] = []
        if line.background_syllables:
            for span in line.background_syllables:
                sb = _ms_to_ttml(span.abs_start_ms)
                se = _ms_to_ttml(span.abs_end_ms)
                escaped = xml_escape(span.text)
                bg_spans.append(f'<span begin="{sb}" end="{se}">{escaped}</span>')
        elif line.background_text:
            bg_start_ms = line.syllables[-1].abs_end_ms if line.syllables else line.start_ms
            bg_end_ms = line.background_end_ms or line.end_ms
            if bg_start_ms >= bg_end_ms:
                bg_start_ms = line.start_ms
                bg_end_ms = line.end_ms
            sb = _ms_to_ttml(bg_start_ms)
            se = _ms_to_ttml(bg_end_ms)
            escaped = xml_escape(line.background_text)
            bg_spans.append(f'<span begin="{sb}" end="{se}">{escaped}</span>')

        bg_xml = f'<span ttm:role="x-bg">{"".join(bg_spans)}</span>' if bg_spans else ""

        if not spans and not bg_spans:
            lines_xml.append(
                f'      <p begin="{begin}" end="{end}"{agent_attr}></p>'
            )
        else:
            lines_xml.append(
                f'      <p begin="{begin}" end="{end}"{agent_attr}>{"".join(spans)}{bg_xml}</p>'
            )

    body = "\n".join(lines_xml)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tt xml:lang="und"\n'
        '    xmlns="http://www.w3.org/ns/ttml"\n'
        '    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"\n'
        '    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"\n'
        '    itunes:timing="Word">\n'
        f'{head_xml}'
        '  <body>\n'
        '    <div>\n'
        f'{body}\n'
        '    </div>\n'
        '  </body>\n'
        '</tt>'
    )
