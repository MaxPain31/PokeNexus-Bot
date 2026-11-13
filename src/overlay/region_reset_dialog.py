from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QLabel, QPushButton, QVBoxLayout

from overlay.appearance import apply_overlay_stylesheet


class RegionResetDialog(QDialog):
    """Explicit, default-cancel confirmation for deleting battle regions."""

    def __init__(self, region_count: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("region_reset_dialog")
        self.setWindowTitle("Reset battle regions?")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        apply_overlay_stylesheet(self)
        self.setFixedWidth(380)

        panel = QFrame(self)
        panel.setObjectName("region_reset_panel")
        title = QLabel("Reset battle regions?", panel)
        title.setObjectName("region_reset_title")
        warning = QLabel(
            f"This will remove all {region_count} placed battle regions and their "
            "position/size adjustments. Running automation will be paused.\n\n"
            "Synchronize, Fishing and Special Pokemon Message regions, settings, statistics "
            "and history will be kept.\n\n"
            "Enter a battle and choose Auto Place to place the battle regions again.",
            panel,
        )
        warning.setObjectName("region_reset_warning")
        warning.setWordWrap(True)
        warning.setTextFormat(Qt.TextFormat.PlainText)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=panel)
        self.reset_button = QPushButton("Reset Battle Regions", panel)
        self.reset_button.setObjectName("region_reset_confirm_button")
        self.reset_button.setAutoDefault(False)
        buttons.addButton(self.reset_button, QDialogButtonBox.ButtonRole.DestructiveRole)
        self.reset_button.clicked.connect(self.accept)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        buttons.rejected.connect(self.reject)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(12)
        panel_layout.addWidget(title)
        panel_layout.addWidget(warning)
        panel_layout.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(panel)
        self.adjustSize()
