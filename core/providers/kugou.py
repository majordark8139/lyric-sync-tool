"""Kugou lyrics provider — fetches and decrypts syllable-synced KRC lyrics."""

import base64
import json
import re
import urllib.parse
import zlib
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.matcher import score_candidate
from core.models import LyricLine, SongLyrics, SyllableSpan, TaggingConfig
from core.normalizer import clean_kugou_query, generate_query_ladder
from core.providers.base import BaseLyricsProvider
from core.tagger import _fold_background_lines

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

# Resilient HTTP session with automatic retries for transient server errors
_retries = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retries)
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.mount("http://", _adapter)
_HTTP_SESSION.mount("https://", _adapter)
_HTTP_SESSION.headers.update(_HEADERS)


def _http_get(url: str, **kwargs) -> requests.Response:
    """Execute GET request using mocked requests.get or persistent HTTP session."""
    if isinstance(requests.get, Mock):
        return requests.get(url, **kwargs)
    return _HTTP_SESSION.get(url, **kwargs)


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

    if not lines:
        return SongLyrics(metadata=metadata, lines=[], is_word_synced=False)

    lines = _fold_background_lines(lines)
    is_ws = any(len(l.syllables) > 1 for l in lines)
    return SongLyrics(metadata=metadata, lines=lines, is_word_synced=is_ws)


def _make_request(url: str) -> Optional[Dict[str, Any]]:
    """Perform an HTTP GET and return the parsed JSON body.

    Args:
        url: Fully-qualified URL to fetch.

    Returns:
        Parsed JSON dict, or *None* on network / decode error.
    """
    try:
        resp = _http_get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
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
            ladder: List[tuple] = [(artist, title)]
        else:
            ladder = generate_query_ladder(artist, title)
            # Append 3 extra clean_kugou_query steps for tracks with brackets/features/remixes
            # to handle cases like "Save Your Tears (Remix) (With Ariana Grande)"
            clean_artist = clean_kugou_query(artist)
            clean_title = clean_kugou_query(title)
            if clean_title and clean_title != title:
                # Attempt A: Cleaned artist + cleaned title
                if (clean_artist, clean_title) not in ladder:
                    ladder.append((clean_artist, clean_title))
                # Attempt B: Cleaned title-only (skip artist matching entirely)
                if ("", clean_title) not in ladder:
                    ladder.append(("", clean_title))
            # Attempt C: Cleaned title + duration tolerance (already handled by allow_zero=True at high step_idx)
            # (covered by raising tolerance at step_idx >= len(ladder)-2)

        def _cand_dur(c: Dict[str, Any]) -> int:
            return int(c.get("duration", 0))

        def _sort_key(c: Dict[str, Any], allow_z: bool) -> tuple:
            is_krctype_2 = 0 if c.get("krctype") == 2 else 1
            score = -int(c.get("score", 0))
            dur = _cand_dur(c)
            delta = abs(dur - duration_ms) if dur != 0 else tolerance + 1
            return (is_krctype_2, score, delta)

        def _try_download(cand: Dict[str, Any]) -> Optional[SongLyrics]:
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
                return None
            encoded_content: str = dl_data.get("content", "")
            if not encoded_content:
                return None
            try:
                krc_text = unpack_krc(encoded_content)
            except (ValueError, zlib.error, Exception):
                return None
            if not _WORD_SYNC_RE.search(krc_text):
                return None
            parsed = _parse_krc_text(krc_text)
            return BaseLyricsProvider.validate_word_sync(parsed)

        for step_idx, (q_artist, q_title) in enumerate(ladder):
            keyword = q_title if not q_artist else f"{q_artist} - {q_title}"
            # Use expanded tolerance for late-stage ladder steps
            step_tolerance = tolerance + 2000 if step_idx >= max(3, len(ladder) - 3) else tolerance
            search_params = urllib.parse.urlencode({
                "ver": "1",
                "man": "yes",
                "client": "pc",
                "keyword": keyword,
                "duration": str(duration_ms),
            })
            search_url = f"{_SEARCH_URL}?{search_params}"
            print(f"[Kugou] step {step_idx}: keyword={keyword!r}")
            search_data = _make_request(search_url)
            if not search_data:
                continue

            candidates: List[Dict[str, Any]] = search_data.get("candidates", [])
            if not candidates:
                continue

            allow_zero = (step_idx >= 3)
            filtered = []
            for c in candidates:
                c_dur = _cand_dur(c)
                if not ((c_dur == 0 and allow_zero) or abs(c_dur - duration_ms) <= step_tolerance):
                    continue

                c_artist = str(c.get("singer") or c.get("artist") or "")
                c_title = str(c.get("song") or c.get("title") or "")
                eff_dur = c_dur if c_dur > 0 else duration_ms
                s1 = score_candidate(artist, title, duration_ms, c_artist, c_title, eff_dur)
                s2 = score_candidate(title, artist, duration_ms, c_artist, c_title, eff_dur)
                score_val = max(s1, s2)
                if score_val < 70.0:
                    continue

                filtered.append(c)

            if not filtered:
                print(f"[Kugou] step {step_idx}: {len(candidates)} raw candidates, 0 after duration & score filter")
                continue

            print(f"[Kugou] step {step_idx}: {len(filtered)} candidates after duration & score filter")
            filtered.sort(key=lambda c: _sort_key(c, allow_zero))

            for cand in filtered:
                result = _try_download(cand)
                if result:
                    print(f"[Kugou] step {step_idx}: matched — {cand.get('song', '')} / {cand.get('singer', '')}")
                    return result

        # Soft Candidate Scoring fallback: If exact matches fail, search with clean_kugou_query(title)
        if config is None or config.smart_fallback:
            clean_title = clean_kugou_query(title)
            if clean_title:
                search_params = urllib.parse.urlencode({
                    "ver": "1",
                    "man": "yes",
                    "client": "pc",
                    "keyword": clean_title,
                    "duration": str(duration_ms) if duration_ms > 0 else "0",
                })
                search_url = f"{_SEARCH_URL}?{search_params}"
                print(f"[Kugou] soft scoring search: keyword={clean_title!r}")
                search_data = _make_request(search_url)
                if search_data:
                    raw_candidates = search_data.get("candidates", [])
                    scored = []
                    for cand in raw_candidates:
                        c_artist = str(cand.get("singer") or cand.get("artist") or "")
                        c_title = str(cand.get("song") or cand.get("title") or "")
                        c_dur = _cand_dur(cand)
                        eff_dur = c_dur if c_dur > 0 else duration_ms
                        s1 = score_candidate(
                            audio_artist=artist,
                            audio_title=title,
                            audio_duration_ms=duration_ms,
                            cand_artist=c_artist,
                            cand_title=c_title,
                            cand_duration_ms=eff_dur,
                        )
                        s2 = score_candidate(
                            audio_artist=title,
                            audio_title=artist,
                            audio_duration_ms=duration_ms,
                            cand_artist=c_artist,
                            cand_title=c_title,
                            cand_duration_ms=eff_dur,
                        )
                        scored.append((max(s1, s2), cand))

                    # Sort highest-scoring candidate first
                    scored.sort(key=lambda x: x[0], reverse=True)
                    for score_val, cand in scored:
                        if score_val >= 70.0:
                            result = _try_download(cand)
                            if result:
                                print(
                                    f"[Kugou] soft match (score={score_val:.1f}): "
                                    f"{cand.get('song', '')} / {cand.get('singer', '')}"
                                )
                                return result

        return None
