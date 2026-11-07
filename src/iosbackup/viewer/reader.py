"""Read access to an iOS backup (unencrypted and encrypted)."""

from __future__ import annotations

import os
import plistlib
import shutil
import sqlite3
import struct
import tempfile
import threading
from dataclasses import dataclass

from .keybag import Keybag, WrongPassword, aes_cbc_decrypt, aes_cbc_decryptor, strip_pkcs7

FLAG_FILE, FLAG_DIR, FLAG_SYMLINK = 1, 2, 4
_CHUNK = 1024 * 1024


class UnsupportedBackup(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    file_id: str
    domain: str
    path: str
    flags: int

    @property
    def is_file(self) -> bool:
        return self.flags == FLAG_FILE

    @property
    def is_dir(self) -> bool:
        return self.flags == FLAG_DIR

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1] if self.path else self.domain


@dataclass
class FileMeta:
    size: int | None = None
    mtime: float | None = None
    protection_class: int | None = None
    encryption_key: bytes | None = None


def parse_file_blob(blob: bytes | None) -> FileMeta:
    meta = FileMeta()
    if not blob:
        return meta
    try:
        pl = plistlib.loads(blob)
        objects = pl["$objects"]
        root = pl["$top"]["root"]
        obj = objects[root.data if isinstance(root, plistlib.UID) else int(root)]
    except Exception:
        return meta
    size = obj.get("Size")
    meta.size = int(size) if isinstance(size, int) else None
    mtime = obj.get("LastModified")
    meta.mtime = float(mtime) if isinstance(mtime, (int, float)) else None
    pc = obj.get("ProtectionClass")
    meta.protection_class = int(pc) if isinstance(pc, int) else None
    ek = obj.get("EncryptionKey")
    if isinstance(ek, plistlib.UID):
        ek_obj = objects[ek.data]
        data = ek_obj.get("NS.data") if isinstance(ek_obj, dict) else ek_obj
        if isinstance(data, bytes) and len(data) > 4:
            meta.encryption_key = data[4:]
    elif isinstance(ek, bytes) and len(ek) > 4:
        meta.encryption_key = ek[4:]
    return meta


