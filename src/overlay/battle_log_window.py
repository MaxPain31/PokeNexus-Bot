import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QWidget

from overlay.log_panel import OverlayLogPanel

WILD_POKEMON_APPEARED = re.compile(
    r"\b(a\s+wild\s+)(.+?)(\s+appeared\b)",
    flags=re.IGNORECASE,
)


class BattleLogWindow(OverlayLogPanel):
    """Non-activating glass panel for chat and battle log entries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            prefix="battle_log",
            title="Battle Logs",
            subtitle="In-game chat & encounter events",
            icon_filename="chat-box.svg",
            preferred_size=QSize(384, 272),
            maximum_blocks=300,
            empty_title="Waiting for battle messages",
            empty_description="Chat updates appear here while battle detection is active.",
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            parent=parent,
        )

    def append_entry(self, message: str) -> None:
        text = message.strip()
        if text:
            message_format = QTextCharFormat()
            message_format.setForeground(QColor("#E6EDF7"))
            with self.append_cursor() as cursor:
                cursor.insertText(text, message_format)

    def append_chat_message(self, timestamp: str, message: str) -> None:
        text = message.strip()
        if not text:
            return

        timestamp_format = QTextCharFormat()
        timestamp_format.setForeground(QColor("#7DD3FC"))
        message_format = QTextCharFormat()
        message_format.setForeground(QColor("#E6EDF7"))
        with self.append_cursor() as cursor:
            cursor.insertText(f"[{timestamp}] ", timestamp_format)
            match = WILD_POKEMON_APPEARED.search(text)
            if match is None:
                cursor.insertText(text, message_format)
            else:
                pokemon_name_format = QTextCharFormat()
                pokemon_name_format.setForeground(QColor("#4ADE80"))
                cursor.insertText(text[:match.start(2)], message_format)
                cursor.insertText(match.group(2), pokemon_name_format)
                cursor.insertText(text[match.end(2):], message_format)
