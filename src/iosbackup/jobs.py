"""Lang laufende Vorgänge (Backup, Restore, Verschlüsselung) im Hintergrund.

idevicebackup2 wird in einem Pseudo-Terminal gestartet. Dadurch
  * kommt die Fortschrittsanzeige sofort (ungepuffert) an,
  * können Passwörter über die interaktive Abfrage (``-i``) eingegeben werden,
    ohne dass sie als Kommandozeilenargument in ``ps`` sichtbar sind.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import termios
import threading
import time

from PySide6.QtCore import QObject, Signal

from .progress import (explain_failure, find_password_prompt, is_success,
                       parse_progress, prompt_role, translate_status)
from .tools import tool_env


class ToolJob(QObject):
    line = Signal(str)               # vollständige Ausgabezeile (fürs Protokoll)
    progress = Signal(float, str)    # Prozent, Detail (z. B. "1.2 GB / 30 GB")
    status = Signal(str)             # menschenlesbarer Status
    finished = Signal(bool, str)     # Erfolg, Meldung

    def __init__(self, kind: str, args: list[str], passwords: dict[str, str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.kind = kind
        self.args = args
        self.passwords = {k: v for k, v in (passwords or {}).items() if v}
        self.output = ""
        self.returncode: int | None = None
        self.cancelled = False
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._done = threading.Event()

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"job-{self.kind}", daemon=True)
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread is not None and not self._done.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def cancel(self) -> None:
        """Bricht sauber ab (SIGINT wie Strg+C), notfalls hart."""
        self.cancelled = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return

        def _escalate():
            for sig, delay in ((signal.SIGINT, 15), (signal.SIGTERM, 5), (signal.SIGKILL, 0)):
                if proc.poll() is not None:
                    return
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    return
                end = time.monotonic() + delay
                while delay and time.monotonic() < end:
                    if proc.poll() is not None:
                        return
                    time.sleep(0.2)

        threading.Thread(target=_escalate, daemon=True).start()

    # ------------------------------------------------------------------
    def _redact(self, text: str) -> str:
        for pw in self.passwords.values():
            if pw:
                text = text.replace(pw, "••••••")
        return text

    def _answer_for(self, prompt: str) -> str | None:
        role = prompt_role(prompt)
        for key in (role, "default", "new", "old"):
            if key in self.passwords:
                return self.passwords[key]
        return None

    def _run(self) -> None:
        ok, message = False, ""
        try:
            ok, message = self._execute()
        except Exception as exc:  # pragma: no cover - Sicherheitsnetz
            ok, message = False, f"Interner Fehler: {exc}"
        finally:
            self._done.set()
            self.finished.emit(ok, message)

    def _execute(self) -> tuple[bool, str]:
        master, slave = pty.openpty()
        # Echo abschalten, damit eingegebene Passwörter nie zurückgespiegelt werden
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        try:
            self._proc = subprocess.Popen(
                self.args, stdin=slave, stdout=slave, stderr=slave,
                env=tool_env(), start_new_session=True, close_fds=True,
            )
        except FileNotFoundError:
            os.close(master)
            os.close(slave)
            return False, f"{self.args[0]} wurde nicht gefunden. Bitte libimobiledevice installieren."
        os.close(slave)

        current = ""
        last_status = ""
        unanswered_prompt = False

        def handle_segment(seg: str, complete: bool) -> None:
            nonlocal last_status
            if not seg.strip():
                return
            prog = parse_progress(seg)
            if prog is not None:
                self.progress.emit(prog.percent, prog.detail)
                return
            if complete:
                self.line.emit(self._redact(seg))
                st = translate_status(seg)
                if st and st != last_status:
                    last_status = st
                    self.status.emit(st)

        while True:
            try:
                r, _, _ = select.select([master], [], [], 0.25)
            except InterruptedError:
                continue
            if not r:
                if self._proc.poll() is not None:
                    # Restdaten abholen
                    try:
                        r, _, _ = select.select([master], [], [], 0.1)
                    except InterruptedError:
                        r = []
                    if not r:
                        break
                else:
                    continue
            try:
                data = os.read(master, 4096)
            except OSError:
                break  # EIO: Kindprozess hat das Terminal geschlossen
            if not data:
                break
            text = data.decode("utf-8", errors="replace")
            self.output += text
            for ch in text:
                if ch == "\n":
                    handle_segment(current, True)
                    current = ""
                elif ch == "\r":
                    # Fortschrittsbalken überschreiben die Zeile mit \r
                    if parse_progress(current) is not None:
                        handle_segment(current, False)
                        current = ""
                else:
                    current += ch
            if current:
                prompt = find_password_prompt(current)
                if prompt:
                    self.line.emit(current.strip())
                    current = ""
                    answer = self._answer_for(prompt)
                    if answer is None:
                        unanswered_prompt = True
                        self.cancel()
                    else:
                        self.status.emit("Passwort wird übermittelt …")
                        os.write(master, answer.encode("utf-8") + b"\n")
                else:
                    prog = parse_progress(current)
                    if prog is not None:
                        self.progress.emit(prog.percent, prog.detail)
        if current:
            handle_segment(current, True)
        os.close(master)
        self.returncode = self._proc.wait()
        self.output = self._redact(self.output)

        if self.cancelled and not unanswered_prompt:
            return False, "Der Vorgang wurde abgebrochen."
        if unanswered_prompt:
            return False, "Das Programm hat unerwartet nach einem Passwort gefragt."
        if is_success(self.kind, self.returncode, self.output):
            return True, ""
        return False, explain_failure(self.output, self.returncode)
