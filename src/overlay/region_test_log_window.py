import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QWidget

from overlay.log_panel import OverlayLogPanel

CAPTURE_TIMESTAMP = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{2} (?:AM|PM))(?:\s+)(.*)$", re.DOTALL
)
STATE_FIELD = re.compile(r"([A-Za-z][A-Za-z0-9]*: )([^,\]]+)")


class RegionTestLogWindow(OverlayLogPanel):
    """Glass log panel for capture, crop, processing, and OCR test results."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            prefix="region_test_log",
            title="Test Battle Configuration",
            subtitle="Region tests, capture & live battle state",
            icon_filename="list_alt_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            preferred_size=QSize(468, 312),
            maximum_blocks=500,
            empty_title="Ready to test your configuration",
            empty_description="Test a saved region to view capture, processing and OCR results.",
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            parent=parent,
        )

    def append_entry(self, level: str, message: str) -> None:
        level_format = QTextCharFormat()
        level_format.setForeground(
            QColor(
                {
                    "INFO": "#7DD3FC",
                    "SUCCESS": "#5EEAD4",
                    "WARNING": "#FCD34D",
                    "ERROR": "#FDA4AF",
                }.get(level.upper(), "#C5D3E5")
            )
        )
        level_format.setFontWeight(600)
        with self.append_cursor() as cursor:
            cursor.insertText(f"[{level}] ", level_format)
            match = CAPTURE_TIMESTAMP.match(message)
            if match is None:
                self._insert_state_message(cursor, message)
            else:
                timestamp_format = QTextCharFormat()
                timestamp_format.setForeground(QColor("#7DD3FC"))
                cursor.insertText(match.group(1), timestamp_format)
                cursor.insertText(" ", timestamp_format)
                self._insert_state_message(cursor, match.group(2))

    @staticmethod
    def _insert_state_message(cursor: QTextCursor, message: str) -> None:
        message_format = QTextCharFormat()
        message_format.setForeground(QColor("#C5D3E5"))
        pokemon_name_format = QTextCharFormat()
        pokemon_name_format.setForeground(QColor("#5EEAD4"))
        turn_number_format = QTextCharFormat()
        turn_number_format.setForeground(QColor("#FCD34D"))
        true_format = QTextCharFormat()
        true_format.setForeground(QColor("#5EEAD4"))
        false_format = QTextCharFormat()
        false_format.setForeground(QColor("#FDA4AF"))

        position = 0
        for match in STATE_FIELD.finditer(message):
            cursor.insertText(message[position:match.start()], message_format)
            field_name = match.group(1)
            value = match.group(2)
            cursor.insertText(field_name, message_format)

            if field_name == "wildPokemonName: " and value != "None":
                value_format = pokemon_name_format
            elif field_name == "turn: " and value.isdigit():
                value_format = turn_number_format
            elif value == "True":
                value_format = true_format
            elif value == "False":
                value_format = false_format
            else:
                value_format = message_format
            cursor.insertText(value, value_format)
            position = match.end()
        cursor.insertText(message[position:], message_format)
