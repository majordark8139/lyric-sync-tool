"""Constant-speed 60 FPS syllable color sweep preview widget with real audio playback."""

import ctypes
import os
import re
import sys
import time
from typing import Optional

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QKeyEvent, QPainter, QPixmap, QPen
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

_SINGER_PREFIX_RE = re.compile(r"^(v\d+|singer\d+|[A-Za-z0-9_]+):\s*", re.IGNORECASE)

try:
    _Py_IncRef = ctypes.pythonapi.Py_IncRef
    _Py_IncRef.argtypes = [ctypes.py_object]
    _Py_IncRef.restype = None
except Exception:
    _Py_IncRef = None

# Shiboken6 void-return bug mitigation: Shiboken returns Py_None without Py_INCREF for void C++ methods,
# causing CPython to decref None on statement pop until none_dealloc triggers.
if _Py_IncRef is not None and not getattr(QPainter, "_none_dealloc_patched", False):
    QPainter._none_dealloc_patched = True

    _orig_setFont = QPainter.setFont
    def _safe_setFont(self, f=None):
        if f is None:
            f = QFont("sans-serif", 10)
        _orig_setFont(self, f)
        _Py_IncRef(None)
    QPainter.setFont = _safe_setFont

    _orig_setPen = QPainter.setPen
    def _safe_setPen(self, pen=None):
        if pen is None:
            pen = QPen(Qt.PenStyle.NoPen)
        _orig_setPen(self, pen)
        _Py_IncRef(None)
    QPainter.setPen = _safe_setPen

    _orig_setRenderHint = QPainter.setRenderHint
    def _safe_setRenderHint(self, hint, on=True):
        _orig_setRenderHint(self, hint, on)
        _Py_IncRef(None)
    QPainter.setRenderHint = _safe_setRenderHint

    _orig_save = QPainter.save
    def _safe_save(self):
        _orig_save(self)
        _Py_IncRef(None)
    QPainter.save = _safe_save

    _orig_restore = QPainter.restore
    def _safe_restore(self):
        _orig_restore(self)
        _Py_IncRef(None)
    QPainter.restore = _safe_restore

    _orig_setClipRect = QPainter.setClipRect
    def _safe_setClipRect(self, *args, **kwargs):
        _orig_setClipRect(self, *args, **kwargs)
        _Py_IncRef(None)
    QPainter.setClipRect = _safe_setClipRect

    _orig_drawText = QPainter.drawText
    def _safe_drawText(self, *args):
        if args:
            new_args = list(args)
            if isinstance(new_args[-1], (str, type(None))):
                new_args[-1] = str(new_args[-1] or "")
            return _orig_drawText(self, *new_args)
        return _orig_drawText(self, *args)
    QPainter.drawText = _safe_drawText

    _orig_update = QWidget.update
    def _safe_update(self, *args, **kwargs):
        _orig_update(self, *args, **kwargs)
        _Py_IncRef(None)
    QWidget.update = _safe_update

    _orig_repaint = QWidget.repaint
    def _safe_repaint(self, *args, **kwargs):
        _orig_repaint(self, *args, **kwargs)
        _Py_IncRef(None)
    QWidget.repaint = _safe_repaint


