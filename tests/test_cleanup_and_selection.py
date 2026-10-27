"""Unit tests for the Clean Up toolbar button, scope selection dialog, and selection dropdown."""

import os
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from core.models import LyricLine, SongLyrics, SyncState, SyllableSpan, TaggingConfig, TrackItem
from gui.app import DeleteLyricsDialog, MainWindow

app = QApplication.instance() or QApplication(sys.argv)


class TestSelectionDropdown(unittest.TestCase):
    """Test selection dropdown modes and check state propagation."""

    def setUp(self) -> None:
        QSettings("lyric-sync-tool", "Config").clear()
        self.win = MainWindow()

    def tearDown(self) -> None:
        if hasattr(self, "win") and self.win:
            self.win.close()
            self.win.deleteLater()
            self.win = None
            QApplication.processEvents()
        QSettings("lyric-sync-tool", "Config").clear()

    def _populate_tracks(self) -> None:
        track_word = TrackItem(
            file_path="/music/album1/01_word.flac",
            title="Word Synced Song",
            artist="Artist 1",
            album="Album A",
            duration_ms=180000,
            sync_state=SyncState.WORD,
        )
        track_line = TrackItem(
            file_path="/music/album1/02_line.flac",
            title="Line Synced Song",
            artist="Artist 1",
            album="Album A",
            duration_ms=200000,
            sync_state=SyncState.LINE,
        )
        track_none = TrackItem(
            file_path="/music/album1/03_none.flac",
            title="Unsynced Song",
            artist="Artist 1",
            album="Album A",
            duration_ms=210000,
            sync_state=SyncState.NONE,
        )
        self.win._on_track_found(track_word)
        self.win._on_track_found(track_line)
        self.win._on_track_found(track_none)
        self.win._apply_selection_filter()

    def test_selection_combo_init(self) -> None:
        self.assertTrue(hasattr(self.win, "selection_combo"))
        items = [self.win.selection_combo.itemText(i) for i in range(self.win.selection_combo.count())]
        expected = [
            "Select Missing Word Sync Only",
            "Select Word Synced Only",
            "Select All",
            "Deselect All",
        ]
        self.assertEqual(items, expected)
        self.assertEqual(self.win.selection_combo.currentText(), "Select Missing Word Sync Only")

    def test_selection_missing_word_sync_only(self) -> None:
        self._populate_tracks()
        self.win.selection_combo.setCurrentText("Select Missing Word Sync Only")

        album = self.win.tree.topLevelItem(0)
        c_word = album.child(0)
        c_line = album.child(1)
        c_none = album.child(2)

        self.assertEqual(c_word.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(c_line.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(c_none.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(album.checkState(0), Qt.CheckState.PartiallyChecked)

    def test_selection_word_synced_only(self) -> None:
        self._populate_tracks()
        self.win.selection_combo.setCurrentText("Select Word Synced Only")

        album = self.win.tree.topLevelItem(0)
        c_word = album.child(0)
        c_line = album.child(1)
        c_none = album.child(2)

        self.assertEqual(c_word.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(c_line.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(c_none.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(album.checkState(0), Qt.CheckState.PartiallyChecked)

    def test_selection_all_and_deselect_all(self) -> None:
        self._populate_tracks()
        album = self.win.tree.topLevelItem(0)

        # Select All
        self.win.selection_combo.setCurrentText("Select All")
        for i in range(3):
            self.assertEqual(album.child(i).checkState(0), Qt.CheckState.Checked)
        self.assertEqual(album.checkState(0), Qt.CheckState.Checked)

        # Deselect All
        self.win.selection_combo.setCurrentText("Deselect All")
        for i in range(3):
            self.assertEqual(album.child(i).checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(album.checkState(0), Qt.CheckState.Unchecked)

    def test_retrigger_same_selection(self) -> None:
        self._populate_tracks()
        self.win.selection_combo.setCurrentText("Select All")
        album = self.win.tree.topLevelItem(0)

        # Manually uncheck one track
        album.child(0).setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(album.child(0).checkState(0), Qt.CheckState.Unchecked)

        # Re-trigger selection filter
        self.win._apply_selection_filter()
        self.assertEqual(album.child(0).checkState(0), Qt.CheckState.Checked)


class TestCleanUpToolbarButtonAndTooltip(unittest.TestCase):
    """Test Clean Up button and its tooltip in the main toolbar."""

    def setUp(self) -> None:
        QSettings("lyric-sync-tool", "Config").clear()
        self.win = MainWindow()

    def tearDown(self) -> None:
        if hasattr(self, "win") and self.win:
            self.win.close()
            self.win.deleteLater()
            self.win = None
            QApplication.processEvents()
        QSettings("lyric-sync-tool", "Config").clear()

    def test_cleanup_button_and_tooltip(self) -> None:
        self.assertTrue(hasattr(self.win, "btn_cleanup"))
        self.assertTrue(hasattr(self.win, "btn_cleanup_info"))
        self.assertIn("Clean Up", self.win.btn_cleanup.text())
        self.assertEqual(self.win.btn_cleanup_info.text(), "ⓘ")

        tooltip = self.win.btn_cleanup_info.toolTip()
        self.assertIn("Batch remove embedded lyric tags and external lyric files", tooltip)
        self.assertIn("(.lrc, .elrc, .ttml) from selected tracks or the entire library.", tooltip)


class TestDeleteLyricsDialogScope(unittest.TestCase):
    """Test DeleteLyricsDialog with and without scope options."""

    def test_dialog_scope_checked_tracks_default(self) -> None:
        dlg = DeleteLyricsDialog(parent=None, show_scope=True, checked_count=3, total_count=10)
        self.assertTrue(hasattr(dlg, "rb_selected"))
        self.assertTrue(hasattr(dlg, "rb_all"))
        self.assertTrue(dlg.rb_selected.isChecked())
        self.assertFalse(dlg.rb_all.isChecked())
        self.assertEqual(dlg.scope, "selected")
        self.assertIn("3 tracks checked", dlg.rb_selected.text())
        self.assertIn("10 tracks", dlg.rb_all.text())

        # Switch to All
        dlg.rb_all.setChecked(True)
        self.assertEqual(dlg.scope, "all")

    def test_dialog_scope_zero_checked_defaults_to_all(self) -> None:
        dlg = DeleteLyricsDialog(parent=None, show_scope=True, checked_count=0, total_count=8)
        self.assertTrue(dlg.rb_all.isChecked())
        self.assertFalse(dlg.rb_selected.isChecked())
        self.assertFalse(dlg.rb_selected.isEnabled())
        self.assertEqual(dlg.scope, "all")

    def test_dialog_format_checkboxes_and_validation(self) -> None:
        dlg = DeleteLyricsDialog(parent=None, show_scope=True, checked_count=2, total_count=5)
        self.assertTrue(dlg.cb_embedded.isChecked())
        self.assertTrue(dlg.cb_lrc.isChecked())
        self.assertTrue(dlg.cb_elrc.isChecked())
        self.assertTrue(dlg.cb_ttml.isChecked())
        self.assertTrue(dlg.btn_delete.isEnabled())

        # Uncheck all -> delete disabled
        dlg.cb_embedded.setChecked(False)
        dlg.cb_lrc.setChecked(False)
        dlg.cb_elrc.setChecked(False)
        dlg.cb_ttml.setChecked(False)
        self.assertFalse(dlg.btn_delete.isEnabled())

        # Re-check one -> re-enabled
        dlg.cb_lrc.setChecked(True)
        self.assertTrue(dlg.btn_delete.isEnabled())
        self.assertTrue(dlg.delete_lrc)
        self.assertFalse(dlg.delete_embedded)

    def test_dialog_without_scope_context_menu_compat(self) -> None:
        dlg = DeleteLyricsDialog(parent=None, track_count=2, show_scope=False)
        self.assertFalse(hasattr(dlg, "rb_selected"))
        self.assertFalse(hasattr(dlg, "rb_all"))
        self.assertTrue(dlg.btn_delete.isEnabled())


class TestCleanUpExecution(unittest.TestCase):
    """Test execution of cleanup logic and confirmation prompt."""

    def setUp(self) -> None:
        QSettings("lyric-sync-tool", "Config").clear()
        self.win = MainWindow()

    def tearDown(self) -> None:
        if hasattr(self, "win") and self.win:
            self.win.close()
            self.win.deleteLater()
            self.win = None
            QApplication.processEvents()
        QSettings("lyric-sync-tool", "Config").clear()

    def _populate_tracks(self) -> None:
        track1 = TrackItem(
            file_path="/music/t1.flac",
            title="Track 1",
            artist="Artist",
            album="Album",
            duration_ms=180000,
            sync_state=SyncState.WORD,
        )
        track2 = TrackItem(
            file_path="/music/t2.flac",
            title="Track 2",
            artist="Artist",
            album="Album",
            duration_ms=200000,
            sync_state=SyncState.WORD,
        )
        self.win._on_track_found(track1)
        self.win._on_track_found(track2)

    def test_cleanup_empty_queue_shows_info(self) -> None:
        with patch.object(QMessageBox, "information") as mock_info:
            self.win._on_cleanup_clicked()
            mock_info.assert_called_once()
            self.assertIn("No tracks loaded", mock_info.call_args[0][2])

    def test_cleanup_dialog_cancelled(self) -> None:
        self._populate_tracks()
        with patch.object(DeleteLyricsDialog, "exec", return_value=QDialog.DialogCode.Rejected), \
             patch("gui.app.strip_lyrics") as mock_strip:
            self.win._on_cleanup_clicked()
            mock_strip.assert_not_called()

    def test_cleanup_confirmation_declined(self) -> None:
        self._populate_tracks()
        with patch.object(DeleteLyricsDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.No) as mock_warn, \
             patch("gui.app.strip_lyrics") as mock_strip:
            self.win._on_cleanup_clicked()
            mock_warn.assert_called_once()
            self.assertIn("permanently delete the selected lyric format(s)", mock_warn.call_args[0][2])
            mock_strip.assert_not_called()

    def test_cleanup_confirmation_confirmed_selected_scope(self) -> None:
        self._populate_tracks()
        album = self.win.tree.topLevelItem(0)
        # Check only track 1
        album.child(0).setCheckState(0, Qt.CheckState.Checked)
        album.child(1).setCheckState(0, Qt.CheckState.Unchecked)

        with patch.object(DeleteLyricsDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(DeleteLyricsDialog, "scope", new_callable=lambda: property(lambda s: "selected")), \
             patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes) as mock_warn, \
             patch("gui.app.strip_lyrics") as mock_strip, \
             patch("gui.app.detect_sync_state", return_value=SyncState.NONE):

            self.win._on_cleanup_clicked()

            mock_warn.assert_called_once()
            self.assertIn("from 1 track(s)", mock_warn.call_args[0][2])
            # Only track 1 stripped
            mock_strip.assert_called_once()
            self.assertEqual(mock_strip.call_args[0][0], "/music/t1.flac")
            self.assertEqual(album.child(0).text(4), "Lyrics Deleted")

    def test_cleanup_confirmation_confirmed_all_scope(self) -> None:
        self._populate_tracks()

        with patch.object(DeleteLyricsDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(DeleteLyricsDialog, "scope", new_callable=lambda: property(lambda s: "all")), \
             patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes) as mock_warn, \
             patch("gui.app.strip_lyrics") as mock_strip, \
             patch("gui.app.detect_sync_state", return_value=SyncState.NONE):

            self.win._on_cleanup_clicked()

            mock_warn.assert_called_once()
            self.assertIn("from 2 track(s)", mock_warn.call_args[0][2])
            # Both tracks stripped
            self.assertEqual(mock_strip.call_count, 2)


if __name__ == "__main__":
    unittest.main()
