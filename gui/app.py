"""Utilitarian, dense desktop application for offline syllable-synced lyrics management."""

import os
import pathlib
import re
import sys
from typing import List, Optional

# Ensure project root is in sys.path for direct script execution
_PROJECT_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.batch_worker import BatchTaggerWorker, ScannerWorker
from core.models import LyricLine, SongLyrics, SyllableSpan, SyncState, TaggingConfig, TrackItem
from core.providers.kugou import KugouProvider
from core.tagger import detect_sync_state, strip_lyrics
from gui.editor_widget import LyricsEditorWidget
from gui.preview_widget import KaraokePreviewWidget
from gui.settings_dialog import TaggingSettingsDialog
from gui.themes import THEMES


def _parse_lrc_content(text: str) -> Optional[SongLyrics]:
    """Parse standard or enhanced LRC content into SongLyrics."""
    if not text:
        return None

    metadata = {}
    lines: List[LyricLine] = []

    # Centisecond regex mm:ss.xx
    lrc_line_re = re.compile(r"^\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)$")
    word_re = re.compile(r"<(\d{2}):(\d{2})\.(\d{2,3})>([^<]*)")

    def _ts_to_ms(m: str, s: str, cs: str) -> int:
        millis = int(cs) * 10 if len(cs) == 2 else int(cs)
        return int(m) * 60000 + int(s) * 1000 + millis

    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        if raw_line.startswith("[") and not raw_line[1].isdigit():
            # Meta tag e.g. [ar:Artist]
            meta_m = re.match(r"^\[([a-zA-Z]+):(.*)\]$", raw_line)
            if meta_m:
                metadata[meta_m.group(1)] = meta_m.group(2)
            continue

        match = lrc_line_re.match(raw_line)
        if not match:
            continue

        start_ms = _ts_to_ms(match.group(1), match.group(2), match.group(3))
        body = match.group(4).strip()

        syllables: List[SyllableSpan] = []
        word_matches = list(word_re.finditer(body))

        if word_matches:
            for j, wm in enumerate(word_matches):
                s_start = _ts_to_ms(wm.group(1), wm.group(2), wm.group(3))
                s_text = wm.group(4)
                if j + 1 < len(word_matches):
                    next_wm = word_matches[j + 1]
                    s_end = _ts_to_ms(next_wm.group(1), next_wm.group(2), next_wm.group(3))
                else:
                    s_end = s_start + 1000
                syllables.append(SyllableSpan(abs_start_ms=s_start, abs_end_ms=s_end, text=s_text))
            end_ms = syllables[-1].abs_end_ms
        else:
            end_ms = start_ms + 3000
            syllables.append(SyllableSpan(abs_start_ms=start_ms, abs_end_ms=end_ms, text=body))

        lines.append(LyricLine(start_ms=start_ms, end_ms=end_ms, syllables=syllables))

    return SongLyrics(metadata=metadata, lines=lines) if lines else None


def load_lyrics_for_file(file_path: str) -> Optional[SongLyrics]:
    """Inspect and load existing lyrics for a file from sidecars or tags."""
    p = pathlib.Path(file_path)
    for ext in (".lrc", ".elrc"):
        sidecar = p.with_suffix(ext)
        if sidecar.is_file():
            try:
                content = sidecar.read_text(encoding="utf-8", errors="replace")
                parsed = _parse_lrc_content(content)
                if parsed:
                    return parsed
            except Exception:
                pass
    return None


