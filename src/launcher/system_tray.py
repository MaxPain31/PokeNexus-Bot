from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from launcher.styles.style_loader import load_stylesheet
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class SystemTrayController(QObject):
    """Manage the launcher tray icon and its restore/exit actions."""

    restore_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.icon = QSystemTrayIcon(self)
        self.icon.setIcon(
            load_svg_icon(application_path("assets", "nexus-mark.svg"), QSize(32, 32))
        )
        self.icon.setToolTip("Game Bot")

        self._menu = QMenu()
        self._menu.setStyleSheet(load_stylesheet("main_window.qss"))
        restore_action = QAction("Show Game Bot", self._menu)
        restore_action.triggered.connect(self.restore_requested)
        self._menu.addAction(restore_action)
        self._menu.addSeparator()
        quit_action = QAction("Quit", self._menu)
        quit_action.triggered.connect(self.quit_requested)
        self._menu.addAction(quit_action)
        self.icon.setContextMenu(self._menu)
        self.icon.activated.connect(self._on_activated)

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_requested.emit()
