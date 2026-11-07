"""Auswertung der Ausgabe von idevicebackup2 / idevicepair."""

from __future__ import annotations

import re
from dataclasses import dataclass

# "[=====                    ]  12% (1.2 MB/3.4 GB)"  bzw. "... 100% Finished"
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
    """Liefert den Prompt-Text, wenn ``text`` mit einer Passwortabfrage endet."""
    m = _PROMPT_RE.search(text)
    return m.group(1).strip() if m else None


def prompt_role(prompt: str) -> str:
    """Ordnet eine Passwortabfrage einer Rolle zu: ``new``, ``old`` oder ``default``."""
    p = prompt.lower()
    if "new" in p:
        return "new"
    if "old" in p or "current" in p:
        return "old"
    return "default"


_STATUS_MAP = [
    ("Requesting backup from device", "Backup wird vom Gerät angefordert …"),
    ("Starting backup", "Backup wird gestartet …"),
    ("Full backup mode", "Vollständiges Backup"),
    ("Incremental backup mode", "Inkrementelles Backup"),
    ("Backup will be encrypted", "Backup wird verschlüsselt"),
    ("Backup will be unencrypted", "Backup wird unverschlüsselt erstellt"),
    ("Receiving files", "Dateien werden vom Gerät empfangen …"),
    ("Sending files", "Dateien werden an das Gerät gesendet …"),
    ("Starting Restore", "Wiederherstellung wird gestartet …"),
    ("Restoring", "Wiederherstellung läuft …"),
    ("Please enter passcode", "Bitte den Code auf dem Gerät eingeben"),
    ("enter the passcode", "Bitte den Code auf dem Gerät eingeben"),
    ("Waiting for device", "Warte auf Gerät …"),
    ("Backup Successful", "Backup erfolgreich abgeschlossen"),
    ("Restore Successful", "Wiederherstellung erfolgreich abgeschlossen"),
]


def translate_status(line: str) -> str | None:
    low = line.lower()
    for needle, german in _STATUS_MAP:
        if needle.lower() in low:
            return german
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
     "„Wo ist?“ / „Mein iPhone suchen“ ist eingeschaltet. Bitte auf dem Gerät unter "
     "Einstellungen → [Dein Name] → Wo ist? deaktivieren und erneut versuchen."),
    (("user denied", "denied the trust"),
     "Die Kopplung wurde auf dem Gerät abgelehnt. Gerät ab- und wieder anstecken und "
     "„Vertrauen“ wählen."),
    (("trust dialog", "pairing dialog response pending"),
     "Bitte auf dem Gerät „Diesem Computer vertrauen?“ mit „Vertrauen“ bestätigen."),
    (("passcode", "password protected"),
     "Das Gerät ist gesperrt bzw. verlangt den Gerätecode. Bitte entsperren, Code "
     "eingeben und erneut versuchen."),
    (("unable to retrieve device list", "usbmuxd"),
     "Der Dienst usbmuxd läuft nicht. Bitte prüfen: sudo systemctl status usbmuxd"),
    (("no device found",),
     "Gerät nicht gefunden – bitte USB-Kabel und Verbindung prüfen."),
    (("not paired",),
     "Gerät ist nicht mit diesem Computer gekoppelt. Bitte zuerst koppeln."),
    (("no space left", "insufficient free disk space", "not enough space"),
     "Kein freier Speicherplatz mehr am Ziel (NAS/Platte voll)."),
    (("permission denied", "operation not permitted"),
     "Keine Schreibrechte im Speicherort. Bei einem NAS-Mount die Optionen uid=/gid= "
     "bzw. die Freigaberechte prüfen."),
    (("wrong password", "incorrect password", "invalid password", "bad password",
      "password is incorrect", "passwords do not match", "wrong backup password"),
     "Das Backup-Passwort ist falsch."),
    (("a backup password is required", "no backup password given"),
     "Für diesen Vorgang wird das Backup-Passwort benötigt."),
    (("cannot open backup directory", "backup directory does not exist",
      "not found in backup directory", "backup directory is not a directory"),
     "Das Backup wurde im Speicherort nicht gefunden."),
    (("aborted", "aborting", "cancelled", "canceled"),
     "Der Vorgang wurde abgebrochen."),
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
            return f"{m.group(2)} (Fehlercode {m.group(1)})"
    for ln in reversed(lines):
        if ln.upper().startswith("ERROR") or "failed" in ln.lower():
            return ln
    if lines:
        return lines[-1]
    if returncode is not None:
        return f"Unbekannter Fehler (Rückgabecode {returncode})."
    return "Unbekannter Fehler."
