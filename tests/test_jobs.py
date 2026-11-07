import os
import plistlib

from iosbackup import tools
from iosbackup.backups import scan_backups
from iosbackup.devices import PAIRED, PENDING_TRUST, fetch_device_info, list_devices
from iosbackup.jobs import ToolJob

UDID = "00008110-000A1B2C3D4E5F60"


def run_job(qapp, job, timeout=20):
    events = {"progress": [], "lines": [], "status": []}
    job.progress.connect(lambda p, d: events["progress"].append((p, d)))
    job.line.connect(events["lines"].append)
    job.status.connect(events["status"].append)
    result = {}
    job.finished.connect(lambda ok, msg: result.update(ok=ok, msg=msg))
    job.start()
    assert job.wait(timeout)
    qapp.processEvents()
    return result, events


def test_devices(fake_tools):
    udids, err = list_devices()
    assert udids == [UDID] and err is None
    info = fetch_device_info(UDID)
    assert info.pairing == PAIRED
    assert info.name == "Joshuas iPhone"
    assert info.model == "iPhone 14 Pro"
    assert info.used_bytes == 255_000_000_000 - 143_500_000_000
    assert info.will_encrypt is False


def test_unpaired(fake_tools, monkeypatch):
    monkeypatch.setenv("FAKE_UNPAIRED", "1")
    assert fetch_device_info(UDID).pairing == PENDING_TRUST


def test_backup_job(qapp, fake_tools):
    dest = fake_tools / "nas"
    dest.mkdir()
    res, ev = run_job(qapp, ToolJob("backup", tools.backup_cmd(UDID, str(dest))))
    assert res["ok"], res
    assert ev["progress"] and ev["progress"][-1][0] == 100
    assert any(d == "2.4 GB / 2.4 GB" for _, d in ev["progress"])
    assert "Dateien werden vom Gerät empfangen …" in ev["status"]
    entries = scan_backups(str(dest))
    assert len(entries) == 1 and entries[0].udid == UDID and entries[0].complete


def test_backup_failure(qapp, fake_tools, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "backup")
    dest = fake_tools / "nas"
    dest.mkdir()
    res, _ = run_job(qapp, ToolJob("backup", tools.backup_cmd(UDID, str(dest))))
    assert not res["ok"]
    assert "Speicherplatz" in res["msg"]


def test_encryption_and_encrypted_restore(qapp, fake_tools):
    # Verschlüsselung einschalten – Passwort geht über das PTY, nicht über argv
    job = ToolJob("encryption", tools.encryption_cmd(UDID, True), {"new": "geheim123"})
    assert "geheim123" not in " ".join(job.args)
    res, ev = run_job(qapp, job)
    assert res["ok"], (res, job.output)
    assert "geheim123" not in job.output
    assert all("geheim123" not in line for line in ev["lines"])
    assert fetch_device_info(UDID).will_encrypt is True

    dest = fake_tools / "nas"
    dest.mkdir()
    res, _ = run_job(qapp, ToolJob("backup", tools.backup_cmd(UDID, str(dest))))
    assert res["ok"]
    assert scan_backups(str(dest))[0].encrypted is True

    # falsches Passwort
    res, _ = run_job(qapp, ToolJob("restore", tools.restore_cmd(UDID, str(dest), UDID),
                                   {"default": "falsch", "old": "falsch"}))
    assert not res["ok"] and "falsch" in res["msg"]

    # richtiges Passwort
    res, ev = run_job(qapp, ToolJob("restore", tools.restore_cmd(UDID, str(dest), UDID),
                                    {"default": "geheim123", "old": "geheim123"}))
    assert res["ok"], res
    assert ev["progress"][-1][0] == 100

    # Passwort ändern, dann ausschalten
    res, _ = run_job(qapp, ToolJob("changepw", tools.changepw_cmd(UDID),
                                   {"old": "geheim123", "new": "neu456"}))
    assert res["ok"], res
    res, _ = run_job(qapp, ToolJob("encryption", tools.encryption_cmd(UDID, False),
                                   {"default": "neu456", "old": "neu456"}))
    assert res["ok"], res
    assert fetch_device_info(UDID).will_encrypt is False


def test_restore_findmy(qapp, fake_tools, monkeypatch):
    dest = fake_tools / "nas"
    (dest / UDID).mkdir(parents=True)
    with open(dest / UDID / "Manifest.plist", "wb") as fh:
        plistlib.dump({"IsEncrypted": False}, fh)
    monkeypatch.setenv("FAKE_FAIL", "findmy")
    res, _ = run_job(qapp, ToolJob("restore", tools.restore_cmd(UDID, str(dest), UDID)))
    assert not res["ok"] and "Wo ist" in res["msg"]


def test_missing_password_does_not_hang(qapp, fake_tools):
    job = ToolJob("encryption", tools.encryption_cmd(UDID, True), {})
    res, _ = run_job(qapp, job, timeout=30)
    assert not res["ok"]


def test_cancel(qapp, fake_tools, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "0.2")
    dest = fake_tools / "nas"
    dest.mkdir()
    job = ToolJob("backup", tools.backup_cmd(UDID, str(dest)))
    result = {}
    job.finished.connect(lambda ok, msg: result.update(ok=ok, msg=msg))
    job.start()
    import time
    time.sleep(0.6)
    job.cancel()
    assert job.wait(20)
    qapp.processEvents()
    assert job.cancelled
    assert not os.path.exists(dest / UDID / "Status.plist")


def test_restore_cmd_options():
    cmd = tools.restore_cmd("T", "/b", source_udid="S", reboot=False, remove=True)
    assert cmd[:5] == ["idevicebackup2", "-u", "T", "-s", "S"]
    assert "-i" in cmd and "--no-reboot" in cmd and "--remove" in cmd and cmd[-1] == "/b"
    assert "-s" not in tools.restore_cmd("T", "/b", source_udid="T")
