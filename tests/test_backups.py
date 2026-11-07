import plistlib
from datetime import datetime

import pytest

from iosbackup.backups import backup_size, delete_backup, read_backup, scan_backups


def make_backup(root, udid, name="iPhone", encrypted=False, finished=True,
                date=datetime(2026, 11, 1, 12, 0)):
    d = root / udid
    d.mkdir(parents=True)
    with open(d / "Info.plist", "wb") as fh:
        plistlib.dump({"Device Name": name, "Product Type": "iPhone15,2",
                       "Product Version": "18.6", "Target Identifier": udid,
                       "Last Backup Date": date}, fh)
    with open(d / "Manifest.plist", "wb") as fh:
        plistlib.dump({"IsEncrypted": encrypted}, fh, fmt=plistlib.FMT_BINARY)
    with open(d / "Status.plist", "wb") as fh:
        plistlib.dump({"SnapshotState": "finished" if finished else "new"}, fh,
                      fmt=plistlib.FMT_BINARY)
    (d / "aa").mkdir()
    (d / "aa" / "file").write_bytes(b"x" * 1000)
    return d


def test_read_and_scan(tmp_path):
    make_backup(tmp_path, "UDID-A", "Phone A", date=datetime(2026, 1, 1))
    make_backup(tmp_path, "UDID-B", "iPad B", encrypted=True, finished=False,
                date=datetime(2026, 6, 1))
    (tmp_path / "not-a-backup").mkdir()
    entries = scan_backups(str(tmp_path))
    assert [e.udid for e in entries] == ["UDID-B", "UDID-A"]  # newest first
    b = entries[0]
    assert b.device_name == "iPad B"
    assert b.encrypted is True
    assert b.complete is False
    assert b.last_backup.year == 2026
    assert entries[1].complete is True


def test_scan_missing_root(tmp_path):
    assert scan_backups(str(tmp_path / "missing")) == []


def test_size(tmp_path):
    d = make_backup(tmp_path, "UDID-A")
    assert backup_size(str(d)) >= 1000


def test_delete_safety(tmp_path):
    d = make_backup(tmp_path, "UDID-A")
    entry = read_backup(str(d))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError):
        delete_backup(entry, str(other))
    delete_backup(entry, str(tmp_path))
    assert not d.exists()
