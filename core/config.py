"""Persistent configuration management backed by QSettings."""

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from PySide6.QtCore import QSettings

from core.models import TaggingConfig


@dataclass
class Settings(TaggingConfig):
    """Application-wide settings extending TaggingConfig with UI preferences."""

    theme: str = "Dark"
    active_provider: str = "Auto"
    column_widths: List[int] = field(default_factory=lambda: [220, 140, 75, 90, 160])

    @property
    def purge_stale(self) -> bool:
        return self.purge_stale_line_sync

    @purge_stale.setter
    def purge_stale(self, val: bool) -> None:
        self.purge_stale_line_sync = bool(val)

    @property
    def create_backup(self) -> bool:
        return self.backup_stale_sidecars

    @create_backup.setter
    def create_backup(self, val: bool) -> None:
        self.backup_stale_sidecars = bool(val)

    @property
    def force_overwrite(self) -> bool:
        return self.overwrite_existing_word_sync

    @force_overwrite.setter
    def force_overwrite(self, val: bool) -> None:
        self.overwrite_existing_word_sync = bool(val)


def _get_bool(qsettings: QSettings, key: str, default: bool) -> bool:
    val = qsettings.value(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    if isinstance(val, (int, float)):
        return bool(val)
    return default


def _get_int(qsettings: QSettings, key: str, default: int) -> int:
    val = qsettings.value(key, default)
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _get_str(qsettings: QSettings, key: str, default: str) -> str:
    val = qsettings.value(key, default)
    return str(val) if val is not None else default


def _get_column_widths(qsettings: QSettings, key: str, default: List[int]) -> List[int]:
    val = qsettings.value(key, None)
    if val is None:
        return list(default)
    if isinstance(val, (list, tuple)):
        try:
            res = [int(x) for x in val]
            if len(res) == len(default):
                return res
        except (ValueError, TypeError):
            pass
    elif isinstance(val, str):
        val_str = val.strip()
        if val_str.startswith("[") and val_str.endswith("]"):
            try:
                parsed = json.loads(val_str)
                if isinstance(parsed, list):
                    res = [int(x) for x in parsed]
                    if len(res) == len(default):
                        return res
            except Exception:
                pass
        try:
            parts = [int(x.strip()) for x in val_str.split(",") if x.strip()]
            if len(parts) == len(default):
                return parts
        except (ValueError, TypeError):
            pass
    return list(default)


class SettingsManager:
    """Manages reading and writing application settings via QSettings."""

    def __init__(self, organization: str = "lyric-sync-tool", application: str = "Config") -> None:
        self._qsettings = QSettings(organization, application)

    def load(self) -> Settings:
        """Load persisted settings, falling back to defaults for missing keys."""
        write_lrc = _get_bool(self._qsettings, "write_lrc", True)
        lrc_extension = _get_str(self._qsettings, "lrc_extension", "lrc")
        write_ttml = _get_bool(self._qsettings, "write_ttml", True)

        # Handle both purge_stale and purge_stale_line_sync
        purge_stale = _get_bool(
            self._qsettings,
            "purge_stale",
            _get_bool(self._qsettings, "purge_stale_line_sync", True),
        )

        # Handle both create_backup and backup_stale_sidecars
        create_backup = _get_bool(
            self._qsettings,
            "create_backup",
            _get_bool(self._qsettings, "backup_stale_sidecars", False),
        )

        # Handle both force_overwrite and overwrite_existing_word_sync
        force_overwrite = _get_bool(
            self._qsettings,
            "force_overwrite",
            _get_bool(self._qsettings, "overwrite_existing_word_sync", False),
        )

        smart_fallback = _get_bool(self._qsettings, "smart_fallback", True)
        duration_tolerance_ms = _get_int(self._qsettings, "duration_tolerance_ms", 2500)
        embed_metadata = _get_bool(self._qsettings, "embed_metadata", True)
        flac_embed_vorbis = _get_bool(self._qsettings, "flac_embed_vorbis", True)
        mp3_embed_uslt_fallback = _get_bool(self._qsettings, "mp3_embed_uslt_fallback", True)
        mp3_embed_txxx = _get_bool(self._qsettings, "mp3_embed_txxx", True)
        mp3_embed_sylt = _get_bool(self._qsettings, "mp3_embed_sylt", False)
        m4a_embed_atom = _get_bool(self._qsettings, "m4a_embed_atom", True)

        theme = _get_str(self._qsettings, "theme", "Dark")
        active_provider = _get_str(self._qsettings, "active_provider", "Auto")
        column_widths = _get_column_widths(self._qsettings, "column_widths", [220, 140, 75, 90, 160])

        return Settings(
            write_lrc=write_lrc,
            lrc_extension=lrc_extension,
            write_ttml=write_ttml,
            purge_stale_line_sync=purge_stale,
            backup_stale_sidecars=create_backup,
            overwrite_existing_word_sync=force_overwrite,
            smart_fallback=smart_fallback,
            duration_tolerance_ms=duration_tolerance_ms,
            embed_metadata=embed_metadata,
            flac_embed_vorbis=flac_embed_vorbis,
            mp3_embed_uslt_fallback=mp3_embed_uslt_fallback,
            mp3_embed_txxx=mp3_embed_txxx,
            mp3_embed_sylt=mp3_embed_sylt,
            m4a_embed_atom=m4a_embed_atom,
            theme=theme,
            active_provider=active_provider,
            column_widths=column_widths,
        )

    def load_settings(self) -> Settings:
        """Alias for load()."""
        return self.load()

    def save(self, settings: Union[Settings, TaggingConfig]) -> None:
        """Persist settings to QSettings."""
        self._qsettings.setValue("write_lrc", getattr(settings, "write_lrc", True))
        self._qsettings.setValue("lrc_extension", getattr(settings, "lrc_extension", "lrc"))
        self._qsettings.setValue("write_ttml", getattr(settings, "write_ttml", True))

        purge_stale_val = getattr(settings, "purge_stale_line_sync", getattr(settings, "purge_stale", True))
        self._qsettings.setValue("purge_stale", purge_stale_val)
        self._qsettings.setValue("purge_stale_line_sync", purge_stale_val)

        backup_val = getattr(settings, "backup_stale_sidecars", getattr(settings, "create_backup", False))
        self._qsettings.setValue("create_backup", backup_val)
        self._qsettings.setValue("backup_stale_sidecars", backup_val)

        overwrite_val = getattr(
            settings, "overwrite_existing_word_sync", getattr(settings, "force_overwrite", False)
        )
        self._qsettings.setValue("force_overwrite", overwrite_val)
        self._qsettings.setValue("overwrite_existing_word_sync", overwrite_val)

        self._qsettings.setValue("smart_fallback", getattr(settings, "smart_fallback", True))
        self._qsettings.setValue("duration_tolerance_ms", getattr(settings, "duration_tolerance_ms", 2500))
        self._qsettings.setValue("embed_metadata", getattr(settings, "embed_metadata", True))
        self._qsettings.setValue("flac_embed_vorbis", getattr(settings, "flac_embed_vorbis", True))
        self._qsettings.setValue("mp3_embed_uslt_fallback", getattr(settings, "mp3_embed_uslt_fallback", True))
        self._qsettings.setValue("mp3_embed_txxx", getattr(settings, "mp3_embed_txxx", True))
        self._qsettings.setValue("mp3_embed_sylt", getattr(settings, "mp3_embed_sylt", False))
        self._qsettings.setValue("m4a_embed_atom", getattr(settings, "m4a_embed_atom", True))

        theme_val = getattr(settings, "theme", "Dark")
        self._qsettings.setValue("theme", theme_val)

        provider_val = getattr(settings, "active_provider", "Auto")
        self._qsettings.setValue("active_provider", provider_val)

        widths = getattr(settings, "column_widths", [220, 140, 75, 90, 160])
        self._qsettings.setValue("column_widths", widths)

        self._qsettings.sync()

    def save_settings(self, settings: Union[Settings, TaggingConfig]) -> None:
        """Alias for save()."""
        self.save(settings)


Config = Settings


def load_config(path: Optional[Union[str, Path]] = None) -> Settings:
    """Load settings from a JSON file path or system QSettings."""
    if path is not None:
        file_path = Path(path)
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "purge_stale" in data and "purge_stale_line_sync" not in data:
                data["purge_stale_line_sync"] = data["purge_stale"]
            if "create_backup" in data and "backup_stale_sidecars" not in data:
                data["backup_stale_sidecars"] = data["create_backup"]
            if "force_overwrite" in data and "overwrite_existing_word_sync" not in data:
                data["overwrite_existing_word_sync"] = data["force_overwrite"]
            valid_fields = Settings.__dataclass_fields__.keys()
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            return Settings(**filtered)
        return Settings()
    return SettingsManager().load()


def save_config(config: Union[Settings, TaggingConfig], path: Optional[Union[str, Path]] = None) -> None:
    """Save settings to a JSON file path or system QSettings."""
    if path is not None:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if is_dataclass(config):
            data = asdict(config)
        else:
            valid_fields = Settings.__dataclass_fields__.keys()
            data = {k: getattr(config, k) for k in valid_fields if hasattr(config, k)}
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    else:
        SettingsManager().save(config)

