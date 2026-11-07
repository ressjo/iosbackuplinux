"""Fotos & Videos aus der Kamerarolle und deren Vorschaubilder."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPolygonF

from .reader import BackupReader, Entry

IMAGE_EXT = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".dng",
             ".webp", ".bmp"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".3gp"}
CAMERA_DOMAIN = "CameraRollDomain"
_HEIF = {".heic", ".heif"}


@dataclass
class MediaItem:
    entry: Entry
    mtime: float | None
    size: int | None
    is_video: bool

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def month_key(self) -> str:
        if not self.mtime:
            return "unbekannt"
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m")


def ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def is_media_entry(e: Entry) -> bool:
    if not e.is_file or e.domain != CAMERA_DOMAIN or not e.path.startswith("Media/DCIM/"):
        return False
    ext = ext_of(e.path)
    return ext in IMAGE_EXT or ext in VIDEO_EXT


def collect_media(reader: BackupReader) -> list[MediaItem]:
    items = []
    for e in reader.entries:
        if is_media_entry(e):
            meta = reader.meta(e)
            items.append(MediaItem(e, meta.mtime, meta.size, ext_of(e.path) in VIDEO_EXT))
    items.sort(key=lambda m: (m.mtime or 0, m.entry.path), reverse=True)
    return items


MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def month_label(key: str) -> str:
    if key == "unbekannt":
        return "Ohne Datum"
    year, month = key.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


# ---------------------------------------------------------------------------
# Vorschaubilder (laufen im Threadpool – nur QImage verwenden, kein QPixmap)
# ---------------------------------------------------------------------------

def heif_converter() -> list[str] | None:
    for tool in ("heif-dec", "heif-convert"):
        path = shutil.which(tool)
        if path:
            return [path]
    return None


def _qt_can_read_heif() -> bool:
    fmts = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
    return "heic" in fmts or "heif" in fmts


def _scaled_from_reader(reader: QImageReader, size: int) -> QImage | None:
    reader.setAutoTransform(True)
    src = reader.size()
    if src.isValid() and src.width() > 0 and src.height() > 0:
        reader.setScaledSize(src.scaled(QSize(size * 2, size * 2), Qt.AspectRatioMode.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        return None
    return img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def _image_from_bytes(data: bytes, size: int) -> QImage | None:
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    return _scaled_from_reader(QImageReader(buf), size)


def _image_from_file(path: str, size: int) -> QImage | None:
    return _scaled_from_reader(QImageReader(path), size)


def _run(cmd: list[str], timeout: float = 60) -> bool:
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def draw_play_badge(img: QImage) -> QImage:
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = max(12, min(img.width(), img.height()) // 6)
    cx, cy = img.width() / 2, img.height() / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 140))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setBrush(QColor(255, 255, 255, 230))
    p.drawPolygon(QPolygonF([QPointF(cx - r * 0.35, cy - r * 0.5),
                             QPointF(cx - r * 0.35, cy + r * 0.5),
                             QPointF(cx + r * 0.55, cy)]))
    p.end()
    return img


def make_thumbnail(reader: BackupReader, item: MediaItem, size: int = 160,
                   cache_dir: str | None = None) -> QImage | None:
    cache_file = None
    if cache_dir:
        cache_file = os.path.join(cache_dir, f"{item.entry.file_id}.jpg")
        if os.path.exists(cache_file):
            img = QImage(cache_file)
            if not img.isNull():
                return img

    img: QImage | None = None
    ext = ext_of(item.name)
    if item.is_video:
        tool = shutil.which("ffmpegthumbnailer")
        if tool:
            src = reader.temp_copy(item.entry)
            with tempfile.TemporaryDirectory(dir=os.path.dirname(src)) as td:
                out = os.path.join(td, "t.png")
                if _run([tool, "-i", src, "-o", out, "-s", str(size * 2)]):
                    img = _image_from_file(out, size)
        if img is not None:
            img = draw_play_badge(img)
    elif ext in _HEIF and not _qt_can_read_heif():
        conv = heif_converter()
        if conv:
            src = reader.temp_copy(item.entry)
            with tempfile.TemporaryDirectory(dir=os.path.dirname(src)) as td:
                out = os.path.join(td, "t.jpg")
                if _run(conv + [src, out]):
                    img = _image_from_file(out, size)
    else:
        img = _image_from_bytes(reader.read_bytes(item.entry), size)

    if img is not None and cache_file:
        try:
            os.makedirs(os.path.dirname(cache_file), mode=0o700, exist_ok=True)
            img.save(cache_file, "JPG", 85)
        except OSError:
            pass
    return img