def _private_tempdir() -> str:
    """Private working directory (0700), preferably in RAM ($XDG_RUNTIME_DIR)."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and os.path.isdir(base):
        try:
            if shutil.disk_usage(base).free > 1_000_000_000:
                return tempfile.mkdtemp(prefix="iosbackup-", dir=base)
        except OSError:
            pass
    return tempfile.mkdtemp(prefix="iosbackup-")


class BackupReader:
    def __init__(self, path: str):
        self.path = path
        manifest_path = os.path.join(path, "Manifest.plist")
        try:
            with open(manifest_path, "rb") as fh:
                self.manifest = plistlib.load(fh)
        except OSError as exc:
            raise UnsupportedBackup(f"Manifest.plist is missing: {exc.strerror}") from exc
        except Exception as exc:
            raise UnsupportedBackup("Manifest.plist is damaged.") from exc
        if not os.path.isfile(os.path.join(path, "Manifest.db")):
            if os.path.isfile(os.path.join(path, "Manifest.mbdb")):
                raise UnsupportedBackup("Backups from iOS 9 and older are not supported.")
            raise UnsupportedBackup("Manifest.db is missing – the backup is incomplete.")
        self.encrypted = bool(self.manifest.get("IsEncrypted"))
        self.keybag: Keybag | None = None
        self.entries: list[Entry] = []
        self._tmp: str | None = None
        self._db: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._meta: dict[str, FileMeta] = {}
        self._temp_files: dict[str, str] = {}

    # ------------------------------------------------------------------
    def open(self, password: str | None = None) -> None:
        self._tmp = _private_tempdir()
        db_copy = os.path.join(self._tmp, "Manifest.db")
        src = os.path.join(self.path, "Manifest.db")
        if self.encrypted:
            if not password:
                raise WrongPassword("This backup is encrypted – a password is required.")
            self.keybag = Keybag(self.manifest["BackupKeyBag"])
            self.keybag.unlock(password)
            mk = self.manifest.get("ManifestKey")
            if not isinstance(mk, bytes) or len(mk) < 8:
                raise UnsupportedBackup("ManifestKey is missing from the encrypted backup.")
            mclass = struct.unpack("<L", mk[:4])[0]
            key = self.keybag.unwrap_for_class(mclass, mk[4:])
            with open(src, "rb") as fh:
                plain = strip_pkcs7(aes_cbc_decrypt(key, fh.read()))
            fd = os.open(db_copy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(plain)
        else:
            # work on a copy: avoids SQLite locking on network shares and never modifies the backup
            shutil.copyfile(src, db_copy)
        try:
            self._db = sqlite3.connect(db_copy, check_same_thread=False)
            rows = self._db.execute(
                "SELECT fileID, domain, relativePath, flags FROM Files").fetchall()
        except sqlite3.DatabaseError as exc:
            self.close()
            raise UnsupportedBackup(f"Cannot read Manifest.db: {exc}") from exc
        self.entries = [Entry(r[0], r[1] or "", r[2] or "", int(r[3] or 0)) for r in rows]

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)
        self._tmp = None
        self._temp_files.clear()

    # ------------------------------------------------------------------
    def meta(self, entry: Entry) -> FileMeta:
        cached = self._meta.get(entry.file_id)
        if cached is not None:
            return cached
        with self._lock:
            if self._db is None:
                return FileMeta()
            row = self._db.execute("SELECT file FROM Files WHERE fileID = ?",
                                   (entry.file_id,)).fetchone()
        meta = parse_file_blob(row[0] if row else None)
        self._meta[entry.file_id] = meta
        return meta

    def source_path(self, entry: Entry) -> str:
        return os.path.join(self.path, entry.file_id[:2], entry.file_id)

    def _file_key(self, entry: Entry, meta: FileMeta) -> bytes | None:
        if not self.encrypted:
            return None
        if meta.encryption_key is None or meta.protection_class is None:
            return None  # empty file
        return self.keybag.unwrap_for_class(meta.protection_class, meta.encryption_key)

    def read_bytes(self, entry: Entry) -> bytes:
        meta = self.meta(entry)
        src = self.source_path(entry)
        if not os.path.exists(src):
            return b""
        with open(src, "rb") as fh:
            data = fh.read()
        key = self._file_key(entry, meta)
        if key is None:
            return data if not self.encrypted else b""
        plain = aes_cbc_decrypt(key, data)
        if meta.size is not None and meta.size <= len(plain):
            return plain[:meta.size]
        return strip_pkcs7(plain)

    def export(self, entry: Entry, dest: str, cancel: threading.Event | None = None) -> None:
        """Write the (decrypted) file to ``dest`` and keep its modification time."""
        meta = self.meta(entry)
        src = self.source_path(entry)
        if not os.path.exists(src):
            if meta.size:
                raise FileNotFoundError(f"File missing from backup: {entry.domain}/{entry.path}")
            open(dest, "wb").close()
        elif not self.encrypted:
            shutil.copyfile(src, dest)
        else:
            key = self._file_key(entry, meta)
            if key is None:
                open(dest, "wb").close()
            else:
                dec = aes_cbc_decryptor(key)
                try:
                    with open(src, "rb") as fin, open(dest, "wb") as fout:
                        rest = b""
                        while True:
                            if cancel is not None and cancel.is_set():
                                raise InterruptedError
                            chunk = fin.read(_CHUNK)
                            if not chunk:
                                break
                            chunk = rest + chunk
                            usable = len(chunk) - len(chunk) % 16
                            rest = chunk[usable:]
                            fout.write(dec.update(chunk[:usable]))
                        fout.write(dec.finalize())
                        written = fout.tell()
                    if meta.size is not None and meta.size <= written:
                        os.truncate(dest, meta.size)
                    else:
                        with open(dest, "rb+") as fh:
                            if written >= 16:
                                fh.seek(written - 16)
                                last = fh.read(16)
                                fh.truncate(written - 16 + len(strip_pkcs7(last)))
                except BaseException:
                    if os.path.exists(dest):
                        os.unlink(dest)
                    raise
        if meta.mtime:
            try:
                os.utime(dest, (meta.mtime, meta.mtime))
            except OSError:
                pass

    def temp_copy(self, entry: Entry) -> str:
        """Extract a file into the private working directory (e.g. to open it)."""
        cached = self._temp_files.get(entry.file_id)
        if cached and os.path.exists(cached):
            return cached
        if self._tmp is None:
            raise RuntimeError("Backup is not open.")
        folder = os.path.join(self._tmp, "files", entry.file_id[:8])
        os.makedirs(folder, mode=0o700, exist_ok=True)
        dest = os.path.join(folder, entry.name or entry.file_id)
        self.export(entry, dest)
        self._temp_files[entry.file_id] = dest
        return dest
