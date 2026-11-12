from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from models.region_config import SavedRegion
from overlay.appearance import apply_overlay_stylesheet


class RegionEditDialog(QDialog):
    geometry_changed = Signal(object)

    def __init__(
        self,
        region: SavedRegion,
        game_geometry: QRect,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("region_edit_dialog")
        self.setWindowTitle(f"Edit {region.label} region")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        apply_overlay_stylesheet(self)
        self.setMinimumWidth(300)

        self.panel = QFrame(self)
        self.panel.setObjectName("region_edit_panel")
        self.title = QLabel(f"Edit {region.label} region", self.panel)
        self.title.setObjectName("region_edit_title")
        self.hint = QLabel(
            "Arrow keys move a selected region by 1 px; Shift moves 10 px.",
            self.panel,
        )
        self.hint.setObjectName("region_edit_hint")
        self.hint.setWordWrap(True)

        form = QFormLayout()
        self.x_input = self._add_spin_box(
            form,
            "X position",
            game_geometry.left(),
            game_geometry.right(),
            region.x,
        )
        self.y_input = self._add_spin_box(
            form,
            "Y position",
            game_geometry.top(),
            game_geometry.bottom(),
            region.y,
        )
        self.width_input = self._add_spin_box(
            form,
            "Width",
            1,
            game_geometry.width(),
            region.width,
        )
        self.height_input = self._add_spin_box(
            form,
            "Height",
            1,
            game_geometry.height(),
            region.height,
        )
        self.x_input.valueChanged.connect(self._update_size_limits)
        self.y_input.valueChanged.connect(self._update_size_limits)
        self._game_geometry = QRect(game_geometry)
        self._update_size_limits()
        self.x_input.valueChanged.connect(self._emit_geometry_changed)
        self.y_input.valueChanged.connect(self._emit_geometry_changed)
        self.width_input.valueChanged.connect(self._emit_geometry_changed)
        self.height_input.valueChanged.connect(self._emit_geometry_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self.panel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(10)
        panel_layout.addWidget(self.title)
        panel_layout.addWidget(self.hint)
        panel_layout.addLayout(form)
        panel_layout.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.panel)
        self.adjustSize()

    @staticmethod
    def _add_spin_box(
        form: QFormLayout,
        label: str,
        minimum: int,
        maximum: int,
        value: int,
    ) -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setObjectName("region_numeric_input")
        spin_box.setRange(minimum, maximum)
        spin_box.setValue(value)
        form.addRow(label, spin_box)
        return spin_box

    def _update_size_limits(self) -> None:
        self.width_input.setMaximum(
            self._game_geometry.right() - self.x_input.value() + 1
        )
        self.height_input.setMaximum(
            self._game_geometry.bottom() - self.y_input.value() + 1
        )

    def _emit_geometry_changed(self) -> None:
        self.geometry_changed.emit(self.edited_geometry)

    @property
    def edited_geometry(self) -> QRect:
        return QRect(
            self.x_input.value(),
            self.y_input.value(),
            self.width_input.value(),
            self.height_input.value(),
        )
