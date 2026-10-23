"""Text-based lyrics editor with debounced reconciliation and timing offset controls."""

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.models import SongLyrics
from core.sync_engine import apply_offset, reconcile_plain_text, to_plain_text


class LyricsEditorWidget(QWidget):
    """Lyrics editor providing timing offset shifting and debounced text reconciliation."""

    lyrics_changed = Signal(SongLyrics)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._base_song: Optional[SongLyrics] = None
        self._current_song: Optional[SongLyrics] = None
        self._current_offset_ms: int = 0
        self._is_updating_text: bool = False

        self._init_ui()

        # 250ms debounced typing timer
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(250)
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── Header bar with offset controls ──
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        lbl_offset = QLabel("Offset:")
        lbl_offset.setStyleSheet("font-weight: 600;")
        header_layout.addWidget(lbl_offset)

        self.btn_nudge_minus = QPushButton("-50ms")
        self.btn_nudge_minus.setFixedWidth(55)
        self.btn_nudge_minus.clicked.connect(lambda: self._nudge_offset(-50))
        header_layout.addWidget(self.btn_nudge_minus)

        self.slider_offset = QSlider(Qt.Horizontal)
        self.slider_offset.setRange(-2000, 2000)
        self.slider_offset.setSingleStep(10)
        self.slider_offset.setPageStep(50)
        self.slider_offset.setValue(0)
        self.slider_offset.valueChanged.connect(self._on_slider_offset_changed)
        header_layout.addWidget(self.slider_offset, stretch=1)

        self.btn_nudge_plus = QPushButton("+50ms")
        self.btn_nudge_plus.setFixedWidth(55)
        self.btn_nudge_plus.clicked.connect(lambda: self._nudge_offset(50))
        header_layout.addWidget(self.btn_nudge_plus)

        self.lbl_offset_val = QLabel("0 ms")
        self.lbl_offset_val.setFixedWidth(65)
        self.lbl_offset_val.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.lbl_offset_val)

        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setFixedWidth(55)
        self.btn_reset.clicked.connect(self._reset_offset)
        header_layout.addWidget(self.btn_reset)

        layout.addLayout(header_layout)

        # ── Plain-text editor area ──
        self.text_editor = QPlainTextEdit()
        self.text_editor.setPlaceholderText("Select a track to view and edit plain-text lyrics...")
        self.text_editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_editor, stretch=1)

    def set_lyrics(self, lyrics: Optional[SongLyrics]) -> None:
        """Load lyrics into the editor and reset offset."""
        self._is_updating_text = True
        self._base_song = lyrics
        self._current_song = lyrics
        self._current_offset_ms = 0

        self.slider_offset.setValue(0)
        self.lbl_offset_val.setText("0 ms")

        if lyrics:
            plain = to_plain_text(lyrics)
            self.text_editor.setPlainText(plain)
        else:
            self.text_editor.setPlainText("")

        self._is_updating_text = False

    def get_current_lyrics(self) -> Optional[SongLyrics]:
        return self._current_song

    def _nudge_offset(self, delta_ms: int) -> None:
        new_val = max(-2000, min(2000, self._current_offset_ms + delta_ms))
        self.slider_offset.setValue(new_val)

    def _reset_offset(self) -> None:
        self.slider_offset.setValue(0)

    def _on_slider_offset_changed(self, value: int) -> None:
        self._current_offset_ms = value
        sign = "+" if value > 0 else ""
        self.lbl_offset_val.setText(f"{sign}{value} ms")

        if self._base_song:
            self._current_song = apply_offset(self._base_song, self._current_offset_ms)
            self.lyrics_changed.emit(self._current_song)

    def _on_text_changed(self) -> None:
        if self._is_updating_text or not self._base_song:
            return
        self._debounce_timer.start()

    def _on_debounce_timeout(self) -> None:
        if not self._base_song:
            return

        edited_text = self.text_editor.toPlainText()
        reconciled = reconcile_plain_text(self._base_song, edited_text)
        self._base_song = reconciled

        if self._current_offset_ms != 0:
            self._current_song = apply_offset(self._base_song, self._current_offset_ms)
        else:
            self._current_song = self._base_song

        self.lyrics_changed.emit(self._current_song)
