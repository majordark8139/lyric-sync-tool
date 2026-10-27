"""Unit tests for cascading Auto provider pipeline and low-resource HDD/RAM optimizations."""

import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.batch_worker import BatchTaggerWorker
from core.models import LyricLine, SongLyrics, SyllableSpan, SyncState, TaggingConfig, TrackItem
from core.providers.auto import AutoLyricsProvider
from core.tagger import DISK_IO_LOCK, apply_lyrics


def _make_word_lyrics(identifier: str) -> SongLyrics:
    """Helper to synthesize valid word-synced lyrics with a unique identifier."""
    lines = [
        LyricLine(
            start_ms=1000,
            end_ms=3000,
            syllables=[
                SyllableSpan(1000, 2000, f"{identifier} "),
                SyllableSpan(2000, 3000, "word"),
            ],
        )
    ]
    return SongLyrics(lines=lines, is_word_synced=True)


class TestAutoLyricsProviderPipeline(unittest.TestCase):
    """Tests for AutoLyricsProvider and BatchTaggerWorker streaming cascade."""

    def test_auto_provider_unison_hit(self) -> None:
        """When Unison returns word sync, it tags immediately without querying lrc.red or Kugou."""
        mock_unison = MagicMock()
        mock_unison.name = "Unison"
        mock_unison.search_word_sync.return_value = _make_word_lyrics("Unison")

        mock_lrc = MagicMock()
        mock_lrc.name = "lrc.red"
        mock_lrc.search_word_sync.return_value = _make_word_lyrics("lrc.red")

        mock_kugou = MagicMock()
        mock_kugou.name = "kugou"
        mock_kugou.search_word_sync.return_value = _make_word_lyrics("kugou")

        provider = AutoLyricsProvider(unison=mock_unison, lrc_red=mock_lrc, kugou=mock_kugou)
        lyrics = provider.search_word_sync("Title", "Artist", 180000)

        self.assertIsNotNone(lyrics)
        self.assertEqual(lyrics.lines[0].syllables[0].text, "Unison ")
        mock_unison.search_word_sync.assert_called_once()
        mock_lrc.search_word_sync.assert_not_called()
        mock_kugou.search_word_sync.assert_not_called()

    def test_auto_provider_unison_fail_lrc_red_hit(self) -> None:
        """When Unison fails, it immediately queries lrc.red without querying Kugou."""
        mock_unison = MagicMock()
        mock_unison.name = "Unison"
        mock_unison.search_word_sync.return_value = None

        mock_lrc = MagicMock()
        mock_lrc.name = "lrc.red"
        mock_lrc.search_word_sync.return_value = _make_word_lyrics("lrc.red")

        mock_kugou = MagicMock()
        mock_kugou.name = "kugou"
        mock_kugou.search_word_sync.return_value = _make_word_lyrics("kugou")

        provider = AutoLyricsProvider(unison=mock_unison, lrc_red=mock_lrc, kugou=mock_kugou)
        lyrics = provider.search_word_sync("Title", "Artist", 180000)

        self.assertIsNotNone(lyrics)
        self.assertEqual(lyrics.lines[0].syllables[0].text, "lrc.red ")
        mock_unison.search_word_sync.assert_called_once()
        mock_lrc.search_word_sync.assert_called_once()
        mock_kugou.search_word_sync.assert_not_called()

    def test_auto_provider_unison_and_lrc_red_fail_kugou_hit(self) -> None:
        """When Unison and lrc.red both fail, it cascades to Kugou."""
        mock_unison = MagicMock()
        mock_unison.name = "Unison"
        mock_unison.search_word_sync.return_value = None

        mock_lrc = MagicMock()
        mock_lrc.name = "lrc.red"
        mock_lrc.search_word_sync.return_value = None

        mock_kugou = MagicMock()
        mock_kugou.name = "kugou"
        mock_kugou.search_word_sync.return_value = _make_word_lyrics("kugou")

        provider = AutoLyricsProvider(unison=mock_unison, lrc_red=mock_lrc, kugou=mock_kugou)
        lyrics = provider.search_word_sync("Title", "Artist", 180000)

        self.assertIsNotNone(lyrics)
        self.assertEqual(lyrics.lines[0].syllables[0].text, "kugou ")
        mock_unison.search_word_sync.assert_called_once()
        mock_lrc.search_word_sync.assert_called_once()
        mock_kugou.search_word_sync.assert_called_once()

    def test_auto_provider_all_fail_returns_none(self) -> None:
        """When all providers return None, Auto returns None."""
        mock_unison = MagicMock()
        mock_unison.search_word_sync.return_value = None
        mock_lrc = MagicMock()
        mock_lrc.search_word_sync.return_value = None
        mock_kugou = MagicMock()
        mock_kugou.search_word_sync.return_value = None

        provider = AutoLyricsProvider(unison=mock_unison, lrc_red=mock_lrc, kugou=mock_kugou)
        lyrics = provider.search_word_sync("Title", "Artist", 180000)

        self.assertIsNone(lyrics)
        mock_unison.search_word_sync.assert_called_once()
        mock_lrc.search_word_sync.assert_called_once()
        mock_kugou.search_word_sync.assert_called_once()

    def test_batch_worker_with_auto_pipeline_stages(self) -> None:
        """Verify BatchTaggerWorker with Auto pipeline updates status and tags tracks."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            track1_path = str(pathlib.Path(tmp_dir) / "track1.mp3")
            track2_path = str(pathlib.Path(tmp_dir) / "track2.mp3")
            track3_path = str(pathlib.Path(tmp_dir) / "track3.mp3")

            t1 = TrackItem(file_path=track1_path, title="Track 1", artist="Artist", duration_ms=180000)
            t2 = TrackItem(file_path=track2_path, title="Track 2", artist="Artist", duration_ms=180000)
            t3 = TrackItem(file_path=track3_path, title="Track 3", artist="Artist", duration_ms=180000)

            mock_unison = MagicMock()
            # Track 1 hits on Unison, others miss
            mock_unison.search_word_sync.side_effect = lambda title, **kwargs: (
                _make_word_lyrics("Unison") if title == "Track 1" else None
            )

            mock_lrc = MagicMock()
            # Track 2 hits on lrc.red, others miss
            mock_lrc.search_word_sync.side_effect = lambda title, **kwargs: (
                _make_word_lyrics("lrc.red") if title == "Track 2" else None
            )

            mock_kugou = MagicMock()
            # Track 3 misses all
            mock_kugou.search_word_sync.return_value = None

            auto_provider = AutoLyricsProvider(unison=mock_unison, lrc_red=mock_lrc, kugou=mock_kugou)

            with patch("core.batch_worker.apply_lyrics") as mock_apply:
                worker = BatchTaggerWorker([t1, t2, t3], TaggingConfig(), provider=auto_provider)
                worker.run()

                # t1 tagged via Unison
                self.assertEqual(t1.status, "Tagged (WORD)")
                self.assertEqual(t1.sync_state, SyncState.WORD)

                # t2 tagged via lrc.red
                self.assertEqual(t2.status, "Tagged (WORD)")
                self.assertEqual(t2.sync_state, SyncState.WORD)

                # t3 missed all providers, file left untouched
                self.assertEqual(t3.status, "No Word Sync Available")

                # apply_lyrics called for t1 and t2 only
                self.assertEqual(mock_apply.call_count, 2)

    def test_disk_io_lock_serializes_writes(self) -> None:
        """Verify that DISK_IO_LOCK serializes concurrent file write operations."""
        execution_order = []
        concurrency_detected = False

        def simulated_write(task_id: int):
            nonlocal concurrency_detected
            with DISK_IO_LOCK:
                if len(execution_order) > 0 and execution_order[-1] == "inside":
                    concurrency_detected = True
                execution_order.append("inside")
                time.sleep(0.02)
                execution_order.append(f"done_{task_id}")

        threads = [threading.Thread(target=simulated_write, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertFalse(concurrency_detected, "DISK_IO_LOCK must strictly serialize write blocks")
        # Every 'inside' is immediately followed by 'done_X' before the next 'inside'
        for i in range(0, len(execution_order), 2):
            self.assertEqual(execution_order[i], "inside")
            self.assertTrue(execution_order[i + 1].startswith("done_"))


class TestGuiResizeAndInfoButton(unittest.TestCase):
    """Tests for GUI interactive resize mode, column widths, and Auto info button."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtCore import QSettings
        QSettings("lyric-sync-tool", "Config").clear()

    def tearDown(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        QSettings("lyric-sync-tool", "Config").clear()
        if hasattr(self, "win") and self.win:
            self.win.close()
            self.win.deleteLater()
            self.win = None
            QApplication.processEvents()

    def test_tree_column_interactive_mode_and_widths(self) -> None:
        from PySide6.QtWidgets import QHeaderView
        from gui.app import MainWindow

        self.win = MainWindow()
        header = self.win.tree.header()

        # Verify all sections are in Interactive resize mode
        for col_idx in range(5):
            mode = header.sectionResizeMode(col_idx)
            self.assertEqual(
                mode,
                QHeaderView.ResizeMode.Interactive,
                f"Column {col_idx} must have Interactive resize mode for user draggable handles",
            )

        # Verify default column widths
        self.assertEqual(self.win.tree.columnWidth(0), 220)
        self.assertEqual(self.win.tree.columnWidth(1), 140)
        self.assertEqual(self.win.tree.columnWidth(2), 75)
        self.assertEqual(self.win.tree.columnWidth(3), 90)
        self.assertEqual(self.win.tree.columnWidth(4), 160)
        self.assertTrue(header.stretchLastSection())

    def test_provider_dropdown_and_info_button(self) -> None:
        from gui.app import MainWindow

        self.win = MainWindow()

        # Verify 'Auto' is first item in provider dropdown
        items = [self.win.cmb_provider.itemText(i) for i in range(self.win.cmb_provider.count())]
        self.assertEqual(items[0], "Auto")
        self.assertEqual(items, ["Auto", "Unison", "lrc.red", "Kugou"])

        # Verify info button exists and has correct styling and tooltip
        self.assertIsNotNone(self.win.provider_info_btn)
        self.assertEqual(self.win.provider_info_btn.text(), "ⓘ")
        tooltip = self.win.provider_info_btn.toolTip()
        self.assertIn("Auto Mode (Cascading Pipeline):", tooltip)
        self.assertIn("Searches Unison first", tooltip)
        self.assertIn("stream to lrc.red", tooltip)
        self.assertIn("fall back to Kugou", tooltip)

    def test_tree_column_widths_restore_and_save(self) -> None:
        from PySide6.QtGui import QCloseEvent
        from gui.app import MainWindow

        self.win = MainWindow()
        # Custom widths
        custom_widths = [300, 150, 80, 100, 180]
        self.win.config.column_widths = custom_widths
        self.win._setup_tree()

        # Check restored widths on tree
        self.assertEqual(self.win.tree.columnWidth(0), 300)
        self.assertEqual(self.win.tree.columnWidth(1), 150)
        self.assertEqual(self.win.tree.columnWidth(2), 80)
        self.assertEqual(self.win.tree.columnWidth(3), 100)
        self.assertEqual(self.win.tree.columnWidth(4), 180)

        # Simulate resize
        self.win.tree.header().resizeSection(0, 350)
        self.assertEqual(self.win.config.column_widths[0], 350)

        # Trigger closeEvent
        close_event = QCloseEvent()
        self.win.closeEvent(close_event)
        self.assertEqual(self.win.config.column_widths[0], 350)


if __name__ == "__main__":
    unittest.main()