class MainWindow(QMainWindow):
    """Foobar2000 / MusicBee utilitarian desktop layout for lyrics synchronization."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Offline Syllable-Synced Lyrics Manager")
        self.resize(1100, 680)

        self.config = TaggingConfig()
        self._tracks: List[TrackItem] = []
        self._current_active_row: int = -1

        self.scanner_thread: Optional[ScannerWorker] = None
        self.tagger_thread: Optional[BatchTaggerWorker] = None

        self._init_ui()
        self.set_theme("Dark")

    def _init_ui(self) -> None:
        # ── Toolbar ──
        toolbar = QToolBar("Main Controls", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.btn_scan = QPushButton("Scan Folder")
        self.btn_scan.clicked.connect(self._on_scan_folder_clicked)
        toolbar.addWidget(self.btn_scan)

        toolbar.addSeparator()

        self.btn_settings = QPushButton("⚙ Settings")
        self.btn_settings.clicked.connect(self._on_settings_clicked)
        toolbar.addWidget(self.btn_settings)

        toolbar.addSeparator()

        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(["Kugou"])
        toolbar.addWidget(self.cmb_provider)

        toolbar.addSeparator()

        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(["Dark", "OLED Dark", "White"])
        self.cmb_theme.currentTextChanged.connect(self.set_theme)
        toolbar.addWidget(self.cmb_theme)

        toolbar.addSeparator()

        self.chk_missing_only = QCheckBox("Select Missing Word Sync Only")
        self.chk_missing_only.setChecked(True)
        self.chk_missing_only.toggled.connect(self._apply_missing_filter)
        toolbar.addWidget(self.chk_missing_only)

        toolbar.addSeparator()

        self.btn_start_tagging = QPushButton("▶ Start Tagging")
        self.btn_start_tagging.setStyleSheet("font-weight: 600;")
        self.btn_start_tagging.clicked.connect(self._on_start_tagging_clicked)
        toolbar.addWidget(self.btn_start_tagging)

        # ── Central Splitter ──
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setHandleWidth(4)

        # Left: Table
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Title", "Artist", "Duration", "Sync State", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._on_row_selected)

        left_layout.addWidget(self.table)
        splitter.addWidget(left_widget)

        # Right: Inspector Tabs
        self.tabs = QTabWidget()
        self.preview_widget = KaraokePreviewWidget()
        self.editor_widget = LyricsEditorWidget()

        # Connect editor text/offset changes to live preview
        self.editor_widget.lyrics_changed.connect(self._on_editor_lyrics_changed)

        self.tabs.addTab(self.preview_widget, "Live Preview")
        self.tabs.addTab(self.editor_widget, "Plain Editor")
        splitter.addWidget(self.tabs)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

        # ── Status Bar ──
        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setVisible(False)
        status_bar.addPermanentWidget(self.progress_bar)

        status_bar.showMessage("Ready. Click 'Scan Folder' to load audio tracks.")

    def set_theme(self, theme_name: str) -> None:
        qss = THEMES.get(theme_name, THEMES["Dark"])
        self.setStyleSheet(qss)

    def _on_scan_folder_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Music Directory", os.path.expanduser("~"))
        if not folder:
            return

        self._tracks.clear()
        self.table.setRowCount(0)
        self.statusBar().showMessage(f"Scanning directory: {folder}...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate

        self.scanner_thread = ScannerWorker(folder, self)
        self.scanner_thread.track_found.connect(self._on_track_found)
        self.scanner_thread.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self.scanner_thread.finished_scanning.connect(self._on_scan_finished)
        self.scanner_thread.start()

    def _on_track_found(self, track: TrackItem) -> None:
        self._tracks.append(track)
        row = self.table.rowCount()
        self.table.insertRow(row)

        chk_item = QTableWidgetItem()
        chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk_item.setCheckState(Qt.Checked)
        self.table.setItem(row, 0, chk_item)

        self.table.setItem(row, 1, QTableWidgetItem(track.title))
        self.table.setItem(row, 2, QTableWidgetItem(track.artist))

        sec = (track.duration_ms // 1000) % 60
        mins = track.duration_ms // 60000
        dur_str = f"{mins:02d}:{sec:02d}" if track.duration_ms > 0 else "--:--"
        self.table.setItem(row, 3, QTableWidgetItem(dur_str))

        self.table.setItem(row, 4, QTableWidgetItem(track.sync_state.name))
        self.table.setItem(row, 5, QTableWidgetItem("Ready"))

        self._apply_row_styling(row, track)

    def _on_scan_finished(self, total: int) -> None:
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"Scan complete. {total} audio file(s) loaded.")
        self._apply_missing_filter()

    def _apply_missing_filter(self) -> None:
        missing_only = self.chk_missing_only.isChecked()
        overwrite = self.config.overwrite_existing_word_sync

        for row, track in enumerate(self._tracks):
            chk_item = self.table.item(row, 0)
            if not chk_item:
                continue

            if missing_only:
                if not overwrite and track.sync_state == SyncState.WORD:
                    chk_item.setCheckState(Qt.Unchecked)
                    self._dim_row(row, True)
                else:
                    chk_item.setCheckState(Qt.Checked)
                    self._dim_row(row, False)
            else:
                self._dim_row(row, False)

    def _dim_row(self, row: int, dimmed: bool) -> None:
        brush = QColor(100, 100, 100) if dimmed else QColor(220, 220, 220)
        for col in range(1, 6):
            item = self.table.item(row, col)
            if item:
                item.setForeground(brush)

    def _apply_row_styling(self, row: int, track: TrackItem) -> None:
        sync_item = self.table.item(row, 4)
        if not sync_item:
            return

        if track.sync_state == SyncState.WORD:
            sync_item.setForeground(QColor(0, 200, 100))
        elif track.sync_state == SyncState.LINE:
            sync_item.setForeground(QColor(230, 180, 0))
        else:
            sync_item.setForeground(QColor(160, 160, 160))

    def _on_row_selected(self) -> None:
        selected_indexes = self.table.selectedIndexes()
        if not selected_indexes:
            return

        row = selected_indexes[0].row()
        if row == self._current_active_row:
            return
        self._current_active_row = row

        track = self._tracks[row]
        if track.matched_lyrics is None:
            track.matched_lyrics = load_lyrics_for_file(track.file_path)

        self.preview_widget.set_lyrics(track.matched_lyrics, track.duration_ms)
        self.editor_widget.set_lyrics(track.matched_lyrics)

    def _on_editor_lyrics_changed(self, updated_lyrics: SongLyrics) -> None:
        if self._current_active_row >= 0:
            track = self._tracks[self._current_active_row]
            track.matched_lyrics = updated_lyrics
            self.preview_widget.set_lyrics(updated_lyrics, track.duration_ms)

    def _show_context_menu(self, pos) -> None:
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        if not selected_rows:
            return

        menu = QMenu(self)
        action_strip = menu.addAction("Delete existing lyrics for selected file(s)")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))

        if action == action_strip:
            count = len(selected_rows)
            reply = QMessageBox.warning(
                self,
                "Confirm Lyrics Deletion",
                f"Caution: This will permanently strip lyrics from the selected {count} file(s) "
                "across all formats (embedded Vorbis/ID3/MP4 tags, plus sibling .lrc, .elrc, and .ttml files).\n\n"
                "Are you sure you want to proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply == QMessageBox.Yes:
                for r in selected_rows:
                    track = self._tracks[r]
                    strip_lyrics(track.file_path)
                    track.sync_state = SyncState.NONE
                    track.matched_lyrics = None
                    self._update_table_row_sync_state(r, SyncState.NONE)
                    self._update_table_row_status(r, "Lyrics Deleted")

                if self._current_active_row in selected_rows:
                    self.preview_widget.set_lyrics(None)
                    self.editor_widget.set_lyrics(None)

                self.statusBar().showMessage(f"Stripped lyrics from {count} file(s).")
                self._apply_missing_filter()

    def _update_table_row_sync_state(self, row: int, state: SyncState) -> None:
        item = self.table.item(row, 4)
        if item:
            item.setText(state.name)
            self._apply_row_styling(row, self._tracks[row])

    def _update_table_row_status(self, row: int, status: str) -> None:
        item = self.table.item(row, 5)
        if item:
            item.setText(status)

    def _on_settings_clicked(self) -> None:
        dlg = TaggingSettingsDialog(self.config, self)
        dlg.config_saved.connect(self._on_config_saved)
        dlg.exec()

    def _on_config_saved(self, new_config: TaggingConfig) -> None:
        self.config = new_config
        self.statusBar().showMessage("Settings saved.")
        self._apply_missing_filter()

    def _on_start_tagging_clicked(self) -> None:
        if self.tagger_thread and self.tagger_thread.isRunning():
            self.tagger_thread.requestInterruption()
            self.btn_start_tagging.setText("▶ Start Tagging")
            self.statusBar().showMessage("Tagging cancelled.")
            return

        selected_tracks: List[TrackItem] = []
        track_row_map = {}

        for row, track in enumerate(self._tracks):
            chk_item = self.table.item(row, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                track_row_map[len(selected_tracks)] = row
                selected_tracks.append(track)

        if not selected_tracks:
            QMessageBox.information(self, "No Tracks Selected", "No tracks checked for tagging.")
            return

        self.btn_start_tagging.setText("⏹ Stop Tagging")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(selected_tracks))
        self.progress_bar.setValue(0)
        self.statusBar().showMessage(f"Starting batch tagging for {len(selected_tracks)} track(s)...")

        self.tagger_thread = BatchTaggerWorker(selected_tracks, self.config, KugouProvider(), self)

        def _on_track_progress(idx: int, status_text: str, track: TrackItem) -> None:
            orig_row = track_row_map.get(idx)
            if orig_row is not None:
                self._update_table_row_status(orig_row, status_text)
                self._update_table_row_sync_state(orig_row, track.sync_state)
                if orig_row == self._current_active_row:
                    self.preview_widget.set_lyrics(track.matched_lyrics, track.duration_ms)
                    self.editor_widget.set_lyrics(track.matched_lyrics)

        self.tagger_thread.track_progress.connect(_on_track_progress)
        self.tagger_thread.overall_progress.connect(lambda done, tot: self.progress_bar.setValue(done))
        self.tagger_thread.batch_finished.connect(self._on_batch_finished)
        self.tagger_thread.start()

    def _on_batch_finished(self) -> None:
        self.btn_start_tagging.setText("▶ Start Tagging")
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage("Batch tagging complete.")
        self._apply_missing_filter()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
