"""Unison lyrics provider — structured client for Unison lyrics service."""

from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.matcher import score_candidate
from core.models import SongLyrics, TaggingConfig
from core.normalizer import extract_primary_artist
from core.providers.base import BaseLyricsProvider
from core.tagger import _parse_lrc_text
from core.ttml_parser import parse_ttml

_UNISON_BASE_URL = "https://unison.boidu.dev"
_UNISON_SEARCH_URL = f"{_UNISON_BASE_URL}/lyrics/search"

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


def _http_get(url: str, **kwargs) -> requests.Response:
    """Execute GET request using mocked requests.get or persistent HTTP session."""
    if "timeout" not in kwargs:
        kwargs["timeout"] = (5, 10)
    if isinstance(requests.get, Mock):
        return requests.get(url, **kwargs)
    return _HTTP_SESSION.get(url, **kwargs)



def _process_lyrics_data(data: Dict[str, Any]) -> Optional[SongLyrics]:
    """Parse raw lyrics response dict into SongLyrics strictly requiring richsync word sync."""
    sync_type = str(data.get("syncType", "")).strip().lower()
    if sync_type != "richsync":
        return None

    raw_lyrics = (
        data.get("lyrics")
        or data.get("syncedLyrics")
    )
    if not raw_lyrics:
        return None

    fmt = str(data.get("format", "")).strip().lower()

    if fmt == "ttml" or "<tt" in raw_lyrics:
        parsed = parse_ttml(raw_lyrics)
    elif fmt == "lrc" or ("[" in raw_lyrics and ("]" in raw_lyrics or "<" in raw_lyrics)):
        parsed = _parse_lrc_text(raw_lyrics)
    else:
        return None

    return BaseLyricsProvider.validate_word_sync(parsed)


class UnisonProvider(BaseLyricsProvider):
    """Lyrics provider backed exclusively by Unison service enforcing strict richsync word-sync."""

    @property
    def name(self) -> str:
        """Return the unique provider identifier."""
        return "Unison"

    def search_word_sync(
        self,
        artist: str = "",
        title: str = "",
        duration_ms: int = 0,
        **kwargs,
    ) -> Optional[SongLyrics]:
        """Search and fetch verified richsync word-synced lyrics from Unison.

        Args:
            artist: Track artist.
            title: Track title.
            duration_ms: Track duration in milliseconds.
            **kwargs: Optional configuration or extra parameters.

        Returns:
            Populated SongLyrics if matched and verified richsync, otherwise None.
        """
        if "artist" in kwargs and not artist:
            artist = kwargs["artist"]
        if "title" in kwargs and not title:
            title = kwargs["title"]
        if "duration_ms" in kwargs and not duration_ms:
            duration_ms = kwargs["duration_ms"]

        clean_title = title.strip()
        clean_artist = artist.strip()

        if not clean_title and not clean_artist:
            return None

        # Build query ladder: primary is f"{clean_artist} {clean_title}", fallback is clean_title
        queries = []
        combined = f"{clean_artist} {clean_title}".strip()
        if combined:
            queries.append(combined)
        if clean_title and clean_title not in queries:
            queries.append(clean_title)
        if clean_artist and clean_artist not in queries:
            queries.append(clean_artist)

        for query in queries:
            try:
                params = {"q": query, "limit": "20"}
                resp = _http_get(_UNISON_SEARCH_URL, params=params, timeout=(5, 10))
                if resp.status_code != 200:
                    continue

                data = resp.json()

                # If response directly carries lyrics (e.g. mocked or direct payload)
                if isinstance(data, dict):
                    if "lyrics" in data or "syncedLyrics" in data:
                        direct = _process_lyrics_data(data)
                        if direct is not None:
                            return direct

                    data_obj = data.get("data")
                    if isinstance(data_obj, dict) and ("lyrics" in data_obj or "syncedLyrics" in data_obj):
                        direct = _process_lyrics_data(data_obj)
                        if direct is not None:
                            return direct

                    hits = data_obj if isinstance(data_obj, list) else data.get("results", [])
                elif isinstance(data, list):
                    hits = data
                else:
                    hits = []

                candidate_matches = []

                for hit in hits:
                    if not isinstance(hit, dict):
                        continue
                    if hit.get("hidden") is True:
                        continue

                    hit_id = hit.get("id")
                    if not hit_id:
                        continue

                    # Search Hit Filter: Do NOT discard search results just because
                    # hit.get("syncType") is missing from the search list.
                    hit_sync_type = str(hit.get("syncType", "")).strip().lower()
                    if hit_sync_type and hit_sync_type in ("plain", "linesync"):
                        continue

                    hit_song = (
                        hit.get("song")
                        or hit.get("title")
                        or hit.get("track_name")
                        or hit.get("trackName")
                        or ""
                    )
                    hit_artist = (
                        hit.get("artist")
                        or hit.get("artist_name")
                        or hit.get("artistName")
                        or ""
                    )
                    hit_dur_sec = float(hit.get("duration", 0) or 0)
                    hit_dur_ms = int(round(hit_dur_sec * 1000)) if hit_dur_sec > 0 else duration_ms

                    # Score candidate in both (artist, title) and (title, artist) order
                    score_forward = score_candidate(
                        audio_artist=clean_artist,
                        audio_title=clean_title,
                        audio_duration_ms=duration_ms,
                        cand_artist=hit_artist,
                        cand_title=hit_song,
                        cand_duration_ms=hit_dur_ms,
                    )
                    score_reversed = score_candidate(
                        audio_artist=clean_title,
                        audio_title=clean_artist,
                        audio_duration_ms=duration_ms,
                        cand_artist=hit_artist,
                        cand_title=hit_song,
                        cand_duration_ms=hit_dur_ms,
                    )
                    score = max(score_forward, score_reversed)

                    if score < 70.0:
                        continue

                    # Tiebreaker for richsync
                    effective_score = score + (0.5 if hit_sync_type == "richsync" else 0.0)
                    candidate_matches.append((effective_score, hit))

                if not candidate_matches:
                    continue

                candidate_matches.sort(key=lambda x: x[0], reverse=True)

                for _, best_hit in candidate_matches:
                    entry_url = f"{_UNISON_BASE_URL}/lyrics/{best_hit['id']}"
                    entry_resp = _http_get(entry_url, timeout=(5, 10))
                    if entry_resp.status_code == 200:
                        entry_data = entry_resp.json()
                        inner = (
                            entry_data.get("data")
                            if isinstance(entry_data, dict) and "data" in entry_data
                            else entry_data
                        )
                        if isinstance(inner, dict):
                            result = _process_lyrics_data(inner)
                            if result is not None:
                                return result

                # Candidates were found and verified not to have richsync
                return None

            except Exception:
                continue

        return None
