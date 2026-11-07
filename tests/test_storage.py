from iosbackup.storage import ERROR, OK, WARN, check_storage, find_mount, suggest_expected_mount


def write_mounts(tmp_path, lines):
    p = tmp_path / "mounts"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def test_find_mount_longest_prefix(tmp_path):
    m = write_mounts(tmp_path, [
        "/dev/sda1 / btrfs rw 0 0",
        "systemd-1 /mnt/nas autofs rw 0 0",
        "//nas/backup /mnt/nas cifs rw 0 0",
        "//nas/x /mnt/nas\\040share cifs rw 0 0",
    ])
    assert find_mount("/mnt/nas/iphone", m) == ("/mnt/nas", "cifs")
    assert find_mount("/mnt/nas share/a", m) == ("/mnt/nas share", "cifs")
    assert find_mount("/home/user", m) == ("/", "btrfs")
    assert find_mount("/mnt/nasx", m) == ("/", "btrfs")


def test_suggest_expected_mount(tmp_path):
    m = write_mounts(tmp_path, ["/dev/sda1 / btrfs rw 0 0", "nas:/b /srv/nas nfs4 rw 0 0"])
    assert suggest_expected_mount("/srv/nas/ios", m) == "/srv/nas"
    assert suggest_expected_mount("/home/user/x", m) is None


def test_check_storage_ok(tmp_path):
    st = check_storage(str(tmp_path))
    assert st.level == OK
    assert st.writable
    assert st.free_bytes is not None


def test_check_storage_missing_and_create(tmp_path):
    target = tmp_path / "a" / "b"
    assert check_storage(str(target)).level == ERROR
    st = check_storage(str(target), create=True)
    assert st.level == OK and target.is_dir()


def test_check_storage_not_mounted(tmp_path):
    m = write_mounts(tmp_path, ["/dev/sda1 / btrfs rw 0 0"])
    st = check_storage(str(tmp_path), expected_mount="/mnt/nas", mounts_file=m)
    assert st.level == ERROR
    assert "nicht eingehängt" in st.message


def test_check_storage_autofs_not_mounted(tmp_path):
    m = write_mounts(tmp_path, ["/dev/sda1 / btrfs rw 0 0", "systemd-1 /mnt/nas autofs rw 0 0"])
    st = check_storage(str(tmp_path), expected_mount="/mnt/nas", mounts_file=m)
    assert st.level == ERROR


def test_check_storage_low_space(tmp_path):
    st = check_storage(str(tmp_path), required_bytes=10**18)
    assert st.level == WARN
