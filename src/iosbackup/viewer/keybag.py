"""Entschlüsselung verschlüsselter iOS-Backups (iOS 10.2 und neuer).

Ablauf (wie bei iTunes/Finder):
  * ``Manifest.plist`` enthält den ``BackupKeyBag`` (TLV-Format) und den ``ManifestKey``.
  * Aus dem Backup-Passwort wird per PBKDF2 (SHA-256 mit DPSL/DPIC, danach SHA-1 mit
    SALT/ITER) der Passwortschlüssel abgeleitet.
  * Damit werden die Klassenschlüssel per AES-Key-Unwrap (RFC 3394) entpackt.
  * Manifest.db und jede Datei sind mit AES-256-CBC (IV = 0) verschlüsselt; der
    Schlüssel ist mit dem Klassenschlüssel der jeweiligen Schutzklasse verpackt.
"""

from __future__ import annotations

import hashlib
import struct

WRAP_PASSCODE = 2
_CLASSKEY_TAGS = {b"CLAS", b"WRAP", b"WPKY", b"KTYP", b"PBKY"}


class MissingCrypto(RuntimeError):
    pass


class WrongPassword(ValueError):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives import keywrap
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - abhängig von der Installation
        raise MissingCrypto(
            "Für verschlüsselte Backups wird das Paket „python-cryptography“ benötigt:\n"
            "sudo pacman -S python-cryptography") from exc
    return keywrap, Cipher, algorithms, modes


def iter_tlv(blob: bytes):
    i = 0
    while i + 8 <= len(blob):
        tag = blob[i:i + 4]
        length = struct.unpack(">L", blob[i + 4:i + 8])[0]
        yield tag, blob[i + 8:i + 8 + length]
        i += 8 + length


def aes_unwrap(kek: bytes, wrapped: bytes) -> bytes:
    keywrap, *_ = _crypto()
    try:
        return keywrap.aes_key_unwrap(kek, wrapped)
    except keywrap.InvalidUnwrap as exc:
        raise WrongPassword("Schlüssel konnte nicht entpackt werden") from exc


def aes_cbc_decryptor(key: bytes):
    _, Cipher, algorithms, modes = _crypto()
    return Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).decryptor()


def strip_pkcs7(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    if 1 <= n <= 16 and len(data) >= n and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return data


def aes_cbc_decrypt(key: bytes, data: bytes) -> bytes:
    dec = aes_cbc_decryptor(key)
    usable = len(data) - (len(data) % 16)
    return dec.update(data[:usable]) + dec.finalize()


class Keybag:
    def __init__(self, blob: bytes):
        self.type = None
        self.uuid = None
        self.wrap = None
        self.attrs: dict[bytes, object] = {}
        self.class_keys: dict[int, dict[bytes, object]] = {}
        self._parse(blob)

    def _parse(self, blob: bytes) -> None:
        current: dict[bytes, object] | None = None
        for tag, data in iter_tlv(blob):
            value: object = struct.unpack(">L", data)[0] if len(data) == 4 else data
            if tag == b"TYPE":
                self.type = value
            elif tag == b"UUID" and self.uuid is None:
                self.uuid = value
            elif tag == b"WRAP" and self.wrap is None:
                self.wrap = value
            elif tag == b"UUID":
                if current is not None and b"CLAS" in current:
                    self.class_keys[int(current[b"CLAS"])] = current
                current = {b"UUID": value}
            elif tag in _CLASSKEY_TAGS and current is not None:
                current[tag] = value
            else:
                self.attrs[tag] = value
        if current is not None and b"CLAS" in current:
            self.class_keys[int(current[b"CLAS"])] = current

    def passcode_key(self, password: str) -> bytes:
        pw = password.encode("utf-8")
        if b"DPSL" in self.attrs and b"DPIC" in self.attrs:
            pw = hashlib.pbkdf2_hmac("sha256", pw, self.attrs[b"DPSL"], int(self.attrs[b"DPIC"]), 32)
        return hashlib.pbkdf2_hmac("sha1", pw, self.attrs[b"SALT"], int(self.attrs[b"ITER"]), 32)

    def unlock(self, password: str) -> None:
        key = self.passcode_key(password)
        for ck in self.class_keys.values():
            if b"WPKY" not in ck:
                continue
            if int(ck.get(b"WRAP", 0)) & WRAP_PASSCODE:
                try:
                    ck[b"KEY"] = aes_unwrap(key, ck[b"WPKY"])
                except WrongPassword:
                    raise WrongPassword("Das Backup-Passwort ist falsch.") from None

    def unwrap_for_class(self, protection_class: int, wrapped: bytes) -> bytes:
        ck = self.class_keys.get(int(protection_class))
        if ck is None or b"KEY" not in ck:
            raise KeyError(f"Kein Schlüssel für Schutzklasse {protection_class}")
        return aes_unwrap(ck[b"KEY"], wrapped)
