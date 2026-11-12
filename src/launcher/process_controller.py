from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


class ProcessController(QObject):
    """Own the game process lifecycle and report events to the launcher UI."""

    log_message = Signal(str, str)
    running_changed = Signal(bool)
    started = Signal()
    warning_requested = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._force_stopped = False
        self._executable: Path | None = None

        self._process = QProcess(self)
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)

        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.setInterval(3000)
        self._stop_timer.timeout.connect(self._force_stop)

    def start(self, executable: Path) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self.log_message.emit("WARNING", "Application is already starting or running.")
            return

        self._stop_requested = False
        self._force_stopped = False
        self._executable = executable
        self.log_message.emit("INFO", f"Starting {executable}")
        self._process.start(str(executable))

    @property
    def is_running(self) -> bool:
        return self._process.state() == QProcess.ProcessState.Running

    @property
    def process_id(self) -> int | None:
        process_id = self._process.processId()
        return process_id or None

    def stop(self) -> None:
        if self._process.state() != QProcess.ProcessState.Running:
            return

        self._stop_requested = True
        self.log_message.emit("INFO", "Stop requested.")
        self._process.terminate()
        self._stop_timer.start()

    def _on_started(self) -> None:
        self.running_changed.emit(True)
        self.log_message.emit("SUCCESS", f"Started {self._executable}")
        self.started.emit()

    def _on_finished(
        self, exit_code: int, exit_status: QProcess.ExitStatus
    ) -> None:
        self._stop_timer.stop()
        stop_requested = self._stop_requested
        force_stopped = self._force_stopped
        self._stop_requested = False
        self._force_stopped = False
        self.running_changed.emit(False)

        if stop_requested:
            if force_stopped:
                self.log_message.emit("ERROR", "Application was force-stopped.")
            else:
                self.log_message.emit("SUCCESS", "Application stopped.")
        elif exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0:
            self.log_message.emit(
                "SUCCESS", "Application exited successfully (code 0)."
            )
        else:
            message = (
                f"Application exited unsuccessfully (code {exit_code}, "
                f"status: {exit_status.name})."
            )
            self.log_message.emit("ERROR", message)
            self.warning_requested.emit(
                "Application exited unexpectedly",
                f"Poke Nexus exited with code {exit_code}.",
            )

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._stop_timer.stop()
            self._stop_requested = False
            self._force_stopped = False
            message = self._process.errorString()
            self.running_changed.emit(False)
            self.log_message.emit("ERROR", f"Could not start application: {message}")
            self.warning_requested.emit(
                "Could not open application",
                f"Unable to start the selected application:\n{message}",
            )
            return

        self.log_message.emit(
            "ERROR",
            f"Process error ({error.name}): {self._process.errorString()}",
        )

    def _on_stdout(self) -> None:
        output = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        for line in output.splitlines():
            self.log_message.emit("APP", line)

    def _on_stderr(self) -> None:
        output = bytes(self._process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        for line in output.splitlines():
            self.log_message.emit("APP ERROR", line)

    def _force_stop(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._force_stopped = True
            self.log_message.emit(
                "WARNING", "Application did not stop in time; forcing exit."
            )
            self._process.kill()
