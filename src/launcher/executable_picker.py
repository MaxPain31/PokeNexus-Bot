from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class _ElidedPathLabel(QLabel):
    """Keep the full location available without letting long paths widen the UI."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(20)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._update_text()

    def _update_text(self) -> None:
        super().setText(
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideMiddle,
                self.contentsRect().width(),
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_text()


class ExecutablePicker(QWidget):
    """Reusable widget for selecting and validating a Windows executable."""

    executable_selected = Signal(object)

    def __init__(
        self,
        expected_filename: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("executable_picker")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setProperty("selected", False)
        self.expected_filename = expected_filename
        self.selected_path: Path | None = None

        file_description = (
            expected_filename if expected_filename is not None else "an application"
        )
        heading = QLabel("GAME APPLICATION")
        heading.setObjectName("eyebrow")
        self.select_button = QPushButton("Browse")
        self.select_button.setObjectName("locate_button")
        self.select_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_button.setAccessibleName(f"Locate {file_description}")
        self.select_button.setToolTip(f"Browse for {file_description}")
        self.select_button.setIcon(
            load_svg_icon(
                application_path("assets", "folder-open.svg"),
                QSize(16, 16),
            )
        )
        self.select_button.setFixedHeight(30)
        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch()
        header.addWidget(self.select_button)

        self.filename_label = QLabel(file_description)
        self.filename_label.setObjectName("executable_name")
        self.filename_label.setTextFormat(Qt.TextFormat.PlainText)

        initial_text = (
            "Browse to your game installation to continue."
            if expected_filename is not None
            else "Select an application to continue"
        )
        self.path_label = _ElidedPathLabel(initial_text)
        self.path_label.setObjectName("executable_path")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(3)
        layout.addLayout(header)
        layout.addWidget(self.filename_label)
        layout.addWidget(self.path_label)

        self.select_button.clicked.connect(self._open_file_dialog)

    def _open_file_dialog(self) -> None:
        dialog_title = (
            f"Select {self.expected_filename}"
            if self.expected_filename is not None
            else "Select an application"
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            dialog_title,
            str(self.selected_path.parent) if self.selected_path is not None else "",
            "Applications (*.exe)",
        )
        if not file_path:
            return

        executable = Path(file_path)
        if not self.set_selected_path(executable):
            expected_description = (
                f"the {self.expected_filename} application"
                if self.expected_filename is not None
                else "a Windows application (.exe)"
            )
            QMessageBox.warning(
                self,
                "Invalid application",
                f"Please select {expected_description}.",
            )
            return

        self.executable_selected.emit(executable)

    def set_selected_path(self, executable: Path) -> bool:
        """Restore a saved executable path when it still passes validation."""
        if not self._is_valid_executable(executable):
            return False

        self.selected_path = executable
        self.filename_label.setText(executable.name)
        self.path_label.setText(str(executable))
        self.select_button.setText("Change")
        self.setProperty("selected", True)
        self.style().unpolish(self)
        self.style().polish(self)
        return True

    def _is_valid_executable(self, path: Path) -> bool:
        if not path.is_file() or path.suffix.casefold() != ".exe":
            return False

        return self.expected_filename is None or self._normalized_stem(
            path
        ) == self._normalized_stem(Path(self.expected_filename))

    @staticmethod
    def _normalized_stem(path: Path) -> str:
        return "".join(
            character for character in path.stem.casefold() if character.isalnum()
        )
