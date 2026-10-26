"""lrc.red lyrics provider — fetches synced lyrics from lrc.red API."""

import urllib.parse
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.models import SongLyrics, TaggingConfig
from core.normalizer import extract_primary_artist, strip_features
from core.providers.base import BaseLyricsProvider
from core.tagger import _parse_lrc_text

_GET_URL = "https://api.lrc.red/api/get"
_SEARCH_URL = "https://api.lrc.red/api/search"

_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
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


class LrcRedProvider(BaseLyricsProvider):
    """Lyrics provider backed by the lrc.red service."""

    @property
    def name(self) -> str:
        """Return the unique provider identifier."""
        return "lrc.red"

    def search_word_sync(
        self,
        title: str = "",
        artist: str = "",
        duration_ms: int = 0,
        config: Optional[TaggingConfig] = None,
        **kwargs,
    ) -> Optional[SongLyrics]:
        """Search and fetch synchronized lyrics from lrc.red.

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
        duration_sec = int(round(duration_ms / 1000.0)) if duration_ms > 0 else 0

        # ── 1. Direct GET Attempt ──
        params: Dict[str, str] = {
            "track_name": title,
            "artist_name": primary_artist,
        }
        if duration_sec > 0:
            params["duration"] = str(duration_sec)

        try:
            resp = _HTTP_SESSION.get(_GET_URL, params=params, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                synced_lrc = data.get("syncedLyrics")
                if synced_lrc:
                    parsed = _parse_lrc_text(synced_lrc)
                    if parsed and parsed.lines:
                        return parsed
        except Exception:
            pass

        # ── 2. Search Fallback Attempt ──
        search_candidates = [
            (primary_artist, title),
            (primary_artist, strip_features(title)),
            ("", title),
        ]

        for s_artist, s_title in search_candidates:
            if not s_title:
                continue
            s_params: Dict[str, str] = {"track_name": s_title}
            if s_artist:
                s_params["artist_name"] = s_artist

            try:
                s_resp = _HTTP_SESSION.get(_SEARCH_URL, params=s_params, timeout=5.0)
                if s_resp.status_code == 200:
                    data = s_resp.json()
                    items: List[Dict[str, Any]] = data if isinstance(data, list) else data.get("results", [])
                    for item in items:
                        synced_lrc = item.get("syncedLyrics")
                        if synced_lrc:
                            parsed = _parse_lrc_text(synced_lrc)
                            if parsed and parsed.lines:
                                return parsed
            except Exception:
                continue

        return None
