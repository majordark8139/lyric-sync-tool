"""Kugou lyrics provider — fetches and decrypts syllable-synced KRC lyrics."""

import base64
import json
import re
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, List, Optional

from core.models import LyricLine, SongLyrics, SyllableSpan, TaggingConfig
from core.normalizer import generate_query_ladder
from core.providers.base import BaseLyricsProvider

# 16-byte cyclic XOR key used by Kugou's KRC container.
_KRC_XOR_KEY: bytes = bytes(
    [0x40, 0x47, 0x61, 0x77, 0x5E, 0x32, 0x74, 0x47,
     0x51, 0x36, 0x31, 0x2D, 0xCE, 0xD2, 0x6E, 0x69]
)

# Matches a fully syllable-timed KRC lyric line, e.g.:
#   [12345,2000]<0,350,-1>Hel<350,300,-1>lo
_WORD_SYNC_RE = re.compile(
    r"\[\d+,\d+\]\s*(?:<\d+,\d+,-?\d+>[^<\r\n]+)+"
)

# Matches a single KRC line header: [line_start_ms, duration_ms]
_LINE_HEADER_RE = re.compile(r"\[(\d+),(\d+)\]")

# Matches each syllable token: <rel_offset_ms, syl_dur_ms, flag>text
_SYLLABLE_RE = re.compile(r"<(\d+),(\d+),-?\d+>([^<\r\n]*)")

_SEARCH_URL = "http://lyrics.kugou.com/search"
_DOWNLOAD_URL = "http://lyrics.kugou.com/download"

_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_DURATION_TOLERANCE_MS: int = 2500


def unpack_krc(base64_payload: str) -> str:
    """Decode and decrypt a Base64-encoded KRC byte payload.

    Steps:
      1. Base64-decode the payload.
      2. Verify the first 4 bytes are the magic ``b"krc1"``.
      3. XOR-decrypt bytes[4:] cyclically against :data:`_KRC_XOR_KEY`.
      4. ``zlib.decompress`` the unmasked stream.
      5. Decode as ``utf-8-sig`` and normalise ``\\r\\n`` → ``\\n``.

    Args:
        base64_payload: Raw Base64 string as returned by the Kugou download API.

    Returns:
        Decrypted, decompressed KRC text with UNIX line endings.

    Raises:
        ValueError: If the magic header is not ``b"krc1"``.
        zlib.error: If zlib decompression fails.
    """
    raw: bytes = base64.b64decode(base64_payload)
    if raw[:4] != b"krc1":
        raise ValueError(
            f"Invalid KRC magic: expected b'krc1', got {raw[:4]!r}"
        )
    payload: bytes = raw[4:]
    unmasked = bytearray(len(payload))
    key_len = len(_KRC_XOR_KEY)
    for i, byte in enumerate(payload):
        unmasked[i] = byte ^ _KRC_XOR_KEY[i & (key_len - 1)]
    decompressed: bytes = zlib.decompress(bytes(unmasked))
    return decompressed.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _parse_krc_text(krc_text: str) -> SongLyrics:
    """Parse decrypted KRC text into a :class:`~core.models.SongLyrics` object.

    Only lines matching ``[line_start,dur]<rel_off,syl_dur,flag>text`` are
    parsed.  Metadata tags (``[ar:]``, ``[ti:]``, etc.) are captured into
    ``SongLyrics.metadata``.

    Args:
        krc_text: Decrypted KRC content with UNIX line endings.

    Returns:
        Populated :class:`~core.models.SongLyrics` instance.
    """
    metadata: Dict[str, str] = {}
    lines: List[LyricLine] = []

    _META_RE = re.compile(r"^\[([a-zA-Z]+):(.*)\]$")

    for raw_line in krc_text.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        # ── Metadata tags ──────────────────────────────────────────────────
        meta_match = _META_RE.match(raw_line)
        if meta_match and not raw_line[1].isdigit():
            metadata[meta_match.group(1).strip()] = meta_match.group(2).strip()
            continue

        # ── Timed lyric line ───────────────────────────────────────────────
        header_match = _LINE_HEADER_RE.match(raw_line)
        if not header_match:
            continue

        line_start_ms: int = int(header_match.group(1))
        line_dur_ms: int = int(header_match.group(2))
        line_end_ms: int = line_start_ms + line_dur_ms

        syllables: List[SyllableSpan] = []
        for syl_match in _SYLLABLE_RE.finditer(raw_line):
            rel_off: int = int(syl_match.group(1))
            syl_dur: int = int(syl_match.group(2))
            text: str = syl_match.group(3)
            abs_start: int = line_start_ms + rel_off
            abs_end: int = abs_start + syl_dur
            syllables.append(SyllableSpan(
                abs_start_ms=abs_start,
                abs_end_ms=abs_end,
                text=text,
            ))

        lines.append(LyricLine(
            start_ms=line_start_ms,
            end_ms=line_end_ms,
            syllables=syllables,
        ))

    return SongLyrics(metadata=metadata, lines=lines)


