"""GUI-Smoke-Test mit simuliertem iPhone (QT_QPA_PLATFORM=offscreen)."""
import time

import pytest
from PySide6.QtWidgets import QMessageBox

UDID = "00008110-000A1B2C3D4E5F60"


def wait_until(qapp, cond, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def window(qapp, fake_tools, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_tools / "config"))
    monkeypatch.setenv("HOME", str(fake_tools / "home"))
    messages = []
    for name in ("information", "critical", "warning", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, _n=name, **k: messages.append((_n, a[2]))
                                         or QMessageBox.StandardButton.Yes))
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope,
                      str(fake_tools / "config"))
    from iosbackup.ui.mainwindow import MainWindow
    nas = fake_tools / "nas"
    nas.mkdir()
    win = MainWindow()
    win.settings.backup_dir = str(nas)
    win.path_edit.setText(str(nas))
    win.refresh_storage()
    win.show()
    win.messages = messages
    yield win
    win.close()


def test_backup_via_gui(qapp, window):
    assert wait_until(qapp, lambda: window.current_device() is not None)
    assert window.dev_name.text() == "Joshuas iPhone"
    assert window.btn_backup.isEnabled()
    window.start_backup()
    assert wait_until(qapp, lambda: window.job is not None and not window.job.is_running()
                      and window.btn_backup.isEnabled(), 20)
    assert ("information", "Backup erfolgreich abgeschlossen.") in window.messages
    assert wait_until(qapp, lambda: window.table.rowCount() == 1)
    assert window.table.item(0, 0).text() == "Joshuas iPhone"
    assert "Letztes Backup" in window.lbl_last_backup.text()

    # Inhalt des Backups über das Hauptfenster öffnen
    window.open_viewer(window.backups[0])
    assert wait_until(qapp, lambda: window.backups[0].path in window.viewers)
    viewer = window.viewers[window.backups[0].path]
    assert viewer.photos.model.rowCount() == 13
    viewer.close()
    assert wait_until(qapp, lambda: not window.viewers)
