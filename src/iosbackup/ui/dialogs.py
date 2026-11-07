"""Dialoge: Passworteingabe und Wiederherstellung."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QLabel, QLineEdit, QMessageBox,
                               QVBoxLayout)

from ..backups import BackupEntry
from ..devices import DeviceInfo, model_name
from ..fmt import format_datetime


class PasswordDialog(QDialog):
    """mode: ``new`` (Verschlüsselung einschalten), ``current`` (ausschalten /
    Restore) oder ``change`` (Passwort ändern)."""

    def __init__(self, mode: str, parent=None, text: str = ""):
        super().__init__(parent)
        self.mode = mode
        titles = {
            "new": "Backup-Verschlüsselung einschalten",
            "current": "Backup-Passwort",
            "change": "Backup-Passwort ändern",
        }
        self.setWindowTitle(titles[mode])
        layout = QVBoxLayout(self)
        if text:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
        form = QFormLayout()
        layout.addLayout(form)

        def pw_field():
            f = QLineEdit()
            f.setEchoMode(QLineEdit.EchoMode.Password)
            f.textChanged.connect(self._validate)
            return f

        self.old = pw_field() if mode in ("current", "change") else None
        self.new = pw_field() if mode in ("new", "change") else None
        self.repeat = pw_field() if mode in ("new", "change") else None
        if self.old is not None:
            form.addRow("Aktuelles Passwort:" if mode == "change" else "Passwort:", self.old)
        if self.new is not None:
            form.addRow("Neues Passwort:", self.new)
            form.addRow("Wiederholen:", self.repeat)
            hint = QLabel("Wichtig: Ohne dieses Passwort lässt sich ein verschlüsseltes "
                          "Backup nicht wiederherstellen. Es wird nicht gespeichert.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(hint)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b;")
        layout.addWidget(self.error)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()
        self.resize(420, self.sizeHint().height())

    def _validate(self) -> None:
        ok, msg = True, ""
        if self.old is not None and not self.old.text():
            ok = False
        if self.new is not None:
            if not self.new.text():
                ok = False
            elif self.new.text() != self.repeat.text():
                ok, msg = False, "Die Passwörter stimmen nicht überein." if self.repeat.text() else ""
        self.error.setText(msg)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def passwords(self) -> dict[str, str]:
        result = {}
        if self.old is not None:
            result["old" if self.mode == "change" else "default"] = self.old.text()
            if self.mode == "current":
                result["old"] = self.old.text()
        if self.new is not None:
            result["new"] = self.new.text()
        return result


class RestoreDialog(QDialog):
    def __init__(self, target: DeviceInfo, backups: list[BackupEntry],
                 preselect: BackupEntry | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Backup auf „{target.display_name}“ wiederherstellen")
        self.target = target
        self.backups = backups
        layout = QVBoxLayout(self)

        warn = QLabel(
            "<b>Achtung:</b> Die Daten und Einstellungen auf dem Gerät werden durch den "
            "Stand des Backups ersetzt. Das Gerät startet danach neu.<br><br>"
            "Voraussetzungen:<ul>"
            "<li>„Wo ist?“ / „Mein iPhone suchen“ ist <b>ausgeschaltet</b> "
            "(Einstellungen → [Name] → Wo ist?)</li>"
            "<li>Das Gerät ist entsperrt und bleibt während des Vorgangs angeschlossen.</li>"
            "<li>Die iOS-Version des Geräts ist gleich oder neuer als im Backup.</li></ul>")
        warn.setWordWrap(True)
        layout.addWidget(warn)

        form = QFormLayout()
        layout.addLayout(form)
        self.combo = QComboBox()
        for b in backups:
            label = (f"{b.device_name or b.udid} – {model_name(b.product_type)} – "
                     f"iOS {b.product_version or '?'} – {format_datetime(b.last_backup)}")
            if b.encrypted:
                label += " 🔒"
            if b.complete is False:
                label += " (unvollständig)"
            self.combo.addItem(label)
        idx = 0
        if preselect is not None and preselect in backups:
            idx = backups.index(preselect)
        else:
            for i, b in enumerate(backups):
                if b.udid == target.udid:
                    idx = i
                    break
        self.combo.setCurrentIndex(idx)
        form.addRow("Backup:", self.combo)

        self.system = QCheckBox("Systemdateien wiederherstellen (empfohlen)")
        self.system.setChecked(True)
        self.settings = QCheckBox("Geräteeinstellungen wiederherstellen (empfohlen)")
        self.settings.setChecked(True)
        self.remove = QCheckBox("Daten entfernen, die nicht im Backup enthalten sind")
        self.reboot = QCheckBox("Gerät danach neu starten (empfohlen)")
        self.reboot.setChecked(True)
        for cb in (self.system, self.settings, self.remove, self.reboot):
            layout.addWidget(cb)

        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.combo.currentIndexChanged.connect(self._update_info)
        self._update_info()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.addButton("Wiederherstellen",
                                           QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, self.sizeHint().height())

    def selected(self) -> BackupEntry | None:
        i = self.combo.currentIndex()
        return self.backups[i] if 0 <= i < len(self.backups) else None

    def _update_info(self) -> None:
        b = self.selected()
        if b is None:
            self.info.setText("")
            return
        notes = []
        if b.udid != self.target.udid:
            notes.append("Dieses Backup stammt von einem anderen Gerät.")
        if b.encrypted:
            notes.append("Das Backup ist verschlüsselt – das Passwort wird gleich abgefragt.")
        if b.complete is False:
            notes.append("⚠ Dieses Backup wurde nicht vollständig abgeschlossen.")
        self.info.setText(" ".join(notes))

    def _confirm(self) -> None:
        b = self.selected()
        if b is None:
            return
        answer = QMessageBox.warning(
            self, "Wiederherstellen bestätigen",
            f"Backup vom {format_datetime(b.last_backup)} wirklich auf "
            f"„{self.target.display_name}“ wiederherstellen?\n\n"
            "Die aktuellen Daten auf dem Gerät werden überschrieben.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.accept()

    def options(self) -> dict:
        return {
            "system": self.system.isChecked(),
            "settings": self.settings.isChecked(),
            "remove": self.remove.isChecked(),
            "reboot": self.reboot.isChecked(),
        }
