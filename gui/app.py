"""Utilitarian, dense desktop application for offline syllable-synced lyrics management."""

import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

# Ensure project root is in sys.path for direct script execution
_PROJECT_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import mutagen
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QKeyEvent,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.batch_worker import BatchTaggerWorker, ScannerWorker, _extract_tag_string
from core.config import Config, SettingsManager, load_config, save_config
from core.models import LyricLine, SongLyrics, SyllableSpan, SyncState, TaggingConfig, TrackItem
from core.providers.auto import AutoLyricsProvider
from core.providers.kugou import KugouProvider
from core.providers.lrc_red import LrcRedProvider
from core.providers.unison import UnisonProvider
from core.tagger import (
    apply_lyrics,
    detect_sync_state,
    read_metadata,
    read_track_lyrics,
    strip_lyrics,
)
from gui.editor_widget import LyricsEditorWidget
from gui.manual_search_dialog import ManualSearchDialog
from gui.preview_widget import KaraokePreviewWidget
from gui.settings_dialog import TaggingSettingsDialog
from gui.themes import THEMES

_AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg"}


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
    """Inspect and load existing lyrics for a file from sidecars or embedded tags."""
    lyrics = read_track_lyrics(file_path)
    if lyrics:
        return lyrics
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


class _TreeItemCompatWrapper:
    """Compatibility adapter mapping old table cells to QTreeWidget child items."""

    def __init__(self, item: QTreeWidgetItem, col_idx: int) -> None:
        self._item = item
        self._col = col_idx

    def text(self) -> str:
        return self._item.text(self._col)

    def setText(self, text: str) -> None:
        self._item.setText(self._col, text)

    def checkState(self) -> Qt.CheckState:
        return self._item.checkState(0)

    def setCheckState(self, state: Qt.CheckState) -> None:
        self._item.setCheckState(0, state)

    def setForeground(self, brush: Any) -> None:
        self._item.setForeground(self._col, brush)


class _TableTreeCompat:
    """Compatibility wrapper emulating QTableWidget API over hierarchical QTreeWidget."""

    def __init__(self, tree: QTreeWidget, window: "MainWindow") -> None:
        self._tree = tree
        self._window = window

    def rowCount(self) -> int:
        count = 0
        for i in range(self._tree.topLevelItemCount()):
            count += self._tree.topLevelItem(i).childCount()
        return count

    def setRowCount(self, count: int) -> None:
        if count == 0:
            self._tree.clear()
            self._window._album_nodes.clear()
            self._window._tracks.clear()
            self._window._current_active_item = None

    def item(self, row: int, col: int) -> Optional[_TreeItemCompatWrapper]:
        curr = 0
        for i in range(self._tree.topLevelItemCount()):
            album = self._tree.topLevelItem(i)
            for j in range(album.childCount()):
                if curr == row:
                    child = album.child(j)
                    # Old columns: 0: Check, 1: Title, 2: Artist, 3: Duration, 4: Sync, 5: Status
                    # New columns: 0: Title, 1: Artist, 2: Duration, 3: Sync, 4: Status
                    target_col = max(0, col - 1) if col > 0 else 0
                    return _TreeItemCompatWrapper(child, target_col)
                curr += 1

