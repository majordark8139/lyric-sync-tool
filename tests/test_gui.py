"""Headless unit tests for PySide6 GUI widgets, themes, and settings."""

import gc
import os
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QKeyEvent, QPaintEvent
from PySide6.QtWidgets import QApplication

from core.models import LyricLine, SongLyrics, SyllableSpan, SyncState, TaggingConfig, TrackItem
from gui.app import MainWindow
from gui.editor_widget import LyricsEditorWidget
from gui.preview_widget import KaraokePreviewWidget, _KaraokeCanvas
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

    def test_settings_dialog_tabs_and_master_lrc_toggle(self) -> None:
        cfg = TaggingConfig(write_lrc=True)
        dlg = TaggingSettingsDialog(cfg)

        # Verify 3 tabs exist
        self.assertEqual(dlg.tab_widget.count(), 3)
        tab_names = [dlg.tab_widget.tabText(i) for i in range(3)]
        self.assertIn("Sidecars & Cleanup", tab_names)
        self.assertIn("Search & Ladder", tab_names)
        self.assertIn("Tag Embedding", tab_names)

        # Verify dual attribute aliases
        self.assertIs(dlg.chk_write_lrc, dlg.cb_write_lrc)
        self.assertIs(dlg.rad_lrc, dlg.rb_lrc)
        self.assertIs(dlg.rad_elrc, dlg.rb_elrc)
        self.assertIs(dlg.rad_both, dlg.rb_both)
        self.assertIs(dlg.spin_tolerance, dlg.sb_tolerance)

        # Radio buttons enabled initially because write_lrc=True
        self.assertTrue(dlg.rad_lrc.isEnabled())
        self.assertTrue(dlg.rad_elrc.isEnabled())
        self.assertTrue(dlg.rad_both.isEnabled())

        # Uncheck master write_lrc -> radio buttons must be disabled
        dlg.chk_write_lrc.setChecked(False)
        self.assertFalse(dlg.rad_lrc.isEnabled())
        self.assertFalse(dlg.rad_elrc.isEnabled())
        self.assertFalse(dlg.rad_both.isEnabled())

        # Returned config has write_lrc=False
        saved_cfg = dlg.get_config()
        self.assertFalse(saved_cfg.write_lrc)

        # Re-enable
        dlg.chk_write_lrc.setChecked(True)
        self.assertTrue(dlg.rad_lrc.isEnabled())
        self.assertTrue(dlg.rad_elrc.isEnabled())
        self.assertTrue(dlg.rad_both.isEnabled())
        self.assertTrue(dlg.get_config().write_lrc)

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

    def test_selection_deserializes_lyrics_and_updates_sync_state(self) -> None:
        from unittest.mock import patch
        track = TrackItem(
            file_path="/music/untagged.mp3",
            title="Song",
            artist="Artist",
            duration_ms=180000,
            sync_state=SyncState.NONE,
        )
        self.window._on_track_found(track)
        album_item = self.window.tree.topLevelItem(0)
        track_item = album_item.child(0)
        self.assertEqual(track_item.text(3), "NONE")

        dummy_lyrics = SongLyrics(
            metadata={},
            lines=[LyricLine(start_ms=1000, end_ms=2000, syllables=[SyllableSpan(1000, 2000, "Word")])],
            is_word_synced=True,
        )

        with patch("gui.app.read_track_lyrics", return_value=dummy_lyrics):
            self.window.tree.setCurrentItem(track_item)
            self.window._on_item_selection_changed()

            self.assertEqual(track.sync_state, SyncState.WORD)
            self.assertEqual(track_item.text(3), "WORD")
            self.assertIsNotNone(self.window.preview_widget._lyrics)
            self.assertEqual(len(self.window.preview_widget._lyrics.lines), 1)

    def test_batch_tagging_preview_and_cleanup_lockout(self) -> None:
        from unittest.mock import MagicMock, patch
        from PySide6.QtCore import QUrl
        from PySide6.QtTest import QTest

        # Add a track so selected_pairs is not empty
        track = TrackItem(file_path="/dummy/test.mp3", title="Song", artist="Artist", duration_ms=1000)
        self.window._on_track_found(track)
        album_item = self.window.tree.topLevelItem(0)
        album_item.child(0).setCheckState(0, Qt.CheckState.Checked)

        self.window.btn_cleanup.setEnabled(True)

        mock_stop = MagicMock()
        mock_set_source = MagicMock()
        self.window.preview_widget.stop = mock_stop
        self.window.preview_widget._player.setSource = mock_set_source

        # Trigger start tagging with mocked BatchTaggerWorker so no threads decode audio
        with patch("gui.app.BatchTaggerWorker") as mock_worker_cls:
            mock_worker = MagicMock()
            mock_worker.isRunning.return_value = True
            mock_worker_cls.return_value = mock_worker

            self.window._on_start_tagging_clicked()

            self.assertFalse(self.window.btn_cleanup.isEnabled())
            mock_stop.assert_called()
            mock_set_source.assert_called_with(QUrl())

            # Cancel tagging restores cleanup button
            self.window._on_cancel_tagging_clicked()
            self.assertTrue(self.window.btn_cleanup.isEnabled())
            mock_worker.cancel.assert_called_once()

            # Batch finished also re-enables cleanup button
            self.window.btn_cleanup.setEnabled(False)
            self.window._on_batch_finished()
            self.assertTrue(self.window.btn_cleanup.isEnabled())

        QTest.qWait(50)

    def test_main_thread_gc_on_progress_and_finished(self) -> None:
        from unittest.mock import patch
        with patch("gc.collect") as mock_gc:
            self.window._on_overall_progress(200, 1000)
            mock_gc.assert_not_called()

        with patch("gc.collect") as mock_gc:
            self.window._on_batch_finished()
            mock_gc.assert_called_once()


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

        for pos_ms in range(0, 30000, 16):
            self.widget._canvas.set_position(pos_ms)
            self.widget._canvas.repaint()

        self.assertEqual(self.widget._canvas._current_ms, 29984)

    def test_paint_event_no_none_refcount_leak(self) -> None:
        """Simulate 1,000 consecutive calls to paintEvent and verify refcount of None does not drain."""
        canvas = _KaraokeCanvas()
        canvas.resize(800, 600)

        lyrics = SongLyrics(
            metadata={"ti": "Eyesight", "ar": "Composer Test"},
            lines=[
                LyricLine(start_ms=0, end_ms=0, text="Composed by Someone"),
                LyricLine(start_ms=0, end_ms=0, text="Lyrics by Someone Else"),
                LyricLine(
                    start_ms=1000,
                    end_ms=3000,
                    syllables=[
                        SyllableSpan(abs_start_ms=1000, abs_end_ms=2000, text="Lead "),
                        SyllableSpan(abs_start_ms=2000, abs_end_ms=3000, text="Line"),
                    ],
                    background_text="(bg vocal)",
                ),
                LyricLine(
                    start_ms=3500,
                    end_ms=5000,
                    syllables=[
                        SyllableSpan(abs_start_ms=3500, abs_end_ms=4200, text="Second "),
                        SyllableSpan(abs_start_ms=4200, abs_end_ms=5000, text="Line"),
                    ],
                    background_syllables=[
                        SyllableSpan(abs_start_ms=3600, abs_end_ms=4900, text="(Ad-lib)"),
                    ],
                ),
                LyricLine(start_ms=6000, end_ms=8000, text="Plain Line Sync"),
                LyricLine(start_ms=9000, end_ms=10000, text=""),  # empty line
            ],
            is_word_synced=True,
        )
        canvas.set_lyrics(lyrics)

        ev = QPaintEvent(QRect(0, 0, 800, 600))
        gc.collect()
        ref_start = sys.getrefcount(None)
        for t in range(0, 10000, 10):
            canvas._current_ms = t
            canvas.paintEvent(ev)
        gc.collect()
        ref_end = sys.getrefcount(None)

        canvas.deleteLater()
        self.assertGreaterEqual(
            ref_end,
            ref_start - 50,
            f"Reference count of None drained significantly: {ref_start} -> {ref_end} (diff: {ref_end - ref_start})",
        )


