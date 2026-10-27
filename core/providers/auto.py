"""Auto cascading lyrics provider — queries Unison -> lrc.red -> Kugou in streaming order."""

from typing import Optional

from core.models import SongLyrics, TaggingConfig
from core.providers.base import BaseLyricsProvider
from core.providers.kugou import KugouProvider
from core.providers.lrc_red import LrcRedProvider
from core.providers.unison import UnisonProvider


class AutoLyricsProvider(BaseLyricsProvider):
    """Cascading provider streaming queries across Unison, lrc.red, and Kugou."""

    @property
    def name(self) -> str:
        return "Auto"

    def __init__(
        self,
        unison: Optional[BaseLyricsProvider] = None,
        lrc_red: Optional[BaseLyricsProvider] = None,
        kugou: Optional[BaseLyricsProvider] = None,
    ) -> None:
        self.unison = unison if unison is not None else UnisonProvider()
        self.lrc_red = lrc_red if lrc_red is not None else LrcRedProvider()
        self.kugou = kugou if kugou is not None else KugouProvider()

    def search_word_sync(
        self,
        title: str,
        artist: str,
        duration_ms: int,
        config: Optional[TaggingConfig] = None,
    ) -> Optional[SongLyrics]:
        """Query Unison first, stream misses to lrc.red, and fallback to Kugou."""
        # Stage 1: Unison
        res = self.unison.search_word_sync(
            title=title,
            artist=artist,
            duration_ms=duration_ms,
            config=config,
        )
        if res:
            return self.validate_word_sync(res)

        # Stage 2: lrc.red
        res = self.lrc_red.search_word_sync(
            title=title,
            artist=artist,
            duration_ms=duration_ms,
            config=config,
        )
        if res:
            return self.validate_word_sync(res)

        # Stage 3: Kugou
        res = self.kugou.search_word_sync(
            title=title,
            artist=artist,
            duration_ms=duration_ms,
            config=config,
        )
        if res:
            return self.validate_word_sync(res)

        return None
