"""Window for browsing a backup: photos & videos and all files."""

from __future__ import annotations

import os

from PySide6.QtCore import (QAbstractItemModel, QAbstractListModel, QModelIndex, QObject,
                            QRect, QRunnable, QSize, QSortFilterProxyModel, Qt, QThreadPool,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QCloseEvent, QColor, QDesktopServices, QFont, QIcon, QImage,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFileIconProvider,
                               QHBoxLayout, QHeaderView, QLineEdit, QListView,
                               QMainWindow, QMessageBox, QProgressDialog, QPushButton,
                               QStackedWidget, QTabWidget, QTreeView, QVBoxLayout, QWidget)

from ..backups import BackupEntry
from ..fmt import format_bytes, format_datetime
from ..viewer.export import ExportJob
from ..viewer.media import MediaItem, make_thumbnail, month_label
from ..viewer.reader import BackupReader, Entry
from ..viewer.tree import Node
from .async_util import run_async
from .style import heading, muted

THUMB = 160


def _ts(mtime: float | None) -> str:
    if not mtime:
        return "–"
    from datetime import datetime, timezone
    return format_datetime(datetime.fromtimestamp(mtime, tz=timezone.utc))


def placeholder_icon(label: str, size: int = THUMB) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRect(size // 8, size // 8, size * 3 // 4, size * 3 // 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(127, 140, 141, 70))
    p.drawRoundedRect(rect, 10, 10)
    p.setPen(QColor(110, 120, 125))
    f = QFont()
    f.setPointSize(11)
    f.setBold(True)
    p.setFont(f)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    return QIcon(pm)


def thumb_cache_dir(udid: str) -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "iosbackuplinux", "thumbs", udid)


# ===========================================================================
# Photos & videos
# ===========================================================================
class _ThumbSignals(QObject):
    done = Signal(str, object)  # file_id, QImage | None


class _ThumbTask(QRunnable):
    def __init__(self, reader: BackupReader, item: MediaItem, cache_dir: str | None,
                 signals: _ThumbSignals):
        super().__init__()
        self.reader, self.item, self.cache_dir, self.signals = reader, item, cache_dir, signals

    def run(self) -> None:
        try:
            img = make_thumbnail(self.reader, self.item, THUMB, self.cache_dir)
        except Exception:
            img = None
        self.signals.done.emit(self.item.entry.file_id, img)


