"""Unison lyrics provider — structured client for Unison and Better Lyrics endpoints."""

import urllib.parse
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.matcher import score_candidate
from core.models import LyricLine, SongLyrics, TaggingConfig
from core.normalizer import extract_primary_artist, strip_features
from core.providers.base import BaseLyricsProvider
from core.tagger import _parse_lrc_text
from core.ttml_parser import parse_ttml

_UNISON_BASE_URL = "https://unison.boidu.dev"
_UNISON_SEARCH_URL = f"{_UNISON_BASE_URL}/lyrics/search"
_LRCLIB_FALLBACK_GET = "https://lrclib.net/api/get"

_HEADERS: Dict[str, str] = {
    "User-Agent": "Noctis (https://github.com/heartached/Noctis)",
    "Accept": "application/json",
}

_retries = Retry(
    total=2,
    backoff_factor=1.0,
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retries)
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.mount("https://", _adapter)
_HTTP_SESSION.mount("http://", _adapter)
_HTTP_SESSION.headers.update(_HEADERS)


def _process_lyrics_data(data: Dict[str, Any]) -> Optional[SongLyrics]:
    """Parse raw lyrics response dict into SongLyrics respecting format and syncType."""
    raw_lyrics = (
        data.get("lyrics")
        or data.get("syncedLyrics")
        or data.get("plainLyrics")
    )
    if not raw_lyrics:
        return None

    fmt = str(data.get("format", "")).strip().lower()
    sync_type = str(data.get("syncType", "")).strip().lower()

    if fmt == "ttml" or "<tt" in raw_lyrics:
        parsed = parse_ttml(raw_lyrics)
    elif fmt == "lrc" or ("[" in raw_lyrics and ("]" in raw_lyrics or "<" in raw_lyrics)):
        parsed = _parse_lrc_text(raw_lyrics)
    else:
        lines = [
            LyricLine(start_ms=0, end_ms=0, text=l.strip(), syllables=[])
            for l in raw_lyrics.splitlines()
            if l.strip()
        ]
        parsed = SongLyrics(lines=lines, is_word_synced=False)

    if not parsed or not parsed.lines:
        return None

    if sync_type == "linesync":
        for line in parsed.lines:
            line.syllables = []
        parsed.is_word_synced = False
    elif sync_type == "richsync":
        if not parsed.is_word_synced:
            # If discrete syllable tokens are missing, fall back to line sync
            for line in parsed.lines:
                line.syllables = []
            parsed.is_word_synced = False
    elif sync_type == "plain":
        for line in parsed.lines:
            line.syllables = []
        parsed.is_word_synced = False

    return parsed


class UnisonProvider(BaseLyricsProvider):
    """Lyrics provider backed by Unison service supporting richsync TTML and LRC."""

    @property
    def name(self) -> str:
        """Return the unique provider identifier."""
        return "Unison"

    def search_word_sync(
        self,
        title: str = "",
        artist: str = "",
        duration_ms: int = 0,
        config: Optional[TaggingConfig] = None,
        **kwargs,
    ) -> Optional[SongLyrics]:
        """Search and fetch synchronized lyrics from Unison.

        Args:
            title: Track title.
            artist: Track artist.
            duration_ms: Track duration in milliseconds.
            config: Optional configuration.

        Returns:
            Populated SongLyrics if matched, otherwise None.
        """
        if "artist" in kwargs and not artist:
            artist = kwargs["artist"]
        if "title" in kwargs and not title:
            title = kwargs["title"]
        if "duration_ms" in kwargs and not duration_ms:
            duration_ms = kwargs["duration_ms"]

        if not title:
            return None

        primary_artist = extract_primary_artist(artist)
        query = f"{primary_artist} {title}".strip() if primary_artist else title.strip()
        duration_sec = int(round(duration_ms / 1000.0)) if duration_ms > 0 else 0

        # ── 1. Query Unison search endpoint ──
        try:
            params = {"q": query, "limit": "20"}
            resp = _HTTP_SESSION.get(_UNISON_SEARCH_URL, params=params, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()

                # Check if response is a direct lyrics object (e.g. mocked or direct)
                if isinstance(data, dict):
                    if "lyrics" in data or "syncedLyrics" in data:
                        parsed = _process_lyrics_data(data)
                        if parsed:
                            return parsed

                    data_obj = data.get("data")
                    if isinstance(data_obj, dict) and ("lyrics" in data_obj or "syncedLyrics" in data_obj):
                        parsed = _process_lyrics_data(data_obj)
                        if parsed:
                            return parsed

                    hits = data_obj if isinstance(data_obj, list) else data.get("results", [])
                elif isinstance(data, list):
                    hits = data
                else:
                    hits = []

                best_hit = None
                best_score = -1.0

                for hit in hits:
                    if not isinstance(hit, dict):
                        continue
                    if hit.get("hidden") is True:
                        continue

                    hit_id = hit.get("id")
                    if not hit_id:
                        continue

                    hit_song = hit.get("song", "")
                    hit_artist = hit.get("artist", "")
                    hit_dur_sec = float(hit.get("duration", 0) or 0)
                    hit_dur_ms = int(round(hit_dur_sec * 1000)) if hit_dur_sec > 0 else duration_ms

                    score = score_candidate(
                        audio_artist=artist,
                        audio_title=title,
                        audio_duration_ms=duration_ms,
                        cand_artist=hit_artist,
                        cand_title=hit_song,
                        cand_duration_ms=hit_dur_ms,
                    )

                    # Require minimum score >= 70.0 (strict title and duration matching)
                    if score < 70.0:
                        continue

                    # Slight tiebreaker for richsync among qualified candidates
                    effective_score = score + (0.5 if str(hit.get("syncType", "")).lower() == "richsync" else 0.0)

                    if effective_score > best_score:
                        best_score = effective_score
                        best_hit = hit

                if hits and not best_hit:
                    # Search hits were returned but none met the >= 70.0 threshold
                    return None

                if best_hit:
                    entry_url = f"{_UNISON_BASE_URL}/lyrics/{best_hit['id']}"
                    entry_resp = _HTTP_SESSION.get(entry_url, timeout=5.0)
                    if entry_resp.status_code == 200:
                        entry_data = entry_resp.json()
                        inner = entry_data.get("data") if isinstance(entry_data, dict) and "data" in entry_data else entry_data
                        if isinstance(inner, dict):
                            parsed = _process_lyrics_data(inner)
                            if parsed:
                                return parsed
        except Exception:
            pass

        # ── 2. Fallback to LRCLib endpoint ──
        try:
            fb_params: Dict[str, str] = {
                "track_name": title,
                "artist_name": primary_artist,
            }
            if duration_sec > 0:
                fb_params["duration"] = str(duration_sec)

            fb_resp = _HTTP_SESSION.get(_LRCLIB_FALLBACK_GET, params=fb_params, timeout=5.0)
            if fb_resp.status_code == 200:
                data = fb_resp.json()
                cand_song = data.get("trackName") or data.get("name") or data.get("track_name") or ""
                cand_artist = data.get("artistName") or data.get("artist") or ""
                cand_dur = int(round(float(data.get("duration", 0) or 0) * 1000)) if data.get("duration") else duration_ms
                if cand_song:
                    fb_score = score_candidate(artist, title, duration_ms, cand_artist, cand_song, cand_dur)
                    if fb_score < 70.0:
                        return None
                parsed = _process_lyrics_data(data)
                if parsed:
                    return parsed
        except Exception:
            pass

        return None
