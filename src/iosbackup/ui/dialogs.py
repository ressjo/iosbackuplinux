"""Dialogs: password entry and restore."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QLabel, QLineEdit, QMessageBox,
                               QVBoxLayout)

from ..backups import BackupEntry
from ..devices import DeviceInfo, model_name
from ..fmt import format_datetime
from .style import ERROR_COLOR, heading, muted


class PasswordDialog(QDialog):
    """mode: ``new`` (turn encryption on), ``current`` (turn it off / restore /
    browse) or ``change`` (change password)."""

    def __init__(self, mode: str, parent=None, text: str = ""):
        super().__init__(parent)
        self.mode = mode
        titles = {
            "new": "Encrypt Local Backups",
            "current": "Backup Password",
            "change": "Change Backup Password",
        }
        self.setWindowTitle(titles[mode])
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(heading(titles[mode], 1.15))
        if text:
            layout.addWidget(muted(text, wrap=True))
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
            form.addRow("Current password:" if mode == "change" else "Password:", self.old)
        if self.new is not None:
            form.addRow("New password:", self.new)
            form.addRow("Verify:", self.repeat)
            layout.addWidget(muted("Without this password an encrypted backup cannot be "
                                   "restored. It is not stored anywhere – keep it safe.",
                                   wrap=True))
        self.error = QLabel()
        self.error.setStyleSheet(f"color: {ERROR_COLOR};")
        layout.addWidget(self.error)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()
        self.resize(440, self.sizeHint().height())

    def _validate(self) -> None:
        ok, msg = True, ""
        if self.old is not None and not self.old.text():
            ok = False
        if self.new is not None:
            if not self.new.text():
                ok = False
            elif self.new.text() != self.repeat.text():
                ok, msg = False, "The passwords do not match." if self.repeat.text() else ""
        self.error.setText(msg)
        self.error.setVisible(bool(msg))
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
        self.setWindowTitle("Restore Backup")
        self.target = target
        self.backups = backups
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(heading(f"Restore “{target.display_name}” from a backup", 1.15))

        intro = QLabel(
            "The data and settings on the device will be replaced with the contents of "
            "the backup. The device restarts when the restore is finished.<br><br>"
            "Before you start:<ul style='margin-left:-20px'>"
            "<li><b>Turn off Find My</b> on the device (Settings → [your name] → Find My).</li>"
            "<li>Keep the device unlocked and connected the whole time.</li>"
            "<li>The device’s iOS version must be the same as or newer than the backup’s.</li>"
            "</ul>")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        layout.addLayout(form)
        self.combo = QComboBox()
        for b in backups:
            label = (f"{b.device_name or b.udid} · {model_name(b.product_type)} · "
                     f"iOS {b.product_version or '?'} · {format_datetime(b.last_backup)}")
            if b.encrypted:
                label += " · encrypted"
            if b.complete is False:
                label += " · incomplete"
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

        self.system = QCheckBox("Restore system files (recommended)")
        self.system.setChecked(True)
        self.settings = QCheckBox("Restore device settings (recommended)")
        self.settings.setChecked(True)
        self.remove = QCheckBox("Remove items that are not in the backup")
        self.reboot = QCheckBox("Restart the device when done (recommended)")
        self.reboot.setChecked(True)
        for cb in (self.system, self.settings, self.remove, self.reboot):
            layout.addWidget(cb)

        self.info = muted(wrap=True)
        layout.addWidget(self.info)
        self.combo.currentIndexChanged.connect(self._update_info)
        self._update_info()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.addButton("Restore", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(580, self.sizeHint().height())

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
            notes.append("This backup belongs to a different device.")
        if b.encrypted:
            notes.append("The backup is encrypted – you will be asked for its password.")
        if b.complete is False:
            notes.append("This backup did not finish completely.")
        self.info.setText(" ".join(notes))
        self.info.setVisible(bool(notes))

    def _confirm(self) -> None:
        b = self.selected()
        if b is None:
            return
        answer = QMessageBox.warning(
            self, "Confirm Restore",
            f"Restore the backup from {format_datetime(b.last_backup)} to "
            f"“{self.target.display_name}”?\n\n"
            "The current data on the device will be overwritten.",
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
