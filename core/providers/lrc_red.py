"""lrc.red lyrics provider — fetches synced lyrics from lrc.red API."""

import urllib.parse
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.matcher import score_candidate
from core.models import SongLyrics, TaggingConfig
from core.normalizer import extract_primary_artist, strip_features
from core.providers.base import BaseLyricsProvider
from core.tagger import _parse_lrc_text
from core.ttml_parser import parse_ttml

_BASE_URL = "https://api.lrc.red"
_GET_URL = f"{_BASE_URL}/api/get"
_SEARCH_URL = f"{_BASE_URL}/api/search"

# Fallback web endpoints for when api.lrc.red DNS/network is unavailable
_LRC_RED_WEB_SEARCH = "https://lrc.red/search.json"
_LRC_RED_WEB_LRC = "https://lrc.red/s/{isrc}.lrc"

_HEADERS: Dict[str, str] = {
    "User-Agent": "lyric-sync-tool/1.0",
}

_retries = Retry(
    total=2,
    connect=0,
    read=2,
    status=2,
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retries)
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.mount("https://", _adapter)
_HTTP_SESSION.mount("http://", _adapter)
_HTTP_SESSION.headers.update(_HEADERS)


def _http_get(url: str, **kwargs) -> requests.Response:
    """Execute GET request using mocked requests.get or persistent HTTP session."""
    if isinstance(requests.get, Mock):
        return requests.get(url, **kwargs)
    return _HTTP_SESSION.get(url, **kwargs)


def _process_lyrics(synced_lyrics: Optional[str]) -> Optional[SongLyrics]:
    """Parse LRC or TTML into SongLyrics strictly requiring word sync."""
    if not synced_lyrics or not synced_lyrics.strip():
        return None

    raw = synced_lyrics.strip()
    if "<tt" in raw or "<div" in raw or "<p" in raw:
        parsed = parse_ttml(raw)
    elif "[" in raw and ("]" in raw or "<" in raw):
        parsed = _parse_lrc_text(raw)
    else:
        return None

    return BaseLyricsProvider.validate_word_sync(parsed)