class PhotoModel(QAbstractListModel):
    def __init__(self, reader: BackupReader, cache_dir: str | None, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.cache_dir = cache_dir
        self.items: list[MediaItem] = []
        self._rows: dict[str, int] = {}
        self._icons: dict[str, QIcon] = {}
        self._pending: set[str] = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self._signals = _ThumbSignals(self)
        self._signals.done.connect(self._on_thumb)
        self._ph_photo = placeholder_icon("Photo")
        self._ph_video = placeholder_icon("Video")
        self._ph_heic = placeholder_icon("HEIC")

    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self.items = items
        self._rows = {m.entry.file_id: i for i, m in enumerate(items)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        m = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return m.name
        if role == Qt.ItemDataRole.DecorationRole:
            icon = self._icons.get(m.entry.file_id)
            if icon is not None:
                return icon
            self._request(m)
            return self._ph_video if m.is_video else self._ph_photo
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{m.name}\n{_ts(m.mtime)}\n{format_bytes(m.size)}"
        if role == Qt.ItemDataRole.UserRole:
            return m
        return None

    def _request(self, m: MediaItem) -> None:
        fid = m.entry.file_id
        if fid in self._pending:
            return
        self._pending.add(fid)
        self.pool.start(_ThumbTask(self.reader, m, self.cache_dir, self._signals))

    def _on_thumb(self, file_id: str, img) -> None:
        row = self._rows.get(file_id)
        m = self.items[row] if row is not None else None
        if isinstance(img, QImage) and not img.isNull():
            self._icons[file_id] = QIcon(QPixmap.fromImage(img))
        elif m is not None:
            ext = os.path.splitext(m.name)[1].lower()
            self._icons[file_id] = (self._ph_video if m.is_video else
                                    self._ph_heic if ext in (".heic", ".heif") else self._ph_photo)
        else:
            self._pending.discard(file_id)
            return
        if row is not None:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    def stop(self) -> None:
        self.pool.clear()
        self.pool.waitForDone(5000)


class PhotosTab(QWidget):
    def __init__(self, window: "BackupViewerWindow", media: list[MediaItem], cache_dir):
        super().__init__()
        self.win = window
        self.media = media
        v = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Photos and Videos", "Photos", "Videos"])
        self.month_combo = QComboBox()
        self.month_combo.addItem("All months", "")
        months = []
        for m in media:
            if m.month_key not in months:
                months.append(m.month_key)
        for key in months:
            self.month_combo.addItem(month_label(key), key)
        self.count_label = muted()
        bar.addWidget(self.type_combo)
        bar.addWidget(self.month_combo)
        bar.addWidget(self.count_label, 1)
        self.btn_all = QPushButton("Select All")
        self.btn_open = QPushButton(QIcon.fromTheme("document-open"), "Open")
        self.btn_export = QPushButton(QIcon.fromTheme("document-export"), "Export…")
        for b in (self.btn_all, self.btn_open, self.btn_export):
            bar.addWidget(b)
        v.addLayout(bar)

        self.stack = QStackedWidget()
        self.model = PhotoModel(window.reader, cache_dir, self)
        self.view = QListView()
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setIconSize(QSize(THUMB, THUMB))
        self.view.setGridSize(QSize(THUMB + 24, THUMB + 34))
        self.view.setUniformItemSizes(True)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setWordWrap(False)
        self.view.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setModel(self.model)
        self.stack.addWidget(self.view)
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        t = heading("No photos or videos in this backup", 1.2)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(t)
        hint = muted("If iCloud Photos is turned on, iOS keeps photos in iCloud and leaves them "
                     "out of local backups.", wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(hint)
        ev.addStretch(2)
        self.stack.addWidget(empty)
        v.addWidget(self.stack, 1)

        self.type_combo.currentIndexChanged.connect(self.apply_filter)
        self.month_combo.currentIndexChanged.connect(self.apply_filter)
        self.view.selectionModel().selectionChanged.connect(self._update_buttons)
        self.view.doubleClicked.connect(lambda idx: self.win.open_entry(self._item(idx).entry))
        self.btn_all.clicked.connect(self.view.selectAll)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_export.clicked.connect(self._export_selected)
        self.apply_filter()

    def _item(self, idx: QModelIndex) -> MediaItem:
        return self.model.items[idx.row()]

    def selected_items(self) -> list[MediaItem]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedIndexes()})
        return [self.model.items[r] for r in rows]

    def apply_filter(self) -> None:
        kind = self.type_combo.currentIndex()
        month = self.month_combo.currentData()
        items = [m for m in self.media
                 if (kind == 0 or (kind == 1) != m.is_video)
                 and (not month or m.month_key == month)]
        self.model.set_items(items)
        photos = sum(1 for m in items if not m.is_video)
        videos = len(items) - photos
        self.count_label.setText(f"{photos} photo{'s' if photos != 1 else ''} · "
                                 f"{videos} video{'s' if videos != 1 else ''}")
        self.stack.setCurrentIndex(0 if self.media else 1)
        for w in (self.type_combo, self.month_combo, self.btn_all):
            w.setEnabled(bool(self.media))
        self._update_buttons()

    def _update_buttons(self, *_) -> None:
        n = len(self.view.selectionModel().selectedIndexes())
        self.btn_export.setEnabled(n > 0)
        self.btn_export.setText(f"Export {n}…" if n else "Export…")
        self.btn_open.setEnabled(n == 1)

    def _open_selected(self) -> None:
        items = self.selected_items()
        if items:
            self.win.open_entry(items[0].entry)

    def _export_selected(self) -> None:
        items = self.selected_items()
        if items:
            self.win.export([(m.entry, m.name) for m in items])


# ===========================================================================
# File browser
# ===========================================================================
class FileTreeModel(QAbstractItemModel):
    HEADERS = ["Name", "Size", "Modified"]

    def __init__(self, reader: BackupReader, root: Node, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.root = root
        prov = QFileIconProvider()
        self._dir_icon = prov.icon(QFileIconProvider.IconType.Folder)
        self._file_icon = prov.icon(QFileIconProvider.IconType.File)

    def node(self, index: QModelIndex) -> Node:
        return index.internalPointer() if index.isValid() else self.root

    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        p = self.node(parent)
        if 0 <= row < len(p.children):
            return self.createIndex(row, column, p.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        p = index.internalPointer().parent
        if p is None or p is self.root:
            return QModelIndex()
        return self.createIndex(p.row, 0, p)

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self.node(parent).children)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 3

    def hasChildren(self, parent=QModelIndex()) -> bool:
        return bool(self.node(parent).children)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        n: Node = index.internalPointer()
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return n.name
            if n.entry is None or not n.entry.is_file:
                return ""
            meta = self.reader.meta(n.entry)
            return format_bytes(meta.size) if col == 1 else _ts(meta.mtime)
        if role == Qt.ItemDataRole.DecorationRole and col == 0:
            return self._dir_icon if n.is_dir else self._file_icon
        if role == Qt.ItemDataRole.ToolTipRole and n.entry is not None:
            return f"{n.entry.domain}\n{n.entry.path}"
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class FilesTab(QWidget):
    def __init__(self, window: "BackupViewerWindow", root: Node):
        super().__init__()
        self.win = window
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search file names…")
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)
        self.btn_open = QPushButton(QIcon.fromTheme("document-open"), "Open")
        self.btn_export = QPushButton(QIcon.fromTheme("document-export"), "Export…")
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_export)
        v.addLayout(bar)

        self.model = FileTreeModel(window.reader, root, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.view = QTreeView()
        self.view.setModel(self.proxy)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setUniformRowHeights(True)
        self.view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.view.header().setStretchLastSection(False)
        self.view.setColumnWidth(1, 100)
        self.view.setColumnWidth(2, 140)
        v.addWidget(self.view, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda _: self._timer.start())
        self.view.selectionModel().selectionChanged.connect(self._update_buttons)
        self.view.doubleClicked.connect(self._on_double_click)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_export.clicked.connect(self._export_selected)
        self._update_buttons()

    def _apply_search(self) -> None:
        text = self.search.text().strip()
        self.proxy.setFilterFixedString(text)
        if text:
            self.view.expandAll()

    def selected_nodes(self) -> list[Node]:
        nodes = []
        for idx in self.view.selectionModel().selectedRows(0):
            nodes.append(self.model.node(self.proxy.mapToSource(idx)))
        return nodes

    def _update_buttons(self, *_) -> None:
        nodes = self.selected_nodes()
        self.btn_export.setEnabled(bool(nodes))
        self.btn_open.setEnabled(len(nodes) == 1 and nodes[0].entry is not None
                                 and nodes[0].entry.is_file)

    def _on_double_click(self, idx: QModelIndex) -> None:
        n = self.model.node(self.proxy.mapToSource(idx.siblingAtColumn(0)))
        if n.entry is not None and n.entry.is_file:
            self.win.open_entry(n.entry)

    def _open_selected(self) -> None:
        nodes = self.selected_nodes()
        if nodes and nodes[0].entry is not None and nodes[0].entry.is_file:
            self.win.open_entry(nodes[0].entry)

    def _export_selected(self) -> None:
        self.win.export(export_items_for_nodes(self.selected_nodes()))


def export_items_for_nodes(nodes: list[Node]) -> list[tuple[Entry, str]]:
    """Files (recursively) with destination paths relative to each selected node."""
    items, seen = [], set()
    for node in nodes:
        prefix = node.parent.path() if node.parent is not None else ""
        for f in node.iter_files():
            fid = f.entry.file_id
            if fid in seen:
                continue
            seen.add(fid)
            full = f.path()
            rel = full[len(prefix) + 1:] if prefix and full.startswith(prefix + "/") else full
            items.append((f.entry, rel))
    items.sort(key=lambda x: x[1])
    return items


# ===========================================================================
# Window
# ===========================================================================
class BackupViewerWindow(QMainWindow):
    closed = Signal(str)

    def __init__(self, reader: BackupReader, media: list[MediaItem], tree: Node,
                 backup: BackupEntry, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.reader = reader
        self.backup = backup
        self.export_job: ExportJob | None = None
        self._last_dir = os.path.join(os.path.expanduser("~"), "Pictures")
        if not os.path.isdir(self._last_dir):
            self._last_dir = os.path.expanduser("~")

        name = backup.device_name or backup.udid
        self.setWindowTitle(f"{name} – Backup from {format_datetime(backup.last_backup)}")
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(14, 12, 14, 10)
        v.setSpacing(8)
        v.addWidget(heading(name, 1.3))
        v.addWidget(muted(" · ".join(
            x for x in (f"iOS {backup.product_version}" if backup.product_version else "",
                        f"Backup from {format_datetime(backup.last_backup)}",
                        "Encrypted" if reader.encrypted else "") if x)))

        cache = None if reader.encrypted else thumb_cache_dir(backup.udid)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.photos = PhotosTab(self, media, cache)
        self.files = FilesTab(self, tree)
        self.tabs.addTab(self.photos, f"Photos && Videos ({len(media)})")
        self.tabs.addTab(self.files, "Files")
        v.addWidget(self.tabs, 1)
        self.status = muted()
        v.addWidget(self.status)
        self.resize(1100, 750)

    # --- open -----------------------------------------------------------
    def open_entry(self, entry: Entry) -> None:
        self.status.setText(f"Opening “{entry.name}”…")
        run_async(self.reader.temp_copy, entry,
                  on_done=self._opened, on_error=self._open_failed)

    def _opened(self, path: str) -> None:
        self.status.setText("")
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_failed(self, msg: str) -> None:
        self.status.setText("")
        QMessageBox.critical(self, "Could Not Open File", msg)

    # --- export ---------------------------------------------------------
    def export(self, items: list[tuple[Entry, str]]) -> None:
        if not items:
            QMessageBox.information(self, "Export", "The selection contains no files.")
            return
        if self.export_job is not None:
            return
        dest = QFileDialog.getExistingDirectory(self, "Choose Export Folder",
                                                self._last_dir)
        if not dest:
            return
        self._last_dir = dest
        self._start_export(items, dest)

    def _start_export(self, items: list[tuple[Entry, str]], dest: str) -> None:
        job = ExportJob(self.reader, items, dest, self)
        self.export_job = job
        dlg = QProgressDialog("Exporting…", "Cancel", 0, len(items), self)
        dlg.setWindowTitle("Export")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(300)
        dlg.canceled.connect(job.cancel)
        self._progress_dialog = dlg

        def on_progress(done: int, total: int, name: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(done)
            if name:
                dlg.setLabelText(f"Exporting {done + 1} of {total}:\n{name}")

        job.progress.connect(on_progress)
        job.finished.connect(lambda ok, errors, cancelled: self._export_done(dest, ok, errors,
                                                                            cancelled))
        job.start()

    def _export_done(self, dest: str, ok: int, errors: list, cancelled: bool) -> None:
        self._progress_dialog.reset()
        self._progress_dialog.deleteLater()
        self.export_job = None
        text = f"Exported {ok} file{'s' if ok != 1 else ''} to\n{dest}"
        if cancelled:
            text = "Export cancelled. " + text
        if errors:
            text += f"\n\n{len(errors)} error{'s' if len(errors) != 1 else ''}:\n" + "\n".join(errors[:10])
        box = QMessageBox(QMessageBox.Icon.Warning if errors else QMessageBox.Icon.Information,
                          "Export", text, parent=self)
        open_btn = box.addButton("Show in Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(dest))

    # --- close ----------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.export_job is not None:
            self.export_job.cancel()
            self.export_job.wait(10)
        self.photos.model.stop()
        self.reader.close()
        self.closed.emit(self.backup.path)
        event.accept()
