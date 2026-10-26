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
    background_text: Optional[str] = None
    background_syllables: List[SyllableSpan] = field(default_factory=list)
    background_end_ms: Optional[int] = None
    _text: Optional[str] = None

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        return "".join(s.text for s in self.syllables).strip()

    @text.setter
    def text(self, val: str) -> None:
        self._text = val

    @property
    def has_background(self) -> bool:
        return bool(self.background_text or self.background_syllables)



@dataclass
class SongLyrics:
    metadata: Dict[str, str] = field(default_factory=dict)
    lines: List[LyricLine] = field(default_factory=list)
    is_word_synced: bool = False  # True when per-syllable timing spans are present

    def __post_init__(self) -> None:
        self.detect_sub_vocals()

    def detect_sub_vocals(self) -> None:
        """Detect and mark background/sub-vocal lines during temporal overlaps."""
        if not self.lines:
            return

        # 1. Any line enclosed in parentheses is classified as background vocal
        for line in self.lines:
            text = "".join(s.text for s in line.syllables).strip()
            if (text.startswith("(") and text.endswith(")")) or (
                text.startswith("（") and text.endswith("）")
            ):
                line.is_background = True

        # 2. Track lead line and classify overlapping lines
        current_lead: Optional[LyricLine] = None
        for i, line in enumerate(self.lines):
            if current_lead is None:
                if not line.is_background:
                    current_lead = line
                continue

            text = "".join(s.text for s in line.syllables).strip()
            lead_text = "".join(s.text for s in current_lead.syllables).strip()

            prev = self.lines[i - 1]
            overlaps = (line.start_ms < current_lead.end_ms) or (line.start_ms < prev.end_ms)

            if overlaps:
                # If overlapping line has identical text as lead line, flag as echo layer
                if text and (text == lead_text or text == "".join(s.text for s in prev.syllables).strip()):
                    line.is_background = True
                    continue

                if line.is_background:
                    continue

                # Compare line with current_lead to designate earlier/longer as Lead
                lead_dur = current_lead.end_ms - current_lead.start_ms
                curr_dur = line.end_ms - line.start_ms

                if current_lead.start_ms < line.start_ms:
                    line.is_background = True
                elif current_lead.start_ms > line.start_ms:
                    current_lead.is_background = True
                    line.is_background = False
                    current_lead = line
                else:
                    if lead_dur >= curr_dur:
                        line.is_background = True
                    else:
                        current_lead.is_background = True
                        line.is_background = False
                        current_lead = line
            else:
                if not line.is_background:
                    current_lead = line


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