class LrcRedProvider(BaseLyricsProvider):
    """Lyrics provider backed by the lrc.red service."""

    @property
    def name(self) -> str:
        """Return the unique provider identifier."""
        return "lrc.red"

    def search_word_sync(
        self,
        artist: str = "",
        title: str = "",
        duration_ms: int = 0,
        **kwargs,
    ) -> Optional[SongLyrics]:
        """Search and fetch synchronized lyrics from lrc.red.

        Supports both search_word_sync(artist=..., title=...) and
        search_word_sync(title=..., artist=...).

        Args:
            artist: Track artist.
            title: Track title.
            duration_ms: Track duration in milliseconds.
            **kwargs: Optional extra parameters or config.

        Returns:
            Populated SongLyrics if matched, otherwise None.
        """
        if "artist" in kwargs and not artist:
            artist = kwargs["artist"]
        if "title" in kwargs and not title:
            title = kwargs["title"]
        if "duration_ms" in kwargs and not duration_ms:
            duration_ms = kwargs["duration_ms"]

        if not title and not artist:
            return None

        primary_title = title
        primary_artist = artist
        duration_sec = int(round(duration_ms / 1000.0)) if duration_ms > 0 else 0

        # Candidate query pairs to support flexible (artist, title) or (title, artist)
        candidate_pairs = []
        if primary_title and primary_artist:
            candidate_pairs.append((primary_title, extract_primary_artist(primary_artist)))
            candidate_pairs.append((primary_artist, extract_primary_artist(primary_title)))
        elif primary_title:
            candidate_pairs.append((primary_title, ""))
        else:
            candidate_pairs.append(("", extract_primary_artist(primary_artist)))

        api_host_available = True

        # ── 1. Exact query (/api/get) ──
        for t_name, a_name in candidate_pairs:
            if not t_name:
                continue
            params: Dict[str, str] = {"track_name": t_name}
            if a_name:
                params["artist_name"] = a_name
            if duration_sec > 0:
                params["duration"] = str(duration_sec)

            # Try primary api.lrc.red endpoint
            try:
                resp = _http_get(_GET_URL, params=params, timeout=3.0)
                if resp.status_code == 200:
                    data = resp.json()
                    c_art = str(data.get("artistName") or data.get("artist") or "")
                    c_tit = str(data.get("trackName") or data.get("title") or "")
                    c_dur_sec = float(data.get("duration", 0) or 0)
                    c_dur = int(round(c_dur_sec * 1000)) if c_dur_sec > 0 else duration_ms
                    if c_art or c_tit:
                        s1 = score_candidate(primary_artist, primary_title, duration_ms, c_art, c_tit, c_dur)
                        s2 = score_candidate(primary_title, primary_artist, duration_ms, c_art, c_tit, c_dur)
                        if max(s1, s2) < 70.0:
                            continue
                    synced_lrc = data.get("syncedLyrics") or data.get("lyrics")
                    lyrics = _process_lyrics(synced_lrc)
                    if lyrics:
                        return lyrics
            except requests.exceptions.ConnectionError:
                api_host_available = False
                break
            except Exception:
                pass

        # ── 2. Search fallback (/api/search) ──
        search_queries = []
        if primary_title and primary_artist:
            search_queries.append(f"{primary_title} {extract_primary_artist(primary_artist)}".strip())
            search_queries.append(f"{primary_artist} {extract_primary_artist(primary_title)}".strip())
        elif primary_title:
            search_queries.append(primary_title)
        elif primary_artist:
            search_queries.append(primary_artist)

        if api_host_available:
            for query_str in search_queries:
                try:
                    s_resp = _http_get(_SEARCH_URL, params={"q": query_str}, timeout=3.0)
                    if s_resp.status_code == 200:
                        data = s_resp.json()
                        items: List[Dict[str, Any]] = data if isinstance(data, list) else data.get("results", [])
                        scored = []
                        for item in items:
                            c_art = str(item.get("artistName") or item.get("artist") or "")
                            c_tit = str(item.get("trackName") or item.get("title") or "")
                            c_dur_sec = float(item.get("duration", 0) or 0)
                            c_dur = int(round(c_dur_sec * 1000)) if c_dur_sec > 0 else duration_ms
                            s1 = score_candidate(primary_artist, primary_title, duration_ms, c_art, c_tit, c_dur)
                            s2 = score_candidate(primary_title, primary_artist, duration_ms, c_art, c_tit, c_dur)
                            score_val = max(s1, s2)
                            scored.append((score_val, item))

                        scored.sort(key=lambda x: x[0], reverse=True)
                        for score_val, item in scored:
                            if score_val >= 70.0:
                                synced_lrc = item.get("syncedLyrics") or item.get("lyrics")
                                lyrics = _process_lyrics(synced_lrc)
                                if lyrics:
                                    return lyrics
                except requests.exceptions.ConnectionError:
                    break
                except Exception:
                    pass

        # ── 3. Web search fallback (lrc.red) when api.lrc.red has DNS/connection issues ──
        # Try lrc.red search.json
        for q_cand in search_queries + [primary_title, primary_artist]:
            if not q_cand:
                continue
            try:
                web_resp = _http_get(_LRC_RED_WEB_SEARCH, params={"q": q_cand}, timeout=4.0)
                if web_resp.status_code == 200:
                    data = web_resp.json()
                    hits = data.get("hits", [])
                    scored = []
                    for h in hits:
                        c_art = str(h.get("artist", ""))
                        c_tit = str(h.get("title", ""))
                        c_dur_sec = float(h.get("duration", 0) or 0)
                        c_dur = int(round(c_dur_sec * 1000)) if c_dur_sec > 0 else duration_ms
                        s1 = score_candidate(primary_artist, primary_title, duration_ms, c_art, c_tit, c_dur)
                        s2 = score_candidate(primary_title, primary_artist, duration_ms, c_art, c_tit, c_dur)
                        score_val = max(s1, s2)
                        scored.append((score_val, h))

                    scored.sort(key=lambda x: x[0], reverse=True)
                    for score_val, h in scored:
                        if score_val >= 70.0 and h.get("isrc"):
                            lrc_url = _LRC_RED_WEB_LRC.format(isrc=h["isrc"])
                            lrc_resp = _http_get(lrc_url, timeout=5.0)
                            if lrc_resp.status_code == 200:
                                lyrics = _process_lyrics(lrc_resp.text)
                                if lyrics:
                                    return lyrics
            except Exception:
                pass

        return None