def _format_time(ms: int) -> str:
    """Format milliseconds to mm:ss.xx centisecond timestamp."""
    ms = max(0, ms)
    cs = (ms // 10) % 100
    sec = (ms // 1000) % 60
    minutes = ms // 60000
    return f"{minutes:02d}:{sec:02d}.{cs:02d}"


class _KaraokeCanvas(QWidget):
    """Sub-pixel clipping QPainter canvas for syllable rendering with sub-vocal support."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lyrics: Optional[SongLyrics] = None
        self._current_ms: int = 0
        self._fallback_pixmap: Optional[QPixmap] = None

        self._base_font = QFont("sans-serif", 13)
        self._active_font = QFont("sans-serif", 14, QFont.Bold)
        self._sub_active_font = QFont("sans-serif", 9, QFont.Bold)
        self._sub_base_font = QFont("sans-serif", 8)
        self._sub_font = self._sub_active_font

        self._dim_color = QColor("#8b949e")
        self._sub_dim_color = QColor(139, 148, 158, 160)
        self._highlight_color = QColor("#58A6FF")
        self._past_line_color = QColor(180, 180, 180)

        self._dim_pen = QPen(self._dim_color)
        self._sub_dim_pen = QPen(self._sub_dim_color)
        self._highlight_pen = QPen(self._highlight_color)
        self._sub_highlight_pen = QPen(self._highlight_color)
        self._past_line_pen = QPen(self._past_line_color)

    def set_lyrics(self, lyrics: Optional[SongLyrics]) -> None:
        self._lyrics = lyrics
        self.update()

    def set_time(self, current_ms: int, is_playing: bool = False) -> None:
        """Update active playback time and conditionally request repaint."""
        delta = abs(current_ms - self._current_ms)
        if delta >= 15 or is_playing:
            self._current_ms = current_ms
            self.update()
        else:
            self._current_ms = current_ms

    def set_position(self, pos_ms: int) -> None:
        """Alias for set_time(pos_ms) used by soak and stability tests."""
        self.set_time(pos_ms, is_playing=True)

    def paintEvent(self, event, custom_painter: Optional[QPainter] = None) -> None:
        owns_painter = custom_painter is None
        painter = custom_painter if custom_painter is not None else QPainter(self)
        if owns_painter and not painter.isActive():
            if self._fallback_pixmap is None or self._fallback_pixmap.size() != self.size():
                self._fallback_pixmap = QPixmap(max(1, self.width()), max(1, self.height()))
            painter = QPainter(self._fallback_pixmap)

        try:
            if not painter.isActive():
                return

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

            width = self.width()
            height = self.height()

            base_font = self._base_font or self.font()
            dim_pen = self._dim_pen or QPen(Qt.PenStyle.NoPen)
            past_line_pen = self._past_line_pen or dim_pen
            sub_base_font = self._sub_base_font or base_font
            sub_active_font = self._sub_active_font or base_font
            sub_dim_pen = self._sub_dim_pen or dim_pen
            sub_highlight_pen = self._sub_highlight_pen or (self._highlight_pen or dim_pen)

            if not self._lyrics or not self._lyrics.lines:
                painter.setFont(base_font)
                painter.setPen(dim_pen)
                rect = QRect(0, 0, int(width), int(height))
                painter.drawText(
                    rect,
                    int(Qt.AlignmentFlag.AlignCenter),
                    "No synchronized lyrics available",
                )
                return

            all_lines = self._lyrics.lines
            is_word_synced = getattr(self._lyrics, "is_word_synced", False)

            # Determine is_word_synced from syllables if not set on SongLyrics object
            if not is_word_synced:
                for line in all_lines:
                    if len(line.syllables) > 1:
                        is_word_synced = True
                        break

            # ── 1. Find the active line index (chronological, all lines visible) ──
            active_idx = -1
            for i, line in enumerate(all_lines):
                if line.start_ms <= self._current_ms <= line.end_ms + 400:
                    active_idx = i
                    break

            # Pre-cursor fallback: snap to last line before current_ms
            if active_idx == -1:
                for i, line in enumerate(all_lines):
                    if line.start_ms > self._current_ms:
                        active_idx = max(0, i - 1)
                        break
                else:
                    active_idx = max(0, len(all_lines) - 1)

            line_height = 42.0
            center_y = float(height) / 2.0

            # ── 2. Render ALL lines in chronological order ─────────────────────
            for i, line in enumerate(all_lines):
                offset_idx = i - active_idx
                y = center_y + (offset_idx * line_height)

                if y < -line_height or y > height + line_height:
                    continue

                raw_line_text = line.text if line.text is not None else "".join(getattr(s, "text", "") for s in line.syllables)
                line_text = str(raw_line_text or "").strip()
                line_text = _SINGER_PREFIX_RE.sub("", line_text)
                if not line_text:
                    continue

                is_active = (line.start_ms <= self._current_ms <= line.end_ms + 400)

                if is_active and i == active_idx:
                    # ── Mode B: Word-synced → true per-syllable sub-pixel sweep ──
                    if is_word_synced and line.syllables:
                        self._draw_word_synced_line(painter, line, line_text, y, float(width), line_height)
                    else:
                        # ── Mode A: Line-synced → entire line in solid active colour ──
                        self._draw_line_synced_active(painter, line_text, y, float(width), line_height, line=line)

                    # Background vocal directly beneath active lead line
                    if line.has_background:
                        raw_bg = line.background_text or "".join(getattr(s, "text", "") for s in line.background_syllables)
                        bg_text = str(raw_bg or "").strip()
                        bg_text = _SINGER_PREFIX_RE.sub("", bg_text)
                        if bg_text:
                            sub_y = y + 20.0
                            if is_word_synced and line.background_syllables:
                                self._draw_word_synced_line(
                                    painter, line, bg_text, sub_y,
                                    float(width), 20.0, sub_active_font, sub_dim_pen,
                                    syllables=line.background_syllables,
                                    highlight_pen=sub_highlight_pen,
                                )
                            else:
                                self._draw_line_synced_active(
                                    painter, bg_text, sub_y, float(width), 20.0,
                                    sub_active_font, sub_highlight_pen, line=line,
                                )
                    elif not line.is_background:
                        active_bg: Optional[LyricLine] = None
                        for bg in all_lines:
                            if bg.is_background and bg.start_ms <= self._current_ms <= bg.end_ms:
                                active_bg = bg
                                break
                        if active_bg is not None:
                            raw_sub = active_bg.text or "".join(getattr(s, "text", "") for s in active_bg.syllables)
                            sub_text = str(raw_sub or "").strip()
                            sub_text = _SINGER_PREFIX_RE.sub("", sub_text)
                            if sub_text:
                                sub_y = y + 20.0
                                if is_word_synced and active_bg.syllables:
                                    self._draw_word_synced_line(
                                        painter, active_bg, sub_text, sub_y,
                                        float(width), 20.0, sub_active_font, sub_dim_pen,
                                        highlight_pen=sub_highlight_pen,
                                    )
                                else:
                                    self._draw_line_synced_active(
                                        painter, sub_text, sub_y, float(width), 20.0,
                                        sub_active_font, sub_highlight_pen, line=active_bg,
                                    )
                else:
                    painter.setFont(base_font)
                    pen = past_line_pen if i < active_idx else dim_pen
                    painter.setPen(pen)
                    fm = QFontMetricsF(base_font)
                    line_w = fm.horizontalAdvance(line_text)
                    if getattr(line, "singer_id", None) == "v2":
                        x = max(40.0, float(width) - line_w - 40.0)
                    else:
                        x = 40.0
                    baseline_y = y + fm.ascent() / 2.0
                    painter.drawText(int(x), int(baseline_y), line_text)

                    # Inactive background vocal display
                    if line.has_background:
                        raw_bg = line.background_text or "".join(getattr(s, "text", "") for s in line.background_syllables)
                        bg_text = str(raw_bg or "").strip()
                        bg_text = _SINGER_PREFIX_RE.sub("", bg_text)
                        if bg_text:
                            sub_y = y + 20.0
                            painter.setFont(sub_base_font)
                            painter.setPen(sub_dim_pen)
                            sub_fm = QFontMetricsF(sub_base_font)
                            bg_w = sub_fm.horizontalAdvance(bg_text)
                            if getattr(line, "singer_id", None) == "v2":
                                sub_x = max(40.0, float(width) - bg_w - 40.0)
                            else:
                                sub_x = 40.0
                            sub_baseline_y = sub_y + sub_fm.ascent() / 2.0
                            painter.drawText(int(sub_x), int(sub_baseline_y), bg_text)
        finally:
            if owns_painter and painter.isActive():
                painter.end()

    def _draw_line_synced_active(
        self,
        painter: QPainter,
        line_text: str,
        y: float,
        view_width: float,
        line_height: float,
        font: Optional[QFont] = None,
        pen: Optional[QPen] = None,
        line: Optional[LyricLine] = None,
        singer_id: Optional[str] = None,
    ) -> None:
        """Mode A: Line-synced — draw entire line in solid active highlight colour.

        No character-level interpolation; the full line lights up solidly while
        ``line.start_ms <= current_ms <= line.end_ms``.
        """
        f = font if font is not None else (self._active_font or self.font())
        p = pen if pen is not None else (self._highlight_pen or QPen(Qt.PenStyle.NoPen))
        painter.setFont(f)
        painter.setPen(p)
        safe_text = str(line_text or "")
        safe_text = _SINGER_PREFIX_RE.sub("", safe_text)
        fm = QFontMetricsF(f)
        line_w = fm.horizontalAdvance(safe_text)
        sid = singer_id or (getattr(line, "singer_id", None) if line else None)
        if sid == "v2":
            x = max(40.0, float(view_width) - line_w - 40.0)
        else:
            x = 40.0
        baseline_y = y + fm.ascent() / 2.0
        painter.drawText(int(x), int(baseline_y), safe_text)

    def _draw_word_synced_line(
        self,
        painter: QPainter,
        line: LyricLine,
        line_text: str,
        y: float,
        view_width: float,
        line_height: float,
        font: Optional[QFont] = None,
        dim_pen: Optional[QPen] = None,
        syllables: Optional[list] = None,
        highlight_pen: Optional[QPen] = None,
    ) -> None:
        """Mode B: Word-synced — true per-syllable sub-pixel sweep.

        Algorithm:
        - Completed syllables (abs_end_ms <= current_ms): fully illuminated.
        - Active syllable k (abs_start_ms <= current_ms < abs_end_ms):
            alpha = (current_ms - abs_start_ms) / max(1, abs_end_ms - abs_start_ms)
            clip width = sum_of_completed_widths + alpha * width_k
        - Future syllables (abs_start_ms > current_ms): dimmed base colour.
        """
        f = font if font is not None else (self._active_font or self.font())
        dp = dim_pen if dim_pen is not None else (self._dim_pen or QPen(Qt.PenStyle.NoPen))
        hp = highlight_pen if highlight_pen is not None else (self._highlight_pen or QPen(Qt.PenStyle.NoPen))
        syls = syllables if syllables is not None else (line.syllables if line is not None else [])
        syls = syls or []

        safe_text = str(line_text or "")
        safe_text = _SINGER_PREFIX_RE.sub("", safe_text)
        fm = QFontMetricsF(f)
        line_w = fm.horizontalAdvance(safe_text)
        if getattr(line, "singer_id", None) == "v2":
            x_start = max(40.0, float(view_width) - line_w - 40.0)
        else:
            x_start = 40.0
        baseline_y = y + fm.ascent() / 2.0

        # 1. Draw full line in dim colour as base layer
        painter.setFont(f)
        painter.setPen(dp)
        painter.drawText(int(x_start), int(baseline_y), safe_text)

        # 2. Compute exact pixel sweep position across syllables
        cursor_x = x_start
        sweep_x = x_start

        for idx, syl in enumerate(syls):
            syl_text = str(getattr(syl, "text", "") or "") + str(getattr(syl, "tail", "") or "")
            if idx == 0:
                syl_text = _SINGER_PREFIX_RE.sub("", syl_text)
            syl_w = fm.horizontalAdvance(syl_text)
            abs_end_ms = getattr(syl, "abs_end_ms", 0)
            abs_start_ms = getattr(syl, "abs_start_ms", 0)
            if self._current_ms >= abs_end_ms:
                # Syllable fully completed
                sweep_x = cursor_x + syl_w
                cursor_x += syl_w
            elif self._current_ms >= abs_start_ms:
                # Active syllable: linear sub-pixel interpolation
                dur = max(1, abs_end_ms - abs_start_ms)
                alpha = min(1.0, max(0.0, (self._current_ms - abs_start_ms) / dur))
                sweep_x = cursor_x + (syl_w * alpha)
                break
            else:
                # Future syllable: stop
                break

        # 3. Clip from line start to sweep_x and redraw in highlight colour
        if sweep_x > x_start:
            painter.save()
            try:
                clip_rect = QRect(
                    int(x_start),
                    int(y - line_height),
                    int(sweep_x - x_start),
                    int(line_height * 2),
                )
                painter.setClipRect(clip_rect, Qt.ClipOperation.ReplaceClip)
                painter.setPen(hp)
                painter.drawText(int(x_start), int(baseline_y), safe_text)
            finally:
                painter.restore()

    def _draw_active_line(
        self,
        painter: QPainter,
        line: LyricLine,
        line_text: str,
        y: float,
        view_width: float,
        line_height: float,
        font: Optional[QFont] = None,
        dim_pen: Optional[QPen] = None,
    ) -> None:
        """Compatibility wrapper for drawing active line."""
        self._draw_word_synced_line(painter, line, line_text, y, view_width, line_height, font, dim_pen)



class KaraokePreviewWidget(QWidget):
    """Karaoke live-preview widget with real audio playback, 60 FPS sweeper, and VLC-style transport bar."""

    previous_requested = Signal()
    next_requested = Signal()
    track_finished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        self._player.mediaStatusChanged.connect(self._on_player_media_status_changed)

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
        self._canvas = self.canvas  # Expose private alias for testing
        layout.addWidget(self.canvas, stretch=1)

        # Transport Bar
        transport_layout = QHBoxLayout()
        transport_layout.setContentsMargins(4, 4, 4, 4)
        transport_layout.setSpacing(6)

        btn_style = (
            "QPushButton {"
            "    height: 26px;"
            "    min-width: 65px;"
            "    padding: 2px 10px;"
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

        # 1. Previous Button
        self.btn_prev = QPushButton("⏮ Prev")
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.clicked.connect(self.previous_requested.emit)
        transport_layout.addWidget(self.btn_prev)

        # 2. Play / Pause Button
        self.btn_play_pause = QPushButton("▶ Play")
        self.btn_play_pause.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play_pause.setStyleSheet(btn_style)
        self.btn_play_pause.clicked.connect(self.toggle_play)
        transport_layout.addWidget(self.btn_play_pause)

        # 3. Stop Button
        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_stop.setStyleSheet(btn_style)
        self.btn_stop.clicked.connect(self.stop)
        transport_layout.addWidget(self.btn_stop)

        # 4. Next Button
        self.btn_next = QPushButton("⏭ Next")
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.clicked.connect(self.next_requested.emit)
        transport_layout.addWidget(self.btn_next)

        # 5. Scrubber Slider
        self.slider_time = QSlider(Qt.Orientation.Horizontal)
        self.slider_time.setRange(0, 0)
        self.slider_time.sliderMoved.connect(self._on_slider_moved)
        transport_layout.addWidget(self.slider_time, stretch=1)

        # 6. Timestamp Display
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
        self.canvas.set_time(self._current_ms, is_playing=True)

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

    def stop(self) -> None:
        """Stop playback and reset seek position to 00:00.00."""
        self.pause()
        if not self._player.source().isEmpty():
            self._player.stop()
        self.seek(0)

    def _update_time_label(self) -> None:
        self.lbl_time.setText(f"{_format_time(self._current_ms)} / {_format_time(self._total_duration_ms)}")

    def _on_slider_moved(self, value: int) -> None:
        self.seek(value)

    def _on_player_position_changed(self, position_ms: int) -> None:
        self._current_ms = position_ms
        if not self.slider_time.isSliderDown():
            self.slider_time.setValue(position_ms)
        self._update_time_label()
        self.canvas.set_time(position_ms, is_playing=self._is_playing)

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

    def _on_player_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.pause()
            self.seek(0)
            self.track_finished.emit()

    def _on_timer_tick(self) -> None:
        now = time.monotonic()
        elapsed_ms = int((now - self._last_tick_time) * 1000)
        self._last_tick_time = now

        new_time = self._current_ms + elapsed_ms
        if self._total_duration_ms > 0 and new_time >= self._total_duration_ms:
            self.seek(self._total_duration_ms)
            self.pause()
            self.seek(0)
            self.track_finished.emit()
        else:
            self._current_ms = new_time
            if not self.slider_time.isSliderDown():
                self.slider_time.setValue(new_time)
            self._update_time_label()
            self.canvas.set_time(new_time, is_playing=True)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Keyboard shortcuts for media playback and seeking."""
        modifiers = event.modifiers()
        key = event.key()

        if modifiers == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Left:
                self.previous_requested.emit()
                event.accept()
                return
            elif key == Qt.Key.Key_Right:
                self.next_requested.emit()
                event.accept()
                return
            elif key == Qt.Key.Key_Period or key == Qt.Key.Key_W:
                self.stop()
                event.accept()
                return
        elif modifiers == Qt.KeyboardModifier.NoModifier:
            if key == Qt.Key.Key_Space:
                self.toggle_play()
                event.accept()
                return
            elif key == Qt.Key.Key_Left:
                self.seek(self._current_ms - 5000)
                event.accept()
                return
            elif key == Qt.Key.Key_Right:
                self.seek(self._current_ms + 5000)
                event.accept()
                return

        super().keyPressEvent(event)
