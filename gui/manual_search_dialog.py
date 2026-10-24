"""Manual lyrics search modal allowing users to query, preview, and apply lyrics asynchronously."""

import urllib.parse
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.models import SongLyrics, TaggingConfig
from core.normalizer import sanitize_search_query
from core.providers.kugou import (
    _DOWNLOAD_URL,
    _SEARCH_URL,
    _make_request,
    _parse_krc_text,
    unpack_krc,
)


class ManualSearchWorker(QThread):
    """Background thread to query Kugou lyrics API asynchronously with smart auto-fallback."""

    # Emits (candidates: List[Dict[str, Any]], fallback_query: str, error_message: str)
    search_finished = Signal(list, str, str)

    def __init__(
        self,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.title = title.strip()
        self.artist = artist.strip()
        self.duration_ms = duration_ms

    def run(self) -> None:
        if not self.title:
            self.search_finished.emit([], "", "")
            return

        # 1. Primary Query: "Artist - Title" (or "Title" if artist is omitted)
        keyword = self.title if not self.artist else f"{self.artist} - {self.title}"
        params = {
            "ver": "1",
            "man": "yes",
            "client": "pc",
            "keyword": keyword,
            "duration": str(self.duration_ms) if self.duration_ms > 0 else "0",
        }
        url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"

        try:
            data = _make_request(url)
            if data is None:
                self.search_finished.emit([], "", "Unable to reach lyrics server")
                return
        except Exception as exc:
            self.search_finished.emit([], "", str(exc))
            return

        candidates = data.get("candidates", []) if isinstance(data, dict) else []
        if candidates:
            # Primary search succeeded
            self.search_finished.emit(candidates, "", "")
            return

        # 2. Smart Auto-Fallback:
        # If searching artist + title returns 0 candidates, automatically retry using
        # sanitize_search_query(title) and title-only search before giving up.
        sanitized_title = sanitize_search_query(self.title)
        if sanitized_title:
            fallback_params = {
                "ver": "1",
                "man": "yes",
                "client": "pc",
                "keyword": sanitized_title,
                "duration": str(self.duration_ms) if self.duration_ms > 0 else "0",
            }
            fallback_url = f"{_SEARCH_URL}?{urllib.parse.urlencode(fallback_params)}"
            try:
                fb_data = _make_request(fallback_url)
                if fb_data and isinstance(fb_data, dict):
                    fb_candidates = fb_data.get("candidates", [])
                    if fb_candidates:
                        self.search_finished.emit(fb_candidates, sanitized_title, "")
                        return
            except Exception:
                pass

        # If fallback also returns 0 candidates
        self.search_finished.emit([], "", "")


class ManualSearchDialog(QDialog):
    """Modal dialog for manually querying lyrics providers and downloading selected lyrics."""

    lyrics_selected = Signal(SongLyrics)

    def __init__(
        self,
        title: str = "",
        artist: str = "",
        duration_ms: int = 0,
        config: Optional[TaggingConfig] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manual Lyrics Search")
        self.setMinimumSize(660, 460)
        self.resize(700, 500)
        self._duration_ms = duration_ms
        self._config = config
        self._candidates: List[Dict[str, Any]] = []
        self._worker: Optional[ManualSearchWorker] = None
        self.selected_lyrics: Optional[SongLyrics] = None

        self._init_ui(title, artist)
        if title:
            self._on_search_clicked()

    def _init_ui(self, title: str, artist: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── Input Fields ──
        search_bar = QHBoxLayout()
        search_bar.setSpacing(8)

        search_bar.addWidget(QLabel("Title:"))
        self.txt_title = QLineEdit(title)
        self.txt_title.setPlaceholderText("Song title...")
        self.txt_title.returnPressed.connect(self._on_search_clicked)
        search_bar.addWidget(self.txt_title, stretch=2)

        search_bar.addWidget(QLabel("Artist:"))
        self.txt_artist = QLineEdit(artist)
        self.txt_artist.setPlaceholderText("Artist name (optional)...")
        self.txt_artist.returnPressed.connect(self._on_search_clicked)
        search_bar.addWidget(self.txt_artist, stretch=2)

        self.btn_search = QPushButton("🔍 Search")
        self.btn_search.setStyleSheet(
            "QPushButton { min-height: 26px; padding: 2px 14px; font-weight: 600; }"
        )
        self.btn_search.clicked.connect(self._on_search_clicked)
        search_bar.addWidget(self.btn_search)

        layout.addLayout(search_bar)

        # ── Results Table ──
        self.table_results = QTableWidget()
        self.table_results.setColumnCount(4)
        self.table_results.setHorizontalHeaderLabels(["Title", "Artist", "Duration", "Sync State"])
        self.table_results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_results.verticalHeader().setVisible(False)
        self.table_results.horizontalHeader().setStretchLastSection(True)
        self.table_results.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table_results.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table_results.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table_results.itemSelectionChanged.connect(self._on_selection_changed)
        self.table_results.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table_results, stretch=1)

        # ── Status Label ──
        self.lbl_status = QLabel("Enter Title and Artist, then click Search.")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("QLabel { color: #8b949e; font-size: 11px; padding: 2px 4px; }")
        layout.addWidget(self.lbl_status)

        # ── Bottom Action Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_apply = QPushButton("✔ Select & Apply")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setStyleSheet(
            "QPushButton { min-height: 28px; min-width: 110px; padding: 4px 14px; font-weight: 600; }"
        )
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        btn_layout.addWidget(self.btn_apply)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(
            "QPushButton { min-height: 28px; min-width: 80px; padding: 4px 14px; }"
        )
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)

    def _on_search_clicked(self) -> None:
        q_title = self.txt_title.text().strip()
        q_artist = self.txt_artist.text().strip()
        if not q_title:
            self.lbl_status.setText("⚠ Please enter a song title to search.")
            return

        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(500)

        self.btn_search.setEnabled(False)
        self.lbl_status.setText("🔍 Searching Kugou... please wait")
        self.table_results.setRowCount(0)
        self._candidates = []
        self.btn_apply.setEnabled(False)

        self._worker = ManualSearchWorker(
            title=q_title,
            artist=q_artist,
            duration_ms=self._duration_ms,
            parent=self,
        )
        self._worker.search_finished.connect(self._on_search_finished)
        self._worker.start()

    def _on_search_finished(
        self,
        candidates: List[Dict[str, Any]],
        fallback_query: str,
        error_message: str,
    ) -> None:
        self.btn_search.setEnabled(True)
        self._candidates = candidates
        self.table_results.setRowCount(0)
        self.btn_apply.setEnabled(False)

        if error_message:
            self.lbl_status.setText(
                f"❌ Connection error: {error_message}. Please check your connection."
            )
            return

        if not candidates:
            self.lbl_status.setText(
                "⚠ No candidates found. Try removing parentheses, featured artists, or clearing the Artist box."
            )
            return

        for row, cand in enumerate(candidates):
            self.table_results.insertRow(row)

            # Title
            t_item = QTableWidgetItem(str(cand.get("song", "")))
            self.table_results.setItem(row, 0, t_item)

            # Artist
            a_item = QTableWidgetItem(str(cand.get("singer", "")))
            self.table_results.setItem(row, 1, a_item)

            # Duration
            dur_raw = cand.get("duration", 0)
            try:
                dur_ms = int(dur_raw * 1000) if int(dur_raw) < 10000 else int(dur_raw)
            except Exception:
                dur_ms = 0
            sec = (dur_ms // 1000) % 60
            mins = dur_ms // 60000
            dur_str = f"{mins:02d}:{sec:02d}" if dur_ms > 0 else "--:--"
            d_item = QTableWidgetItem(dur_str)
            self.table_results.setItem(row, 2, d_item)

            # Sync State
            krctype = cand.get("krctype", 1)
            sync_str = "WORD" if krctype == 2 else "LINE"
            s_item = QTableWidgetItem(sync_str)
            self.table_results.setItem(row, 3, s_item)

        if fallback_query:
            self.lbl_status.setText(f"Showing results for fallback query: '{fallback_query}'")
        else:
            self.lbl_status.setText(
                f"✓ Found {len(candidates)} result(s). Select a track and click 'Select & Apply'."
            )

        if self.table_results.rowCount() > 0:
            self.table_results.selectRow(0)
            self.btn_apply.setEnabled(True)

    def _on_selection_changed(self) -> None:
        self.btn_apply.setEnabled(self.table_results.currentRow() >= 0)

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        self.table_results.selectRow(row)
        self._on_apply_clicked()

    def _on_apply_clicked(self) -> None:
        row = self.table_results.currentRow()
        if row < 0 or row >= len(self._candidates):
            return

        cand = self._candidates[row]
        cand_id = cand.get("id", "")
        accesskey = cand.get("accesskey", "")

        params = {
            "ver": "1",
            "client": "pc",
            "id": str(cand_id),
            "accesskey": str(accesskey),
            "fmt": "krc",
            "charset": "utf8",
        }
        url = f"{_DOWNLOAD_URL}?{urllib.parse.urlencode(params)}"
        dl_data = _make_request(url)
        if not dl_data:
            self.lbl_status.setText("❌ Connection error: Failed to download lyrics from server.")
            return

        content = dl_data.get("content", "")
        if not content:
            self.lbl_status.setText("❌ Download error: Received empty lyrics payload.")
            return

        try:
            krc_text = unpack_krc(content)
            parsed = _parse_krc_text(krc_text)
            if parsed and parsed.lines:
                self.selected_lyrics = parsed
                self.lyrics_selected.emit(parsed)
                self.accept()
            else:
                self.lbl_status.setText("⚠ Lyrics payload contains no timestamped lines.")
        except Exception as exc:
            self.lbl_status.setText(f"❌ Decryption error: {exc}")

    def wait_for_search(self, timeout_ms: int = 5000) -> bool:
        """Wait for the background search worker to finish and process pending UI events."""
        if self._worker and self._worker.isRunning():
            ret = self._worker.wait(timeout_ms)
            app = QApplication.instance()
            if app:
                app.processEvents()
            return ret
        return True

    def get_selected_lyrics(self) -> Optional[SongLyrics]:
        """Return the downloaded and parsed SongLyrics, or None."""
        return self.selected_lyrics

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(500)
        super().closeEvent(event)

    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(500)
        super().reject()
