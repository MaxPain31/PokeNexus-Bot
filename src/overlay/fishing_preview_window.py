import numpy as np
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from overlay.appearance import apply_overlay_stylesheet
from overlay.panel_geometry import position_panel
from overlay.window_drag import OverlayWindowDrag


class FishingPreviewWindow(QWidget):
    """Show the live fishing-region image and detected marker positions."""

    closed = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("fishing_preview_window")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Fishing Region Test")
        self._preferred_size = QSize(520, 190)
        self.setFixedSize(self._preferred_size)
        apply_overlay_stylesheet(self)

        container = QFrame(self)
        container.setObjectName("region_test_log_container")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.status_label = QLabel("Waiting for fishing-bar capture...")
        self.status_label.setObjectName("region_test_log_title")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.status_label.setCursor(Qt.CursorShape.SizeAllCursor)
        header = QHBoxLayout()
        header.addWidget(self.status_label, 1)
        close_button = QToolButton(container)
        close_button.setObjectName("fishing_preview_close_button")
        close_button.setText("×")
        close_button.setFixedSize(26, 26)
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.setAccessibleName("Close fishing preview")
        close_button.setToolTip("Close fishing preview")
        close_button.clicked.connect(self.close)
        header.addWidget(close_button)
        layout.addLayout(header)
        self.image_label = QLabel("Live fishing region preview")
        self.image_label.setObjectName("fishing_preview_image")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(QSize(1, 1))
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        layout.addWidget(self.image_label, 1)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(3, 3, 3, 3)
        outer_layout.addWidget(container)
        self._drag_controller = OverlayWindowDrag(self, (container, self.status_label))
        self.hide()

    def show_for_game(self, game_geometry: QRect) -> None:
        if not game_geometry.isValid():
            return
        position_panel(
            self, game_geometry, self._preferred_size,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        if not self.isVisible():
            self.show()
            self.raise_()

    def set_frame(self, frame: object, detection: object) -> None:
        if not isinstance(frame, np.ndarray):
            return
        rgb = frame[:, :, ::-1].copy()
        image = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()
        self.image_label.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        zone = getattr(detection, "target_zone", None)
        yellow_zone = getattr(detection, "yellow_zone", None)
        hook_x = getattr(detection, "moving_hook_x", None)
        overlap = getattr(detection, "moving_hook_overlaps_target", False)
        self.status_label.setText(
            f"Green zone: {zone or 'not found'}  |  "
            f"Yellow zone: {yellow_zone or 'not found'}  |  "
            f"Moving hook: {hook_x if hook_x is not None else 'not found'}  |  "
            f"{'HOOK IN ZONE' if overlap else 'tracking'}"
        )
        self.status_label.setToolTip(self.status_label.text())

    def set_capture_error(self, level: str, message: str) -> None:
        self.status_label.setText(f"{level}: {message}")
        self.status_label.setToolTip(self.status_label.text())

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)
