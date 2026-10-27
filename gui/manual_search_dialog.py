"""Manual lyrics search modal allowing users to query, preview, and apply lyrics asynchronously."""

import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
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
from core.providers.lrc_red import (
    _SEARCH_URL as _LRC_RED_SEARCH_URL,
    _http_get as _lrc_red_http_get,
)
from core.providers.unison import (
    _UNISON_BASE_URL,
    _UNISON_SEARCH_URL,
    _http_get as _unison_http_get,
)
from core.tagger import _parse_lrc_text
from core.ttml_parser import parse_ttml


class ManualSearchWorker(QThread):
    """Background thread to query lyrics providers asynchronously."""

    # Emits (candidates: List[Dict[str, Any]], fallback_query: str, error_message: str)
    search_finished = Signal(list, str, str)

    def __init__(
        self,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        provider_name: str = "Auto",
        album_artist: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.title = title.strip()
        self.artist = artist.strip()
        self.duration_ms = duration_ms
        self.provider_name = provider_name.strip()
        self.album_artist = album_artist.strip()

    def run(self) -> None:
        if not self.title:
            self.search_finished.emit([], "", "")
            return

        candidates: List[Dict[str, Any]] = []
        fallback_query = ""
        error_message = ""

        def _search_kugou() -> Tuple[List[Dict[str, Any]], str, str]:
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
                    return [], "", "Unable to reach lyrics server"
            except Exception as exc:
                return [], "", str(exc)

            cands = data.get("candidates", []) if isinstance(data, dict) else []
            fb_q = ""
            if not cands:
                sanitized_title = sanitize_search_query(self.title)
                if sanitized_title:
                    fb_params = {
                        "ver": "1",
                        "man": "yes",
                        "client": "pc",
                        "keyword": sanitized_title,
                        "duration": str(self.duration_ms) if self.duration_ms > 0 else "0",
                    }
                    fb_url = f"{_SEARCH_URL}?{urllib.parse.urlencode(fb_params)}"
                    try:
                        fb_data = _make_request(fb_url)
                        if fb_data and isinstance(fb_data, dict):
                            cands = fb_data.get("candidates", [])
                            if cands:
                                fb_q = sanitized_title
                    except Exception:
                        pass

            formatted: List[Dict[str, Any]] = []
            for c in cands:
                dur_raw = c.get("duration", 0)
                try:
                    dur_ms = int(dur_raw * 1000) if int(dur_raw) < 10000 else int(dur_raw)
                except Exception:
                    dur_ms = 0
                krctype = c.get("krctype", 1)
                sync_type = "WORD" if krctype == 2 else "LINE"
                formatted.append({
                    "id": c.get("id", ""),
                    "accesskey": c.get("accesskey", ""),
                    "song": c.get("song", ""),
                    "singer": c.get("singer", ""),
                    "duration_ms": dur_ms,
                    "provider": "Kugou",
                    "sync_type": sync_type,
                    "krctype": krctype,
                    "raw": c,
                })
            return formatted, fb_q, ""

        def _search_unison() -> Tuple[List[Dict[str, Any]], str, str]:
            queries = []
            if self.artist and self.title:
                queries.append(f"{self.artist} {self.title}".strip())
            if self.album_artist and self.album_artist.lower() != self.artist.lower():
                queries.append(f"{self.artist} & {self.album_artist} {self.title}".strip())
            if self.title and self.title not in queries:
                queries.append(self.title)

            hits: List[Dict[str, Any]] = []
            err_msg = ""
            for q in queries:
                try:
                    params = {"q": q, "limit": "20"}
                    resp = _unison_http_get(_UNISON_SEARCH_URL, params=params, timeout=(4, 8))
                    if resp and resp.status_code == 200:
                        data = resp.json()
                        raw_hits = (
                            data.get("data")
                            if isinstance(data, dict) and "data" in data
                            else (data if isinstance(data, list) else (data.get("results", []) if isinstance(data, dict) else []))
                        )
                        if isinstance(raw_hits, list) and raw_hits:
                            hits = raw_hits
                            break
                except Exception as exc:
                    err_msg = str(exc)

            formatted: List[Dict[str, Any]] = []
            for h in hits:
                if not isinstance(h, dict) or h.get("hidden"):
                    continue
                hit_id = h.get("id")
                if not hit_id:
                    continue
                song = h.get("song") or h.get("title") or h.get("track_name") or h.get("trackName") or ""
                artist = h.get("artist") or h.get("artist_name") or h.get("artistName") or ""
                dur_sec = float(h.get("duration", 0) or 0)
                dur_ms = int(round(dur_sec * 1000)) if dur_sec > 0 else self.duration_ms
                hit_sync = str(h.get("syncType", "")).strip().lower()
                sync_type = "WORD" if hit_sync == "richsync" else ("LINE" if hit_sync == "linesync" else "WORD")
                formatted.append({
                    "id": hit_id,
                    "song": song,
                    "singer": artist,
                    "duration_ms": dur_ms,
                    "provider": "Unison",
                    "sync_type": sync_type,
                    "raw": h,
                })
            return formatted, "", err_msg if not formatted else ""

        def _search_lrc_red() -> Tuple[List[Dict[str, Any]], str, str]:
            queries = []
            if self.title and self.artist:
                queries.append(f"{self.title} {self.artist}".strip())
                queries.append(f"{self.artist} {self.title}".strip())
            elif self.title:
                queries.append(self.title)

            items: List[Dict[str, Any]] = []
            err_msg = ""
            for q in queries:
                try:
                    resp = _lrc_red_http_get(_LRC_RED_SEARCH_URL, params={"q": q}, timeout=4.0)
                    if resp and resp.status_code == 200:
                        data = resp.json()
                        raw_items = data if isinstance(data, list) else (data.get("results", []) if isinstance(data, dict) else [])
                        if isinstance(raw_items, list) and raw_items:
                            items = raw_items
                            break
                except Exception as exc:
                    err_msg = str(exc)

            formatted: List[Dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                song = str(item.get("trackName") or item.get("title") or "")
                artist = str(item.get("artistName") or item.get("artist") or "")
                dur_sec = float(item.get("duration", 0) or 0)
                dur_ms = int(round(dur_sec * 1000)) if dur_sec > 0 else self.duration_ms
                synced = item.get("syncedLyrics") or item.get("lyrics") or ""
                is_word = ("<" in synced and ">" in synced)
                sync_type = "WORD" if is_word else ("LINE" if synced else "LINE")
                formatted.append({
                    "id": item.get("id") or item.get("isrc") or "",
                    "song": song,
                    "singer": artist,
                    "duration_ms": dur_ms,
                    "provider": "lrc.red",
                    "sync_type": sync_type,
                    "syncedLyrics": synced,
                    "isrc": item.get("isrc"),
                    "raw": item,
                })
            return formatted, "", err_msg if not formatted else ""

        prov = self.provider_name.lower()
        if prov == "kugou":
            candidates, fallback_query, error_message = _search_kugou()
        elif prov == "unison":
            candidates, fallback_query, error_message = _search_unison()
        elif prov in ("lrc.red", "lrcred", "lrc_red"):
            candidates, fallback_query, error_message = _search_lrc_red()
        else:  # "auto"
            u_cands, _, u_err = _search_unison()
            candidates.extend(u_cands)

            l_cands, _, l_err = _search_lrc_red()
            candidates.extend(l_cands)

            k_cands, k_fb, k_err = _search_kugou()
            candidates.extend(k_cands)
            if k_fb and not fallback_query:
                fallback_query = k_fb

            if not candidates:
                error_message = u_err or l_err or k_err

        self.search_finished.emit(candidates, fallback_query, error_message)


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
        provider: Optional[Any] = None,
        album_artist: str = "",
        provider_name: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manual Lyrics Search")
        self.setMinimumSize(720, 480)
        self.resize(760, 520)
        self._duration_ms = duration_ms
        self._config = config
        self._provider = provider_name or provider
        self._album_artist = album_artist
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

        search_bar.addWidget(QLabel("Provider:"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["Auto", "Unison", "lrc.red", "Kugou"])

        default_prov = "Auto"
        if isinstance(self._provider, str) and self._provider:
            default_prov = self._provider
        elif hasattr(self._provider, "name") and isinstance(self._provider.name, str) and self._provider.name:
            default_prov = self._provider.name
        elif self._config and hasattr(self._config, "active_provider") and self._config.active_provider:
            default_prov = self._config.active_provider

        for idx in range(self.provider_combo.count()):
            if self.provider_combo.itemText(idx).lower() == default_prov.lower():
                self.provider_combo.setCurrentIndex(idx)
                break

        self.provider_combo.currentIndexChanged.connect(
            lambda _: self._on_search_clicked() if self.txt_title.text().strip() else None
        )
        search_bar.addWidget(self.provider_combo)

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
        self.table_results.setColumnCount(5)
        self.table_results.setHorizontalHeaderLabels(["Title", "Artist", "Duration", "Provider Source", "Sync Type"])
        self.table_results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_results.verticalHeader().setVisible(False)
        self.table_results.horizontalHeader().setStretchLastSection(True)
        self.table_results.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table_results.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table_results.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table_results.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
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

        prov = self.provider_combo.currentText()
        self.btn_search.setEnabled(False)
        self.lbl_status.setText(f"🔍 Searching {prov}... please wait")
        self.table_results.setRowCount(0)
        self._candidates = []
        self.btn_apply.setEnabled(False)

        self._worker = ManualSearchWorker(
            title=q_title,
            artist=q_artist,
            duration_ms=self._duration_ms,
            provider_name=prov,
            album_artist=self._album_artist,
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

        if error_message and not candidates:
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

            # 0: Title
            t_item = QTableWidgetItem(str(cand.get("song", "")))
            self.table_results.setItem(row, 0, t_item)

            # 1: Artist
            a_item = QTableWidgetItem(str(cand.get("singer", "")))
            self.table_results.setItem(row, 1, a_item)

            # 2: Duration
            dur_ms = cand.get("duration_ms", 0)
            sec = (dur_ms // 1000) % 60
            mins = dur_ms // 60000
            dur_str = f"{mins:02d}:{sec:02d}" if dur_ms > 0 else "--:--"
            d_item = QTableWidgetItem(dur_str)
            self.table_results.setItem(row, 2, d_item)

            # 3: Provider Source
            prov_str = str(cand.get("provider", "Kugou"))
            p_item = QTableWidgetItem(prov_str)
            self.table_results.setItem(row, 3, p_item)

            # 4: Sync Type
            sync_str = str(cand.get("sync_type", "WORD"))
            s_item = QTableWidgetItem(sync_str)
            self.table_results.setItem(row, 4, s_item)

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
        provider = cand.get("provider", "Kugou")

        parsed: Optional[SongLyrics] = None
        error_msg = ""

        if provider == "Kugou":
            cand_id = cand.get("id", "")
            accesskey = cand.get("accesskey", "")
            krctype = cand.get("krctype", 1)
            fmt = "krc" if krctype == 2 else "lrc"

            params = {
                "ver": "1",
                "client": "pc",
                "id": str(cand_id),
                "accesskey": str(accesskey),
                "fmt": fmt,
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
                if fmt == "krc":
                    krc_text = unpack_krc(content)
                    parsed = _parse_krc_text(krc_text)
                else:
                    parsed = _parse_lrc_text(content)
            except Exception as exc:
                error_msg = str(exc)

        elif provider == "Unison":
            hit_id = cand.get("id")
            if not hit_id:
                self.lbl_status.setText("❌ Invalid Unison candidate ID.")
                return
            try:
                entry_url = f"{_UNISON_BASE_URL}/lyrics/{hit_id}"
                entry_resp = _unison_http_get(entry_url, timeout=(5, 10))
                if entry_resp and entry_resp.status_code == 200:
                    entry_data = entry_resp.json()
                    inner = entry_data.get("data") if isinstance(entry_data, dict) and "data" in entry_data else entry_data
                    if isinstance(inner, dict):
                        raw_lyrics = inner.get("lyrics") or inner.get("syncedLyrics")
                        if raw_lyrics:
                            fmt = str(inner.get("format", "")).strip().lower()
                            if fmt == "ttml" or "<tt" in raw_lyrics:
                                parsed = parse_ttml(raw_lyrics)
                            else:
                                parsed = _parse_lrc_text(raw_lyrics)
            except Exception as exc:
                error_msg = str(exc)

        elif provider == "lrc.red":
            synced = cand.get("syncedLyrics") or cand.get("raw", {}).get("syncedLyrics") or cand.get("raw", {}).get("lyrics")
            if synced:
                parsed = _parse_lrc_text(synced)
            elif cand.get("isrc"):
                try:
                    lrc_url = f"https://api.lrc.red/api/lyrics?isrc={cand['isrc']}"
                    resp = _lrc_red_http_get(lrc_url, timeout=5.0)
                    if resp and resp.status_code == 200:
                        data = resp.json()
                        raw_lyrics = data.get("syncedLyrics") or data.get("lyrics")
                        if raw_lyrics:
                            parsed = _parse_lrc_text(raw_lyrics)
                except Exception as exc:
                    error_msg = str(exc)

        if parsed and parsed.lines:
            self.selected_lyrics = parsed
            self.lyrics_selected.emit(parsed)
            self.accept()
        else:
            if error_msg:
                self.lbl_status.setText(f"❌ Error fetching lyrics: {error_msg}")
            else:
                self.lbl_status.setText("⚠ Lyrics payload contains no timestamped lines.")

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
