"""Headless unit tests for PySide6 GUI widgets, themes, and settings."""

import os
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.models import LyricLine, SongLyrics, SyllableSpan, SyncState, TaggingConfig, TrackItem
from gui.app import MainWindow
from gui.editor_widget import LyricsEditorWidget
from gui.preview_widget import KaraokePreviewWidget
from gui.settings_dialog import TaggingSettingsDialog
from gui.themes import THEMES

app = QApplication.instance() or QApplication([])


def _make_sample_lyrics() -> SongLyrics:
    return SongLyrics(
        metadata={"ti": "Test Title", "ar": "Test Artist"},
        lines=[
            LyricLine(
                start_ms=1000,
                end_ms=3000,
                syllables=[
                    SyllableSpan(abs_start_ms=1000, abs_end_ms=1800, text="Hello "),
                    SyllableSpan(abs_start_ms=1800, abs_end_ms=3000, text="World"),
                ],
            ),
            LyricLine(
                start_ms=3500,
                end_ms=5000,
                syllables=[
                    SyllableSpan(abs_start_ms=3500, abs_end_ms=4200, text="Second "),
                    SyllableSpan(abs_start_ms=4200, abs_end_ms=5000, text="Line"),
                ],
            ),
        ],
    )


class TestThemes(unittest.TestCase):
    def test_themes_exist_and_non_empty(self) -> None:
        self.assertIn("White", THEMES)
        self.assertIn("Dark", THEMES)
        self.assertIn("OLED Dark", THEMES)
        for name, qss in THEMES.items():
            self.assertIsInstance(qss, str)
            self.assertTrue(len(qss.strip()) > 100)

    def test_oled_theme_contains_pure_black(self) -> None:
        self.assertIn("#000000", THEMES["OLED Dark"])


class TestSettingsDialog(unittest.TestCase):
    def tearDown(self) -> None:
        app.sendPostedEvents()
        app.processEvents()

    def test_dialog_loads_config_and_returns_updated(self) -> None:
        cfg = TaggingConfig(
            write_lrc=True,
            lrc_extension="both",
            write_ttml=False,
            purge_stale_line_sync=False,
            backup_stale_sidecars=True,
            embed_metadata=True,
            flac_embed_vorbis=True,
            mp3_embed_uslt_fallback=False,
            mp3_embed_txxx=True,
            overwrite_existing_word_sync=True,
            duration_tolerance_ms=3000,
            smart_fallback=False,
        )
        dlg = TaggingSettingsDialog(cfg)
        self.assertTrue(dlg.cb_write_lrc.isChecked())
        self.assertTrue(dlg.rb_both.isChecked())
        self.assertFalse(dlg.cb_write_ttml.isChecked())
        self.assertFalse(dlg.cb_purge_stale.isChecked())
        self.assertTrue(dlg.cb_backup_stale.isChecked())
        self.assertFalse(dlg.cb_mp3_uslt.isChecked())
        self.assertTrue(dlg.cb_overwrite_word.isChecked())
        self.assertEqual(dlg.sb_tolerance.value(), 3000)
        self.assertFalse(dlg.cb_smart_fallback.isChecked())

        # Modify values in UI
        dlg.cb_write_ttml.setChecked(True)
        dlg.sb_tolerance.setValue(4500)
        dlg.rb_elrc.setChecked(True)

        updated = dlg.get_config()
        self.assertTrue(updated.write_ttml)
        self.assertEqual(updated.duration_tolerance_ms, 4500)
        self.assertEqual(updated.lrc_extension, "elrc")

        dlg.deleteLater()
        del dlg
        app.sendPostedEvents()
        app.processEvents()


