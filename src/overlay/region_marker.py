from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from models.region_config import SavedRegion


class RegionMarkerWindow(QWidget):
    """Visible selection outline that can be dragged or Alt-clicked away."""

    remove_requested = Signal(object)
    move_requested = Signal(object, object)
    selected_changed = Signal(object)

    def __init__(self, region: SavedRegion, bounds: QRect) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.region = region
        self._bounds = QRect(bounds)
        self._drag_offset: QPoint | None = None
        self._initial_geometry: QRect | None = None
        self._selected = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(region.x, region.y, region.width, region.height)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            f"{region.label} — drag to move; Alt-click to remove; "
            "click its region entry to edit size and position"
        )

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(56, 189, 248, 112) if self._selected else QColor(56, 189, 248, 82)
        outline = QColor(253, 224, 71, 255) if self._selected else QColor(34, 211, 238, 245)
        painter.fillRect(self.rect(), fill)
        painter.setPen(QPen(outline, 3 if self._selected else 2))
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

        label_rect = QRect(0, 0, self.width(), self.height()).adjusted(
            4, 4, -4, -4
        )
        if label_rect.width() < 24 or label_rect.height() < 20:
            return

        badge_width = min(
            label_rect.width(),
            max(72, painter.fontMetrics().horizontalAdvance(self.region.label) + 24),
        )
        badge_height = min(30, label_rect.height())
        badge = QRect(0, 0, badge_width, badge_height)
        badge.moveCenter(label_rect.center())

        painter.setPen(QPen(QColor(186, 230, 253, 210), 1))
        painter.setBrush(QColor(15, 23, 42, 205))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setPen(QColor(249, 250, 251))
        painter.drawText(
            badge.adjusted(8, 2, -8, -2),
            Qt.AlignmentFlag.AlignCenter,
            self.region.label,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self.remove_requested.emit(self.region)
            self.close()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.selected_changed.emit(self)
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._initial_geometry = QRect(self.geometry())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            event.ignore()
            return

        point = event.globalPosition().toPoint() - self._drag_offset
        x = max(
            self._bounds.left(),
            min(point.x(), self._bounds.right() - self.width() + 1),
        )
        y = max(
            self._bounds.top(),
            min(point.y(), self._bounds.bottom() - self.height() + 1),
        )
        self.move(x, y)
        event.accept()

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        direction = {
            Qt.Key.Key_Left: QPoint(-1, 0),
            Qt.Key.Key_Right: QPoint(1, 0),
            Qt.Key.Key_Up: QPoint(0, -1),
            Qt.Key.Key_Down: QPoint(0, 1),
        }.get(event.key())
        if direction is None:
            super().keyPressEvent(event)
            return

        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        x = max(
            self._bounds.left(),
            min(
                self.x() + direction.x() * step,
                self._bounds.right() - self.width() + 1,
            ),
        )
        y = max(
            self._bounds.top(),
            min(
                self.y() + direction.y() * step,
                self._bounds.bottom() - self.height() + 1,
            ),
        )
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)
            self.move_requested.emit(self.region, QRect(self.geometry()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            initial_geometry = self._initial_geometry
            self._drag_offset = None
            self._initial_geometry = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if initial_geometry is not None and self.geometry() != initial_geometry:
                self.move_requested.emit(self.region, QRect(self.geometry()))
            event.accept()
            return
        event.ignore()
