from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional


class SyncState(Enum):
    NONE = auto()
    LINE = auto()
    WORD = auto()


@dataclass(frozen=True)
class SyllableSpan:
    abs_start_ms: int
    abs_end_ms: int
    text: str


@dataclass
class LyricLine:
    start_ms: int
    end_ms: int
    syllables: List[SyllableSpan] = field(default_factory=list)
    is_background: bool = False


@dataclass
class SongLyrics:
    metadata: Dict[str, str] = field(default_factory=dict)
    lines: List[LyricLine] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.detect_sub_vocals()

    def detect_sub_vocals(self) -> None:
        """Detect and mark background/sub-vocal lines during temporal overlaps."""
        for i in range(1, len(self.lines)):
            prev = self.lines[i - 1]
            curr = self.lines[i]
            # If line overlaps with previous line or text is parenthesized during overlap
            if curr.start_ms < prev.end_ms:
                curr.is_background = True
            else:
                full_text = "".join(s.text for s in curr.syllables).strip()
                if (full_text.startswith("(") and full_text.endswith(")")) and curr.start_ms < prev.end_ms:
                    curr.is_background = True


@dataclass
class TrackItem:
    file_path: str
    title: str
    artist: str
    duration_ms: int
    sync_state: SyncState
    matched_lyrics: Optional[SongLyrics] = None
    album: str = ""
    album_artist: str = ""


@dataclass
class TaggingConfig:
    write_lrc: bool = True
    lrc_extension: str = "lrc"  # "lrc", "elrc", "both"
    write_ttml: bool = True
    purge_stale_line_sync: bool = True  # Deletes line-synced / plain sidecars once word sync is written
    backup_stale_sidecars: bool = False
    embed_metadata: bool = True
    flac_embed_vorbis: bool = True
    mp3_embed_uslt_fallback: bool = True
    mp3_embed_txxx: bool = True
    mp3_embed_sylt: bool = False
    m4a_embed_atom: bool = True
    overwrite_existing_word_sync: bool = False
    duration_tolerance_ms: int = 2500
    smart_fallback: bool = True  # Enables cascading ladder if raw query yields no word-synced result
