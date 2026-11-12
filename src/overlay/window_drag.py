from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget


class OverlayWindowDrag(QObject):
    """Move a frameless overlay by dragging a designated surface."""

    def __init__(
        self,
        window: QWidget,
        surfaces: tuple[QWidget, ...],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._press_position: QPoint | None = None
        self._window_position = QPoint()
        self._dragging = False
        self._bounds = QRect()
        for surface in surfaces:
            surface.installEventFilter(self)

    def set_bounds(self, bounds: QRect) -> None:
        self._bounds = QRect(bounds)
        if self._bounds.isValid():
            self._window.move(self._clamp_position(self._window.pos()))

    def _clamp_position(self, position: QPoint) -> QPoint:
        if not self._bounds.isValid():
            return position
        maximum_x = max(
            self._bounds.left(),
            self._bounds.right() - self._window.width() + 1,
        )
        maximum_y = max(
            self._bounds.top(),
            self._bounds.bottom() - self._window.height() + 1,
        )
        return QPoint(
            max(self._bounds.left(), min(position.x(), maximum_x)),
            max(self._bounds.top(), min(position.y(), maximum_y)),
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        del watched
        if event.type() == QEvent.Type.MouseButtonPress:
            mouse_event = event
            if (
                isinstance(mouse_event, QMouseEvent)
                and mouse_event.button() == Qt.MouseButton.LeftButton
            ):
                self._press_position = mouse_event.globalPosition().toPoint()
                self._window_position = self._window.pos()
                self._dragging = False
            return False

        if event.type() == QEvent.Type.MouseMove and self._press_position is not None:
            mouse_event = event
            if not isinstance(mouse_event, QMouseEvent):
                return False
            current_position = mouse_event.globalPosition().toPoint()
            if (
                not self._dragging
                and (current_position - self._press_position).manhattanLength()
                < QApplication.startDragDistance()
            ):
                return False
            self._dragging = True
            self._window.move(
                self._clamp_position(
                    self._window_position + current_position - self._press_position
                )
            )
            self._window.setProperty("_overlay_user_moved", True)
            return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            was_dragging = self._dragging
            self._press_position = None
            self._dragging = False
            return was_dragging

        return False
