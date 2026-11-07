"""Öffnet ein Backup komplett im Hintergrund (Entschlüsseln, Einlesen, Fotos, Baum)."""

from __future__ import annotations

from .media import collect_media
from .reader import BackupReader
from .tree import build_tree


def load_backup(path: str, password: str | None):
    reader = BackupReader(path)
    try:
        reader.open(password)
        media = collect_media(reader)
        tree = build_tree(reader.entries)
    except BaseException:
        reader.close()
        raise
    return reader, media, tree
