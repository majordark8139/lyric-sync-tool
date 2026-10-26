"""Granular settings dialog mapping directly to TaggingConfig."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from typing import Optional

from core.config import Settings, SettingsManager
from core.models import TaggingConfig

_SUB_LABEL_STYLE = "font-size: 10px; color: #8b949e; margin-bottom: 6px;"


def _make_sub_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_SUB_LABEL_STYLE)
    lbl.setWordWrap(True)
    return lbl


class TaggingSettingsDialog(QDialog):
    """3-tab settings dialog for configuring file tagging, sidecars, and search options."""

    config_saved = Signal(TaggingConfig)

    def __init__(self, current_config: Optional[TaggingConfig] = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tagging & Synchronization Settings")
        self.setMinimumSize(540, 480)
        self.resize(540, 480)
        self._settings_mgr = SettingsManager()
        if current_config is not None:
            self._config = current_config
        else:
            self._config = self._settings_mgr.load_settings()
        self._init_ui()
        self._load_config(self._config)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 16)
        main_layout.setSpacing(10)

        # ── 3-Tab Widget Container ──
        self.tab_widget = QTabWidget(self)

        # =====================================================================
        # Tab 1: Sidecars & Cleanup
        # =====================================================================
        tab_sidecars = QWidget()
        sidecars_layout = QVBoxLayout(tab_sidecars)
        sidecars_layout.setContentsMargins(14, 14, 14, 14)
        sidecars_layout.setSpacing(8)

        # Group 1: Sidecar Files
        grp_sidecars = QGroupBox("Sidecar Files")
        grp_sidecars_layout = QVBoxLayout(grp_sidecars)
        grp_sidecars_layout.setSpacing(4)

        self.chk_write_lrc = QCheckBox("Write Enhanced LRC file")
        self.cb_write_lrc = self.chk_write_lrc
        grp_sidecars_layout.addWidget(self.chk_write_lrc)

        ext_layout = QHBoxLayout()
        ext_layout.setContentsMargins(20, 0, 0, 0)
        self.rad_lrc = QRadioButton(".lrc")
        self.rad_elrc = QRadioButton(".elrc")
        self.rad_both = QRadioButton("Both")
        self.rb_lrc = self.rad_lrc
        self.rb_elrc = self.rad_elrc
        self.rb_both = self.rad_both

        self.ext_group = QButtonGroup(self)
        self.ext_group.addButton(self.rad_lrc)
        self.ext_group.addButton(self.rad_elrc)
        self.ext_group.addButton(self.rad_both)

        ext_layout.addWidget(self.rad_lrc)
        ext_layout.addWidget(self.rad_elrc)
        ext_layout.addWidget(self.rad_both)
        ext_layout.addStretch()
        grp_sidecars_layout.addLayout(ext_layout)

        grp_sidecars_layout.addWidget(
            _make_sub_label("Saves word-by-word timing sidecars (.lrc / .elrc) in your music folder for players like foobar2000 or Namida.")
        )

        self.chk_write_lrc.toggled.connect(self._toggle_lrc_extensions)

        self.chk_write_ttml = QCheckBox("Write Apple TTML XML (.ttml)")
        self.cb_write_ttml = self.chk_write_ttml
        grp_sidecars_layout.addWidget(self.chk_write_ttml)
        grp_sidecars_layout.addWidget(
            _make_sub_label("Creates Apple Music-style XML lyric files. Leave unchecked if you only want standard .lrc files.")
        )

        sidecars_layout.addWidget(grp_sidecars)

        # Group 2: Cleanup & Overwrite
        grp_cleanup = QGroupBox("Cleanup && Overwrite")
        grp_cleanup_layout = QVBoxLayout(grp_cleanup)
        grp_cleanup_layout.setSpacing(4)

        self.chk_purge_stale = QCheckBox("Purge stale line-synced sidecars upon word-sync match")
        self.cb_purge_stale = self.chk_purge_stale
        grp_cleanup_layout.addWidget(self.chk_purge_stale)
        grp_cleanup_layout.addWidget(
            _make_sub_label("Automatically deletes older line-synced lyric files once new high-precision word-synced lyrics are saved.")
        )

        backup_layout = QHBoxLayout()
        backup_layout.setContentsMargins(20, 0, 0, 0)
        self.chk_backup_stale = QCheckBox("Create .bak backups of purged sidecars")
        self.cb_backup_stale = self.chk_backup_stale
        backup_layout.addWidget(self.chk_backup_stale)
        grp_cleanup_layout.addLayout(backup_layout)

        backup_sub_layout = QHBoxLayout()
        backup_sub_layout.setContentsMargins(20, 0, 0, 0)
        backup_sub_layout.addWidget(
            _make_sub_label("Keeps a .bak safety backup before removing any replaced sidecars.")
        )
        grp_cleanup_layout.addLayout(backup_sub_layout)

        self.chk_purge_stale.toggled.connect(self.chk_backup_stale.setEnabled)

        self.chk_overwrite_word = QCheckBox("Force overwrite existing true word-synced lyrics")
        self.cb_overwrite_word = self.chk_overwrite_word
        grp_cleanup_layout.addWidget(self.chk_overwrite_word)
        grp_cleanup_layout.addWidget(
            _make_sub_label("Re-downloads and replaces lyrics even if the track already has word sync.")
        )

        sidecars_layout.addWidget(grp_cleanup)
        sidecars_layout.addStretch()

        self.tab_widget.addTab(tab_sidecars, "Sidecars & Cleanup")

        # =====================================================================
        # Tab 2: Search & Ladder
        # =====================================================================
        tab_search = QWidget()
        search_layout = QVBoxLayout(tab_search)
        search_layout.setContentsMargins(14, 14, 14, 14)
        search_layout.setSpacing(10)

        grp_search = QGroupBox("Search && Matching")
        grp_search_form = QFormLayout(grp_search)
        grp_search_form.setSpacing(8)

        self.chk_smart_fallback = QCheckBox("Enable Smart Fallback (Cascading Query Ladder)")
        self.cb_smart_fallback = self.chk_smart_fallback
        grp_search_form.addRow(self.chk_smart_fallback)
        grp_search_form.addRow(_make_sub_label("Automatically tests variations (stripping features, bonus tags, or searching title-only) if the exact match fails."))

        self.spin_tolerance = QSpinBox()
        self.spin_tolerance.setRange(500, 10000)
        self.spin_tolerance.setSingleStep(250)
        self.spin_tolerance.setValue(2500)
        self.spin_tolerance.setSuffix(" ms")
        self.sb_tolerance = self.spin_tolerance
        grp_search_form.addRow("Duration tolerance:", self.spin_tolerance)
        grp_search_form.addRow(_make_sub_label("Maximum allowed duration difference (in ms) between your audio file and online lyrics to avoid incorrect versions."))

        search_layout.addWidget(grp_search)
        search_layout.addStretch()

        self.tab_widget.addTab(tab_search, "Search & Ladder")

        # =====================================================================
        # Tab 3: Tag Embedding
        # =====================================================================
        tab_embed = QWidget()
        embed_layout = QVBoxLayout(tab_embed)
        embed_layout.setContentsMargins(14, 14, 14, 14)
        embed_layout.setSpacing(10)

        grp_embed = QGroupBox("In-File Tag Embedding")
        grp_embed_layout = QVBoxLayout(grp_embed)
        grp_embed_layout.setSpacing(6)

        self.chk_embed_metadata = QCheckBox("Master embed metadata into audio files")
        self.cb_embed_metadata = self.chk_embed_metadata
        grp_embed_layout.addWidget(self.chk_embed_metadata)
        grp_embed_layout.addWidget(
            _make_sub_label("Writes lyrics directly inside the audio file so they stay attached when moving songs to other devices.")
        )

        sub_embed_layout = QVBoxLayout()
        sub_embed_layout.setContentsMargins(20, 0, 0, 0)
        sub_embed_layout.setSpacing(4)

        self.chk_flac_vorbis = QCheckBox("FLAC/OGG Vorbis comment (LYRICS)")
        self.chk_mp3_uslt = QCheckBox("MP3 Plain fallback (USLT frame)")
        self.chk_mp3_txxx = QCheckBox("MP3 Enhanced LRC (TXXX:LYRICS frame)")
        self.chk_mp3_sylt = QCheckBox("MP3 Binary SYLT frame (Word sync)")
        self.chk_m4a_atom = QCheckBox("M4A atom (©lyr)")

        self.cb_flac_vorbis = self.chk_flac_vorbis
        self.cb_mp3_uslt = self.chk_mp3_uslt
        self.cb_mp3_txxx = self.chk_mp3_txxx
        self.cb_mp3_sylt = self.chk_mp3_sylt
        self.cb_m4a_atom = self.chk_m4a_atom

        sub_embed_layout.addWidget(self.chk_flac_vorbis)
        sub_embed_layout.addWidget(self.chk_mp3_uslt)
        sub_embed_layout.addWidget(self.chk_mp3_txxx)
        sub_embed_layout.addWidget(self.chk_mp3_sylt)
        sub_embed_layout.addWidget(self.chk_m4a_atom)
        grp_embed_layout.addLayout(sub_embed_layout)

        self.sub_embed_boxes = [
            self.chk_flac_vorbis,
            self.chk_mp3_uslt,
            self.chk_mp3_txxx,
            self.chk_mp3_sylt,
            self.chk_m4a_atom,
        ]
        self.chk_embed_metadata.toggled.connect(self._toggle_embed_suboptions)

        embed_layout.addWidget(grp_embed)
        embed_layout.addStretch()

        self.tab_widget.addTab(tab_embed, "Tag Embedding")

        main_layout.addWidget(self.tab_widget)

        # ── Dialog Buttons ──
        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btn_box.setStyleSheet("QPushButton { min-height: 28px; min-width: 80px; padding: 4px 16px; margin-bottom: 2px; }")
        btn_box.accepted.connect(self._save_and_accept)
        btn_box.rejected.connect(self.reject)
        main_layout.addWidget(btn_box)

    def _toggle_lrc_extensions(self, enabled: bool) -> None:
        self.rad_lrc.setEnabled(enabled)
        self.rad_elrc.setEnabled(enabled)
        self.rad_both.setEnabled(enabled)

    def _toggle_embed_suboptions(self, enabled: bool) -> None:
        for box in self.sub_embed_boxes:
            box.setEnabled(enabled)

    def _load_config(self, cfg: TaggingConfig) -> None:
        self.chk_write_lrc.setChecked(cfg.write_lrc)
        ext = cfg.lrc_extension.lower()
        if ext == "elrc":
            self.rad_elrc.setChecked(True)
        elif ext == "both":
            self.rad_both.setChecked(True)
        else:
            self.rad_lrc.setChecked(True)
        self._toggle_lrc_extensions(cfg.write_lrc)

        self.chk_write_ttml.setChecked(cfg.write_ttml)
        self.chk_purge_stale.setChecked(cfg.purge_stale_line_sync)
        self.chk_backup_stale.setChecked(cfg.backup_stale_sidecars)
        self.chk_backup_stale.setEnabled(cfg.purge_stale_line_sync)
        self.chk_overwrite_word.setChecked(cfg.overwrite_existing_word_sync)

        self.chk_smart_fallback.setChecked(cfg.smart_fallback)
        self.spin_tolerance.setValue(cfg.duration_tolerance_ms)

        self.chk_embed_metadata.setChecked(cfg.embed_metadata)
        self.chk_flac_vorbis.setChecked(cfg.flac_embed_vorbis)
        self.chk_mp3_uslt.setChecked(cfg.mp3_embed_uslt_fallback)
        self.chk_mp3_txxx.setChecked(cfg.mp3_embed_txxx)
        self.chk_mp3_sylt.setChecked(cfg.mp3_embed_sylt)
        self.chk_m4a_atom.setChecked(cfg.m4a_embed_atom)
        self._toggle_embed_suboptions(cfg.embed_metadata)

    def get_config(self) -> Settings:
        if self.rad_elrc.isChecked():
            lrc_ext = "elrc"
        elif self.rad_both.isChecked():
            lrc_ext = "both"
        else:
            lrc_ext = "lrc"

        theme = getattr(self._config, "theme", "Dark")
        active_provider = getattr(self._config, "active_provider", "Kugou")

        return Settings(
            write_lrc=self.chk_write_lrc.isChecked(),
            lrc_extension=lrc_ext,
            write_ttml=self.chk_write_ttml.isChecked(),
            purge_stale_line_sync=self.chk_purge_stale.isChecked(),
            backup_stale_sidecars=self.chk_backup_stale.isChecked(),
            overwrite_existing_word_sync=self.chk_overwrite_word.isChecked(),
            smart_fallback=self.chk_smart_fallback.isChecked(),
            duration_tolerance_ms=self.spin_tolerance.value(),
            embed_metadata=self.chk_embed_metadata.isChecked(),
            flac_embed_vorbis=self.chk_flac_vorbis.isChecked(),
            mp3_embed_uslt_fallback=self.chk_mp3_uslt.isChecked(),
            mp3_embed_txxx=self.chk_mp3_txxx.isChecked(),
            mp3_embed_sylt=self.chk_mp3_sylt.isChecked(),
            m4a_embed_atom=self.chk_m4a_atom.isChecked(),
            theme=theme,
            active_provider=active_provider,
        )

    def _save_and_accept(self) -> None:
        cfg = self.get_config()
        self._config = cfg
        self._settings_mgr.save_settings(cfg)
        self.config_saved.emit(cfg)
        self.accept()
