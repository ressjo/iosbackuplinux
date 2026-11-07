"""Parsing the output of idevicebackup2 / idevicepair."""

from __future__ import annotations

import re
from dataclasses import dataclass

# "[=====                    ]  12% (1.2 MB/3.4 GB)"  or  "... 100% Finished"
_PROGRESS_RE = re.compile(r"\[[= >#-]*\]\s*(\d{1,3}(?:\.\d+)?)\s*%\s*(.*)$")
_SIZES_RE = re.compile(r"\(\s*([^/()]+?)\s*/\s*([^()]+?)\s*\)")
_PROMPT_RE = re.compile(r"([^\n:]*password[^:\n]*):\s*$", re.IGNORECASE)


@dataclass
class Progress:
    percent: float
    detail: str = ""


def parse_progress(text: str) -> Progress | None:
    m = _PROGRESS_RE.search(text.strip())
    if not m:
        return None
    pct = max(0.0, min(100.0, float(m.group(1))))
    rest = m.group(2).strip()
    detail = ""
    sm = _SIZES_RE.search(rest)
    if sm:
        detail = f"{sm.group(1)} / {sm.group(2)}"
    return Progress(pct, detail)


def find_password_prompt(text: str) -> str | None:
    """Return the prompt text if ``text`` ends with a password prompt."""
    m = _PROMPT_RE.search(text)
    return m.group(1).strip() if m else None


def prompt_role(prompt: str) -> str:
    """Classify a password prompt as ``new``, ``old`` or ``default``."""
    p = prompt.lower()
    if "new" in p:
        return "new"
    if "old" in p or "current" in p:
        return "old"
    return "default"


_STATUS_MAP = [
    ("Requesting backup from device", "Requesting backup from device…"),
    ("Starting backup", "Starting backup…"),
    ("Full backup mode", "Full backup"),
    ("Incremental backup mode", "Incremental backup"),
    ("Backup will be encrypted", "Backup will be encrypted"),
    ("Backup will be unencrypted", "Backup will not be encrypted"),
    ("Receiving files", "Receiving files from device…"),
    ("Sending files", "Sending files to device…"),
    ("Starting Restore", "Starting restore…"),
    ("Restoring", "Restoring…"),
    ("Please enter passcode", "Enter the passcode on the device"),
    ("enter the passcode", "Enter the passcode on the device"),
    ("Waiting for device", "Waiting for device…"),
    ("Backup Successful", "Backup completed"),
    ("Restore Successful", "Restore completed"),
]


def translate_status(line: str) -> str | None:
    low = line.lower()
    for needle, text in _STATUS_MAP:
        if needle.lower() in low:
            return text
    return None


SUCCESS_MARKERS = {
    "backup": ["Backup Successful"],
    "restore": ["Restore Successful"],
    "encryption": ["has been enabled successfully", "has been disabled successfully"],
    "changepw": ["changed successfully"],
    "pair": ["SUCCESS: Paired"],
}

_FAILURE_RE = re.compile(r"(^|\n)\s*ERROR|Failed|Aborted|Aborting", re.IGNORECASE)


def is_success(kind: str, returncode: int, output: str) -> bool:
    if any(m in output for m in SUCCESS_MARKERS.get(kind, [])):
        return True
    return returncode == 0 and not _FAILURE_RE.search(output)


_ERRORS = [
    (("find my", "findmy", "find my iphone"),
     "Find My is turned on. Turn it off on the device under Settings → [your name] → "
     "Find My, then try again."),
    (("user denied", "denied the trust"),
     "Pairing was declined on the device. Unplug and reconnect it, then tap “Trust”."),
    (("trust dialog", "pairing dialog response pending"),
     "Tap “Trust” on the device when asked “Trust This Computer?”."),
    (("passcode", "password protected"),
     "The device is locked or asking for its passcode. Unlock it, enter the passcode "
     "and try again."),
    (("unable to retrieve device list", "usbmuxd"),
     "The usbmuxd service is not running. Check with: sudo systemctl status usbmuxd"),
    (("no device found",),
     "Device not found. Check the USB cable and connection."),
    (("not paired",),
     "The device is not paired with this computer. Pair it first."),
    (("no space left", "insufficient free disk space", "not enough space"),
     "The backup location is out of free space."),
    (("permission denied", "operation not permitted"),
     "No write permission in the backup location. For a network share, check the "
     "mount options (uid=/gid=) and share permissions."),
    (("wrong password", "incorrect password", "invalid password", "bad password",
      "password is incorrect", "passwords do not match", "wrong backup password"),
     "The backup password is incorrect."),
    (("a backup password is required", "no backup password given"),
     "This operation requires the backup password."),
    (("cannot open backup directory", "backup directory does not exist",
      "not found in backup directory", "backup directory is not a directory"),
     "The backup was not found in the backup location."),
    (("aborted", "aborting", "cancelled", "canceled"),
     "The operation was cancelled."),
]


def explain_failure(output: str, returncode: int | None = None) -> str:
    low = output.lower()
    for needles, text in _ERRORS:
        if any(n in low for n in needles):
            return text
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in reversed(lines):
        m = re.match(r"ErrorCode\s+(\d+):\s*(.+)", ln)
        if m:
            return f"{m.group(2)} (error code {m.group(1)})"
    for ln in reversed(lines):
        if ln.upper().startswith("ERROR") or "failed" in ln.lower():
            return ln
    if lines:
        return lines[-1]
    if returncode is not None:
        return f"Unknown error (exit code {returncode})."
    return "Unknown error."
