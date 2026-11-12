import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from launcher.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    # Process Python signals while Qt's event loop runs.
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    def handle_interrupt(signum, frame) -> None:
        app.quit()

    signal.signal(signal.SIGINT, handle_interrupt)

    try:
        exit_code = app.exec()
    finally:
        signal_timer.stop()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
