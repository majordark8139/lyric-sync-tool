from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class SyncState(str, Enum):
    NONE = "NONE"
    LINE = "LINE"
    WORD = "WORD"


@dataclass
class Syllable:
    text: str = ""
    start_ms: int = 0
    duration_ms: int = 0

    def __init__(
        self,
        text: str = "",
        start_ms: int = 0,
        duration_ms: int = 0,
        *,
        abs_start_ms: Optional[int] = None,
        abs_end_ms: Optional[int] = None,
    ):
        self.text = text
        if abs_start_ms is not None:
            self.start_ms = abs_start_ms
        else:
            self.start_ms = start_ms

        if abs_end_ms is not None:
            self.duration_ms = max(0, abs_end_ms - self.start_ms)
        else:
            self.duration_ms = duration_ms

    @property
    def abs_start_ms(self) -> int:
        return self.start_ms

    @abs_start_ms.setter
    def abs_start_ms(self, val: int) -> None:
        end = self.start_ms + self.duration_ms
        self.start_ms = val
        self.duration_ms = max(0, end - val)

    @property
    def abs_end_ms(self) -> int:
        return self.start_ms + self.duration_ms

    @abs_end_ms.setter
    def abs_end_ms(self, val: int) -> None:
        self.duration_ms = max(0, val - self.start_ms)


@dataclass(frozen=True)
class SyllableSpan:
    abs_start_ms: int
    abs_end_ms: int
    text: str

    @property
    def start_ms(self) -> int:
        return self.abs_start_ms

    @property
    def duration_ms(self) -> int:
        return max(0, self.abs_end_ms - self.abs_start_ms)


@dataclass(init=False)
class LyricLine:
    start_ms: int = 0
    end_ms: int = 0
    syllables: List[Any] = field(default_factory=list)
    is_background: bool = False
    background_text: Optional[str] = None
    background_syllables: List[Any] = field(default_factory=list)
    background_end_ms: Optional[int] = None
    _text: Optional[str] = None
    singer_id: Optional[str] = None

    def __init__(
        self,
        start_ms: int = 0,
        end_ms: Optional[int] = None,
        syllables: Optional[List[Any]] = None,
        is_background: bool = False,
        background_text: Optional[str] = None,
        background_syllables: Optional[List[Any]] = None,
        background_end_ms: Optional[int] = None,
        _text: Optional[str] = None,
        text: Optional[str] = None,
        singer_id: Optional[str] = None,
    ):
        self.start_ms = start_ms
        self.syllables = list(syllables) if syllables is not None else []
        self.is_background = is_background
        self.background_text = background_text
        self.background_syllables = list(background_syllables) if background_syllables is not None else []
        self.background_end_ms = background_end_ms
        self._text = text if text is not None else _text
        self.singer_id = singer_id

        if end_ms is not None:
            self.end_ms = end_ms
        elif self.syllables:
            self.end_ms = max(getattr(s, "abs_end_ms", 0) for s in self.syllables)
        else:
            self.end_ms = start_ms

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        if self.syllables:
            return "".join(getattr(s, "text", "") for s in self.syllables).strip()
        return ""

    @text.setter
    def text(self, val: str) -> None:
        self._text = val

    @property
    def has_background(self) -> bool:
        return bool(self.background_text or self.background_syllables)



@dataclass(init=False)
class SongLyrics:
    metadata: Dict[str, str] = field(default_factory=dict)
    lines: List[LyricLine] = field(default_factory=list)
    _is_word_synced: bool = False
    agents: Dict[str, str] = field(default_factory=dict)

    def __init__(
        self,
        metadata: Optional[Dict[str, str]] = None,
        lines: Optional[List[LyricLine]] = None,
        is_word_synced: bool = False,
        agents: Optional[Dict[str, str]] = None,
    ):
        self.metadata = metadata if metadata is not None else {}
        self.lines = list(lines) if lines is not None else []
        self._is_word_synced = is_word_synced
        self.agents = dict(agents) if agents is not None else {}
        self.detect_sub_vocals()

    @property
    def is_word_synced(self) -> bool:
        if not self._is_word_synced:
            return False
        if not self.lines:
            return False

        has_valid_word_sync = False
        for line in self.lines:
            if not line.syllables:
                continue
            if len(line.syllables) >= 2:
                if any(
                    getattr(s, "duration_ms", 0) > 0
                    or (getattr(s, "abs_end_ms", 0) - getattr(s, "abs_start_ms", 0) > 0)
                    for s in line.syllables
                ):
                    has_valid_word_sync = True
                    break
            elif len(line.syllables) == 1:
                s = line.syllables[0]
                words = line.text.strip().split()
                if len(words) > 1:
                    continue
                dur = getattr(s, "duration_ms", 0) or (getattr(s, "abs_end_ms", 0) - getattr(s, "abs_start_ms", 0))
                if dur > 0:
                    has_valid_word_sync = True

        return has_valid_word_sync

    @is_word_synced.setter
    def is_word_synced(self, val: bool) -> None:
        self._is_word_synced = bool(val)

    def detect_sub_vocals(self) -> None:
        """Detect and mark background/sub-vocal lines during temporal overlaps."""
        if not self.lines:
            return

        # 1. Any line enclosed in parentheses is classified as background vocal
        for line in self.lines:
            text = "".join(getattr(s, "text", "") for s in line.syllables).strip()
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

            text = "".join(getattr(s, "text", "") for s in line.syllables).strip()
            lead_text = "".join(getattr(s, "text", "") for s in current_lead.syllables).strip()

            prev = self.lines[i - 1]
            overlaps = (line.start_ms < current_lead.end_ms) or (line.start_ms < prev.end_ms)

            if overlaps:
                # If overlapping line has identical text as lead line, flag as echo layer
                if text and (text == lead_text or text == "".join(getattr(s, "text", "") for s in prev.syllables).strip()):
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


@dataclass(init=False)
class TrackItem:
    file_path: str = ""
    title: str = ""
    artist: str = ""
    duration_ms: int = 0
    _sync_state: SyncState = SyncState.NONE
    matched_lyrics: Optional[SongLyrics] = None
    album: str = ""
    album_artist: str = ""
    status: str = ""

    def __init__(
        self,
        file_path: str = "",
        title: str = "",
        artist: str = "",
        duration_ms: int = 0,
        sync_state: Union[SyncState, str] = SyncState.NONE,
        matched_lyrics: Optional[SongLyrics] = None,
        album: str = "",
        album_artist: str = "",
        status: str = "",
    ):
        self.file_path = file_path
        self.title = title
        self.artist = artist
        self.duration_ms = duration_ms
        self.sync_state = sync_state
        self.matched_lyrics = matched_lyrics
        self.album = album
        self.album_artist = album_artist
        self.status = status

    @property
    def sync_state(self) -> SyncState:
        return self._sync_state

    @sync_state.setter
    def sync_state(self, val: Union[SyncState, str]) -> None:
        if isinstance(val, str):
            self._sync_state = SyncState[val.upper()] if val.upper() in SyncState.__members__ else SyncState.NONE
        else:
            self._sync_state = val


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

