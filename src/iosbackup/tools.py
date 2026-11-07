"""Aufruf der libimobiledevice-Kommandozeilenwerkzeuge.

Alle Apple-Protokolle werden von libimobiledevice erledigt; diese App ist eine
grafische Oberfläche um ``idevice_id``, ``ideviceinfo``, ``idevicepair`` und
``idevicebackup2``.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass

REQUIRED_TOOLS = ("idevice_id", "ideviceinfo", "idevicepair", "idevicebackup2")

INSTALL_HINT = "sudo pacman -S --needed libimobiledevice usbmuxd"


def missing_tools() -> list[str]:
    return [t for t in REQUIRED_TOOLS if shutil.which(t) is None]


def tool_env() -> dict[str, str]:
    """Umgebung für Tool-Aufrufe: englische Ausgabe, damit sie auswertbar bleibt."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


@dataclass
class ToolResult:
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_tool(args: list[str], timeout: float = 20) -> ToolResult:
    """Führt ein Tool kurz aus und liefert Rückgabecode und kombinierte Ausgabe."""
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=tool_env(),
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return ToolResult(127, f"{args[0]}: Programm nicht gefunden")
    except subprocess.TimeoutExpired:
        return ToolResult(124, f"{args[0]}: Zeitüberschreitung")
    return ToolResult(proc.returncode, proc.stdout.decode("utf-8", errors="replace"))


def run_plist(args: list[str], timeout: float = 20) -> dict:
    """Führt ein Tool mit XML-Plist-Ausgabe (``-x``) aus und parst das Ergebnis."""
    res = run_tool(args, timeout)
    if not res.ok:
        return {}
    text = res.output
    start = text.find("<?xml")
    if start < 0:
        return {}
    try:
        data = plistlib.loads(text[start:].encode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Befehlszeilen für idevicebackup2
# ---------------------------------------------------------------------------

def backup_cmd(udid: str, directory: str, full: bool = False) -> list[str]:
    cmd = ["idevicebackup2", "-u", udid, "backup"]
    if full:
        cmd.append("--full")
    cmd.append(directory)
    return cmd


def restore_cmd(
    udid: str,
    directory: str,
    source_udid: str | None = None,
    system: bool = True,
    settings: bool = True,
    reboot: bool = True,
    remove: bool = False,
) -> list[str]:
    cmd = ["idevicebackup2", "-u", udid]
    if source_udid and source_udid != udid:
        cmd += ["-s", source_udid]
    # -i: Passwörter werden interaktiv (über das PTY) abgefragt, nicht per Argument
    cmd += ["-i", "restore"]
    if system:
        cmd.append("--system")
    if settings:
        cmd.append("--settings")
    if remove:
        cmd.append("--remove")
    if not reboot:
        cmd.append("--no-reboot")
    cmd.append(directory)
    return cmd


def encryption_cmd(udid: str, enable: bool) -> list[str]:
    return ["idevicebackup2", "-u", udid, "-i", "encryption", "on" if enable else "off"]


def changepw_cmd(udid: str) -> list[str]:
    return ["idevicebackup2", "-u", udid, "-i", "changepw"]


def pair_cmd(udid: str) -> list[str]:
    return ["idevicepair", "-u", udid, "pair"]
