from PySide6.QtCore import QObject, QSize, Signal, Qt
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QToolButton,
    QWidget,
)

from utils.logger import log_message
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


ASSETS_DIR = application_path("assets")


class _LogHighlighter(QSyntaxHighlighter):
    """Highlight the existing log prefixes without changing their plain text."""

    def highlightBlock(self, text: str) -> None:
        timestamp_end = text.find("]")
        level_end = text.find("]", timestamp_end + 1)
        if not text.startswith("[") or level_end < 0:
            return
        timestamp_format = QTextCharFormat()
        timestamp_format.setForeground(QColor("#8295AE"))
        self.setFormat(0, timestamp_end + 1, timestamp_format)
        level_start = timestamp_end + 2
        level = text[level_start + 1 : level_end]
        color = {
            "INFO": "#7DD3FC",
            "SUCCESS": "#5EEAD4",
            "WARNING": "#FCD34D",
            "ERROR": "#FDA4AF",
            "APP ERROR": "#FDA4AF",
        }.get(level, "#C5D3E5")
        level_format = QTextCharFormat()
        level_format.setForeground(QColor(color))
        self.setFormat(level_start, level_end - level_start + 1, level_format)


class LogViewer(QObject):
    """Log output view with show/hide and clear controls."""

    visibility_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.text_edit = QPlainTextEdit(parent)
        self.text_edit.setObjectName("log_panel")
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText(
            "Session events and diagnostics will appear here."
        )
        self.text_edit.setAccessibleName("Session activity log")
        self.text_edit.document().setMaximumBlockCount(1000)
        self.text_edit.setFixedHeight(140)
        self._highlighter = _LogHighlighter(self.text_edit.document())
        self.text_edit.hide()

        self.controls = QWidget(parent)
        self.toggle_button = self._create_button(
            "logs_button",
            "list_alt_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            "Show logs",
            self.controls,
        )
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle_button.setText("Show log")
        self.toggle_button.setFixedWidth(100)
        self.clear_button = self._create_button(
            "clear_logs_button",
            "delete_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            "Clear logs",
            self.controls,
        )
        self.clear_button.hide()
        self.clear_button.setEnabled(False)

        self.controls_layout = QHBoxLayout(self.controls)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.setSpacing(6)
        self.controls_layout.addWidget(self.clear_button)
        self.controls_layout.addWidget(self.toggle_button)

        self.toggle_button.toggled.connect(self._set_visible)
        self.clear_button.clicked.connect(self.text_edit.clear)
        self.text_edit.textChanged.connect(
            lambda: self.clear_button.setEnabled(not self.text_edit.document().isEmpty())
        )

    @staticmethod
    def _create_button(
        object_name: str,
        icon_name: str,
        tooltip: str,
        parent: QWidget,
    ) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName(object_name)
        button.setIcon(
            load_svg_icon(ASSETS_DIR / icon_name, QSize(20, 20), fill_color="#E5E8EE")
        )
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(32, 32)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def append(self, level: str, message: str) -> None:
        log_message(self.text_edit, level, message)

    def _set_visible(self, visible: bool) -> None:
        self.text_edit.setVisible(visible)
        self.clear_button.setVisible(visible)
        label = "Hide logs" if visible else "Show logs"
        self.toggle_button.setText("Hide log" if visible else "Show log")
        self.toggle_button.setToolTip(label)
        self.toggle_button.setAccessibleName(label)
        self.visibility_changed.emit(visible)
