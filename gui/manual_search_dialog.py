"""Manual lyrics search modal allowing users to query, preview, and apply lyrics."""

import urllib.parse
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from core.providers.kugou import (
    _DOWNLOAD_URL,
    _SEARCH_URL,
    _make_request,
    _parse_krc_text,
    unpack_krc,
)


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
        self.setMinimumSize(640, 440)
        self.resize(680, 480)
        self._duration_ms = duration_ms
        self._config = config
        self._candidates: List[Dict[str, Any]] = []
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
        self.btn_search.setStyleSheet("QPushButton { min-height: 26px; padding: 2px 14px; font-weight: 600; }")
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
        self.table_results.cellDoubleClicked.connect(lambda r, c: self._on_apply_clicked())
        layout.addWidget(self.table_results, stretch=1)

        # ── Bottom Action Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_apply = QPushButton("✔ Select & Apply")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setStyleSheet("QPushButton { min-height: 28px; min-width: 110px; padding: 4px 14px; font-weight: 600; }")
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        btn_layout.addWidget(self.btn_apply)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet("QPushButton { min-height: 28px; min-width: 80px; padding: 4px 14px; }")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)

    def _on_search_clicked(self) -> None:
        q_title = self.txt_title.text().strip()
        q_artist = self.txt_artist.text().strip()
        if not q_title:
            return

        keyword = q_title if not q_artist else f"{q_artist} - {q_title}"
        params = {
            "ver": "1",
            "man": "yes",
            "client": "pc",
            "keyword": keyword,
            "duration": str(self._duration_ms) if self._duration_ms > 0 else "0",
        }
        url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        data = _make_request(url)

        self.table_results.setRowCount(0)
        self._candidates = []
        self.btn_apply.setEnabled(False)

        if not data:
            return

        self._candidates = data.get("candidates", [])
        for row, cand in enumerate(self._candidates):
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

        if self.table_results.rowCount() > 0:
            self.table_results.selectRow(0)

    def _on_selection_changed(self) -> None:
        self.btn_apply.setEnabled(self.table_results.currentRow() >= 0)

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
            return

        content = dl_data.get("content", "")
        if not content:
            return

        try:
            krc_text = unpack_krc(content)
            parsed = _parse_krc_text(krc_text)
            if parsed and parsed.lines:
                self.selected_lyrics = parsed
                self.lyrics_selected.emit(parsed)
                self.accept()
        except Exception:
            pass

    def get_selected_lyrics(self) -> Optional[SongLyrics]:
        """Return the downloaded and parsed SongLyrics, or None."""
        return self.selected_lyrics
