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
        artist: str = "",
        title: str = "",
        duration_ms: int = 0,
        **kwargs,
    ) -> Optional[SongLyrics]:
        """Search and fetch word/syllable-synchronized lyrics for a track.

        Args:
            artist: Song artist.
            title: Song title.
            duration_ms: Track duration in milliseconds.
            **kwargs: Optional provider-specific parameters (e.g. config).

        Returns:
            SongLyrics object if matching lyrics are found, otherwise None.
        """
        pass

    @staticmethod
    def validate_word_sync(lyrics: Optional[SongLyrics]) -> Optional[SongLyrics]:
        """Validate that lyrics are genuinely word/syllable-synchronized.

        Enforces:
        - lyrics is not None
        - lyrics.is_word_synced is True
        - lyrics has at least one line with syllables
        - If all lines have 0 or 1 syllable spanning the whole duration: return None

        Returns:
            SongLyrics if strictly valid word-sync, otherwise None.
        """
        if lyrics is None:
            return None

        if not getattr(lyrics, "is_word_synced", False):
            return None

        if not lyrics.lines:
            return None

        # Check for genuine discrete word sync across lines
        has_multi_syllable = any(len(line.syllables) > 1 for line in lyrics.lines)
        if not has_multi_syllable:
            all_flat = True
            for line in lyrics.lines:
                if not line.syllables:
                    continue
                if len(line.syllables) == 1:
                    s = line.syllables[0]
                    s_dur = getattr(s, "duration_ms", 0) or (
                        getattr(s, "abs_end_ms", 0) - getattr(s, "abs_start_ms", 0)
                    )
                    line_dur = line.end_ms - line.start_ms
                    words = line.text.strip().split()
                    if len(words) > 1 or (line_dur > 0 and s_dur >= line_dur):
                        continue
                    else:
                        all_flat = False
                        break
            if all_flat:
                return None

        return lyrics
