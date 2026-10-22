from abc import ABC, abstractmethod
from typing import Optional

from core.models import SongLyrics, TaggingConfig


class BaseLyricsProvider(ABC):
    """Abstract Base Class for syllable-synchronized lyrics providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique provider identifier/name."""
        pass

    @abstractmethod
    def search_word_sync(
        self,
        title: str,
        artist: str,
        duration_ms: int,
        config: Optional[TaggingConfig] = None,
    ) -> Optional[SongLyrics]:
        """Search and fetch word/syllable-synchronized lyrics for a track.

        Args:
            title: Song title.
            artist: Song artist.
            duration_ms: Track duration in milliseconds.
            config: Optional TaggingConfig controlling tolerances and fallback ladders.

        Returns:
            SongLyrics object if matching lyrics are found, otherwise None.
        """
        pass
