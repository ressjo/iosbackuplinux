"""Small, palette-based styling helpers.

Everything derives from the active Qt palette, so the app follows the desktop theme
(Breeze light/dark on KDE) instead of shipping its own colours.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPalette
from PySide6.QtWidgets import (QApplication, QFrame, QLabel, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem)

STYLESHEET = """
QPushButton#primary {
    background: palette(highlight);
    color: palette(highlighted-text);
    border: 1px solid palette(highlight);
    border-radius: 4px;
    padding: 6px 18px;
    font-weight: 600;
}
QPushButton#primary:hover { border-color: palette(highlighted-text); }
QPushButton#primary:disabled {
    background: palette(button);
    color: palette(placeholder-text);
    border-color: palette(mid);
}
QProgressBar#meter {
    border: none;
    border-radius: 3px;
    background: palette(midlight);
    max-height: 6px;
    min-height: 6px;
}
QProgressBar#meter::chunk { background: palette(highlight); border-radius: 3px; }
QLabel[muted="true"] { color: palette(placeholder-text); }
QFrame#banner {
    background: palette(alternate-base);
    border: 1px solid palette(mid);
    border-radius: 6px;
}
QListView#devices { border: none; background: transparent; }
"""

OK_COLOR, WARN_COLOR, ERROR_COLOR = "#27ae60", "#f67400", "#da4453"  # Breeze accents


def muted(text: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("muted", True)
    lbl.setWordWrap(wrap)
    return lbl


def heading(text: str, scale: float = 1.0) -> QLabel:
    lbl = QLabel(text)
    f = QFont(lbl.font())
    f.setWeight(QFont.Weight.DemiBold)
    if scale != 1.0:
        f.setPointSizeF(f.pointSizeF() * scale)
    lbl.setFont(f)
    return lbl


def section(text: str) -> QLabel:
    """Small, uppercase section caption (as used in KDE System Settings sidebars)."""
    lbl = QLabel(text.upper())
    f = QFont(lbl.font())
    f.setPointSizeF(f.pointSizeF() * 0.85)
    f.setWeight(QFont.Weight.DemiBold)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
    lbl.setFont(f)
    lbl.setProperty("muted", True)
    return lbl


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def dot(color: str) -> str:
    return f"<span style='color:{color};'>●</span>"


class TwoLineDelegate(QStyledItemDelegate):
    """List item with icon, bold title and a muted subtitle (``Qt.UserRole + 1``)."""

    SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 1

    def sizeHint(self, option, index) -> QSize:
        fm = option.fontMetrics
        return QSize(200, max(44, fm.height() * 2 + 16))

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        title, opt.text = opt.text, ""
        icon = QIcon(opt.icon)  # copy: opt.icon is a reference into the option struct
        opt.icon = QIcon()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)

        r = opt.rect.adjusted(8, 0, -8, 0)
        isz = 32
        icon.paint(painter, QRect(r.left(), r.center().y() - isz // 2, isz, isz))
        text_left = r.left() + isz + 10
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        pal = opt.palette
        title_color = pal.color(QPalette.ColorRole.HighlightedText if selected
                                else QPalette.ColorRole.Text)
        sub_color = pal.color(QPalette.ColorRole.HighlightedText if selected
                              else QPalette.ColorRole.PlaceholderText)

        painter.save()
        f = QFont(opt.font)
        f.setWeight(QFont.Weight.DemiBold)
        painter.setFont(f)
        painter.setPen(title_color)
        fm = painter.fontMetrics()
        line_h = fm.height()
        top = r.center().y() - line_h
        tr = QRect(text_left, top, r.right() - text_left, line_h)
        painter.drawText(tr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(title, Qt.TextElideMode.ElideRight, tr.width()))
        subtitle = index.data(self.SUBTITLE_ROLE) or ""
        painter.setFont(opt.font)
        painter.setPen(sub_color)
        fm = painter.fontMetrics()
        sr = QRect(text_left, top + line_h, r.right() - text_left, line_h)
        painter.drawText(sr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(subtitle, Qt.TextElideMode.ElideRight, sr.width()))
        painter.restore()
