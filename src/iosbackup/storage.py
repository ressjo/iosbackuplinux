"""Prüfung des Speicherorts (lokal oder gemountetes NAS)."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass

from .fmt import format_bytes

NETWORK_FS = {
    "cifs", "smb3", "smbfs", "nfs", "nfs4", "9p", "afpfs", "davfs", "ncpfs",
    "fuse.sshfs", "sshfs", "fuse.rclone", "fuse.gvfsd-fuse", "fuse.kio-fuse",
    "glusterfs", "ceph", "fuse.ceph",
}

OK, WARN, ERROR = "ok", "warn", "error"


def _unescape(field: str) -> str:
    # /proc/mounts kodiert Leerzeichen usw. oktal (\040)
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and i + 4 <= len(field) and field[i + 1:i + 4].isdigit():
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def read_mounts(mounts_file: str = "/proc/mounts") -> list[tuple[str, str]]:
    result = []
    try:
        with open(mounts_file, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3:
                    result.append((_unescape(parts[1]), parts[2]))
    except OSError:
        pass
    return result


def find_mount(path: str, mounts_file: str = "/proc/mounts") -> tuple[str, str]:
    """Liefert (Mountpoint, Dateisystemtyp) für ``path`` (längster Präfix, letzter Eintrag gewinnt)."""
    path = os.path.realpath(path) if os.path.exists(path) else os.path.abspath(path)
    best, best_fs = "/", ""
    for mp, fs in read_mounts(mounts_file):
        if path == mp or path.startswith(mp.rstrip("/") + "/") or mp == "/":
            if len(mp) >= len(best):
                best, best_fs = mp, fs
    return best, best_fs


def suggest_expected_mount(path: str, mounts_file: str = "/proc/mounts") -> str | None:
    """Wenn der Ordner auf einem Netz-/Wechsellaufwerk liegt, dessen Mountpoint merken."""
    os.path.exists(path)  # löst ggf. systemd-automount aus
    mp, fs = find_mount(path, mounts_file)
    if mp == "/":
        return None
    if fs in NETWORK_FS or mp.startswith(("/mnt", "/media", "/run/media", "/srv", "/nas")):
        return mp
    return None


@dataclass
class StorageStatus:
    path: str
    level: str
    message: str
    exists: bool = False
    writable: bool = False
    mountpoint: str = ""
    fstype: str = ""
    free_bytes: int | None = None
    total_bytes: int | None = None

    @property
    def is_network(self) -> bool:
        return self.fstype in NETWORK_FS

    @property
    def usable(self) -> bool:
        return self.level != ERROR


def _writable(path: str) -> bool:
    try:
        fd, name = tempfile.mkstemp(prefix=".iosbackup-test-", dir=path)
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def check_storage(
    path: str,
    expected_mount: str | None = None,
    required_bytes: int | None = None,
    create: bool = False,
    mounts_file: str = "/proc/mounts",
) -> StorageStatus:
    if not path:
        return StorageStatus(path, ERROR, "Kein Speicherort gewählt.")

    # Zugriff löst bei x-systemd.automount das Einhängen aus
    exists = os.path.isdir(path)

    if expected_mount:
        expected = expected_mount.rstrip("/") or "/"
        os.path.isdir(expected)  # automount auslösen
        mp, fs = find_mount(expected, mounts_file)
        if mp != expected or fs == "autofs":
            return StorageStatus(
                path, ERROR,
                f"Das Laufwerk „{expected_mount}“ ist nicht eingehängt (NAS offline?). "
                "Es wird nichts gesichert, damit nicht versehentlich auf die lokale "
                "Festplatte geschrieben wird.",
                exists=exists, mountpoint=mp, fstype=fs,
            )

    if not exists and create:
        try:
            os.makedirs(path, exist_ok=True)
            exists = True
        except OSError as exc:
            return StorageStatus(path, ERROR, f"Ordner kann nicht angelegt werden: {exc.strerror}")

    if not exists:
        return StorageStatus(path, ERROR, "Der Ordner existiert nicht.")

    mp, fs = find_mount(path, mounts_file)
    st = StorageStatus(path, OK, "", exists=True, mountpoint=mp, fstype=fs)
    try:
        du = shutil.disk_usage(path)
        st.free_bytes, st.total_bytes = du.free, du.total
    except OSError:
        pass

    st.writable = _writable(path)
    if not st.writable:
        st.level = ERROR
        st.message = "Keine Schreibrechte im Speicherort."
        return st

    where = f"Netzlaufwerk ({fs}) unter {mp}" if st.is_network else (
        f"Laufwerk unter {mp}" if mp != "/" else "lokale Festplatte")
    free = f"{format_bytes(st.free_bytes)} frei" if st.free_bytes is not None else ""
    st.message = ", ".join(x for x in (where, free) if x)

    if required_bytes and st.free_bytes is not None and st.free_bytes < required_bytes:
        st.level = WARN
        st.message += f" – möglicherweise zu wenig Platz (benötigt ca. {format_bytes(required_bytes)})"
    return st
