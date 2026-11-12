from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QVBoxLayout,
)

from overlay.appearance import apply_overlay_stylesheet


class RegionNameDialog(QDialog):
    def __init__(self, role_names: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("region_name_dialog")
        self.setWindowTitle("Choose region role")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        apply_overlay_stylesheet(self)
        self.setMinimumWidth(300)

        self.panel = QFrame(self)
        self.panel.setObjectName("region_name_panel")
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("Choose region role", self.panel)
        title.setObjectName("region_name_title")
        layout.addWidget(title)
        layout.addWidget(
            QLabel("Choose what this screen region contains:", self.panel)
        )

        self.role_input = QComboBox(self.panel)
        self.role_input.setObjectName("region_role_input")
        self.role_input.setEditable(False)
        self.role_input.addItems(role_names)
        layout.addWidget(self.role_input)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self.panel,
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(bool(role_names))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(2, 2, 2, 2)
        outer_layout.addWidget(self.panel)
        self.adjustSize()

    @property
    def region_name(self) -> str:
        return self.role_input.currentText().strip()
