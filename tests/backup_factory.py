"""Erzeugt synthetische iOS-Backups (Format wie iTunes/idevicebackup2) für Tests und Demo.

Verschlüsselte Backups werden wie im Original aufgebaut: Keybag mit per Passwort
gewrappten Klassenschlüsseln, Manifest.db und Dateien AES-256-CBC (IV 0) verschlüsselt.
Wird auch vom Fake-idevicebackup2 benutzt (nur Standardbibliothek + optional cryptography).
"""
import hashlib
import os
import plistlib
import sqlite3
import struct
import tempfile
import zlib
from datetime import datetime, timezone


def file_id(domain, path):
    return hashlib.sha1(f"{domain}-{path}".encode()).hexdigest()


def tlv(tag, value):
    if isinstance(value, int):
        value = struct.pack(">L", value)
    return tag + struct.pack(">L", len(value)) + value


def _pad(data):
    n = 16 - len(data) % 16
    return data + bytes([n]) * n


def _encrypt(key, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    enc = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
    return enc.update(_pad(data)) + enc.finalize()


def _wrap(kek, key):
    from cryptography.hazmat.primitives.keywrap import aes_key_wrap
    return aes_key_wrap(kek, key)


def file_blob(path, size, mtime, protection_class=3, encryption_key=None, is_dir=False):
    objects = ["$null"]
    root = {
        "$class": plistlib.UID(0),  # wird unten gesetzt
        "RelativePath": plistlib.UID(2),
        "LastModified": int(mtime), "LastStatusChange": int(mtime), "Birth": int(mtime),
        "Size": 0 if is_dir else size, "ProtectionClass": protection_class,
        "Mode": 0o40755 if is_dir else 0o100644, "Flags": 0,
        "UserID": 501, "GroupID": 501, "InodeNumber": 12345,
    }
    objects += [root, path]
    if encryption_key is not None:
        root["EncryptionKey"] = plistlib.UID(len(objects))
        objects.append({"NS.data": encryption_key, "$class": plistlib.UID(len(objects) + 1)})
        objects.append({"$classname": "NSMutableData", "$classes": ["NSMutableData", "NSData", "NSObject"]})
    root["$class"] = plistlib.UID(len(objects))
    objects.append({"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]})
    return plistlib.dumps({"$version": 100000, "$archiver": "NSKeyedArchiver",
                           "$top": {"root": plistlib.UID(1)}, "$objects": objects},
                          fmt=plistlib.FMT_BINARY)


def png_bytes(width, height, hue):
    """Einfaches Verlaufsbild als PNG (ohne externe Bibliotheken)."""
    import colorsys
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            r, g, b = colorsys.hsv_to_rgb((hue + x / width * 0.15) % 1.0, 0.55 + 0.4 * y / height, 0.95 - 0.35 * y / height)
            row += bytes([int(r * 255), int(g * 255), int(b * 255)])
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(t, d):
        return struct.pack(">L", len(d)) + t + d + struct.pack(">L", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">LLBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def build_backup(root, udid, files, password=None, iterations=(10, 10), device_name="Test-iPhone",
                 product_type="iPhone15,2", product_version="18.6.2"):
    """files: Liste (domain, relativePath, bytes oder None für Ordner, mtime)."""
    target = os.path.join(root, udid)
    os.makedirs(target, exist_ok=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    manifest = {"IsEncrypted": bool(password), "Date": now, "Version": "10.0",
                "Lockdown": {"DeviceName": device_name, "ProductType": product_type,
                             "ProductVersion": product_version, "UniqueDeviceID": udid}}
    class_keys = {}
    if password:
        salt, dpsl = os.urandom(20), os.urandom(20)
        iter_, dpic = iterations
        k1 = hashlib.pbkdf2_hmac("sha256", password.encode(), dpsl, dpic, 32)
        pkey = hashlib.pbkdf2_hmac("sha1", k1, salt, iter_, 32)
        bag = (tlv(b"VERS", 4) + tlv(b"TYPE", 1) + tlv(b"UUID", os.urandom(16))
               + tlv(b"HMCK", os.urandom(40)) + tlv(b"WRAP", 0) + tlv(b"SALT", salt)
               + tlv(b"ITER", iter_) + tlv(b"DPWT", 1) + tlv(b"DPIC", dpic) + tlv(b"DPSL", dpsl))
        for clas in range(1, 12):
            ck = os.urandom(32)
            class_keys[clas] = ck
            bag += (tlv(b"UUID", os.urandom(16)) + tlv(b"CLAS", clas) + tlv(b"WRAP", 3)
                    + tlv(b"KTYP", 0) + tlv(b"WPKY", _wrap(pkey, ck)))
        manifest["BackupKeyBag"] = bag

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, "
               "flags INTEGER, file BLOB)")
    db.execute("CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB)")
    for domain, path, data, mtime in files:
        fid = file_id(domain, path)
        if data is None:
            db.execute("INSERT INTO Files VALUES (?,?,?,?,?)",
                       (fid, domain, path, 2, file_blob(path, 0, mtime, is_dir=True)))
            continue
        enc_key = None
        content = data
        if password:
            fkey = os.urandom(32)
            enc_key = struct.pack("<L", 3) + _wrap(class_keys[3], fkey)
            content = _encrypt(fkey, data)
        d = os.path.join(target, fid[:2])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, fid), "wb") as fh:
            fh.write(content)
        db.execute("INSERT INTO Files VALUES (?,?,?,?,?)",
                   (fid, domain, path, 1, file_blob(path, len(data), mtime, 3, enc_key)))
    db.commit()
    db.close()
    with open(db_path, "rb") as fh:
        db_bytes = fh.read()
    os.unlink(db_path)
    if password:
        mkey = os.urandom(32)
        manifest["ManifestKey"] = struct.pack("<L", 4) + _wrap(class_keys[4], mkey)
        db_bytes = _encrypt(mkey, db_bytes)
    with open(os.path.join(target, "Manifest.db"), "wb") as fh:
        fh.write(db_bytes)
    with open(os.path.join(target, "Manifest.plist"), "wb") as fh:
        plistlib.dump(manifest, fh, fmt=plistlib.FMT_BINARY)
    with open(os.path.join(target, "Info.plist"), "wb") as fh:
        plistlib.dump({"Device Name": device_name, "Display Name": device_name,
                       "Product Type": product_type, "Product Version": product_version,
                       "Target Identifier": udid, "Unique Identifier": udid,
                       "Last Backup Date": now}, fh)
    with open(os.path.join(target, "Status.plist"), "wb") as fh:
        plistlib.dump({"SnapshotState": "finished", "Date": now, "IsFullBackup": False}, fh,
                      fmt=plistlib.FMT_BINARY)
    return target


def sample_files(count=12, base_mtime=1_760_000_000, image_size=(96, 72)):
    """Beispielinhalt: Kamerarolle mit Fotos und einem Video, App-Daten, Notiz."""
    files = [("CameraRollDomain", "Media", None, base_mtime),
             ("CameraRollDomain", "Media/DCIM", None, base_mtime),
             ("CameraRollDomain", "Media/DCIM/100APPLE", None, base_mtime)]
    for i in range(count):
        mtime = base_mtime - i * 86400 * 9
        files.append(("CameraRollDomain", f"Media/DCIM/100APPLE/IMG_{1000 + i:04d}.PNG",
                      png_bytes(*image_size, (i * 0.083) % 1.0), mtime))
    files.append(("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0999.MOV",
                  b"\x00\x00\x00\x14ftypqt  " + os.urandom(3000), base_mtime + 3600))
    files += [
        ("HomeDomain", "Library/Notes", None, base_mtime),
        ("HomeDomain", "Library/Notes/notes.txt", "Einkaufsliste: Milch, Brot".encode(), base_mtime),
        ("AppDomain-net.whatsapp.WhatsApp", "Documents", None, base_mtime),
        ("AppDomain-net.whatsapp.WhatsApp", "Documents/ChatStorage.sqlite", os.urandom(5000), base_mtime),
        ("MediaDomain", "Media/Recordings/Aufnahme 1.m4a", os.urandom(2000), base_mtime),
        ("HomeDomain", "Library/Empty.txt", b"", base_mtime),
    ]
    return files
