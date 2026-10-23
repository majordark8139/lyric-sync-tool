"""Constant-speed 60 FPS syllable color sweep preview widget with real audio playback."""

import os
import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.models import LyricLine, SongLyrics


def _format_time(ms: int) -> str:
    """Format milliseconds to mm:ss.xx centisecond timestamp."""
    ms = max(0, ms)
    cs = (ms // 10) % 100
    sec = (ms // 1000) % 60
    minutes = ms // 60000
    return f"{minutes:02d}:{sec:02d}.{cs:02d}"


class _KaraokeCanvas(QWidget):
    """Sub-pixel clipping QPainter canvas for syllable rendering."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lyrics: Optional[SongLyrics] = None
        self._current_ms: int = 0

        self._base_font = QFont("sans-serif", 13)
        self._active_font = QFont("sans-serif", 14, QFont.Bold)

        self._dim_color = QColor(140, 140, 140)
        self._highlight_color = QColor(0, 180, 255)
        self._past_line_color = QColor(180, 180, 180)

    def set_lyrics(self, lyrics: Optional[SongLyrics]) -> None:
        self._lyrics = lyrics
        self.update()

    def set_time(self, current_ms: int) -> None:
        self._current_ms = current_ms
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

            width = float(self.width())
            height = float(self.height())

            if not self._lyrics or not self._lyrics.lines:
                painter.setFont(self._base_font)
                painter.setPen(QPen(self._dim_color))
                painter.setClipping(False)
                painter.drawText(
                    QRectF(0.0, 0.0, width, height),
                    int(Qt.AlignmentFlag.AlignCenter),
                    "No synchronized lyrics available",
                )
                return

            lines = self._lyrics.lines
            # Find active line index
            active_idx = 0
            for i, line in enumerate(lines):
                if line.start_ms <= self._current_ms <= line.end_ms:
                    active_idx = i
                    break
                elif line.start_ms > self._current_ms:
                    active_idx = max(0, i - 1)
                    break
            else:
                active_idx = len(lines) - 1

            line_height = 36.0
            center_y = height / 2.0

            # Draw lines around the active line
            for i, line in enumerate(lines):
                offset_idx = i - active_idx
                y = center_y + (offset_idx * line_height)

                if y < -line_height or y > height + line_height:
                    continue

                line_text = "".join(s.text for s in line.syllables)
                if not line_text:
                    continue

                is_active_line = (i == active_idx) and (line.start_ms <= self._current_ms <= line.end_ms + 400)

                if is_active_line:
                    self._draw_active_line(painter, line, line_text, y, width, line_height)
                else:
                    painter.setFont(self._base_font)
                    color = self._past_line_color if i < active_idx else self._dim_color
                    painter.setPen(QPen(color))
                    painter.setClipping(False)
                    fm = QFontMetricsF(self._base_font)
                    text_width = fm.horizontalAdvance(line_text)
                    x = max(10.0, (width - text_width) / 2.0)
                    painter.drawText(QPointF(x, y + fm.ascent() / 2.0), line_text)
        finally:
            painter.end()

    def _draw_active_line(
        self,
        painter: QPainter,
        line: LyricLine,
        line_text: str,
        y: float,
        view_width: float,
        line_height: float,
    ) -> None:
        """Render active line with constant-speed syllable clipping sweep."""
        fm_active = QFontMetricsF(self._active_font)
        text_width = fm_active.horizontalAdvance(line_text)
        x_start = max(10.0, (view_width - text_width) / 2.0)
        baseline_y = y + fm_active.ascent() / 2.0

        # 1. Base Layer (Inactive text)
        painter.setFont(self._active_font)
        painter.setPen(QPen(self._dim_color))
        painter.setClipping(False)
        painter.drawText(QPointF(x_start, baseline_y), line_text)

        # 2. Compute sweep clipping width across syllables
        current_x = x_start
        sweep_x = x_start

        for syl in line.syllables:
            syl_w = fm_active.horizontalAdvance(syl.text)
            if self._current_ms <= syl.abs_start_ms:
                # Not started yet
                break
            elif self._current_ms >= syl.abs_end_ms:
                # Fully finished
                sweep_x = current_x + syl_w
            else:
                # Partially active syllable: linear interpolation
                dur = max(1, syl.abs_end_ms - syl.abs_start_ms)
                progress = min(1.0, max(0.0, (self._current_ms - syl.abs_start_ms) / dur))
                sweep_x = current_x + (syl_w * progress)
                break
            current_x += syl_w

        # 3. Active Layer: Clip from line start up to sweep_x
        if sweep_x > x_start:
            painter.save()
            try:
                clip_rect = QRectF(x_start, y - line_height / 2.0, sweep_x - x_start, line_height)
                painter.setClipRect(clip_rect)
                painter.setClipping(True)
                painter.setPen(QPen(self._highlight_color))
                painter.drawText(QPointF(x_start, baseline_y), line_text)
            finally:
                painter.restore()
        else:
            painter.setClipping(False)


class KaraokePreviewWidget(QWidget):
    """Karaoke live-preview widget with real audio playback, 60 FPS sweeper, and transport bar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._audio_path: Optional[str] = None
        self._lyrics: Optional[SongLyrics] = None
        self._total_duration_ms: int = 0
        self._current_ms: int = 0
        self._is_playing: bool = False
        self._last_tick_time: float = 0.0

        # Audio Playback Engine
        self._audio_output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_player_position_changed)
        self._player.durationChanged.connect(self._on_player_duration_changed)
        self._player.playbackStateChanged.connect(self._on_player_playback_state_changed)

        self._init_ui()

        # 60 FPS synthetic fallback clock (~16ms timer interval)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_timer_tick)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Canvas
        self.canvas = _KaraokeCanvas(self)
        layout.addWidget(self.canvas, stretch=1)

        # Transport Bar
        transport_layout = QHBoxLayout()
        transport_layout.setContentsMargins(4, 4, 4, 4)
        transport_layout.setSpacing(8)

        # Play/Pause Button Restyle
        self.btn_play_pause = QPushButton("▶ Play")
        self.btn_play_pause.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play_pause.setStyleSheet(
            "QPushButton {"
            "    height: 26px;"
            "    min-width: 75px;"
            "    padding: 2px 14px;"
            "    border: 1px solid #4a4a4a;"
            "    border-radius: 3px;"
            "    background-color: #2e2e2e;"
            "    color: #e0e0e0;"
            "    font-weight: 600;"
            "    font-size: 11px;"
            "}"
            "QPushButton:hover {"
            "    background-color: #3a3a3a;"
            "    border-color: #606060;"
            "}"
            "QPushButton:pressed {"
            "    background-color: #222222;"
            "    border-color: #333333;"
            "}"
        )
        self.btn_play_pause.clicked.connect(self.toggle_play)
        transport_layout.addWidget(self.btn_play_pause)

        self.slider_time = QSlider(Qt.Orientation.Horizontal)
        self.slider_time.setRange(0, 0)
        self.slider_time.sliderMoved.connect(self._on_slider_moved)
        transport_layout.addWidget(self.slider_time, stretch=1)

        self.lbl_time = QLabel("00:00.00 / 00:00.00")
        self.lbl_time.setFixedWidth(130)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        transport_layout.addWidget(self.lbl_time)

        layout.addLayout(transport_layout)

    def load_track(self, audio_path: str, lyrics: Optional[SongLyrics]) -> None:
        """Load an audio file and synchronized lyrics into the preview widget."""
        self.pause()
        self._audio_path = audio_path
        self.set_lyrics(lyrics)

        if audio_path and os.path.isfile(audio_path):
            self._player.setSource(QUrl.fromLocalFile(audio_path))
        else:
            self._player.setSource(QUrl())

    def set_lyrics(self, lyrics: Optional[SongLyrics], total_duration_ms: int = 0) -> None:
        """Set synchronized lyrics and initialize duration."""
        self._lyrics = lyrics
        if total_duration_ms > 0:
            self._total_duration_ms = total_duration_ms
        elif lyrics and lyrics.lines:
            self._total_duration_ms = max(line.end_ms for line in lyrics.lines) + 2000
        else:
            self._total_duration_ms = 0

        self.slider_time.setRange(0, self._total_duration_ms)
        self.canvas.set_lyrics(lyrics)
        self.seek(0)

    def seek(self, ms: int) -> None:
        """Seek both the UI display and audio player to the given millisecond position."""
        self._current_ms = max(0, min(ms, self._total_duration_ms))
        if not self.slider_time.isSliderDown():
            self.slider_time.setValue(self._current_ms)
        self._update_time_label()
        self.canvas.set_time(self._current_ms)

        if not self._player.source().isEmpty():
            if abs(self._player.position() - self._current_ms) > 100:
                self._player.setPosition(self._current_ms)

    def toggle_play(self) -> None:
        """Toggle audio playback between play and pause."""
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        """Begin audio playback or fallback timer sweep."""
        if not self._is_playing:
            self._is_playing = True
            self.btn_play_pause.setText("⏸ Pause")
            if not self._player.source().isEmpty():
                self._player.play()
            else:
                self._last_tick_time = time.monotonic()
                self._timer.start()

    def pause(self) -> None:
        """Pause playback and synthetic timer."""
        if self._is_playing:
            self._is_playing = False
            self.btn_play_pause.setText("▶ Play")
            if not self._player.source().isEmpty():
                self._player.pause()
            self._timer.stop()

    def _update_time_label(self) -> None:
        self.lbl_time.setText(f"{_format_time(self._current_ms)} / {_format_time(self._total_duration_ms)}")

    def _on_slider_moved(self, value: int) -> None:
        self.seek(value)

    def _on_player_position_changed(self, position_ms: int) -> None:
        self._current_ms = position_ms
        if not self.slider_time.isSliderDown():
            self.slider_time.setValue(position_ms)
        self._update_time_label()
        self.canvas.set_time(position_ms)

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self._total_duration_ms = duration_ms
            self.slider_time.setRange(0, duration_ms)
            self._update_time_label()

    def _on_player_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._is_playing = True
            self.btn_play_pause.setText("⏸ Pause")
        else:
            if self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self._is_playing = False
                self.btn_play_pause.setText("▶ Play")
                self.seek(0)

    def _on_timer_tick(self) -> None:
        now = time.monotonic()
        elapsed_ms = int((now - self._last_tick_time) * 1000)
        self._last_tick_time = now

        new_time = self._current_ms + elapsed_ms
        if self._total_duration_ms > 0 and new_time >= self._total_duration_ms:
            self.seek(self._total_duration_ms)
            self.pause()
        else:
            self.seek(new_time)
