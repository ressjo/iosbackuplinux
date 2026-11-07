"""Exporting files from a backup into a regular folder."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Signal

from .reader import BackupReader, Entry


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def safe_name(name: str) -> str:
    name = name.replace("/", "_").replace("\0", "")
    return name if name not in ("", ".", "..") else "_"


class ExportJob(QObject):
    """items: list of (Entry, relative destination path)."""

    progress = Signal(int, int, str)
    finished = Signal(int, list, bool)  # number ok, errors, cancelled

    def __init__(self, reader: BackupReader, items: list[tuple[Entry, str]], dest_dir: str,
                 parent=None):
        super().__init__(parent)
        self.reader = reader
        self.items = items
        self.dest_dir = dest_dir
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        ok, errors = 0, []
        total = len(self.items)
        for i, (entry, rel) in enumerate(self.items):
            if self.cancel_event.is_set():
                break
            self.progress.emit(i, total, rel)
            parts = [safe_name(p) for p in rel.split("/") if p]
            dest = os.path.join(self.dest_dir, *parts)
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                self.reader.export(entry, unique_path(dest), self.cancel_event)
                ok += 1
            except InterruptedError:
                break
            except Exception as exc:
                errors.append(f"{rel}: {exc}")
        self.progress.emit(total, total, "")
        self.finished.emit(ok, errors, self.cancel_event.is_set())
