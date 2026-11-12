from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QToolButton


class HoverToolButton(QToolButton):
    hovered = Signal()
    unhovered = Signal()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.hovered.emit()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.unhovered.emit()