class DeleteLyricsDialog(QDialog):
    """Granular multi-format lyrics deletion dialog with optional scope selection."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        track_count: int = 1,
        show_scope: bool = False,
        checked_count: int = 0,
        total_count: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Delete Lyrics")
        self.setModal(True)
        self.setMinimumWidth(380)

        self.show_scope = show_scope
        self.checked_count = checked_count
        self.total_count = total_count

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        if self.show_scope:
            scope_box = QGroupBox("Scope Selection")
            scope_layout = QVBoxLayout(scope_box)
            scope_layout.setSpacing(8)

            self.scope_group = QButtonGroup(self)
            self.rb_selected = QRadioButton(f"Selected tracks only ({checked_count} tracks checked)")
            self.rb_all = QRadioButton(f"All tracks ({total_count} tracks)")

            self.scope_group.addButton(self.rb_selected)
            self.scope_group.addButton(self.rb_all)

            scope_layout.addWidget(self.rb_selected)
            scope_layout.addWidget(self.rb_all)

            if checked_count > 0:
                self.rb_selected.setChecked(True)
            else:
                self.rb_all.setChecked(True)
                self.rb_selected.setEnabled(False)

            layout.addWidget(scope_box)

            lbl_warning = QLabel("Select the lyric formats you wish to remove:")
        else:
            # Warning / prompt text
            lbl_warning = QLabel("Select the lyric formats you wish to remove from the selected track(s):")

        lbl_warning.setWordWrap(True)
        lbl_warning.setStyleSheet("font-weight: 500; font-size: 13px; line-height: 1.4;")
        layout.addWidget(lbl_warning)

        # Four distinct checkboxes, all checked by default
        cb_layout = QVBoxLayout()
        cb_layout.setSpacing(10)
        cb_layout.setContentsMargins(8, 4, 8, 4)

        self.cb_embedded = QCheckBox("Embedded tags (FLAC Vorbis / ID3 / MP4)")
        self.cb_embedded.setChecked(True)
        cb_layout.addWidget(self.cb_embedded)

        self.cb_lrc = QCheckBox("Standard LRC (.lrc)")
        self.cb_lrc.setChecked(True)
        cb_layout.addWidget(self.cb_lrc)

        self.cb_elrc = QCheckBox("Enhanced LRC (.elrc)")
        self.cb_elrc.setChecked(True)
        cb_layout.addWidget(self.cb_elrc)

        self.cb_ttml = QCheckBox("Apple TTML (.ttml)")
        self.cb_ttml.setChecked(True)
        cb_layout.addWidget(self.cb_ttml)

        layout.addLayout(cb_layout)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_delete = QPushButton("Delete Selected")
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: #ffffff;
                font-weight: bold;
                padding: 6px 16px;
                border-radius: 4px;
                border: 1px solid #b91c1c;
            }
            QPushButton:hover {
                background-color: #ef4444;
            }
            QPushButton:pressed {
                background-color: #991b1b;
            }
            QPushButton:disabled {
                background-color: #4b5563;
                border-color: #374151;
                color: #9ca3af;
            }
        """)
        self.btn_delete.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_delete)

        layout.addLayout(btn_layout)

        # Connect toggles to enable/disable "Delete Selected"
        self.cb_embedded.toggled.connect(self._update_btn_state)
        self.cb_lrc.toggled.connect(self._update_btn_state)
        self.cb_elrc.toggled.connect(self._update_btn_state)
        self.cb_ttml.toggled.connect(self._update_btn_state)

        self._update_btn_state()

    def _update_btn_state(self) -> None:
        has_any = any([
            self.cb_embedded.isChecked(),
            self.cb_lrc.isChecked(),
            self.cb_elrc.isChecked(),
            self.cb_ttml.isChecked(),
        ])
        self.btn_delete.setEnabled(has_any)

    @property
    def scope(self) -> str:
        if hasattr(self, "rb_selected") and self.rb_selected.isChecked():
            return "selected"
        return "all"

    @property
    def delete_embedded(self) -> bool:
        return self.cb_embedded.isChecked()

    @property
    def delete_lrc(self) -> bool:
        return self.cb_lrc.isChecked()

    @property
    def delete_elrc(self) -> bool:
        return self.cb_elrc.isChecked()

    @property
    def delete_ttml(self) -> bool:
        return self.cb_ttml.isChecked()


