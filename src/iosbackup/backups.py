"""Find, read and delete existing backups in the backup location.

idevicebackup2 creates one folder per device, ``<location>/<UDID>/``, containing
``Info.plist``, ``Manifest.plist``, ``Status.plist`` and the files (same format as
iTunes/Finder).
"""

from __future__ import annotations

import os
import plistlib
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BackupEntry:
    udid: str
    path: str
    device_name: str = ""
    product_type: str = ""
    product_version: str = ""
    serial: str = ""
    last_backup: datetime | None = None
    encrypted: bool | None = None
    complete: bool | None = None
    size: int | None = field(default=None, compare=False)

    @property
    def root(self) -> str:
        return os.path.dirname(self.path)


def _load_plist(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            data = plistlib.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def is_backup_dir(path: str) -> bool:
    return os.path.isdir(path) and (
        os.path.isfile(os.path.join(path, "Info.plist"))
        or os.path.isfile(os.path.join(path, "Manifest.plist"))
    )


def read_backup(path: str) -> BackupEntry | None:
    if not is_backup_dir(path):
        return None
    info = _load_plist(os.path.join(path, "Info.plist"))
    manifest = _load_plist(os.path.join(path, "Manifest.plist"))
    status = _load_plist(os.path.join(path, "Status.plist"))
    lockdown = manifest.get("Lockdown", {}) if isinstance(manifest.get("Lockdown"), dict) else {}

    udid = (
        info.get("Target Identifier")
        or info.get("Unique Identifier")
        or lockdown.get("UniqueDeviceID")
        or os.path.basename(path.rstrip(os.sep))
    )
    entry = BackupEntry(
        udid=str(udid),
        path=path,
        device_name=str(info.get("Device Name") or info.get("Display Name")
                        or lockdown.get("DeviceName") or ""),
        product_type=str(info.get("Product Type") or lockdown.get("ProductType") or ""),
        product_version=str(info.get("Product Version") or lockdown.get("ProductVersion") or ""),
        serial=str(info.get("Serial Number") or lockdown.get("SerialNumber") or ""),
        last_backup=_as_utc(info.get("Last Backup Date"))
        or _as_utc(status.get("Date"))
        or _as_utc(manifest.get("Date")),
    )
    if "IsEncrypted" in manifest:
        entry.encrypted = bool(manifest["IsEncrypted"])
    if "SnapshotState" in status:
        entry.complete = str(status["SnapshotState"]).lower() == "finished"
    return entry


def scan_backups(root: str) -> list[BackupEntry]:
    entries: list[BackupEntry] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return entries
    for name in names:
        if name.startswith("."):
            continue
        entry = read_backup(os.path.join(root, name))
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: e.last_backup or datetime.min.replace(tzinfo=timezone.utc),
                 reverse=True)
    return entries


def backup_size(path: str) -> int:
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for de in it:
                    try:
                        if de.is_dir(follow_symlinks=False):
                            stack.append(de.path)
                        else:
                            total += de.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def delete_backup(entry: BackupEntry, root: str) -> None:
    """Delete a backup – but only if it really is a backup directly inside the location."""
    path = os.path.realpath(entry.path)
    if os.path.dirname(path) != os.path.realpath(root):
        raise ValueError("Backup is not directly inside the backup location – refusing to delete.")
    if not is_backup_dir(path):
        raise ValueError("Folder does not look like an iOS backup – refusing to delete.")
    shutil.rmtree(path)
