"""Hauptfenster."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from .. import tools
from ..backups import BackupEntry, backup_size, delete_backup, scan_backups
from ..devices import PAIRING_TEXT, DeviceInfo, DeviceMonitor, classify_pairing, model_name
from ..fmt import format_bytes, format_datetime
from ..jobs import ToolJob
from ..settings import Settings
from ..storage import ERROR, OK, WARN, StorageStatus, check_storage, find_mount, suggest_expected_mount
from .async_util import run_async
from .dialogs import PasswordDialog, RestoreDialog

RESOURCES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")


def app_icon() -> QIcon:
    return QIcon.fromTheme("iosbackuplinux", QIcon(os.path.join(RESOURCES, "iosbackuplinux.svg")))


def device_icon(dev: DeviceInfo) -> QIcon:
    if dev.is_ipad:
        return QIcon.fromTheme("tablet", QIcon.fromTheme("computer-tablet", app_icon()))
    return QIcon.fromTheme("smartphone", QIcon.fromTheme("phone", app_icon()))


def _status_dot(level: str) -> str:
    color = {OK: "#27ae60", WARN: "#f39c12", ERROR: "#c0392b"}.get(level, "#7f8c8d")
    return f"<span style='color:{color}; font-size:14pt;'>●</span>"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.devices: dict[str, DeviceInfo] = {}
        self.backups: list[BackupEntry] = []
        self.backup_sizes: dict[str, int] = {}
        self.storage: StorageStatus | None = None
        self.job: ToolJob | None = None
        self.job_udid: str | None = None
        self._scan_generation = 0
        self.viewers: dict = {}

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self._build_ui()
        if self.settings.geometry is not None:
            self.restoreGeometry(self.settings.geometry)
        else:
            self.resize(1000, 680)

        self.monitor = DeviceMonitor(parent=self)
        self.monitor.devicesChanged.connect(self._on_devices_changed)
        self.monitor.listError.connect(self._on_list_error)
        self.monitor.start()

        self.storage_timer = QTimer(self)
        self.storage_timer.timeout.connect(self.refresh_storage)
        self.storage_timer.start(30000)
        self.refresh_storage()
        self.refresh_backups()
        self._update_actions()

    # ==================================================================
    # Aufbau der Oberfläche
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # --- links: Geräte ------------------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Geräte")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lv.addWidget(title)
        self.device_list = QListWidget()
        self.device_list.setIconSize(QSize(32, 32))
        self.device_list.currentItemChanged.connect(lambda *_: self._show_current_device())
        lv.addWidget(self.device_list, 1)
        self.list_error = QLabel()
        self.list_error.setWordWrap(True)
        self.list_error.setStyleSheet("color: #c0392b;")
        self.list_error.hide()
        lv.addWidget(self.list_error)
        splitter.addWidget(left)

        # --- rechts: Tabs -------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview(), "Übersicht")
        self.tabs.addTab(self._build_backups_tab(), "Backups")
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([260, 740])

        # --- unten: Speicherort, Fortschritt, Protokoll ------------------
        root.addWidget(self._build_storage_box())
        root.addWidget(self._build_progress_box())

    def _build_overview(self) -> QWidget:
        self.overview_stack = QStackedWidget()

        empty = QLabel("Bitte ein iPhone oder iPad per USB-Kabel anschließen und entsperren.\n\n"
                       "Beim ersten Anschließen erscheint auf dem Gerät die Frage "
                       "„Diesem Computer vertrauen?“ – bitte mit „Vertrauen“ bestätigen.")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        empty.setStyleSheet("color: palette(placeholder-text); font-size: 12pt;")
        self.overview_stack.addWidget(empty)

        page = QWidget()
        v = QVBoxLayout(page)

        header = QHBoxLayout()
        self.dev_icon = QLabel()
        header.addWidget(self.dev_icon)
        hv = QVBoxLayout()
        self.dev_name = QLabel()
        nf = QFont(self.dev_name.font())
        nf.setPointSizeF(nf.pointSizeF() * 1.6)
        nf.setBold(True)
        self.dev_name.setFont(nf)
        self.dev_model = QLabel()
        hv.addWidget(self.dev_name)
        hv.addWidget(self.dev_model)
        header.addLayout(hv, 1)
        v.addLayout(header)

        # Kopplung
        self.pair_frame = QFrame()
        self.pair_frame.setFrameShape(QFrame.Shape.StyledPanel)
        ph = QHBoxLayout(self.pair_frame)
        self.pair_label = QLabel()
        self.pair_label.setWordWrap(True)
        ph.addWidget(self.pair_label, 1)
        self.pair_button = QPushButton("Koppeln")
        self.pair_button.clicked.connect(self.pair_device)
        ph.addWidget(self.pair_button)
        v.addWidget(self.pair_frame)

        info_box = QGroupBox("Gerät")
        form = QFormLayout(info_box)
        self.lbl_ios = QLabel()
        self.lbl_serial = QLabel()
        self.lbl_udid = QLabel()
        self.lbl_udid.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.storage_bar = QProgressBar()
        self.storage_bar.setTextVisible(True)
        form.addRow("iOS-Version:", self.lbl_ios)
        form.addRow("Seriennummer:", self.lbl_serial)
        form.addRow("UDID:", self.lbl_udid)
        form.addRow("Speicher:", self.storage_bar)
        v.addWidget(info_box)

        bk_box = QGroupBox("Backups")
        bv = QVBoxLayout(bk_box)
        self.lbl_last_backup = QLabel()
        bv.addWidget(self.lbl_last_backup)

        enc_row = QHBoxLayout()
        self.cb_encrypt = QCheckBox("Lokales Backup verschlüsseln")
        self.cb_encrypt.setToolTip("Verschlüsselte Backups enthalten zusätzlich gespeicherte "
                                   "Passwörter, WLAN-Zugänge, Health- und Website-Daten.")
        self.cb_encrypt.clicked.connect(self.toggle_encryption)
        enc_row.addWidget(self.cb_encrypt)
        self.btn_changepw = QPushButton("Passwort ändern …")
        self.btn_changepw.clicked.connect(self.change_password)
        enc_row.addWidget(self.btn_changepw)
        enc_row.addStretch(1)
        bv.addLayout(enc_row)

        self.cb_full = QCheckBox("Vollständiges Backup erzwingen (sonst nur Änderungen übertragen)")
        self.cb_full.setChecked(self.settings.force_full)
        self.cb_full.toggled.connect(lambda c: setattr(self.settings, "force_full", c))
        bv.addWidget(self.cb_full)

        btn_row = QHBoxLayout()
        self.btn_backup = QPushButton(QIcon.fromTheme("document-save"), "Jetzt sichern")
        self.btn_backup.setMinimumHeight(36)
        self.btn_backup.clicked.connect(self.start_backup)
        self.btn_restore = QPushButton(QIcon.fromTheme("document-revert"), "Backup wiederherstellen …")
        self.btn_restore.setMinimumHeight(36)
        self.btn_restore.clicked.connect(lambda: self.start_restore())
        btn_row.addWidget(self.btn_backup)
        btn_row.addWidget(self.btn_restore)
        bv.addLayout(btn_row)
        v.addWidget(bk_box)
        v.addStretch(1)

        self.overview_stack.addWidget(page)
        return self.overview_stack

    def _build_backups_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Gerät", "Modell", "iOS", "Letztes Backup", "Größe", "Verschlüsselt", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.doubleClicked.connect(lambda *_: self.open_viewer())
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton(QIcon.fromTheme("view-refresh"), "Aktualisieren")
        self.btn_refresh.clicked.connect(self.refresh_backups)
        self.btn_restore_sel = QPushButton(QIcon.fromTheme("document-revert"),
                                           "Auf ausgewähltes Gerät wiederherstellen …")
        self.btn_restore_sel.clicked.connect(
            lambda: self.start_restore(self._selected_backup()))
        self.btn_view = QPushButton(QIcon.fromTheme("document-preview"), "Inhalt anzeigen …")
        self.btn_view.setToolTip("Fotos, Videos und Dateien des Backups ansehen und exportieren")
        self.btn_view.clicked.connect(lambda: self.open_viewer())
        self.btn_open = QPushButton(QIcon.fromTheme("folder-open"), "Ordner öffnen")
        self.btn_open.clicked.connect(self.open_selected_backup)
        self.btn_delete = QPushButton(QIcon.fromTheme("edit-delete"), "Löschen …")
        self.btn_delete.clicked.connect(self.delete_selected_backup)
        for b in (self.btn_refresh, self.btn_view, self.btn_restore_sel, self.btn_open):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_delete)
        v.addLayout(row)
        return page

    def _build_storage_box(self) -> QWidget:
        box = QGroupBox("Speicherort der Backups")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(self.settings.backup_dir)
        self.path_edit.setReadOnly(True)
        row.addWidget(self.path_edit, 1)
        self.btn_choose = QPushButton(QIcon.fromTheme("folder"), "Ändern …")
        self.btn_choose.clicked.connect(self.choose_storage)
        row.addWidget(self.btn_choose)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.storage_label = QLabel("Prüfe Speicherort …")
        self.storage_label.setWordWrap(True)
        row2.addWidget(self.storage_label, 1)
        self.cb_guard = QCheckBox()
        self.cb_guard.setToolTip(
            "Ist das NAS nicht eingehängt, würde sonst in den leeren Mount-Ordner auf der "
            "lokalen Festplatte geschrieben. Mit dieser Option wird das Backup dann verweigert.")
        self.cb_guard.toggled.connect(self._on_guard_toggled)
        row2.addWidget(self.cb_guard)
        v.addLayout(row2)
        self._update_guard_checkbox()
        return box

    def _build_progress_box(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.status_label = QLabel("Bereit.")
        row.addWidget(self.status_label, 1)
        self.detail_label = QLabel()
        row.addWidget(self.detail_label)
        self.btn_cancel = QPushButton(QIcon.fromTheme("process-stop"), "Abbrechen")
        self.btn_cancel.clicked.connect(self.cancel_job)
        self.btn_cancel.hide()
        row.addWidget(self.btn_cancel)
        self.btn_log = QToolButton()
        self.btn_log.setText("Protokoll")
        self.btn_log.setCheckable(True)
        self.btn_log.setArrowType(Qt.ArrowType.RightArrow)
        self.btn_log.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_log.toggled.connect(self._toggle_log)
        row.addWidget(self.btn_log)
        v.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        v.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFont(QFont("monospace"))
        self.log.setMinimumHeight(140)
        self.log.hide()
        v.addWidget(self.log)
        return w

    def _toggle_log(self, on: bool) -> None:
        self.log.setVisible(on)
        self.btn_log.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def log_line(self, text: str) -> None:
        self.log.appendPlainText(text)

    # ==================================================================
    # Geräte
    # ==================================================================
    def current_device(self) -> DeviceInfo | None:
        item = self.device_list.currentItem()
        if item is None:
            return None
        return self.devices.get(item.data(Qt.ItemDataRole.UserRole))

    def _on_list_error(self, text: str) -> None:
        self.list_error.setText(text)
        self.list_error.setVisible(bool(text))

    def _on_devices_changed(self, infos: list) -> None:
        current = self.current_device()
        current_udid = current.udid if current else None
        self.devices = {d.udid: d for d in infos}
        self.device_list.blockSignals(True)
        self.device_list.clear()
        select_row = 0
        for i, d in enumerate(sorted(infos, key=lambda d: d.display_name.lower())):
            text = d.display_name
            if not d.paired:
                text += f"\n{PAIRING_TEXT.get(d.pairing, '')}"
            else:
                text += f"\n{d.model}"
            item = QListWidgetItem(device_icon(d), text)
            item.setData(Qt.ItemDataRole.UserRole, d.udid)
            self.device_list.addItem(item)
            if d.udid == current_udid:
                select_row = i
        if self.device_list.count():
            self.device_list.setCurrentRow(select_row)
        self.device_list.blockSignals(False)
        self._show_current_device()

    def _show_current_device(self) -> None:
        dev = self.current_device()
        if dev is None:
            self.overview_stack.setCurrentIndex(0)
            self._update_actions()
            return
        self.overview_stack.setCurrentIndex(1)
        self.dev_icon.setPixmap(device_icon(dev).pixmap(64, 64))
        self.dev_name.setText(dev.display_name)
        self.dev_model.setText(dev.model)
        self.lbl_ios.setText(f"iOS {dev.product_version}" if dev.product_version else "–")
        self.lbl_serial.setText(dev.serial or "–")
        self.lbl_udid.setText(dev.udid)

        if dev.data_capacity:
            used = dev.used_bytes or 0
            self.storage_bar.setRange(0, 1000)
            self.storage_bar.setValue(int(used * 1000 / dev.data_capacity))
            self.storage_bar.setFormat(f"{format_bytes(used)} von {format_bytes(dev.data_capacity)} belegt")
        else:
            self.storage_bar.setRange(0, 1)
            self.storage_bar.setValue(0)
            self.storage_bar.setFormat("–")

        if dev.paired:
            self.pair_frame.hide()
        else:
            self.pair_frame.show()
            self.pair_label.setText(
                f"<b>{PAIRING_TEXT.get(dev.pairing, 'Nicht gekoppelt')}</b><br>"
                "Gerät entsperren und bei „Diesem Computer vertrauen?“ auf „Vertrauen“ "
                "tippen (ggf. Gerätecode eingeben). Danach wird es automatisch erkannt.")

        last = self._backup_for(dev.udid)
        if last is not None:
            text = f"Letztes Backup: {format_datetime(last.last_backup)}"
            size = self.backup_sizes.get(last.path)
            if size is not None:
                text += f" · {format_bytes(size)}"
            if last.complete is False:
                text += " · ⚠ unvollständig"
        else:
            text = "Noch kein Backup in diesem Speicherort."
        self.lbl_last_backup.setText(text)

        self.cb_encrypt.blockSignals(True)
        self.cb_encrypt.setChecked(bool(dev.will_encrypt))
        self.cb_encrypt.blockSignals(False)
        self._update_actions()

    def pair_device(self) -> None:
        dev = self.current_device()
        if dev is None:
            return
        self.pair_button.setEnabled(False)
        self.status_label.setText("Kopplung wird angefragt – bitte auf dem Gerät bestätigen …")
        udid = dev.udid
        run_async(tools.run_tool, tools.pair_cmd(udid),
                  on_done=lambda res: self._on_paired(udid, res))

    def _on_paired(self, udid: str, res) -> None:
        self.pair_button.setEnabled(True)
        self.log_line(res.output.strip())
        state = classify_pairing(res.returncode, res.output)
        self.status_label.setText(PAIRING_TEXT.get(state, "") if state != "paired"
                                  else "Gerät erfolgreich gekoppelt.")
        self.monitor.request_refresh(udid)

    # ==================================================================
    # Speicherort
    # ==================================================================
    def choose_storage(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Speicherort für Backups wählen", self.settings.backup_dir)
        if not path:
            return
        self.settings.backup_dir = path
        self.path_edit.setText(path)
        expected = suggest_expected_mount(path)
        self.settings.expected_mount = expected
        self._update_guard_checkbox()
        self.refresh_storage()
        self.refresh_backups()

    def _update_guard_checkbox(self) -> None:
        expected = self.settings.expected_mount
        mp, _fs = find_mount(self.settings.backup_dir)
        target = expected or (mp if mp != "/" else "")
        self.cb_guard.blockSignals(True)
        self.cb_guard.setChecked(bool(expected))
        self.cb_guard.blockSignals(False)
        self.cb_guard.setEnabled(bool(target))
        self.cb_guard.setText(f"Nur sichern, wenn „{target}“ eingehängt ist" if target
                              else "Nur sichern, wenn Laufwerk eingehängt ist")
        self.cb_guard.setVisible(bool(target))

    def _on_guard_toggled(self, on: bool) -> None:
        if on:
            mp, _ = find_mount(self.settings.backup_dir)
            self.settings.expected_mount = mp if mp != "/" else ""
        else:
            self.settings.expected_mount = ""
        self._update_guard_checkbox()
        self.refresh_storage()

    def refresh_storage(self) -> None:
        run_async(check_storage, self.settings.backup_dir, self.settings.expected_mount or None,
                  on_done=self._on_storage_status)

    def _on_storage_status(self, st: StorageStatus) -> None:
        if st.path != self.settings.backup_dir:
            return
        self.storage = st
        self.storage_label.setText(f"{_status_dot(st.level)} {st.message}")

    # ==================================================================
    # Backup-Liste
    # ==================================================================
    def refresh_backups(self) -> None:
        self._scan_generation += 1
        gen = self._scan_generation
        root = self.settings.backup_dir
        run_async(scan_backups, root, on_done=lambda entries: self._on_backups(gen, entries))

    def _on_backups(self, gen: int, entries: list) -> None:
        if gen != self._scan_generation:
            return
        self.backups = entries
        self._fill_table()
        self._show_current_device()

        def sizes(paths):
            return {p: backup_size(p) for p in paths}

        run_async(sizes, [e.path for e in entries],
                  on_done=lambda s: self._on_sizes(gen, s))

    def _on_sizes(self, gen: int, sizes: dict) -> None:
        if gen != self._scan_generation:
            return
        self.backup_sizes.update(sizes)
        self._fill_table()
        self._show_current_device()

    def _fill_table(self) -> None:
        selected = self._selected_backup()
        self.table.setRowCount(len(self.backups))
        for row, b in enumerate(self.backups):
            size = self.backup_sizes.get(b.path)
            status = {True: "Vollständig", False: "Unvollständig", None: "–"}[b.complete]
            values = [
                b.device_name or b.udid,
                model_name(b.product_type),
                b.product_version or "–",
                format_datetime(b.last_backup),
                format_bytes(size) if size is not None else "…",
                {True: "Ja 🔒", False: "Nein", None: "–"}[b.encrypted],
                status,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setToolTip(f"UDID: {b.udid}\n{b.path}")
                self.table.setItem(row, col, item)
            if selected is not None and selected.path == b.path:
                self.table.selectRow(row)
        self._update_actions()

    def _selected_backup(self) -> BackupEntry | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1:
            return None
        row = rows.pop()
        return self.backups[row] if 0 <= row < len(self.backups) else None

    def _backup_for(self, udid: str) -> BackupEntry | None:
        for b in self.backups:
            if b.udid == udid:
                return b
        return None

    def open_selected_backup(self) -> None:
        b = self._selected_backup()
        path = b.path if b else self.settings.backup_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # --- Backup-Inhalt ansehen -----------------------------------------
    def open_viewer(self, entry: BackupEntry | None = None) -> None:
        b = entry or self._selected_backup()
        if b is None:
            return
        if b.path in self.viewers:
            win = self.viewers[b.path]
            win.raise_()
            win.activateWindow()
            return
        password = None
        if b.encrypted:
            dlg = PasswordDialog("current", self, "Das Backup ist verschlüsselt. Zum Ansehen wird "
                                 "das Backup-Passwort benötigt.")
            if dlg.exec() != PasswordDialog.DialogCode.Accepted:
                return
            password = dlg.passwords().get("default")
        self.status_label.setText("Backup wird geöffnet …" + (
            " (Entschlüsseln kann einige Sekunden dauern)" if b.encrypted else ""))
        self.btn_view.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        from ..viewer.loader import load_backup
        run_async(load_backup, b.path, password,
                  on_done=lambda res: self._viewer_loaded(b, res),
                  on_error=self._viewer_failed)

    def _viewer_loaded(self, backup: BackupEntry, result) -> None:
        from .viewer_window import BackupViewerWindow
        QApplication.restoreOverrideCursor()
        self._update_actions()
        reader, media, tree = result
        self.status_label.setText("Bereit.")
        win = BackupViewerWindow(reader, media, tree, backup, self)
        win.closed.connect(lambda path: self.viewers.pop(path, None))
        self.viewers[backup.path] = win
        win.show()

    def _viewer_failed(self, message: str) -> None:
        QApplication.restoreOverrideCursor()
        self._update_actions()
        self.status_label.setText("Backup konnte nicht geöffnet werden.")
        QMessageBox.critical(self, "Backup öffnen", message)

    def delete_selected_backup(self) -> None:
        b = self._selected_backup()
        if b is None or self._busy():
            return
        if b.path in self.viewers:
            self.viewers[b.path].close()
        answer = QMessageBox.warning(
            self, "Backup löschen",
            f"Backup von „{b.device_name or b.udid}“ ({format_datetime(b.last_backup)}) "
            "endgültig löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText("Backup wird gelöscht …")
        self.btn_delete.setEnabled(False)
        run_async(delete_backup, b, self.settings.backup_dir,
                  on_done=lambda _: self._after_delete(None),
                  on_error=lambda msg: self._after_delete(msg))

    def _after_delete(self, error: str | None) -> None:
        if error:
            QMessageBox.critical(self, "Löschen fehlgeschlagen", error)
            self.status_label.setText("Löschen fehlgeschlagen.")
        else:
            self.status_label.setText("Backup gelöscht.")
        self.refresh_backups()
        self.refresh_storage()

    # ==================================================================
    # Aktionen: Backup / Restore / Verschlüsselung
    # ==================================================================
    def _busy(self) -> bool:
        return self.job is not None and self.job.is_running()

    def _update_actions(self) -> None:
        dev = self.current_device()
        busy = self._busy()
        ready = dev is not None and dev.paired and not busy
        self.btn_backup.setEnabled(ready)
        self.btn_restore.setEnabled(ready and bool(self.backups))
        self.cb_encrypt.setEnabled(ready)
        self.btn_changepw.setEnabled(ready and bool(dev and dev.will_encrypt))
        self.pair_button.setEnabled(not busy)
        selected = self._selected_backup()
        self.btn_restore_sel.setEnabled(ready and selected is not None)
        self.btn_delete.setEnabled(not busy and selected is not None)
        self.btn_open.setEnabled(True)
        self.btn_view.setEnabled(selected is not None)
        self.btn_choose.setEnabled(not busy)
        self.cb_guard.setEnabled(not busy and not self.cb_guard.isHidden())

    def _require_device(self) -> DeviceInfo | None:
        dev = self.current_device()
        if dev is None:
            QMessageBox.information(self, APP_NAME, "Bitte zuerst ein Gerät auswählen.")
            return None
        if not dev.paired:
            QMessageBox.information(self, APP_NAME,
                                    "Das Gerät ist noch nicht gekoppelt. Bitte auf dem Gerät "
                                    "„Vertrauen“ bestätigen.")
            return None
        return dev

    # --- Backup -------------------------------------------------------
    def start_backup(self) -> None:
        dev = self._require_device()
        if dev is None or self._busy():
            return
        required = dev.used_bytes
        existing = self._backup_for(dev.udid)
        if required and existing is not None:
            required = max(0, required - self.backup_sizes.get(existing.path, 0))
        self.btn_backup.setEnabled(False)
        self.status_label.setText("Speicherort wird geprüft …")
        path = self.settings.backup_dir
        run_async(check_storage, path, self.settings.expected_mount or None, required, True,
                  on_done=lambda st: self._backup_storage_checked(dev, st))

    def _backup_storage_checked(self, dev: DeviceInfo, st: StorageStatus) -> None:
        self._on_storage_status(st)
        self._update_actions()
        if st.level == ERROR:
            self.status_label.setText("Backup nicht gestartet.")
            QMessageBox.critical(self, "Speicherort nicht verfügbar", st.message)
            return
        if st.level == WARN:
            answer = QMessageBox.question(self, "Wenig Speicherplatz",
                                          st.message + "\n\nTrotzdem sichern?")
            if answer != QMessageBox.StandardButton.Yes:
                self.status_label.setText("Backup nicht gestartet.")
                return
        if dev.udid not in self.devices:
            self.status_label.setText("Gerät nicht mehr verbunden.")
            return
        cmd = tools.backup_cmd(dev.udid, st.path, full=self.cb_full.isChecked())
        self._start_job(ToolJob("backup", cmd), dev.udid,
                        f"Backup von „{dev.display_name}“ läuft …")

    # --- Restore ------------------------------------------------------
    def start_restore(self, entry: BackupEntry | None = None) -> None:
        dev = self._require_device()
        if dev is None or self._busy():
            return
        if not self.backups:
            QMessageBox.information(self, APP_NAME, "Im Speicherort wurden keine Backups gefunden.")
            return
        dlg = RestoreDialog(dev, self.backups, preselect=entry, parent=self)
        if dlg.exec() != RestoreDialog.DialogCode.Accepted:
            return
        backup = dlg.selected()
        if backup is None:
            return
        passwords = {}
        if backup.encrypted:
            pw = PasswordDialog("current", self, "Das Backup ist verschlüsselt. Bitte das "
                                "Backup-Passwort eingeben.")
            if pw.exec() != PasswordDialog.DialogCode.Accepted:
                return
            passwords = pw.passwords()
        opts = dlg.options()
        cmd = tools.restore_cmd(dev.udid, backup.root, source_udid=backup.udid, **opts)
        self._start_job(ToolJob("restore", cmd, passwords), dev.udid,
                        f"Wiederherstellung auf „{dev.display_name}“ läuft – Gerät nicht trennen!")

    # --- Verschlüsselung ----------------------------------------------
    def toggle_encryption(self, checked: bool) -> None:
        dev = self._require_device()
        # Haken erst setzen, wenn es geklappt hat
        self.cb_encrypt.blockSignals(True)
        self.cb_encrypt.setChecked(not checked)
        self.cb_encrypt.blockSignals(False)
        if dev is None or self._busy():
            return
        if checked:
            dlg = PasswordDialog("new", self, "Passwort für verschlüsselte Backups festlegen. "
                                 "Eventuell musst du anschließend den Gerätecode auf dem "
                                 "iPhone/iPad eingeben.")
        else:
            dlg = PasswordDialog("current", self, "Zum Ausschalten der Verschlüsselung wird "
                                 "das aktuelle Backup-Passwort benötigt.")
        if dlg.exec() != PasswordDialog.DialogCode.Accepted:
            return
        self._start_job(ToolJob("encryption", tools.encryption_cmd(dev.udid, checked),
                                dlg.passwords()), dev.udid,
                        "Verschlüsselung wird eingeschaltet … (ggf. Code auf dem Gerät eingeben)"
                        if checked else "Verschlüsselung wird ausgeschaltet …")

    def change_password(self) -> None:
        dev = self._require_device()
        if dev is None or self._busy():
            return
        dlg = PasswordDialog("change", self)
        if dlg.exec() != PasswordDialog.DialogCode.Accepted:
            return
        self._start_job(ToolJob("changepw", tools.changepw_cmd(dev.udid), dlg.passwords()),
                        dev.udid, "Backup-Passwort wird geändert … (ggf. Code auf dem Gerät eingeben)")

    # ==================================================================
    # Laufender Vorgang
    # ==================================================================
    def _start_job(self, job: ToolJob, udid: str, title: str) -> None:
        self.job = job
        self.job_udid = udid
        job.setParent(self)
        job.line.connect(self.log_line)
        job.progress.connect(self._on_job_progress)
        job.status.connect(self._on_job_status)
        job.finished.connect(self._on_job_finished)
        self.monitor.set_busy(udid, True)
        self.log_line(f"$ {' '.join(job.args)}")
        self.status_label.setText(title)
        self.detail_label.setText("")
        self.progress.setRange(0, 0)  # unbestimmt, bis Fortschritt gemeldet wird
        self.progress.show()
        self.btn_cancel.show()
        self.btn_cancel.setEnabled(True)
        job.start()
        self._update_actions()

    def _on_job_progress(self, pct: float, detail: str) -> None:
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(int(pct))
        self.detail_label.setText(detail)

    def _on_job_status(self, text: str) -> None:
        self.status_label.setText(text)

    def cancel_job(self) -> None:
        if not self._busy():
            return
        if self.job.kind == "restore":
            answer = QMessageBox.warning(
                self, "Wiederherstellung abbrechen?",
                "Ein Abbruch während der Wiederherstellung kann das Gerät in einem "
                "unvollständigen Zustand hinterlassen. Wirklich abbrechen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.btn_cancel.setEnabled(False)
        self.status_label.setText("Wird abgebrochen …")
        self.job.cancel()

    def _on_job_finished(self, ok: bool, message: str) -> None:
        job, udid = self.job, self.job_udid
        self.btn_cancel.hide()
        self.progress.setRange(0, 100)
        if ok:
            self.progress.setValue(100)
        self.detail_label.setText("")
        if udid:
            self.monitor.set_busy(udid, False)
        self.log_line(f"→ {'Erfolgreich' if ok else 'Fehlgeschlagen: ' + message}")

        texts = {
            "backup": ("Backup erfolgreich abgeschlossen.", "Backup fehlgeschlagen"),
            "restore": ("Wiederherstellung abgeschlossen. Das Gerät startet neu.",
                        "Wiederherstellung fehlgeschlagen"),
            "encryption": ("Einstellung für die Backup-Verschlüsselung gespeichert.",
                           "Verschlüsselung konnte nicht geändert werden"),
            "changepw": ("Backup-Passwort geändert.", "Passwort konnte nicht geändert werden"),
        }
        good, bad = texts.get(job.kind if job else "", ("Fertig.", "Fehler"))
        self._update_actions()
        if ok:
            self.status_label.setText(good)
            if job and job.kind in ("backup", "restore"):
                QMessageBox.information(self, APP_NAME, good)
        else:
            self.status_label.setText(f"{bad}: {message}")
            self.progress.hide()
            if not (job and job.cancelled):
                QMessageBox.critical(self, bad, message)
        if job and job.kind == "backup":
            self.refresh_backups()
            self.refresh_storage()
        if udid:
            self.monitor.request_refresh(udid)

    # ==================================================================
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._busy():
            answer = QMessageBox.question(
                self, APP_NAME, "Ein Vorgang läuft noch. Abbrechen und beenden?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.job.cancel()
            self.job.wait(25)
        self.settings.geometry = self.saveGeometry()
        self.settings.sync()
        for viewer in list(self.viewers.values()):
            viewer.close()
        self.monitor.stop()
        event.accept()
