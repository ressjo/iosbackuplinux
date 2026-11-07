"""Main window."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QFontMetrics, QIcon, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from .. import tools
from ..backups import BackupEntry, backup_size, delete_backup, scan_backups
from ..devices import PAIRING_TEXT, DeviceInfo, DeviceMonitor, classify_pairing, model_name
from ..fmt import format_bytes, format_datetime
from ..jobs import ToolJob
from ..settings import Settings
from ..storage import (ERROR, OK, WARN, StorageStatus, check_storage, find_mount,
                       suggest_expected_mount)
from .async_util import run_async
from .dialogs import PasswordDialog, RestoreDialog
from .style import (ERROR_COLOR, OK_COLOR, WARN_COLOR, TwoLineDelegate, dot, heading, hline,
                    muted, section)

RESOURCES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")


def app_icon() -> QIcon:
    return QIcon.fromTheme("iosbackuplinux", QIcon(os.path.join(RESOURCES, "iosbackuplinux.svg")))


def device_icon(dev: DeviceInfo) -> QIcon:
    if dev.is_ipad:
        return QIcon.fromTheme("tablet", QIcon.fromTheme("computer-tablet", app_icon()))
    return QIcon.fromTheme("smartphone", QIcon.fromTheme("phone", app_icon()))


class ElidedLabel(QLabel):
    """Single-line label that elides long text (e.g. paths) in the middle."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setFullText(text)

    def setFullText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._update()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update()

    def _update(self) -> None:
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle,
                                      max(40, self.width())))


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
            self.resize(1040, 700)

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
    # Layout
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- sidebar ------------------------------------------------------
        sidebar = QWidget()
        sidebar.setFixedWidth(250)
        sidebar.setAutoFillBackground(True)
        sidebar.setBackgroundRole(QPalette.ColorRole.Base)
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(10, 14, 10, 10)
        sv.setSpacing(8)
        cap = section("Devices")
        cap.setContentsMargins(6, 0, 0, 0)
        sv.addWidget(cap)
        self.device_list = QListWidget()
        self.device_list.setObjectName("devices")
        self.device_list.setItemDelegate(TwoLineDelegate(self.device_list))
        self.device_list.setIconSize(QSize(32, 32))
        self.device_list.setFrameShape(QFrame.Shape.NoFrame)
        self.device_list.currentItemChanged.connect(lambda *_: self._show_current_device())
        sv.addWidget(self.device_list, 1)
        self.list_error = QLabel()
        self.list_error.setWordWrap(True)
        self.list_error.setStyleSheet(f"color: {ERROR_COLOR};")
        self.list_error.hide()
        sv.addWidget(self.list_error)
        outer.addWidget(sidebar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        # --- content ------------------------------------------------------
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(14, 10, 14, 10)
        cv.setSpacing(10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_overview(), "Summary")
        self.tabs.addTab(self._build_backups_tab(), "Backups")
        cv.addWidget(self.tabs, 1)
        cv.addWidget(hline())
        cv.addWidget(self._build_footer())
        outer.addWidget(content, 1)

    def _build_overview(self) -> QWidget:
        self.overview_stack = QStackedWidget()

        # empty state
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(2)
        icon = QLabel()
        icon.setPixmap(QIcon.fromTheme("smartphone", app_icon()).pixmap(72, 72, QIcon.Mode.Disabled))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(icon)
        title = heading("No device connected", 1.3)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(title)
        hint = muted("Connect an iPhone or iPad with a USB cable and unlock it.\n"
                     "The first time, tap “Trust” on the device when asked.", wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(hint)
        ev.addStretch(3)
        self.overview_stack.addWidget(empty)

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(6, 14, 6, 6)
        v.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.dev_icon = QLabel()
        header.addWidget(self.dev_icon, 0, Qt.AlignmentFlag.AlignTop)
        hv = QVBoxLayout()
        hv.setSpacing(2)
        self.dev_name = heading("", 1.6)
        self.dev_model = muted()
        hv.addWidget(self.dev_name)
        hv.addWidget(self.dev_model)
        hv.addStretch(1)
        header.addLayout(hv, 1)
        v.addLayout(header)

        # pairing banner
        self.pair_frame = QFrame()
        self.pair_frame.setObjectName("banner")
        ph = QHBoxLayout(self.pair_frame)
        ph.setContentsMargins(12, 10, 12, 10)
        self.pair_label = QLabel()
        self.pair_label.setWordWrap(True)
        ph.addWidget(self.pair_label, 1)
        self.pair_button = QPushButton("Pair")
        self.pair_button.clicked.connect(self.pair_device)
        ph.addWidget(self.pair_button)
        v.addWidget(self.pair_frame)

        # device facts
        v.addWidget(section("Device"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self.lbl_serial = QLabel()
        self.lbl_udid = QLabel()
        self.lbl_udid.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.storage_bar = QProgressBar()
        self.storage_bar.setObjectName("meter")
        self.storage_bar.setTextVisible(False)
        self.storage_text = muted()
        cap_box = QVBoxLayout()
        cap_box.setSpacing(4)
        cap_box.addWidget(self.storage_bar)
        cap_box.addWidget(self.storage_text)
        for row, (label, widget) in enumerate((("Serial number", self.lbl_serial),
                                               ("UDID", self.lbl_udid))):
            grid.addWidget(muted(label), row, 0)
            grid.addWidget(widget, row, 1)
        grid.addWidget(muted("Capacity"), 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addLayout(cap_box, 2, 1)
        v.addLayout(grid)

        # backups
        v.addSpacing(4)
        v.addWidget(section("Backups"))
        self.lbl_last_backup = QLabel()
        v.addWidget(self.lbl_last_backup)

        enc_row = QHBoxLayout()
        self.cb_encrypt = QCheckBox("Encrypt local backup")
        self.cb_encrypt.clicked.connect(self.toggle_encryption)
        enc_row.addWidget(self.cb_encrypt)
        self.btn_changepw = QPushButton("Change Password…")
        self.btn_changepw.setFlat(True)
        self.btn_changepw.clicked.connect(self.change_password)
        enc_row.addWidget(self.btn_changepw)
        enc_row.addStretch(1)
        v.addLayout(enc_row)
        enc_hint = muted("Encrypted backups also contain saved passwords, Wi-Fi settings "
                         "and Health data.", wrap=True)
        enc_hint.setContentsMargins(24, 0, 0, 0)
        v.addWidget(enc_hint)

        self.cb_full = QCheckBox("Always make a full backup")
        self.cb_full.setToolTip("Otherwise only changes since the last backup are transferred.")
        self.cb_full.setChecked(self.settings.force_full)
        self.cb_full.toggled.connect(lambda c: setattr(self.settings, "force_full", c))
        v.addWidget(self.cb_full)
        v.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_restore = QPushButton("Restore Backup…")
        self.btn_restore.clicked.connect(lambda: self.start_restore())
        self.btn_backup = QPushButton("Back Up Now")
        self.btn_backup.setObjectName("primary")
        self.btn_backup.clicked.connect(self.start_backup)
        btn_row.addWidget(self.btn_restore)
        btn_row.addWidget(self.btn_backup)
        v.addLayout(btn_row)

        self.overview_stack.addWidget(page)
        return self.overview_stack

    def _build_backups_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 10, 0, 0)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Device", "Model", "iOS", "Last backup", "Size", "Encrypted", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(
            QFontMetrics(self.table.font()).height() + 14)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.doubleClicked.connect(lambda *_: self.open_viewer())
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton(QIcon.fromTheme("view-refresh"), "Refresh")
        self.btn_refresh.clicked.connect(self.refresh_backups)
        self.btn_view = QPushButton(QIcon.fromTheme("document-preview"), "Browse…")
        self.btn_view.setToolTip("View and export photos, videos and files in this backup")
        self.btn_view.clicked.connect(lambda: self.open_viewer())
        self.btn_restore_sel = QPushButton(QIcon.fromTheme("document-revert"), "Restore to Device…")
        self.btn_restore_sel.clicked.connect(lambda: self.start_restore(self._selected_backup()))
        self.btn_open = QPushButton(QIcon.fromTheme("folder-open"), "Show in Folder")
        self.btn_open.clicked.connect(self.open_selected_backup)
        self.btn_delete = QPushButton(QIcon.fromTheme("edit-delete"), "Delete…")
        self.btn_delete.clicked.connect(self.delete_selected_backup)
        for b in (self.btn_view, self.btn_restore_sel, self.btn_open, self.btn_refresh):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_delete)
        v.addLayout(row)
        return page

    def _build_footer(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        loc = QGridLayout()
        loc.setHorizontalSpacing(12)
        loc.setVerticalSpacing(2)
        loc.setColumnStretch(1, 1)
        loc.addWidget(muted("Backup location"), 0, 0)
        self.path_label = ElidedLabel(self.settings.backup_dir)
        loc.addWidget(self.path_label, 0, 1)
        self.btn_choose = QPushButton("Change…")
        self.btn_choose.clicked.connect(self.choose_storage)
        loc.addWidget(self.btn_choose, 0, 2, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        self.storage_label = muted("Checking…")
        loc.addWidget(self.storage_label, 1, 1)
        self.cb_guard = QCheckBox()
        self.cb_guard.setToolTip(
            "If the network share is not mounted, the backup would otherwise be written to "
            "the empty mount folder on your local disk. With this option the backup is "
            "refused instead.")
        self.cb_guard.toggled.connect(self._on_guard_toggled)
        loc.addWidget(self.cb_guard, 2, 1)
        v.addLayout(loc)
        self._update_guard_checkbox()

        status = QHBoxLayout()
        status.setSpacing(10)
        self.status_label = QLabel("Ready")
        status.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("meter")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setMaximumWidth(320)
        self.progress.hide()
        status.addWidget(self.progress, 1)
        self.detail_label = muted()
        status.addWidget(self.detail_label)
        status.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.cancel_job)
        self.btn_cancel.hide()
        status.addWidget(self.btn_cancel)
        self.btn_log = QToolButton()
        self.btn_log.setText("Log")
        self.btn_log.setCheckable(True)
        self.btn_log.setAutoRaise(True)
        self.btn_log.setArrowType(Qt.ArrowType.RightArrow)
        self.btn_log.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_log.toggled.connect(self._toggle_log)
        status.addWidget(self.btn_log)
        v.addLayout(status)

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
    # Devices
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
            item = QListWidgetItem(device_icon(d), d.display_name)
            item.setData(Qt.ItemDataRole.UserRole, d.udid)
            item.setData(TwoLineDelegate.SUBTITLE_ROLE,
                         d.model if d.paired else PAIRING_TEXT.get(d.pairing, ""))
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
        sub = [dev.model]
        if dev.product_version:
            sub.append(f"iOS {dev.product_version}")
        self.dev_model.setText(" · ".join(sub))
        self.lbl_serial.setText(dev.serial or "–")
        self.lbl_udid.setText(dev.udid)

        if dev.data_capacity:
            used = dev.used_bytes or 0
            self.storage_bar.setRange(0, 1000)
            self.storage_bar.setValue(int(used * 1000 / dev.data_capacity))
            self.storage_text.setText(f"{format_bytes(used)} of {format_bytes(dev.data_capacity)} "
                                      f"used · {format_bytes(dev.data_available)} available")
        else:
            self.storage_bar.setRange(0, 1)
            self.storage_bar.setValue(0)
            self.storage_text.setText("Available after pairing")

        if dev.paired:
            self.pair_frame.hide()
        else:
            self.pair_frame.show()
            self.pair_label.setText(
                f"<b>{PAIRING_TEXT.get(dev.pairing, 'Not paired')}</b><br>"
                "Unlock the device and tap “Trust” when asked “Trust This Computer?” "
                "(you may need to enter the passcode). It will be detected automatically.")

        last = self._backup_for(dev.udid)
        if last is not None:
            parts = [f"Latest backup: {format_datetime(last.last_backup)}"]
            size = self.backup_sizes.get(last.path)
            if size is not None:
                parts.append(format_bytes(size))
            if last.complete is False:
                parts.append("incomplete")
            text = " · ".join(parts)
        else:
            text = "No backup in this location yet."
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
        self.status_label.setText("Requesting pairing – confirm on the device…")
        udid = dev.udid
        run_async(tools.run_tool, tools.pair_cmd(udid),
                  on_done=lambda res: self._on_paired(udid, res))

    def _on_paired(self, udid: str, res) -> None:
        self.pair_button.setEnabled(True)
        self.log_line(res.output.strip())
        state = classify_pairing(res.returncode, res.output)
        self.status_label.setText("Device paired." if state == "paired"
                                  else PAIRING_TEXT.get(state, ""))
        self.monitor.request_refresh(udid)

    # ==================================================================
    # Backup location
    # ==================================================================
    def choose_storage(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose Backup Location", self.settings.backup_dir)
        if not path:
            return
        self.settings.backup_dir = path
        self.path_label.setFullText(path)
        self.settings.expected_mount = suggest_expected_mount(path)
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
        self.cb_guard.setText(f"Only back up when {target} is mounted" if target else "")
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
        color = {OK: OK_COLOR, WARN: WARN_COLOR, ERROR: ERROR_COLOR}.get(st.level, OK_COLOR)
        self.storage_label.setText(f"{dot(color)}&nbsp; {st.message}")

    # ==================================================================
    # Backup list
    # ==================================================================
    def refresh_backups(self) -> None:
        self._scan_generation += 1
        gen = self._scan_generation
        run_async(scan_backups, self.settings.backup_dir,
                  on_done=lambda entries: self._on_backups(gen, entries))

    def _on_backups(self, gen: int, entries: list) -> None:
        if gen != self._scan_generation:
            return
        self.backups = entries
        self._fill_table()
        self._show_current_device()

        def sizes(paths):
            return {p: backup_size(p) for p in paths}

        run_async(sizes, [e.path for e in entries], on_done=lambda s: self._on_sizes(gen, s))

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
            values = [
                b.device_name or b.udid,
                model_name(b.product_type),
                b.product_version or "–",
                format_datetime(b.last_backup),
                format_bytes(size) if size is not None else "…",
                {True: "Yes", False: "No", None: "–"}[b.encrypted],
                {True: "Complete", False: "Incomplete", None: "–"}[b.complete],
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
        QDesktopServices.openUrl(QUrl.fromLocalFile(b.path if b else self.settings.backup_dir))

    # --- browse backup contents ---------------------------------------
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
            dlg = PasswordDialog("current", self, "This backup is encrypted. Enter the backup "
                                 "password to browse it.")
            if dlg.exec() != PasswordDialog.DialogCode.Accepted:
                return
            password = dlg.passwords().get("default")
        self.status_label.setText("Opening backup…" + (
            " (decrypting may take a few seconds)" if b.encrypted else ""))
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
        self.status_label.setText("Ready")
        win = BackupViewerWindow(reader, media, tree, backup, self)
        win.closed.connect(lambda path: self.viewers.pop(path, None))
        self.viewers[backup.path] = win
        win.show()

    def _viewer_failed(self, message: str) -> None:
        QApplication.restoreOverrideCursor()
        self._update_actions()
        self.status_label.setText("Could not open the backup.")
        QMessageBox.critical(self, "Open Backup", message)

    def delete_selected_backup(self) -> None:
        b = self._selected_backup()
        if b is None or self._busy():
            return
        if b.path in self.viewers:
            self.viewers[b.path].close()
        answer = QMessageBox.warning(
            self, "Delete Backup",
            f"Permanently delete the backup of “{b.device_name or b.udid}” from "
            f"{format_datetime(b.last_backup)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText("Deleting backup…")
        self.btn_delete.setEnabled(False)
        run_async(delete_backup, b, self.settings.backup_dir,
                  on_done=lambda _: self._after_delete(None),
                  on_error=lambda msg: self._after_delete(msg))

    def _after_delete(self, error: str | None) -> None:
        if error:
            QMessageBox.critical(self, "Delete Failed", error)
            self.status_label.setText("Delete failed.")
        else:
            self.status_label.setText("Backup deleted.")
        self.refresh_backups()
        self.refresh_storage()

    # ==================================================================
    # Actions: backup / restore / encryption
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
        self.btn_changepw.setVisible(bool(dev and dev.will_encrypt))
        self.btn_changepw.setEnabled(ready)
        self.pair_button.setEnabled(not busy)
        selected = self._selected_backup()
        self.btn_restore_sel.setEnabled(ready and selected is not None)
        self.btn_delete.setEnabled(not busy and selected is not None)
        self.btn_view.setEnabled(selected is not None)
        self.btn_choose.setEnabled(not busy)
        self.cb_guard.setEnabled(not busy)

    def _require_device(self) -> DeviceInfo | None:
        dev = self.current_device()
        if dev is None:
            QMessageBox.information(self, APP_NAME, "Select a device first.")
            return None
        if not dev.paired:
            QMessageBox.information(self, APP_NAME, "The device is not paired yet. Tap "
                                    "“Trust” on the device.")
            return None
        return dev

    # --- backup -------------------------------------------------------
    def start_backup(self) -> None:
        dev = self._require_device()
        if dev is None or self._busy():
            return
        required = dev.used_bytes
        existing = self._backup_for(dev.udid)
        if required and existing is not None:
            required = max(0, required - self.backup_sizes.get(existing.path, 0))
        self.btn_backup.setEnabled(False)
        self.status_label.setText("Checking backup location…")
        run_async(check_storage, self.settings.backup_dir, self.settings.expected_mount or None,
                  required, True, on_done=lambda st: self._backup_storage_checked(dev, st))

    def _backup_storage_checked(self, dev: DeviceInfo, st: StorageStatus) -> None:
        self._on_storage_status(st)
        self._update_actions()
        if st.level == ERROR:
            self.status_label.setText("Backup not started.")
            QMessageBox.critical(self, "Backup Location Unavailable", st.message)
            return
        if st.level == WARN:
            answer = QMessageBox.question(self, "Low Disk Space",
                                          st.message + "\n\nBack up anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                self.status_label.setText("Backup not started.")
                return
        if dev.udid not in self.devices:
            self.status_label.setText("The device is no longer connected.")
            return
        cmd = tools.backup_cmd(dev.udid, st.path, full=self.cb_full.isChecked())
        self._start_job(ToolJob("backup", cmd), dev.udid,
                        f"Backing up “{dev.display_name}”…")

    # --- restore ------------------------------------------------------
    def start_restore(self, entry: BackupEntry | None = None) -> None:
        dev = self._require_device()
        if dev is None or self._busy():
            return
        if not self.backups:
            QMessageBox.information(self, APP_NAME, "No backups found in the backup location.")
            return
        dlg = RestoreDialog(dev, self.backups, preselect=entry, parent=self)
        if dlg.exec() != RestoreDialog.DialogCode.Accepted:
            return
        backup = dlg.selected()
        if backup is None:
            return
        passwords = {}
        if backup.encrypted:
            pw = PasswordDialog("current", self, "This backup is encrypted. Enter the backup "
                                "password.")
            if pw.exec() != PasswordDialog.DialogCode.Accepted:
                return
            passwords = pw.passwords()
        cmd = tools.restore_cmd(dev.udid, backup.root, source_udid=backup.udid, **dlg.options())
        self._start_job(ToolJob("restore", cmd, passwords), dev.udid,
                        f"Restoring “{dev.display_name}” – do not disconnect…")

    # --- encryption ---------------------------------------------------
    def toggle_encryption(self, checked: bool) -> None:
        dev = self._require_device()
        # only show the new state once it actually succeeded
        self.cb_encrypt.blockSignals(True)
        self.cb_encrypt.setChecked(not checked)
        self.cb_encrypt.blockSignals(False)
        if dev is None or self._busy():
            return
        if checked:
            dlg = PasswordDialog("new", self, "Choose a password for encrypted backups. You "
                                 "may need to enter the device passcode on the iPhone/iPad "
                                 "afterwards.")
        else:
            dlg = PasswordDialog("current", self, "Turning off encryption requires the current "
                                 "backup password.")
        if dlg.exec() != PasswordDialog.DialogCode.Accepted:
            return
        self._start_job(ToolJob("encryption", tools.encryption_cmd(dev.udid, checked),
                                dlg.passwords()), dev.udid,
                        "Turning on encryption… (enter the passcode on the device if asked)"
                        if checked else "Turning off encryption…")

    def change_password(self) -> None:
        dev = self._require_device()
        if dev is None or self._busy():
            return
        dlg = PasswordDialog("change", self)
        if dlg.exec() != PasswordDialog.DialogCode.Accepted:
            return
        self._start_job(ToolJob("changepw", tools.changepw_cmd(dev.udid), dlg.passwords()),
                        dev.udid, "Changing backup password… (enter the passcode on the "
                        "device if asked)")

    # ==================================================================
    # Running operation
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
        self.progress.setRange(0, 0)  # indeterminate until progress is reported
        self.progress.show()
        self.btn_cancel.show()
        self.btn_cancel.setEnabled(True)
        job.start()
        self._update_actions()

    def _on_job_progress(self, pct: float, detail: str) -> None:
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(int(pct))
        self.detail_label.setText(f"{detail}  ·  {int(pct)} %" if detail else f"{int(pct)} %")

    def _on_job_status(self, text: str) -> None:
        self.status_label.setText(text)

    def cancel_job(self) -> None:
        if not self._busy():
            return
        if self.job.kind == "restore":
            answer = QMessageBox.warning(
                self, "Cancel Restore?",
                "Cancelling a restore can leave the device in an incomplete state. "
                "Cancel anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.btn_cancel.setEnabled(False)
        self.status_label.setText("Cancelling…")
        self.job.cancel()

    def _on_job_finished(self, ok: bool, message: str) -> None:
        job, udid = self.job, self.job_udid
        self.btn_cancel.hide()
        self.progress.hide()
        self.detail_label.setText("")
        if udid:
            self.monitor.set_busy(udid, False)
        self.log_line(f"→ {'Succeeded' if ok else 'Failed: ' + message}")

        texts = {
            "backup": ("Backup completed.", "Backup Failed"),
            "restore": ("Restore completed. The device is restarting.", "Restore Failed"),
            "encryption": ("Backup encryption setting saved.", "Could Not Change Encryption"),
            "changepw": ("Backup password changed.", "Could Not Change Password"),
        }
        good, bad = texts.get(job.kind if job else "", ("Done.", "Error"))
        self._update_actions()
        if ok:
            self.status_label.setText(good)
            if job and job.kind in ("backup", "restore"):
                QMessageBox.information(self, APP_NAME, good)
        else:
            self.status_label.setText(f"{bad}: {message}")
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
                self, APP_NAME, "An operation is still running. Cancel it and quit?",
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
