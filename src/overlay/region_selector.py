from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class RegionSelectionWindow(QWidget):
    """Transparent virtual-desktop surface for selecting a screen rectangle."""

    selection_completed = Signal(QRect)
    selection_cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Select screen region")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._start: QPoint | None = None
        self._current = QPoint()
        self._selection_bounds = QRect()
        self._override_cursor_active = False
        self.hide()

    def begin_selection(self, selection_bounds: QRect) -> None:
        if not selection_bounds.isValid():
            raise ValueError("The game window bounds are invalid.")
        self._start = None
        self._current = QPoint()
        self._selection_bounds = QRect(selection_bounds)
        self.setGeometry(selection_bounds)
        QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)
        self._override_cursor_active = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.globalPosition().toPoint()
            if not self._selection_bounds.contains(position):
                event.ignore()
                return
            self._start = position
            self._current = self._start
            self.grabMouse()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._start is not None:
            self._current = self._clamp_to_game_window(
                event.globalPosition().toPoint()
            )
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._start is not None:
            start = self._start
            end = self._clamp_to_game_window(event.globalPosition().toPoint())
            self._start = None
            selected = QRect(start, end).normalized()
            self.releaseMouse()
            self.hide()
            self._restore_cursor()
            if selected.width() >= 8 and selected.height() >= 8:
                self.selection_completed.emit(selected)
            else:
                self.selection_cancelled.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clamp_to_game_window(self, point: QPoint) -> QPoint:
        return QPoint(
            max(self._selection_bounds.left(), min(point.x(), self._selection_bounds.right())),
            max(self._selection_bounds.top(), min(point.y(), self._selection_bounds.bottom())),
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._start = None
            self.releaseMouse()
            self.hide()
            self._restore_cursor()
            self.selection_cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(31, 41, 55, 28))
        prompt = QRect(16, 16, min(360, max(200, self.width() - 32)), 38)
        painter.setPen(QPen(QColor(255, 255, 255, 170), 1))
        painter.setBrush(QColor(17, 24, 39, 215))
        painter.drawRoundedRect(prompt, 10, 10)
        painter.setPen(QColor(249, 250, 251))
        painter.drawText(
            prompt.adjusted(12, 0, -8, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "Drag to select a game region  •  Esc to cancel",
        )
        if self._start is None:
            return

        selection = QRect(
            self.mapFromGlobal(self._start), self.mapFromGlobal(self._current)
        ).normalized()
        painter.fillRect(selection, QColor(56, 189, 248, 105))
        painter.setPen(QPen(QColor(34, 211, 238, 245), 2))
        painter.drawRect(selection.adjusted(1, 1, -1, -1))

    def _restore_cursor(self) -> None:
        if self._override_cursor_active:
            QApplication.restoreOverrideCursor()
            self._override_cursor_active = False

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._restore_cursor()