def _make_request(url: str) -> Optional[Dict[str, Any]]:
    """Perform an HTTP GET and return the parsed JSON body.

    Args:
        url: Fully-qualified URL to fetch.

    Returns:
        Parsed JSON dict, or *None* on network / decode error.
    """
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body: bytes = resp.read()
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


class KugouProvider(BaseLyricsProvider):
    """Lyrics provider backed by the Kugou public lyrics API.

    Fetches KRC (Kugou Rich Captions) payloads which carry per-syllable
    timing information and decrypts them locally — no proprietary SDK required.
    Supports Cascading Query Ladder search with candidate re-ranking.
    """

    @property
    def name(self) -> str:
        """Return the unique provider identifier."""
        return "kugou"

    def search_word_sync(
        self,
        title: str,
        artist: str,
        duration_ms: int,
        config: Optional[TaggingConfig] = None,
    ) -> Optional[SongLyrics]:
        """Search Kugou for syllable-synced lyrics using a cascading ladder.

        Args:
            title: Song title as tagged on the audio file.
            artist: Primary artist name.
            duration_ms: Track duration in milliseconds.
            config: Optional TaggingConfig controlling duration tolerance and ladder fallback.

        Returns:
            Fully populated :class:`~core.models.SongLyrics` with per-syllable
            timing, or *None* if no matching word-synced lyrics are found.
        """
        tolerance = config.duration_tolerance_ms if config else _DURATION_TOLERANCE_MS

        if config and not config.smart_fallback:
            ladder = [(artist, title)]
        else:
            ladder = generate_query_ladder(artist, title)

        for step_idx, (q_artist, q_title) in enumerate(ladder):
            keyword = f"{q_artist} - {q_title}"
            search_params = urllib.parse.urlencode({
                "ver": "1",
                "man": "yes",
                "client": "pc",
                "keyword": keyword,
                "duration": str(duration_ms),
            })
            search_url = f"{_SEARCH_URL}?{search_params}"
            search_data = _make_request(search_url)
            if not search_data:
                continue

            candidates: List[Dict[str, Any]] = search_data.get("candidates", [])
            if not candidates:
                continue

            allow_zero = (step_idx >= 3)

            def _duration_ms(c: Dict[str, Any]) -> int:
                """Candidate duration in ms."""
                return int(c.get("duration", 0))

            filtered = [
                c for c in candidates
                if (_duration_ms(c) == 0 and allow_zero) or abs(_duration_ms(c) - duration_ms) <= tolerance
            ]
            if not filtered:
                continue

            # Sort prioritizing cand.get("krctype") == 2 first, then -cand.get("score"), then smallest duration delta
            def _sort_key(c: Dict[str, Any]):
                is_krctype_2 = 0 if c.get("krctype") == 2 else 1
                score = -int(c.get("score", 0))
                dur = _duration_ms(c)
                delta = abs(dur - duration_ms) if dur != 0 else tolerance + 1
                return (is_krctype_2, score, delta)

            filtered.sort(key=_sort_key)

            # Download and validate candidates in order
            for cand in filtered:
                dl_params = urllib.parse.urlencode({
                    "ver": "1",
                    "client": "pc",
                    "id": str(cand.get("id", "")),
                    "accesskey": str(cand.get("accesskey", "")),
                    "fmt": "krc",
                    "charset": "utf8",
                })
                dl_url = f"{_DOWNLOAD_URL}?{dl_params}"
                dl_data = _make_request(dl_url)
                if not dl_data:
                    continue

                encoded_content: str = dl_data.get("content", "")
                if not encoded_content:
                    continue

                try:
                    krc_text = unpack_krc(encoded_content)
                except (ValueError, zlib.error, Exception):
                    continue

                # Validation check: must have syllable tokens
                if not _WORD_SYNC_RE.search(krc_text):
                    continue

                parsed = _parse_krc_text(krc_text)
                if parsed and parsed.lines:
                    return parsed

        return None