class MainWindow(QMainWindow):
    """Foobar2000 / MusicBee utilitarian desktop layout with album-grouped track queue."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Offline Syllable-Synced Lyrics Manager")
        self.resize(1120, 700)

        self.setAcceptDrops(True)

        self.settings_manager = SettingsManager()
        self.current_settings = self.settings_manager.load_settings()
        self.config = self.current_settings
        self._tree_was_sorting: bool = False
        self._tracks: List[TrackItem] = []
        self._album_nodes: Dict[Tuple[str, str], QTreeWidgetItem] = {}
        self._current_active_item: Optional[QTreeWidgetItem] = None
        self._blocking_checks: bool = False

        self.scanner_thread: Optional[ScannerWorker] = None
        self.tagger_thread: Optional[BatchTaggerWorker] = None

        self._init_ui()
        self._restore_layout_state()

        # Restore active provider
        saved_provider = getattr(self.current_settings, "active_provider", "Auto")
        p_idx = self.cmb_provider.findText(saved_provider)
        if p_idx >= 0:
            self.cmb_provider.setCurrentIndex(p_idx)

        # Restore active theme
        saved_theme = getattr(self.current_settings, "theme", "Dark")
        self.set_theme(saved_theme)
        t_idx = self.cmb_theme.findText(saved_theme)
        if t_idx >= 0:
            self.cmb_theme.setCurrentIndex(t_idx)

    def _init_ui(self) -> None:
        # ── Toolbar ──
        toolbar = QToolBar("Main Controls", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.btn_scan = QPushButton("Scan Folder")
        self.btn_scan.clicked.connect(self._on_scan_folder_clicked)
        toolbar.addWidget(self.btn_scan)

        toolbar.addSeparator()

        self.btn_expand_all = QPushButton("▼ Expand All")
        self.btn_expand_all.setToolTip("Expand all album groups (Ctrl+Shift+E)")
        self.btn_expand_all.clicked.connect(self._expand_all)
        toolbar.addWidget(self.btn_expand_all)

        self.btn_collapse_all = QPushButton("▲ Collapse All")
        self.btn_collapse_all.setToolTip("Collapse all album groups (Ctrl+Shift+C)")
        self.btn_collapse_all.clicked.connect(self._collapse_all)
        toolbar.addWidget(self.btn_collapse_all)

        toolbar.addSeparator()

        self.btn_settings = QPushButton("⚙ Settings")
        self.btn_settings.clicked.connect(self._on_settings_clicked)
        toolbar.addWidget(self.btn_settings)

        toolbar.addSeparator()

        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(["Auto", "Unison", "lrc.red", "Kugou"])
        self.cmb_provider.currentTextChanged.connect(self._on_provider_changed)
        toolbar.addWidget(self.cmb_provider)

        self.provider_info_btn = QToolButton()
        self.provider_info_btn.setText("ⓘ")
        self.provider_info_btn.setToolTip(
            "Auto Mode (Cascading Pipeline):\n"
            "Searches Unison first for high-fidelity Apple Music word sync.\n"
            "Tracks missing word sync immediately stream to lrc.red in parallel.\n"
            "Any remaining misses fall back to Kugou KRC syllable sync.\n"
            "Ensures maximum library coverage at peak tagging speed."
        )
        self.provider_info_btn.setStyleSheet("""
            QToolButton {
                border: 1px solid #555;
                border-radius: 9px;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                font-size: 11px;
                font-weight: bold;
                color: #aaa;
                background: transparent;
            }
            QToolButton:hover {
                border-color: #3b82f6;
                color: #3b82f6;
                background: rgba(59, 130, 246, 0.1);
            }
        """)
        toolbar.addWidget(self.provider_info_btn)

        toolbar.addSeparator()

        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(["Dark", "OLED Dark", "White"])
        self.cmb_theme.currentTextChanged.connect(self.set_theme)
        toolbar.addWidget(self.cmb_theme)

        toolbar.addSeparator()

        self.selection_combo = QComboBox()
        self.selection_combo.addItems([
            "Select Missing Word Sync Only",
            "Select Word Synced Only",
            "Select All",
            "Deselect All",
        ])
        self.selection_combo.setCurrentText("Select Missing Word Sync Only")
        self.selection_combo.currentTextChanged.connect(self._apply_selection_filter)
        self.selection_combo.activated.connect(lambda _: self._apply_selection_filter())
        toolbar.addWidget(self.selection_combo)

        toolbar.addSeparator()

        self.btn_start_tagging = QPushButton("▶ Start Tagging")
        self.btn_start_tagging.setStyleSheet("font-weight: 600;")
        self.btn_start_tagging.clicked.connect(self._on_start_tagging_clicked)
        toolbar.addWidget(self.btn_start_tagging)

        self.btn_cancel_tagging = QPushButton("⏹ Cancel")
        self.btn_cancel_tagging.setEnabled(False)
        self.btn_cancel_tagging.clicked.connect(self._on_cancel_tagging_clicked)
        toolbar.addWidget(self.btn_cancel_tagging)

        self.btn_cleanup = QPushButton("🧹 Clean Up")
        self.btn_cleanup.setStyleSheet("font-weight: 500;")
        self.btn_cleanup.clicked.connect(self._on_cleanup_clicked)
        toolbar.addWidget(self.btn_cleanup)

        self.btn_cleanup_info = QToolButton()
        self.btn_cleanup_info.setText("ⓘ")
        cleanup_tooltip = (
            "Batch remove embedded lyric tags and external lyric files "
            "(.lrc, .elrc, .ttml) from selected tracks or the entire library."
        )
        self.btn_cleanup_info.setToolTip(cleanup_tooltip)
        self.btn_cleanup_info.setStyleSheet("""
            QToolButton {
                border: 1px solid #555;
                border-radius: 9px;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                font-size: 11px;
                font-weight: bold;
                color: #aaa;
                background: transparent;
            }
            QToolButton:hover {
                border-color: #ef4444;
                color: #ef4444;
                background: rgba(239, 68, 68, 0.1);
            }
        """)
        self.btn_cleanup_info.clicked.connect(
            lambda: QToolTip.showText(QCursor.pos(), cleanup_tooltip, self.btn_cleanup_info)
        )
        toolbar.addWidget(self.btn_cleanup_info)

        # ── Central Splitter ──
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setHandleWidth(4)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        # Left: Hierarchical Tree Widget
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 4, 4, 4)

        self.tree = QTreeWidget()
        self._setup_tree()

        # Compatibility wrapper for existing tests
        self.table = _TableTreeCompat(self.tree, self)

        left_layout.addWidget(self.tree)
        self.splitter.addWidget(left_widget)

        # Right: Inspector Tabs
        self.tabs = QTabWidget()
        self.preview_widget = KaraokePreviewWidget()
        self.editor_widget = LyricsEditorWidget()

        # Connect editor text/offset changes to live preview
        self.editor_widget.lyrics_changed.connect(self._on_editor_lyrics_changed)

        # Connect transport signals
        self.preview_widget.previous_requested.connect(self._on_previous_track)
        self.preview_widget.next_requested.connect(self._on_next_track)
        self.preview_widget.track_finished.connect(self._on_next_track)

        self.tabs.addTab(self.preview_widget, "Live Preview")
        self.tabs.addTab(self.editor_widget, "Plain Editor")
        self.splitter.addWidget(self.tabs)

        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self.splitter)

        # ── Status Bar ──
        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setVisible(False)
        status_bar.addPermanentWidget(self.progress_bar)

        status_bar.showMessage("Ready. Click 'Scan Folder' or drop audio files/folders.")

    def set_theme(self, theme_name: str) -> None:
        qss = THEMES.get(theme_name, THEMES["Dark"])
        self.setStyleSheet(qss)

    def _expand_all(self) -> None:
        self.tree.expandAll()

    def _collapse_all(self) -> None:
        self.tree.collapseAll()

    def _setup_tree(self) -> None:
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Title", "Artist", "Duration", "Sync State", "Status"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        widths = getattr(self.config, "column_widths", [200, 140, 70, 80, 150])
        if not isinstance(widths, (list, tuple)) or len(widths) != 5:
            widths = [200, 140, 70, 80, 150]

        for col_idx, width in enumerate(widths):
            if width > 20:
                header.resizeSection(col_idx, width)

        header.sectionResized.connect(self._on_header_section_resized)

        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_item_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.setAcceptDrops(True)

    def _restore_layout_state(self) -> None:
        # Splitter restoration
        splitter_restored = False
        if getattr(self.config, "splitter_state", ""):
            try:
                splitter_restored = self.splitter.restoreState(bytes.fromhex(self.config.splitter_state))
            except Exception:
                splitter_restored = False
        if not splitter_restored:
            self.splitter.setSizes([550, 450])

        # Header restoration
        header = self.tree.header()
        header_restored = False
        if getattr(self.config, "header_state", ""):
            try:
                header_restored = header.restoreState(bytes.fromhex(self.config.header_state))
            except Exception:
                header_restored = False
        if not header_restored:
            widths = getattr(self.config, "column_widths", [200, 140, 70, 80, 150])
            if not isinstance(widths, (list, tuple)) or len(widths) != 5:
                widths = [200, 140, 70, 80, 150]
            for col_idx, width in enumerate(widths):
                if width > 20:
                    header.resizeSection(col_idx, width)

        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    def _on_header_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        header = self.tree.header()
        self.config.header_state = bytes(header.saveState()).hex()
        self.config.column_widths = [header.sectionSize(i) for i in range(header.count())]
        self.settings_manager.save(self.config)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        self.config.splitter_state = bytes(self.splitter.saveState()).hex()
        self.settings_manager.save(self.config)

    # ── Drag and Drop Ingestion ───────────────────────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return

        added_count = 0
        for url in urls:
            local_path = url.toLocalFile()
            if not local_path:
                continue

            if os.path.isdir(local_path):
                for dirpath, _, filenames in os.walk(local_path):
                    for fname in sorted(filenames):
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in _AUDIO_EXTS:
                            self._ingest_audio_file(os.path.join(dirpath, fname))
                            added_count += 1
            elif os.path.isfile(local_path):
                ext = os.path.splitext(local_path)[1].lower()
                if ext in _AUDIO_EXTS:
                    self._ingest_audio_file(local_path)
                    added_count += 1

        self.statusBar().showMessage(f"Ingested {added_count} track(s) via drag-and-drop.")
        self._apply_missing_filter()

    def _ingest_audio_file(self, full_path: str) -> None:
        fname = os.path.basename(full_path)
        meta = read_metadata(full_path)
        sync_state = detect_sync_state(full_path)

        track = TrackItem(
            file_path=full_path,
            title=meta["title"] or pathlib.Path(fname).stem,
            artist=meta["artist"] or "Unknown Artist",
            duration_ms=meta["duration_ms"],
            sync_state=sync_state,
            matched_lyrics=None,
            album=meta["album"] or "Unknown Album",
            album_artist=meta["album_artist"],
        )
        self._on_track_found(track)

    def _on_scan_folder_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Music Directory", os.path.expanduser("~"))
        if not folder:
            return

        self._tracks.clear()
        self._album_nodes.clear()
        self.tree.clear()
        self._current_active_item = None
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

        # Determine album artist and album
        album_artist = track.album_artist.strip() if track.album_artist.strip() else (track.artist.strip() or "Unknown Artist")
        album = track.album.strip() or "Unknown Album"
        album_key = (album_artist, album)

        # Find or create top-level Album Node
        self._blocking_checks = True
        try:
            if album_key not in self._album_nodes:
                album_item = QTreeWidgetItem(self.tree)
                album_item.setFlags(album_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                album_item.setCheckState(0, Qt.CheckState.Checked)
                font = album_item.font(0)
                font.setBold(True)
                album_item.setFont(0, font)
                album_item.setExpanded(True)
                self._album_nodes[album_key] = album_item
            else:
                album_item = self._album_nodes[album_key]

            # Create Child Track Row
            child_item = QTreeWidgetItem(album_item)
            child_item.setFlags(child_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child_item.setCheckState(0, Qt.CheckState.Checked)
            child_item.setText(0, track.title)
            child_item.setText(1, track.artist)

            sec = (track.duration_ms // 1000) % 60
            mins = track.duration_ms // 60000
            dur_str = f"{mins:02d}:{sec:02d}" if track.duration_ms > 0 else "--:--"
            child_item.setText(2, dur_str)
            child_item.setText(3, track.sync_state.name)
            child_item.setText(4, "Ready")

            child_item.setData(0, Qt.ItemDataRole.UserRole, track)

            # Update Album Node Text with count
            count = album_item.childCount()
            album_item.setText(0, f"{album_artist} - {album} ({count} tracks)")

            self._apply_item_styling(child_item, track)
        finally:
            self._blocking_checks = False

    def _on_scan_finished(self, total: int) -> None:
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"Scan complete. {total} audio file(s) loaded.")
        self._apply_missing_filter()

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self.tree.signalsBlocked() or getattr(self, "_blocking_checks", False):
            return
        if column != 0:
            return

        self._blocking_checks = True
        try:
            # Top-level Album Node: propagate check state to all children
            if item.parent() is None:
                state = item.checkState(0)
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, state)
            else:
                # Child Track Row: update parent album check state
                parent = item.parent()
                if parent:
                    total = parent.childCount()
                    checked = sum(1 for i in range(total) if parent.child(i).checkState(0) == Qt.CheckState.Checked)
                    if checked == total:
                        parent.setCheckState(0, Qt.CheckState.Checked)
                    elif checked == 0:
                        parent.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        finally:
            self._blocking_checks = False

    @property
    def chk_missing_only(self) -> Any:
        class _CompatCheck:
            def __init__(self, combo: QComboBox):
                self._combo = combo

            def isChecked(self) -> bool:
                return self._combo.currentText() == "Select Missing Word Sync Only"

            def setChecked(self, val: bool) -> None:
                if val:
                    self._combo.setCurrentText("Select Missing Word Sync Only")
                else:
                    self._combo.setCurrentText("Select All")

        return _CompatCheck(self.selection_combo)

    def _apply_missing_filter(self) -> None:
        self._apply_selection_filter()

    def _apply_selection_filter(self, mode: Optional[Union[str, int]] = None) -> None:
        if not hasattr(self, "selection_combo"):
            return
        if self.tree.topLevelItemCount() == 0:
            return
        if not isinstance(mode, str):
            mode = self.selection_combo.currentText()

        self.tree.blockSignals(True)
        try:
            for i in range(self.tree.topLevelItemCount()):
                album_item = self.tree.topLevelItem(i)
                child_count = album_item.childCount()
                checked_count = 0
                for j in range(child_count):
                    child = album_item.child(j)
                    track: Optional[TrackItem] = child.data(0, Qt.ItemDataRole.UserRole)
                    if not track:
                        continue

                    is_word = (
                        track.sync_state == SyncState.WORD
                        or track.sync_state == "WORD"
                        or getattr(track.sync_state, "name", "") == "WORD"
                    )

                    if mode == "Select Missing Word Sync Only":
                        should_check = not is_word
                    elif mode == "Select Word Synced Only":
                        should_check = is_word
                    elif mode == "Select All":
                        should_check = True
                    elif mode == "Deselect All":
                        should_check = False
                    else:
                        should_check = not is_word

                    if should_check:
                        child.setCheckState(0, Qt.CheckState.Checked)
                        self._dim_item(child, False)
                        self._apply_item_styling(child, track)
                        checked_count += 1
                    else:
                        child.setCheckState(0, Qt.CheckState.Unchecked)
                        self._dim_item(child, True)

                if child_count > 0:
                    if checked_count == child_count:
                        album_item.setCheckState(0, Qt.CheckState.Checked)
                    elif checked_count == 0:
                        album_item.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        album_item.setCheckState(0, Qt.CheckState.PartiallyChecked)
        finally:
            self.tree.blockSignals(False)

    def _dim_item(self, item: QTreeWidgetItem, dimmed: bool) -> None:
        brush = QColor(100, 100, 100) if dimmed else QColor(220, 220, 220)
        for col in range(5):
            item.setForeground(col, brush)

    def _apply_item_styling(self, item: QTreeWidgetItem, track: TrackItem) -> None:
        if track.sync_state == SyncState.WORD:
            item.setForeground(3, QColor(0, 200, 100))
        elif track.sync_state == SyncState.LINE:
            item.setForeground(3, QColor(230, 180, 0))
        else:
            item.setForeground(3, QColor(160, 160, 160))

    def _get_all_track_children(self) -> List[QTreeWidgetItem]:
        """Return all leaf track items across all albums in display order."""
        tracks = []
        for i in range(self.tree.topLevelItemCount()):
            album = self.tree.topLevelItem(i)
            for j in range(album.childCount()):
                tracks.append(album.child(j))
        return tracks

    def _on_item_selection_changed(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            return

        item = selected[0]
        if item.parent() is None:
            # Clicked album node: don't load into preview
            return

        if item == self._current_active_item:
            return
        self._current_active_item = item

        track: Optional[TrackItem] = item.data(0, Qt.ItemDataRole.UserRole)
        if not track:
            return

        if track.matched_lyrics is None:
            track.matched_lyrics = load_lyrics_for_file(track.file_path)

        if track.matched_lyrics is not None:
            if getattr(track.matched_lyrics, "is_word_synced", False):
                track.sync_state = SyncState.WORD
            else:
                track.sync_state = SyncState.LINE
            item.setText(3, track.sync_state.name)
            self._apply_item_styling(item, track)

        self.preview_widget.load_track(track.file_path, track.matched_lyrics)
        self.editor_widget.set_lyrics(track.matched_lyrics)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if item.parent() is None:
            # Album node: toggle expand/collapse
            item.setExpanded(not item.isExpanded())
        else:
            # Track row: load and immediately start playback
            self._select_and_play_item(item)

    def _select_and_play_item(self, item: QTreeWidgetItem) -> None:
        self.tree.setCurrentItem(item)
        self._current_active_item = item
        track: Optional[TrackItem] = item.data(0, Qt.ItemDataRole.UserRole)
        if track:
            if track.matched_lyrics is None:
                track.matched_lyrics = load_lyrics_for_file(track.file_path)
            if track.matched_lyrics is not None:
                if getattr(track.matched_lyrics, "is_word_synced", False):
                    track.sync_state = SyncState.WORD
                else:
                    track.sync_state = SyncState.LINE
                item.setText(3, track.sync_state.name)
                self._apply_item_styling(item, track)
            self.preview_widget.load_track(track.file_path, track.matched_lyrics)
            self.editor_widget.set_lyrics(track.matched_lyrics)
            self.preview_widget.play()

    def _on_previous_track(self) -> None:
        children = self._get_all_track_children()
        if not children:
            return

        current_idx = -1
        if self._current_active_item in children:
            current_idx = children.index(self._current_active_item)

        prev_idx = max(0, current_idx - 1) if current_idx > 0 else 0
        self._select_and_play_item(children[prev_idx])

    def _on_next_track(self) -> None:
        children = self._get_all_track_children()
        if not children:
            return

        current_idx = -1
        if self._current_active_item in children:
            current_idx = children.index(self._current_active_item)

        if current_idx + 1 < len(children):
            self._select_and_play_item(children[current_idx + 1])

    def _on_editor_lyrics_changed(self, updated_lyrics: SongLyrics) -> None:
        if self._current_active_item:
            track: Optional[TrackItem] = self._current_active_item.data(0, Qt.ItemDataRole.UserRole)
            if track:
                track.matched_lyrics = updated_lyrics
                self.preview_widget.set_lyrics(updated_lyrics, track.duration_ms)

    def _execute_lyrics_deletion(
        self,
        target_items: List[QTreeWidgetItem],
        del_emb: bool,
        del_lrc: bool,
        del_elrc: bool,
        del_ttml: bool,
    ) -> None:
        count = len(target_items)
        for item in target_items:
            track: Optional[TrackItem] = item.data(0, Qt.ItemDataRole.UserRole)
            if not track:
                continue
            strip_lyrics(
                track.file_path,
                delete_embedded=del_emb,
                delete_lrc=del_lrc,
                delete_elrc=del_elrc,
                delete_ttml=del_ttml,
            )
            track.sync_state = detect_sync_state(track.file_path)
            if track.sync_state == SyncState.NONE:
                track.matched_lyrics = None
                item.setText(3, SyncState.NONE.name)
                item.setText(4, "Lyrics Deleted")
            else:
                track.matched_lyrics = read_track_lyrics(track.file_path)
                item.setText(3, track.sync_state.name)
                item.setText(4, "Ready")
            self._apply_item_styling(item, track)

        if self._current_active_item in target_items:
            act_track: Optional[TrackItem] = self._current_active_item.data(0, Qt.ItemDataRole.UserRole)
            lyrics = act_track.matched_lyrics if act_track else None
            dur = act_track.duration_ms if act_track else 0
            self.preview_widget.set_lyrics(lyrics, dur)
            self.editor_widget.set_lyrics(lyrics)

        self.statusBar().showMessage(f"Stripped selected lyrics from {count} file(s).")
        self._apply_selection_filter()

    def _on_cleanup_clicked(self) -> None:
        all_items: List[QTreeWidgetItem] = []
        checked_items: List[QTreeWidgetItem] = []
        for i in range(self.tree.topLevelItemCount()):
            album_node = self.tree.topLevelItem(i)
            for j in range(album_node.childCount()):
                child = album_node.child(j)
                all_items.append(child)
                if child.checkState(0) == Qt.CheckState.Checked:
                    checked_items.append(child)

        total_count = len(all_items)
        if total_count == 0:
            QMessageBox.information(self, "Clean Up", "No tracks loaded in the queue.")
            return

        checked_count = len(checked_items)

        dlg = DeleteLyricsDialog(
            parent=self,
            track_count=checked_count if checked_count > 0 else total_count,
            show_scope=True,
            checked_count=checked_count,
            total_count=total_count,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        target_items = checked_items if dlg.scope == "selected" else all_items
        count = len(target_items)
        if count == 0:
            QMessageBox.information(self, "Clean Up", "No tracks selected to clean up.")
            return

        reply = QMessageBox.warning(
            self,
            "Confirm Deletion",
            f"Are you sure you want to permanently delete the selected lyric format(s) from {count} track(s)? This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._execute_lyrics_deletion(
            target_items,
            del_emb=dlg.delete_embedded,
            del_lrc=dlg.delete_lrc,
            del_elrc=dlg.delete_elrc,
            del_ttml=dlg.delete_ttml,
        )

    def _show_context_menu(self, pos) -> None:
        selected_items = [it for it in self.tree.selectedItems() if it.parent() is not None]
        if not selected_items:
            return

        menu = QMenu(self)
        action_strip = menu.addAction("Delete existing lyrics for selected file(s)")
        action_manual = menu.addAction("Search Manually...")

        action = menu.exec(self.tree.viewport().mapToGlobal(pos))

        if action == action_strip:
            count = len(selected_items)
            dlg = DeleteLyricsDialog(parent=self, track_count=count)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._execute_lyrics_deletion(
                    selected_items,
                    del_emb=dlg.delete_embedded,
                    del_lrc=dlg.delete_lrc,
                    del_elrc=dlg.delete_elrc,
                    del_ttml=dlg.delete_ttml,
                )

        elif action == action_manual:
            target_item = selected_items[0]
            track: TrackItem = target_item.data(0, Qt.ItemDataRole.UserRole)
            dlg = ManualSearchDialog(
                title=track.title,
                artist=track.artist,
                duration_ms=track.duration_ms,
                config=self.config,
                parent=self,
                provider=self._get_active_provider(),
                provider_name=self.cmb_provider.currentText(),
                album_artist=getattr(track, "album_artist", ""),
            )
            if dlg.exec() == ManualSearchDialog.DialogCode.Accepted:
                lyrics = dlg.get_selected_lyrics()
                if lyrics:
                    apply_lyrics(track.file_path, lyrics, config=self.config)
                    track.sync_state = detect_sync_state(track.file_path)
                    track.matched_lyrics = lyrics
                    target_item.setText(3, track.sync_state.name)
                    target_item.setText(4, "Tagged (WORD)" if track.sync_state == SyncState.WORD else "Tagged")
                    self._apply_item_styling(target_item, track)
                    if target_item == self._current_active_item:
                        self.preview_widget.set_lyrics(lyrics, track.duration_ms)
                        self.editor_widget.set_lyrics(lyrics)
                    self.statusBar().showMessage(f"Applied manual lyrics to '{track.title}'.")

    def _on_provider_changed(self, text: str) -> None:
        self.current_settings.active_provider = text
        self.settings_manager.save_settings(self.current_settings)

    def _on_theme_changed(self, text: str) -> None:
        self.set_theme(text)
        self.current_settings.theme = text
        self.settings_manager.save_settings(self.current_settings)

    def _get_active_provider(self):
        text = self.cmb_provider.currentText()
        if text == "Auto":
            return AutoLyricsProvider()
        elif text == "lrc.red":
            return LrcRedProvider()
        elif text == "Unison":
            return UnisonProvider()
        return KugouProvider()

    def _on_settings_clicked(self) -> None:
        dlg = TaggingSettingsDialog(self.current_settings, self)
        dlg.config_saved.connect(self._on_config_saved)
        dlg.exec()

    def _on_config_saved(self, new_config: TaggingConfig) -> None:
        self.current_settings = new_config
        self.config = new_config
        self.settings_manager.save_settings(new_config)
        self.statusBar().showMessage("Settings saved.")
        if self.tree.topLevelItemCount() > 0:
            self._apply_selection_filter()

    def _on_start_tagging_clicked(self) -> None:
        selected_pairs: List[Tuple[QTreeWidgetItem, TrackItem]] = []
        for i in range(self.tree.topLevelItemCount()):
            album_node = self.tree.topLevelItem(i)
            for j in range(album_node.childCount()):
                child = album_node.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    track: Optional[TrackItem] = child.data(0, Qt.ItemDataRole.UserRole)
                    if track:
                        selected_pairs.append((child, track))

        if not selected_pairs:
            QMessageBox.information(self, "No Tracks Selected", "No tracks checked for tagging.")
            return

        self.btn_start_tagging.setEnabled(False)
        self.btn_cancel_tagging.setEnabled(True)
        if hasattr(self, "btn_cleanup") and self.btn_cleanup:
            self.btn_cleanup.setEnabled(False)

        # Stop active playback and detach file handles from preview widget
        if hasattr(self, "preview_widget") and self.preview_widget:
            self.preview_widget.stop()
            if hasattr(self.preview_widget, "_player"):
                self.preview_widget._player.setSource(QUrl())

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(selected_pairs))
        self.progress_bar.setValue(0)
        self.statusBar().showMessage(f"Starting batch tagging for {len(selected_pairs)} track(s)...")

        # Disable tree sorting before launching BatchTaggerWorker
        self._tree_was_sorting = self.tree.isSortingEnabled()
        self.tree.setSortingEnabled(False)

        selected_tracks = [t for _, t in selected_pairs]
        self.tagger_thread = BatchTaggerWorker(selected_tracks, self.config, self._get_active_provider(), self)

        def _on_track_progress(idx: int, status_text: str, track: TrackItem) -> None:
            if idx < len(selected_pairs):
                child_item, _ = selected_pairs[idx]
                child_item.setText(4, status_text)
                child_item.setText(3, track.sync_state.name)
                self._apply_item_styling(child_item, track)
                if child_item == self._current_active_item:
                    self.preview_widget.set_lyrics(track.matched_lyrics, track.duration_ms)
                    self.editor_widget.set_lyrics(track.matched_lyrics)

        self.tagger_thread.track_progress.connect(_on_track_progress)
        self.tagger_thread.overall_progress.connect(self._on_overall_progress)
        self.tagger_thread.batch_finished.connect(self._on_batch_finished)
        self.tagger_thread.start()

    def _on_overall_progress(self, current: int, total: int) -> None:
        self.progress_bar.setValue(current)
        if current > 0 and current % 200 == 0:
            import gc
            gc.collect()

    def _on_cancel_tagging_clicked(self) -> None:
        if self.tagger_thread and self.tagger_thread.isRunning():
            self.tagger_thread.cancel()
            self.btn_cancel_tagging.setEnabled(False)
            self.btn_start_tagging.setEnabled(True)
            if hasattr(self, "btn_cleanup") and self.btn_cleanup:
                self.btn_cleanup.setEnabled(True)
            self.tree.setSortingEnabled(getattr(self, "_tree_was_sorting", False))
            self.statusBar().showMessage("Tagging cancelled.")

    def _on_batch_finished(self) -> None:
        self.tree.setSortingEnabled(getattr(self, "_tree_was_sorting", False))
        self.btn_start_tagging.setEnabled(True)
        self.btn_cancel_tagging.setEnabled(False)
        if hasattr(self, "btn_cleanup") and self.btn_cleanup:
            self.btn_cleanup.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage("Batch tagging complete.")
        import gc
        gc.collect()
        self._apply_missing_filter()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Window-level keyboard shortcuts for media controls and tree management."""
        focus_w = QApplication.focusWidget()
        if focus_w and isinstance(focus_w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            super().keyPressEvent(event)
            return

        modifiers = event.modifiers()
        key = event.key()

        # Ctrl+Shift shortcuts
        if modifiers == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            if key == Qt.Key.Key_E:
                self._expand_all()
                event.accept()
                return
            elif key == Qt.Key.Key_C:
                self._collapse_all()
                event.accept()
                return

        # Ctrl shortcuts
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Left:
                self._on_previous_track()
                event.accept()
                return
            elif key == Qt.Key.Key_Right:
                self._on_next_track()
                event.accept()
                return
            elif key == Qt.Key.Key_Period or key == Qt.Key.Key_W:
                self.preview_widget.stop()
                event.accept()
                return

        # Plain shortcuts
        if modifiers == Qt.KeyboardModifier.NoModifier:
            if key == Qt.Key.Key_Space:
                self.preview_widget.toggle_play()
                event.accept()
                return
            elif key == Qt.Key.Key_Left:
                self.preview_widget.seek(self.preview_widget._current_ms - 5000)
                event.accept()
                return
            elif key == Qt.Key.Key_Right:
                self.preview_widget.seek(self.preview_widget._current_ms + 5000)
                event.accept()
                return

        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        try:
            if hasattr(self, "splitter") and self.splitter:
                self.config.splitter_state = bytes(self.splitter.saveState()).hex()
            if hasattr(self, "tree") and self.tree.header():
                header = self.tree.header()
                self.config.header_state = bytes(header.saveState()).hex()
                self.config.column_widths = [header.sectionSize(i) for i in range(header.count())]
            self.settings_manager.save(self.config)
        except Exception:
            pass
        if hasattr(self, "preview_widget") and self.preview_widget:
            try:
                if hasattr(self.preview_widget, "_player"):
                    self.preview_widget._player.stop()
                if hasattr(self.preview_widget, "_timer"):
                    self.preview_widget._timer.stop()
            except Exception:
                pass
        if hasattr(self, "editor_widget") and self.editor_widget:
            try:
                if hasattr(self.editor_widget, "_debounce_timer"):
                    self.editor_widget._debounce_timer.stop()
            except Exception:
                pass
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.terminate()
        if self.tagger_thread and self.tagger_thread.isRunning():
            self.tagger_thread.cancel()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
