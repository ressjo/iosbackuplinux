"""Persistent settings (~/.config/iosbackuplinux/iosbackuplinux.conf)."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

from . import APP_ID

DEFAULT_BACKUP_DIR = os.path.join(os.path.expanduser("~"), "iOS-Backups")


class Settings:
    def __init__(self) -> None:
        self._s = QSettings(APP_ID, APP_ID)

    def _get(self, key: str, default):
        value = self._s.value(key, default)
        return default if value is None else value

    @property
    def backup_dir(self) -> str:
        return str(self._get("storage/backup_dir", DEFAULT_BACKUP_DIR))

    @backup_dir.setter
    def backup_dir(self, value: str) -> None:
        self._s.setValue("storage/backup_dir", value)

    @property
    def expected_mount(self) -> str:
        """Mount point that must be mounted before each backup ("" = no check)."""
        return str(self._get("storage/expected_mount", ""))

    @expected_mount.setter
    def expected_mount(self, value: str | None) -> None:
        self._s.setValue("storage/expected_mount", value or "")

    @property
    def force_full(self) -> bool:
        return str(self._get("backup/force_full", "false")).lower() == "true"

    @force_full.setter
    def force_full(self, value: bool) -> None:
        self._s.setValue("backup/force_full", "true" if value else "false")

    @property
    def geometry(self):
        return self._s.value("window/geometry")

    @geometry.setter
    def geometry(self, value) -> None:
        self._s.setValue("window/geometry", value)

    def sync(self) -> None:
        self._s.sync()
