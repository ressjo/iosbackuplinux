"""GUI-Test des Backup-Viewers (offscreen)."""
import time

import pytest
from PySide6.QtWidgets import QMessageBox

from backup_factory import build_backup, sample_files
from iosbackup.backups import read_backup
from iosbackup.viewer.loader import load_backup

UDID = "00008110-000A1B2C3D4E5F60"


def wait_until(qapp, cond, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(autouse=True)
def no_dialogs(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: 0))


@pytest.mark.parametrize("password", [None, "geheim"], ids=["plain", "encrypted"])
def test_viewer_window(qapp, tmp_path, password):
    from iosbackup.ui.viewer_window import BackupViewerWindow, export_items_for_nodes
    files = sample_files()
    path = build_backup(str(tmp_path / "nas"), UDID, files, password=password)
    reader, media, tree = load_backup(path, password)
    win = BackupViewerWindow(reader, media, tree, read_backup(path))
    win.show()
    win.grab()  # löst das Zeichnen und damit das Laden der Vorschaubilder aus
    model = win.photos.model
    assert model.rowCount() == 13
    assert wait_until(qapp, lambda: len(model._icons) >= 13)
    cache = tmp_path / "cache" / "iosbackuplinux" / "thumbs" / UDID
    # Vorschaubilder verschlüsselter Backups landen nie auf der Platte
    assert cache.is_dir() == (password is None)

    # Filter
    win.photos.type_combo.setCurrentIndex(2)
    assert model.rowCount() == 1
    win.photos.type_combo.setCurrentIndex(0)

    # Fotos exportieren
    win.photos.view.selectAll()
    items = [(m.entry, m.name) for m in win.photos.selected_items()]
    assert len(items) == 13
    out = tmp_path / "export"
    out.mkdir()
    win._start_export(items, str(out))
    assert wait_until(qapp, lambda: win.export_job is None, 20)
    expected = {rel.rsplit("/", 1)[-1]: data for _, rel, data, _ in files
                if data is not None and rel.startswith("Media/DCIM/")}
    assert {p.name: p.read_bytes() for p in out.iterdir()} == expected

    # Ordner im Dateibrowser exportieren (Struktur bleibt erhalten)
    apps = next(c for c in tree.children if c.name == "Apps")
    items = export_items_for_nodes([apps])
    assert [rel for _, rel in items] == [
        "Apps/net.whatsapp.WhatsApp/Documents/ChatStorage.sqlite"]

    # Suche
    win.files.search.setText("notes")
    win.files._apply_search()
    assert win.files.proxy.rowCount() >= 1

    tmp = reader._tmp
    win.close()
    qapp.processEvents()
    import os
    assert not os.path.exists(tmp)
