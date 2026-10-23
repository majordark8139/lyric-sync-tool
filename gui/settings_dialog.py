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
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from core.models import TaggingConfig


class TaggingSettingsDialog(QDialog):
    """Settings dialog for configuring file tagging, sidecars, and search options."""

    config_saved = Signal(TaggingConfig)

    def __init__(self, current_config: TaggingConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tagging & Synchronization Settings")
        self.resize(460, 540)
        self.setMinimumHeight(540)
        self._config = current_config
        self._init_ui()
        self._load_config(self._config)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 16)
        layout.setSpacing(8)

        # ── 1. Sidecars Group ──
        sidecar_group = QGroupBox("Sidecar Files")
        sidecar_layout = QVBoxLayout(sidecar_group)
        sidecar_layout.setSpacing(6)

        self.cb_write_lrc = QCheckBox("Write Enhanced LRC file")
        sidecar_layout.addWidget(self.cb_write_lrc)

        ext_layout = QHBoxLayout()
        ext_layout.setContentsMargins(20, 0, 0, 0)
        self.rb_lrc = QRadioButton(".lrc")
        self.rb_elrc = QRadioButton(".elrc")
        self.rb_both = QRadioButton("Both")
        self.ext_group = QButtonGroup(self)
        self.ext_group.addButton(self.rb_lrc)
        self.ext_group.addButton(self.rb_elrc)
        self.ext_group.addButton(self.rb_both)
        ext_layout.addWidget(self.rb_lrc)
        ext_layout.addWidget(self.rb_elrc)
        ext_layout.addWidget(self.rb_both)
        ext_layout.addStretch()
        sidecar_layout.addLayout(ext_layout)

        self.cb_write_lrc.toggled.connect(self._toggle_lrc_extensions)

        self.cb_write_ttml = QCheckBox("Write Apple TTML XML (.ttml)")
        sidecar_layout.addWidget(self.cb_write_ttml)

        layout.addWidget(sidecar_group)

        # ── 2. Cleanup & Overwrite Group ──
        cleanup_group = QGroupBox("Cleanup && Overwrite")
        cleanup_layout = QVBoxLayout(cleanup_group)
        cleanup_layout.setSpacing(6)

        self.cb_purge_stale = QCheckBox("Purge stale line-synced sidecars upon word-sync match")
        cleanup_layout.addWidget(self.cb_purge_stale)

        backup_layout = QHBoxLayout()
        backup_layout.setContentsMargins(20, 0, 0, 0)
        self.cb_backup_stale = QCheckBox("Create .bak backups of purged sidecars")
        backup_layout.addWidget(self.cb_backup_stale)
        cleanup_layout.addLayout(backup_layout)

        self.cb_purge_stale.toggled.connect(self.cb_backup_stale.setEnabled)

        self.cb_overwrite_word = QCheckBox("Force overwrite existing true word-synced lyrics")
        cleanup_layout.addWidget(self.cb_overwrite_word)

        layout.addWidget(cleanup_group)

        # ── 3. Search & Matching Group ──
        search_group = QGroupBox("Search && Matching")
        search_layout = QFormLayout(search_group)
        search_layout.setSpacing(6)

        self.cb_smart_fallback = QCheckBox("Enable Smart Fallback (Cascading Query Ladder)")
        search_layout.addRow(self.cb_smart_fallback)

        self.sb_tolerance = QSpinBox()
        self.sb_tolerance.setRange(0, 15000)
        self.sb_tolerance.setSingleStep(250)
        self.sb_tolerance.setSuffix(" ms")
        search_layout.addRow("Duration tolerance:", self.sb_tolerance)

        layout.addWidget(search_group)

        # ── 4. In-File Embedding Group ──
        embed_group = QGroupBox("In-File Tag Embedding")
        embed_layout = QVBoxLayout(embed_group)
        embed_layout.setSpacing(6)

        self.cb_embed_metadata = QCheckBox("Master embed metadata into audio files")
        embed_layout.addWidget(self.cb_embed_metadata)

        sub_embed_layout = QVBoxLayout()
        sub_embed_layout.setContentsMargins(20, 0, 0, 0)
        sub_embed_layout.setSpacing(4)

        self.cb_flac_vorbis = QCheckBox("FLAC/OGG Vorbis comment (LYRICS)")
        self.cb_mp3_uslt = QCheckBox("MP3 Plain fallback (USLT frame)")
        self.cb_mp3_txxx = QCheckBox("MP3 Enhanced LRC (TXXX:LYRICS frame)")
        self.cb_mp3_sylt = QCheckBox("MP3 Binary SYLT frame (Word sync)")
        self.cb_m4a_atom = QCheckBox("M4A atom (©lyr)")

        sub_embed_layout.addWidget(self.cb_flac_vorbis)
        sub_embed_layout.addWidget(self.cb_mp3_uslt)
        sub_embed_layout.addWidget(self.cb_mp3_txxx)
        sub_embed_layout.addWidget(self.cb_mp3_sylt)
        sub_embed_layout.addWidget(self.cb_m4a_atom)
        embed_layout.addLayout(sub_embed_layout)

        self.sub_embed_boxes = [
            self.cb_flac_vorbis,
            self.cb_mp3_uslt,
            self.cb_mp3_txxx,
            self.cb_mp3_sylt,
            self.cb_m4a_atom,
        ]
        self.cb_embed_metadata.toggled.connect(self._toggle_embed_suboptions)

        layout.addWidget(embed_group)

        # ── Dialog Buttons ──
        layout.addSpacing(10)
        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btn_box.setStyleSheet("QPushButton { min-height: 26px; min-width: 75px; padding: 4px 14px; }")
        btn_box.accepted.connect(self._save_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _toggle_lrc_extensions(self, enabled: bool) -> None:
        self.rb_lrc.setEnabled(enabled)
        self.rb_elrc.setEnabled(enabled)
        self.rb_both.setEnabled(enabled)

    def _toggle_embed_suboptions(self, enabled: bool) -> None:
        for box in self.sub_embed_boxes:
            box.setEnabled(enabled)

    def _load_config(self, cfg: TaggingConfig) -> None:
        self.cb_write_lrc.setChecked(cfg.write_lrc)
        ext = cfg.lrc_extension.lower()
        if ext == "elrc":
            self.rb_elrc.setChecked(True)
        elif ext == "both":
            self.rb_both.setChecked(True)
        else:
            self.rb_lrc.setChecked(True)
        self._toggle_lrc_extensions(cfg.write_lrc)

        self.cb_write_ttml.setChecked(cfg.write_ttml)
        self.cb_purge_stale.setChecked(cfg.purge_stale_line_sync)
        self.cb_backup_stale.setChecked(cfg.backup_stale_sidecars)
        self.cb_backup_stale.setEnabled(cfg.purge_stale_line_sync)
        self.cb_overwrite_word.setChecked(cfg.overwrite_existing_word_sync)

        self.cb_smart_fallback.setChecked(cfg.smart_fallback)
        self.sb_tolerance.setValue(cfg.duration_tolerance_ms)

        self.cb_embed_metadata.setChecked(cfg.embed_metadata)
        self.cb_flac_vorbis.setChecked(cfg.flac_embed_vorbis)
        self.cb_mp3_uslt.setChecked(cfg.mp3_embed_uslt_fallback)
        self.cb_mp3_txxx.setChecked(cfg.mp3_embed_txxx)
        self.cb_mp3_sylt.setChecked(cfg.mp3_embed_sylt)
        self.cb_m4a_atom.setChecked(cfg.m4a_embed_atom)
        self._toggle_embed_suboptions(cfg.embed_metadata)

    def get_config(self) -> TaggingConfig:
        if self.rb_elrc.isChecked():
            lrc_ext = "elrc"
        elif self.rb_both.isChecked():
            lrc_ext = "both"
        else:
            lrc_ext = "lrc"

        return TaggingConfig(
            write_lrc=self.cb_write_lrc.isChecked(),
            lrc_extension=lrc_ext,
            write_ttml=self.cb_write_ttml.isChecked(),
            purge_stale_line_sync=self.cb_purge_stale.isChecked(),
            backup_stale_sidecars=self.cb_backup_stale.isChecked(),
            overwrite_existing_word_sync=self.cb_overwrite_word.isChecked(),
            smart_fallback=self.cb_smart_fallback.isChecked(),
            duration_tolerance_ms=self.sb_tolerance.value(),
            embed_metadata=self.cb_embed_metadata.isChecked(),
            flac_embed_vorbis=self.cb_flac_vorbis.isChecked(),
            mp3_embed_uslt_fallback=self.cb_mp3_uslt.isChecked(),
            mp3_embed_txxx=self.cb_mp3_txxx.isChecked(),
            mp3_embed_sylt=self.cb_mp3_sylt.isChecked(),
            m4a_embed_atom=self.cb_m4a_atom.isChecked(),
        )

    def _save_and_accept(self) -> None:
        cfg = self.get_config()
        self._config = cfg
        self.config_saved.emit(cfg)
        self.accept()