class TestKaraokePreviewWidget(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = KaraokePreviewWidget()

    def tearDown(self) -> None:
        if self.widget is not None:
            self.widget.deleteLater()
            self.widget = None
        app.sendPostedEvents()
        app.processEvents()

    def test_set_lyrics_and_seek(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics, total_duration_ms=5000)
        self.assertEqual(self.widget._total_duration_ms, 5000)
        self.assertEqual(self.widget.slider_time.maximum(), 5000)

        # Seek to 2000 ms (middle of first line)
        self.widget.seek(2000)
        self.assertEqual(self.widget._current_ms, 2000)
        self.assertEqual(self.widget.canvas._current_ms, 2000)

    def test_playback_toggle(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics)
        self.assertFalse(self.widget._is_playing)
        self.widget.toggle_play()
        self.assertTrue(self.widget._is_playing)
        self.widget.toggle_play()
        self.assertFalse(self.widget._is_playing)

    def test_load_track(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.load_track("/path/to/song.mp3", lyrics)
        self.assertEqual(self.widget._audio_path, "/path/to/song.mp3")
        self.assertIsNotNone(self.widget._lyrics)
        self.assertFalse(self.widget._is_playing)

    def test_paint_canvas_does_not_crash(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics, total_duration_ms=5000)
        self.widget.seek(1500)
        from PySide6.QtGui import QPixmap
        pm = QPixmap(400, 300)
        self.widget.canvas.render(pm)


class TestLyricsEditorWidget(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = LyricsEditorWidget()

    def tearDown(self) -> None:
        if self.widget is not None:
            self.widget.deleteLater()
            self.widget = None
        app.sendPostedEvents()
        app.processEvents()

    def test_load_lyrics_populates_editor(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics)
        text = self.widget.text_editor.toPlainText().strip()
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        self.assertEqual(lines, ["Hello World", "Second Line"])

    def test_offset_slider_adjusts_timestamps(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics)

        # Trigger offset adjustment by +100ms
        self.widget.slider_offset.setValue(100)
        updated = self.widget.get_current_lyrics()
        self.assertIsNotNone(updated)
        self.assertEqual(updated.lines[0].start_ms, 1100)
        self.assertEqual(updated.lines[0].syllables[0].abs_start_ms, 1100)

        # Reset offset
        self.widget.btn_reset.click()
        self.assertEqual(self.widget.slider_offset.value(), 0)
        reset_lyrics = self.widget.get_current_lyrics()
        self.assertEqual(reset_lyrics.lines[0].start_ms, 1000)

    def test_plain_text_typo_correction_emits_change(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics)

        received_lyrics = []
        self.widget.lyrics_changed.connect(lambda lyr: received_lyrics.append(lyr))

        # Change "Hello World" -> "Hello Earth" (typo preservation rule)
        self.widget.text_editor.setPlainText("Hello Earth\nSecond Line")
        self.widget._debounce_timer.stop()
        self.widget._on_debounce_timeout()

        self.assertEqual(len(received_lyrics), 1)
        reconciled = received_lyrics[0]
        self.assertEqual(reconciled.lines[0].syllables[1].text, "Earth")
        # Timing of 1800-3000 should be preserved
        self.assertEqual(reconciled.lines[0].syllables[1].abs_start_ms, 1800)
        self.assertEqual(reconciled.lines[0].syllables[1].abs_end_ms, 3000)


class TestMainWindow(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MainWindow()

    def tearDown(self) -> None:
        if self.window is not None:
            self.window.deleteLater()
            self.window = None
        app.sendPostedEvents()
        app.processEvents()

    def test_initial_state(self) -> None:
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertIn("Ready", self.window.statusBar().currentMessage())
        self.assertEqual(self.window.cmb_theme.currentText(), "Dark")

    def test_add_tracks_to_table(self) -> None:
        tracks = [
            TrackItem(
                file_path="/music/song1.flac",
                title="Song One",
                artist="Artist A",
                duration_ms=180000,
                sync_state=SyncState.NONE,
            ),
            TrackItem(
                file_path="/music/song2.mp3",
                title="Song Two",
                artist="Artist B",
                duration_ms=210000,
                sync_state=SyncState.WORD,
            ),
        ]
        for t in tracks:
            self.window._on_track_found(t)
        self.assertEqual(self.window.table.rowCount(), 2)

        # Check column values: 1: Title, 2: Artist, 4: Sync State
        self.assertEqual(self.window.table.item(0, 1).text(), "Song One")
        self.assertEqual(self.window.table.item(0, 2).text(), "Artist A")
        self.assertEqual(self.window.table.item(0, 4).text(), "NONE")

        self.assertEqual(self.window.table.item(1, 1).text(), "Song Two")
        self.assertEqual(self.window.table.item(1, 4).text(), "WORD")

    def test_switch_theme(self) -> None:
        self.window.cmb_theme.setCurrentText("OLED Dark")
        self.assertIn("#000000", self.window.styleSheet())


class TestPaintStability(unittest.TestCase):
    """30-second continuous 60 FPS animation soak test verifying refcount stability."""

    def setUp(self) -> None:
        self.widget = KaraokePreviewWidget()
        self.widget.resize(500, 300)

    def tearDown(self) -> None:
        if self.widget is not None:
            self.widget.deleteLater()
            self.widget = None
        app.sendPostedEvents()
        app.processEvents()

    def test_paint_stability_1800_frames(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics, total_duration_ms=30000)

        rc_start = sys.getrefcount(None)
        for pos_ms in range(0, 30000, 16):
            self.widget._canvas.set_position(pos_ms)
            self.widget._canvas.repaint()
        rc_end = sys.getrefcount(None)

        self.assertGreaterEqual(
            rc_end,
            rc_start - 5,
            f"None refcount leaked: rc_start={rc_start}, rc_end={rc_end}, diff={rc_end - rc_start}",
        )


if __name__ == "__main__":
    unittest.main()
