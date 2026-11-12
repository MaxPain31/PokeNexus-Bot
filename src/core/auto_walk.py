import math
import random
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from capture.game_input import GameInput

_DIRECTIONS = {
    "Horizontal": (0x25, 0x27),
    "Vertical": (0x26, 0x28),
    "Random": (0x25, 0x27, 0x26, 0x28),
}
_DURATION_MODES = ("Default", "Random")


def choose_walk_key(direction: str) -> int:
    try:
        choices = _DIRECTIONS[direction]
    except KeyError as error:
        raise ValueError(f"Unsupported auto-walk direction: {direction}.") from error
    return random.choice(choices)


def choose_walk_duration(
    duration_mode: str,
    default_seconds: float,
    minimum_seconds: float,
    maximum_seconds: float,
) -> float:
    durations = (default_seconds, minimum_seconds, maximum_seconds)
    if any(
        not math.isfinite(duration) or duration < 0.5 or duration > 3600.0
        for duration in durations
    ):
        raise ValueError("Walk durations must be between 0.5 and 3600 seconds.")
    if duration_mode not in _DURATION_MODES:
        raise ValueError(f"Unsupported auto-walk duration mode: {duration_mode}.")
    if duration_mode == "Default":
        return default_seconds
    if minimum_seconds > maximum_seconds:
        raise ValueError("Random walk maximum duration cannot be less than minimum.")
    return random.uniform(minimum_seconds, maximum_seconds)


class AutoWalkController(QObject):
    """Walk in safe, timed key segments while realtime OCR reports no battle."""

    def __init__(
        self,
        game_input: GameInput,
        log_message: Callable[[str, str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._game_input = game_input
        self._log_message = log_message
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish_segment)
        self._enabled = False
        self._window_handle = 0
        self._direction = "Horizontal"
        self._duration_mode = "Default"
        self._default_seconds = 0.5
        self._minimum_seconds = 0.5
        self._maximum_seconds = 3.0
        self._held_key: int | None = None
        self._last_error: str | None = None

    def update(
        self,
        *,
        enabled: bool,
        on_battle_mode: object,
        window_handle: int,
        direction: str,
        duration_mode: str,
        default_seconds: float,
        minimum_seconds: float,
        maximum_seconds: float,
    ) -> None:
        if not enabled or on_battle_mode is not False or window_handle <= 0:
            self.stop()
            return

        self._enabled = True
        self._window_handle = window_handle
        self._direction = direction
        self._duration_mode = duration_mode
        self._default_seconds = default_seconds
        self._minimum_seconds = minimum_seconds
        self._maximum_seconds = maximum_seconds
        try:
            is_foreground = self._game_input.is_game_foreground(window_handle)
        except (OSError, RuntimeError, ValueError) as error:
            self._report_error(error)
            self._pause_and_retry()
            return

        if not is_foreground:
            self._pause_and_retry()
            return
        if self._held_key is None and not self._timer.isActive():
            self._start_segment()

    def stop(self) -> None:
        self._enabled = False
        self._timer.stop()
        self._release_held_key()

    def _start_segment(self) -> None:
        if not self._enabled:
            return
        try:
            if not self._game_input.is_game_foreground(self._window_handle):
                self._pause_and_retry()
                return
            key = choose_walk_key(self._direction)
            duration = choose_walk_duration(
                self._duration_mode,
                self._default_seconds,
                self._minimum_seconds,
                self._maximum_seconds,
            )
            self._game_input.press_key(self._window_handle, key)
        except (OSError, RuntimeError, ValueError) as error:
            self._report_error(error)
            self._schedule_retry()
            return

        self._last_error = None
        self._held_key = key
        self._timer.start(max(1, round(duration * 1000)))

    def _finish_segment(self) -> None:
        self._release_held_key()
        self._start_segment()

    def _pause_and_retry(self) -> None:
        if self._held_key is not None:
            self._timer.stop()
            self._release_held_key()
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        if self._enabled and not self._timer.isActive():
            self._timer.start(1000)

    def _release_held_key(self) -> None:
        key = self._held_key
        self._held_key = None
        if key is None:
            return
        try:
            self._game_input.release_key(key)
        except (OSError, RuntimeError, ValueError) as error:
            self._report_error(error)

    def _report_error(self, error: Exception) -> None:
        message = str(error)
        if message == self._last_error:
            return
        self._last_error = message
        self._log_message("ERROR", f"Auto Walk input failed: {message}")
