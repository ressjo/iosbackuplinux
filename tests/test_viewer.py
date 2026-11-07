import os
import threading

import pytest

from backup_factory import build_backup, sample_files
from iosbackup.viewer.export import unique_path
from iosbackup.viewer.keybag import WrongPassword
from iosbackup.viewer.media import collect_media, month_label
from iosbackup.viewer.reader import BackupReader, UnsupportedBackup
from iosbackup.viewer.tree import build_tree, domain_location

UDID = "00008110-000A1B2C3D4E5F60"


@pytest.fixture(params=[None, "geheim"], ids=["plain", "encrypted"])
def backup(request, tmp_path):
    files = sample_files()
    path = build_backup(str(tmp_path / "nas"), UDID, files, password=request.param)
    return path, request.param, files


def open_reader(path, password):
    r = BackupReader(path)
    r.open(password)
    return r


def test_reader_roundtrip(backup, tmp_path):
    path, password, files = backup
    r = open_reader(path, password)
    try:
        assert r.encrypted == bool(password)
        by_path = {(e.domain, e.path): e for e in r.entries}
        for domain, rel, data, mtime in files:
            e = by_path[(domain, rel)]
            if data is None:
                assert e.is_dir
                continue
            assert r.read_bytes(e) == data
            meta = r.meta(e)
            assert meta.size == len(data) and meta.mtime == mtime
            dest = tmp_path / "out" / e.name
            dest.parent.mkdir(exist_ok=True)
            r.export(e, str(dest))
            assert dest.read_bytes() == data
            assert int(os.path.getmtime(dest)) == mtime
        tmp = r._tmp
    finally:
        r.close()
    assert not os.path.exists(tmp)


def test_wrong_password(tmp_path):
    path = build_backup(str(tmp_path), UDID, sample_files(2), password="richtig")
    with pytest.raises(WrongPassword):
        open_reader(path, "falsch")
    with pytest.raises(WrongPassword):
        open_reader(path, None)


def test_large_file_chunked_export(tmp_path):
    data = os.urandom(3 * 1024 * 1024 + 7)
    files = [("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.MOV", data, 1_700_000_000)]
    path = build_backup(str(tmp_path), UDID, files, password="pw")
    r = open_reader(path, "pw")
    out = tmp_path / "big.mov"
    r.export(r.entries[0], str(out))
    assert out.read_bytes() == data
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        r.export(r.entries[0], str(tmp_path / "c.mov"), cancel)
    assert not (tmp_path / "c.mov").exists()
    r.close()


def test_media_collection(backup):
    path, password, _ = backup
    r = open_reader(path, password)
    items = collect_media(r)
    assert len(items) == 13
    assert items[0].name == "IMG_0999.MOV" and items[0].is_video
    assert all(items[i].mtime >= items[i + 1].mtime for i in range(len(items) - 1))
    assert month_label("2026-03") == "März 2026"
    r.close()


def test_tree(backup):
    path, password, _ = backup
    r = open_reader(path, password)
    root = build_tree(r.entries)
    names = [c.name for c in root.children]
    assert names[:3] == ["Kamerarolle", "Medien", "Apps"]
    apps = root.children[2]
    assert apps.children[0].name == "net.whatsapp.WhatsApp"
    camera = root.children[0]
    assert sum(1 for _ in camera.iter_files()) == 13
    assert domain_location("AppDomainGroup-group.x") == ("group.x", "App-Gruppen")
    r.close()


def test_unique_path(tmp_path):
    p = tmp_path / "a.jpg"
    assert unique_path(str(p)) == str(p)
    p.write_bytes(b"")
    assert unique_path(str(p)) == str(tmp_path / "a (2).jpg")


def test_unsupported(tmp_path):
    d = tmp_path / "old"
    d.mkdir()
    (d / "Manifest.plist").write_bytes(b"")
    with pytest.raises(UnsupportedBackup):
        BackupReader(str(d))
