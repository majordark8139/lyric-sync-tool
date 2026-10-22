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


@dataclass
class SongLyrics:
    metadata: Dict[str, str] = field(default_factory=dict)
    lines: List[LyricLine] = field(default_factory=list)


@dataclass
class TrackItem:
    file_path: str
    title: str
    artist: str
    duration_ms: int
    sync_state: SyncState
    matched_lyrics: Optional[SongLyrics] = None


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