class TestAlbumGroupedTree(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MainWindow()

    def tearDown(self) -> None:
        if self.window is not None:
            self.window.deleteLater()
            self.window = None
        app.sendPostedEvents()
        app.processEvents()

    def test_album_grouping_and_node_text(self) -> None:
        t1 = TrackItem(
            file_path="/m/1.mp3",
            title="Song 1",
            artist="Artist A",
            duration_ms=120000,
            sync_state=SyncState.NONE,
            album="Greatest Hits",
            album_artist="Artist A",
        )
        t2 = TrackItem(
            file_path="/m/2.mp3",
            title="Song 2",
            artist="Artist A",
            duration_ms=150000,
            sync_state=SyncState.WORD,
            album="Greatest Hits",
            album_artist="Artist A",
        )
        t3 = TrackItem(
            file_path="/m/3.mp3",
            title="Song 3",
            artist="Artist B",
            duration_ms=180000,
            sync_state=SyncState.LINE,
            album="Different Album",
            album_artist="Artist B",
        )

        self.window._on_track_found(t1)
        self.window._on_track_found(t2)
        self.window._on_track_found(t3)

        # 2 distinct album nodes
        self.assertEqual(self.window.tree.topLevelItemCount(), 2)

        album1 = self.window.tree.topLevelItem(0)
        self.assertEqual(album1.text(0), "Artist A - Greatest Hits (2 tracks)")
        self.assertEqual(album1.childCount(), 2)
        self.assertEqual(album1.child(0).text(0), "Song 1")
        self.assertEqual(album1.child(1).text(0), "Song 2")

        album2 = self.window.tree.topLevelItem(1)
        self.assertEqual(album2.text(0), "Artist B - Different Album (1 tracks)")
        self.assertEqual(album2.childCount(), 1)

    def test_album_checkbox_cascades_to_children(self) -> None:
        t1 = TrackItem(file_path="/m/1.mp3", title="Song 1", artist="Artist", duration_ms=100, sync_state=SyncState.NONE, album="Alb")
        t2 = TrackItem(file_path="/m/2.mp3", title="Song 2", artist="Artist", duration_ms=100, sync_state=SyncState.NONE, album="Alb")
        self.window._on_track_found(t1)
        self.window._on_track_found(t2)

        album = self.window.tree.topLevelItem(0)
        self.assertEqual(album.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(album.child(0).checkState(0), Qt.CheckState.Checked)
        self.assertEqual(album.child(1).checkState(0), Qt.CheckState.Checked)

        # Uncheck album node -> children become unchecked
        album.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(album.child(0).checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(album.child(1).checkState(0), Qt.CheckState.Unchecked)

        # Re-check child 0 -> album becomes partially checked
        album.child(0).setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(album.checkState(0), Qt.CheckState.PartiallyChecked)

    def test_expand_collapse_controls(self) -> None:
        t = TrackItem(file_path="/m/1.mp3", title="Song", artist="Artist", duration_ms=100, sync_state=SyncState.NONE, album="Alb")
        self.window._on_track_found(t)
        album = self.window.tree.topLevelItem(0)

        self.window._collapse_all()
        self.assertFalse(album.isExpanded())

        self.window._expand_all()
        self.assertTrue(album.isExpanded())


class TestTransportControlsAndShortcuts(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = KaraokePreviewWidget()

    def tearDown(self) -> None:
        if self.widget is not None:
            self.widget.deleteLater()
            self.widget = None
        app.sendPostedEvents()
        app.processEvents()

    def test_transport_buttons_exist(self) -> None:
        self.assertIsNotNone(self.widget.btn_prev)
        self.assertIsNotNone(self.widget.btn_play_pause)
        self.assertIsNotNone(self.widget.btn_stop)
        self.assertIsNotNone(self.widget.btn_next)

    def test_stop_resets_seek_and_pauses(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics, total_duration_ms=5000)
        self.widget.seek(2500)
        self.widget.play()
        self.assertTrue(self.widget._is_playing)
        self.assertEqual(self.widget._current_ms, 2500)

        self.widget.stop()
        self.assertFalse(self.widget._is_playing)
        self.assertEqual(self.widget._current_ms, 0)
        self.assertEqual(self.widget.btn_play_pause.text(), "▶ Play")

    def test_keyboard_shortcuts(self) -> None:
        lyrics = _make_sample_lyrics()
        self.widget.set_lyrics(lyrics, total_duration_ms=10000)
        self.widget.seek(2000)

        # Space toggles play/pause
        event_space = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        self.widget.keyPressEvent(event_space)
        self.assertTrue(self.widget._is_playing)

        self.widget.keyPressEvent(event_space)
        self.assertFalse(self.widget._is_playing)

        # Right arrow seeks +5000ms
        event_right = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
        self.widget.keyPressEvent(event_right)
        self.assertEqual(self.widget._current_ms, 7000)

        # Left arrow seeks -5000ms
        event_left = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
        self.widget.keyPressEvent(event_left)
        self.assertEqual(self.widget._current_ms, 2000)


class TestSubVocals(unittest.TestCase):
    def test_sub_vocal_detection_during_overlap(self) -> None:
        song = SongLyrics(
            lines=[
                LyricLine(start_ms=1000, end_ms=3000, syllables=[SyllableSpan(1000, 3000, "Lead Vocal")]),
                LyricLine(start_ms=2000, end_ms=3500, syllables=[SyllableSpan(2000, 3500, "(Sub Vocal)")]),
                LyricLine(start_ms=4000, end_ms=6000, syllables=[SyllableSpan(4000, 6000, "Next Line")]),
            ]
        )
        # Line 0 is lead
        self.assertFalse(song.lines[0].is_background)
        # Line 1 overlaps with Line 0 (start 2000 < end 3000) -> is_background = True
        self.assertTrue(song.lines[1].is_background)
        # Line 2 does not overlap (start 4000 >= end 3500) -> is_background = False
        self.assertFalse(song.lines[2].is_background)

    def test_lead_vs_background_earlier_longer_and_echo(self) -> None:
        # 1. Parenthesized line standalone
        song_paren = SongLyrics(
            lines=[
                LyricLine(start_ms=1000, end_ms=2000, syllables=[SyllableSpan(1000, 2000, "(Chant)")]),
                LyricLine(start_ms=2500, end_ms=4000, syllables=[SyllableSpan(2500, 4000, "Lead Vocal")]),
            ]
        )
        self.assertTrue(song_paren.lines[0].is_background)
        self.assertFalse(song_paren.lines[1].is_background)

        # 2. Earlier line is lead, overlapping line is background
        song_earlier = SongLyrics(
            lines=[
                LyricLine(start_ms=1000, end_ms=3000, syllables=[SyllableSpan(1000, 3000, "Main Lead")]),
                LyricLine(start_ms=1800, end_ms=2800, syllables=[SyllableSpan(1800, 2800, "Backing Harmony")]),
            ]
        )
        self.assertFalse(song_earlier.lines[0].is_background)
        self.assertTrue(song_earlier.lines[1].is_background)

        # 3. Echo layer with exact same text
        song_echo = SongLyrics(
            lines=[
                LyricLine(start_ms=1000, end_ms=3000, syllables=[SyllableSpan(1000, 3000, "Hello World")]),
                LyricLine(start_ms=1500, end_ms=2500, syllables=[SyllableSpan(1500, 2500, "Hello World")]),
            ]
        )
        self.assertFalse(song_echo.lines[0].is_background)
        self.assertTrue(song_echo.lines[1].is_background)

    def test_sub_vocal_rendering_does_not_crash(self) -> None:
        from PySide6.QtGui import QPixmap
        song = SongLyrics(
            lines=[
                LyricLine(start_ms=1000, end_ms=3000, syllables=[SyllableSpan(1000, 3000, "Lead Vocal")]),
                LyricLine(start_ms=1500, end_ms=3200, syllables=[SyllableSpan(1500, 3200, "(Harmonies)")]),
            ]
        )
        widget = KaraokePreviewWidget()
        widget.set_lyrics(song, total_duration_ms=5000)
        widget.seek(2000)

        pm = QPixmap(500, 300)
        widget.canvas.render(pm)
        widget.deleteLater()


class TestManualSearchDialog(unittest.TestCase):
    def test_initial_state_and_status_label(self) -> None:
        from gui.manual_search_dialog import ManualSearchDialog
        dlg = ManualSearchDialog()
        self.assertEqual(dlg.lbl_status.text(), "Enter Title and Artist, then click Search.")
        self.assertEqual(dlg.table_results.rowCount(), 0)
        dlg.deleteLater()

    def test_manual_search_and_apply(self) -> None:
        from unittest.mock import patch
        from tests.test_core import _make_krc_payload
        from gui.manual_search_dialog import ManualSearchDialog

        valid_krc = (
            "[ti:Test Title]\n[ar:Test Artist]\n"
            "[1000,2000]<0,500,-1>Hel<500,500,-1>lo\n"
        )
        krc_payload = _make_krc_payload(valid_krc)

        def mock_request(url: str):
            if "search" in url:
                return {
                    "candidates": [
                        {"id": "101", "accesskey": "acc1", "song": "Test Title", "singer": "Test Artist", "duration": 180000, "krctype": 2}
                    ]
                }
            elif "download" in url:
                return {"content": krc_payload}
            return None

        with patch("gui.manual_search_dialog._make_request", side_effect=mock_request):
            dlg = ManualSearchDialog(title="Test Title", artist="Test Artist", duration_ms=180000, provider_name="Kugou")
            dlg.wait_for_search()
            self.assertEqual(dlg.table_results.rowCount(), 1)
            self.assertEqual(dlg.table_results.item(0, 0).text(), "Test Title")
            self.assertEqual(dlg.table_results.item(0, 3).text(), "Kugou")
            self.assertEqual(dlg.table_results.item(0, 4).text(), "WORD")
            self.assertIn("Found 1 result(s)", dlg.lbl_status.text())

            # Select and apply
            dlg.table_results.selectRow(0)
            dlg._on_apply_clicked()

            lyrics = dlg.get_selected_lyrics()
            self.assertIsNotNone(lyrics)
            self.assertEqual(len(lyrics.lines), 1)
            dlg.deleteLater()

    def test_smart_auto_fallback(self) -> None:
        from unittest.mock import patch
        from gui.manual_search_dialog import ManualSearchDialog

        def mock_request(url: str):
            # Primary search with artist fails
            if "keyword=Coldplay+-+Yellow+%28Live%29" in url or "keyword=Coldplay" in url:
                return {"candidates": []}
            # Fallback search with sanitized title succeeds
            if "keyword=Yellow+Live" in url:
                return {
                    "candidates": [
                        {"id": "202", "accesskey": "acc2", "song": "Yellow Live", "singer": "Coldplay", "duration": 210000, "krctype": 2}
                    ]
                }
            return {"candidates": []}

        with patch("gui.manual_search_dialog._make_request", side_effect=mock_request):
            dlg = ManualSearchDialog(title="Yellow (Live)", artist="Coldplay", duration_ms=210000, provider_name="Kugou")
            dlg.wait_for_search()
            self.assertEqual(dlg.table_results.rowCount(), 1)
            self.assertIn("Showing results for fallback query: 'Yellow Live'", dlg.lbl_status.text())
            dlg.deleteLater()

    def test_zero_candidates_status(self) -> None:
        from unittest.mock import patch
        from gui.manual_search_dialog import ManualSearchDialog

        with patch("gui.manual_search_dialog._make_request", return_value={"candidates": []}):
            dlg = ManualSearchDialog(title="Nonexistent Song", artist="Unknown", duration_ms=100000, provider_name="Kugou")
            dlg.wait_for_search()
            self.assertEqual(dlg.table_results.rowCount(), 0)
            self.assertIn("No candidates found", dlg.lbl_status.text())
            dlg.deleteLater()

    def test_network_error_status(self) -> None:
        from unittest.mock import patch
        from gui.manual_search_dialog import ManualSearchDialog

        with patch("gui.manual_search_dialog._make_request", return_value=None):
            dlg = ManualSearchDialog(title="Test Song", artist="Test", duration_ms=100000, provider_name="Kugou")
            dlg.wait_for_search()
            self.assertEqual(dlg.table_results.rowCount(), 0)
            self.assertIn("Connection error", dlg.lbl_status.text())
            dlg.deleteLater()

    def test_cell_double_click_triggers_apply(self) -> None:
        from unittest.mock import patch
        from tests.test_core import _make_krc_payload
        from gui.manual_search_dialog import ManualSearchDialog

        valid_krc = (
            "[ti:Test Title]\n[ar:Test Artist]\n"
            "[1000,2000]<0,500,-1>Hel<500,500,-1>lo\n"
        )
        krc_payload = _make_krc_payload(valid_krc)

        def mock_request(url: str):
            if "search" in url:
                return {
                    "candidates": [
                        {"id": "101", "accesskey": "acc1", "song": "Test Title", "singer": "Test Artist", "duration": 180000, "krctype": 2}
                    ]
                }
            elif "download" in url:
                return {"content": krc_payload}
            return None

        with patch("gui.manual_search_dialog._make_request", side_effect=mock_request):
            dlg = ManualSearchDialog(title="Test Title", artist="Test Artist", duration_ms=180000, provider_name="Kugou")
            dlg.wait_for_search()
            # Double click cell (0, 0)
            dlg._on_cell_double_clicked(0, 0)
            self.assertIsNotNone(dlg.get_selected_lyrics())
            dlg.deleteLater()

    def test_provider_combo_initialization(self) -> None:
        from gui.manual_search_dialog import ManualSearchDialog
        dlg = ManualSearchDialog(provider_name="Unison")
        self.assertEqual(dlg.provider_combo.count(), 4)
        items = [dlg.provider_combo.itemText(i) for i in range(4)]
        self.assertEqual(items, ["Auto", "Unison", "lrc.red", "Kugou"])
        self.assertEqual(dlg.provider_combo.currentText(), "Unison")
        dlg.deleteLater()

    def test_manual_search_with_unison_provider(self) -> None:
        from unittest.mock import patch, MagicMock
        from gui.manual_search_dialog import ManualSearchDialog

        mock_search_resp = MagicMock()
        mock_search_resp.status_code = 200
        mock_search_resp.json.return_value = {
            "data": [
                {
                    "id": "unison-123",
                    "title": "Unison Track",
                    "artist": "Unison Artist",
                    "duration": 200,
                    "syncType": "richsync",
                }
            ]
        }

        mock_lyrics_resp = MagicMock()
        mock_lyrics_resp.status_code = 200
        mock_lyrics_resp.json.return_value = {
            "data": {
                "lyrics": """<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">
  <body>
    <div>
      <p begin="00:01.000" end="00:03.000">
        <span begin="00:01.000" end="00:02.000">Hel</span>
        <span begin="00:02.000" end="00:03.000">lo</span>
      </p>
    </div>
  </body>
</tt>""",
                "format": "ttml",
            }
        }

        def fake_unison_get(url, *args, **kwargs):
            if "/lyrics/unison-123" in url:
                return mock_lyrics_resp
            return mock_search_resp

        with patch("gui.manual_search_dialog._unison_http_get", side_effect=fake_unison_get):
            dlg = ManualSearchDialog(title="Unison Track", artist="Unison Artist", duration_ms=200000, provider_name="Unison")
            dlg.wait_for_search()
            self.assertEqual(dlg.table_results.rowCount(), 1)
            self.assertEqual(dlg.table_results.item(0, 0).text(), "Unison Track")
            self.assertEqual(dlg.table_results.item(0, 3).text(), "Unison")
            self.assertEqual(dlg.table_results.item(0, 4).text(), "WORD")

            dlg.table_results.selectRow(0)
            dlg._on_apply_clicked()
            lyrics = dlg.get_selected_lyrics()
            self.assertIsNotNone(lyrics)
            self.assertTrue(lyrics.is_word_synced)
            dlg.deleteLater()

    def test_manual_search_with_lrc_red_provider(self) -> None:
        from unittest.mock import patch, MagicMock
        from gui.manual_search_dialog import ManualSearchDialog

        mock_search_resp = MagicMock()
        mock_search_resp.status_code = 200
        mock_search_resp.json.return_value = [
            {
                "id": 999,
                "title": "Red Track",
                "artist": "Red Artist",
                "duration": 150,
                "syncedLyrics": "[00:01.00]<00:01.00>Red <00:01.50>Song",
            }
        ]

        with patch("gui.manual_search_dialog._lrc_red_http_get", return_value=mock_search_resp):
            dlg = ManualSearchDialog(title="Red Track", artist="Red Artist", duration_ms=150000, provider_name="lrc.red")
            dlg.wait_for_search()
            self.assertEqual(dlg.table_results.rowCount(), 1)
            self.assertEqual(dlg.table_results.item(0, 0).text(), "Red Track")
            self.assertEqual(dlg.table_results.item(0, 3).text(), "lrc.red")
            self.assertEqual(dlg.table_results.item(0, 4).text(), "WORD")

            dlg.table_results.selectRow(0)
            dlg._on_apply_clicked()
            lyrics = dlg.get_selected_lyrics()
            self.assertIsNotNone(lyrics)
            self.assertTrue(lyrics.is_word_synced)
            dlg.deleteLater()


class TestBatchWorkerCancellation(unittest.TestCase):
    def test_worker_cancel_flag(self) -> None:
        from core.batch_worker import BatchTaggerWorker
        worker = BatchTaggerWorker([], TaggingConfig())
        self.assertFalse(worker._is_cancelled)
        worker.cancel()
        self.assertTrue(worker._is_cancelled)


if __name__ == "__main__":
    unittest.main()
