from iosbackup.progress import (explain_failure, find_password_prompt, is_success,
                                parse_progress, prompt_role, translate_status)


def test_parse_progress_with_sizes():
    p = parse_progress("[=====                                             ]  10% (12.3 MB/1.2 GB)")
    assert p.percent == 10
    assert p.detail == "12.3 MB / 1.2 GB"


def test_parse_progress_finished():
    p = parse_progress("[" + "=" * 50 + "] 100% Finished")
    assert p.percent == 100
    assert p.detail == ""


def test_parse_progress_none():
    assert parse_progress("Receiving files") is None


def test_password_prompts():
    assert find_password_prompt("Enter backup password: ") == "Enter backup password"
    assert find_password_prompt("Enter new backup password (repeat): ")
    assert find_password_prompt("Backup will be encrypted.") is None
    assert prompt_role("Enter new backup password (repeat)") == "new"
    assert prompt_role("Enter current backup password") == "old"
    assert prompt_role("Enter old backup password") == "old"
    assert prompt_role("Enter backup password") == "default"


def test_success_detection():
    assert is_success("backup", 0, "...\nBackup Successful.\n")
    assert not is_success("backup", 255, "Backup Failed (Error Code 105).")
    assert not is_success("backup", 0, "ERROR: Could not connect")
    assert is_success("encryption", 0, "Backup encryption has been enabled successfully.")


def test_explain_failure():
    assert "Wo ist" in explain_failure("ErrorCode 211: Find My iPhone must be disabled")
    assert "Vertrauen" in explain_failure(
        "ERROR: Could not connect to lockdownd: Pairing dialog response pending (-19)")
    assert "usbmuxd" in explain_failure("ERROR: Unable to retrieve device list!")
    assert "falsch" in explain_failure("ErrorCode 207: Wrong password")
    assert explain_failure("something odd happened") == "something odd happened"


def test_translate_status():
    assert translate_status("Receiving files") == "Dateien werden vom Gerät empfangen …"
    assert translate_status("random") is None


def test_explain_prefers_device_error_description():
    out = "ErrorCode 999: Something specific\nBackup Failed (Error Code 999).\n"
    assert explain_failure(out) == "Something specific (Fehlercode 999)"
