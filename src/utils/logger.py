from datetime import datetime

from PySide6.QtWidgets import QPlainTextEdit


def log_message(log_panel: QPlainTextEdit, level: str, message: str) -> None:
    """Append a timestamped message to a read-only log panel."""
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    log_panel.appendPlainText(f"[{timestamp}] [{level}] {message}")
