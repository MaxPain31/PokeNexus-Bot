import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtWidgets import QWidget

from utils.screen_coordinates import physical_to_logical_rect


class GameWindowTracker(QObject):
    """Track a process's visible top-level Windows window."""

    window_updated = Signal(int, QRect, bool)
    tracking_error = Signal(str)

    _GW_OWNER = 4

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process_id: int | None = None
        self._auxiliary_window_id: int | None = None
        self._overlay_window_id: int | None = None
        self._region_window_id: int | None = None
        self._region_marker_window_ids: set[int] = set()
        self._region_selection_window_id: int | None = None
        self._region_dialog_window_id: int | None = None
        self._battle_log_window_id: int | None = None
        self._encounter_summary_window_id: int | None = None
        self._region_test_log_window_id: int | None = None
        self._fishing_preview_window_id: int | None = None
        windows_api = getattr(ctypes, "WinDLL", None)
        self._user32 = (
            windows_api("user32", use_last_error=True)
            if windows_api is not None
            else None
        )
        self._timer = QTimer(self)
        self._timer.setInterval(75)
        self._timer.timeout.connect(self._refresh)
        if self._user32 is not None:
            self._configure_api()

    def _configure_api(self) -> None:
        self._user32.EnumWindows.argtypes = (
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        )
        self._user32.EnumWindows.restype = wintypes.BOOL
        self._user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        self._user32.IsWindowVisible.restype = wintypes.BOOL
        self._user32.IsIconic.argtypes = (wintypes.HWND,)
        self._user32.IsIconic.restype = wintypes.BOOL
        self._user32.GetForegroundWindow.argtypes = ()
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
        self._user32.GetWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetWindowRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        self._user32.GetWindowRect.restype = wintypes.BOOL
        self._user32.GetClientRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        self._user32.GetClientRect.restype = wintypes.BOOL
        self._user32.ClientToScreen.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.POINT),
        )
        self._user32.ClientToScreen.restype = wintypes.BOOL

    def client_geometry(self, window_id: int, fallback: QRect) -> QRect:
        """Return UI bounds below the title bar, preserving full-window capture bounds."""
        if self._user32 is None or window_id <= 0:
            return QRect(fallback)
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(window_id, ctypes.byref(rect)):
            return QRect(fallback)
        origin = wintypes.POINT(rect.left, rect.top)
        if not self._user32.ClientToScreen(window_id, ctypes.byref(origin)):
            return QRect(fallback)
        physical_geometry = QRect(
            origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top
        )
        try:
            geometry = physical_to_logical_rect(physical_geometry)
        except (OSError, RuntimeError, ValueError):
            return QRect(fallback)
        if fallback.isValid():
            geometry = geometry.intersected(fallback)
        return geometry if geometry.isValid() else QRect(fallback)

    def attach(self, process_id: int) -> None:
        self._process_id = process_id
        self._timer.start()
        self._refresh()

    def stop(self) -> None:
        self._timer.stop()
        self._process_id = None
        self.window_updated.emit(0, QRect(), False)

    def set_auxiliary_window(self, window_id: int | None) -> None:
        self._auxiliary_window_id = window_id

    def set_overlay_window(self, window_id: int | None) -> None:
        self._overlay_window_id = window_id

    def set_region_window(self, window_id: int | None) -> None:
        self._region_window_id = window_id

    def set_region_marker_windows(self, window_ids: tuple[int, ...]) -> None:
        self._region_marker_window_ids = {
            window_id for window_id in window_ids if window_id > 0
        }

    def set_region_selection_window(self, window_id: int | None) -> None:
        self._region_selection_window_id = window_id

    def set_region_dialog_window(self, window_id: int | None) -> None:
        self._region_dialog_window_id = window_id

    def set_battle_log_window(self, window_id: int | None) -> None:
        self._battle_log_window_id = window_id

    def set_encounter_summary_window(self, window_id: int | None) -> None:
        self._encounter_summary_window_id = window_id

    def set_region_test_log_window(self, window_id: int | None) -> None:
        self._region_test_log_window_id = window_id

    def set_fishing_preview_window(self, window_id: int | None) -> None:
        self._fishing_preview_window_id = window_id

    def _is_overlay_foreground(self, window_id: int | None) -> bool:
        """Include owned selector popups, not every window in the bot process."""
        if self._user32 is None:
            return False
        overlay_windows = {
            self._auxiliary_window_id,
            self._overlay_window_id,
            self._region_window_id,
            self._region_selection_window_id,
            self._region_dialog_window_id,
            *self._region_marker_window_ids,
            self._battle_log_window_id,
            self._encounter_summary_window_id,
            self._region_test_log_window_id,
            self._fishing_preview_window_id,
        }
        overlay_windows.discard(None)
        overlay_windows.discard(0)
        visited: set[int] = set()
        window_id = int(window_id or 0)
        while window_id > 0 and window_id not in visited:
            if window_id in overlay_windows:
                return True
            widget = QWidget.find(window_id)
            while widget is not None:
                # Windows can leave Qt.Popup's native owner unset. The Qt
                # parent chain still links both selector menus to Settings.
                if int(widget.effectiveWinId() or 0) in overlay_windows:
                    return True
                widget = widget.parentWidget()
            visited.add(window_id)
            # Qt.Popup is a separate foreground HWND. Its owner can be the
            # Settings panel or another popup (the selector's rarity menu).
            window_id = int(self._user32.GetWindow(window_id, self._GW_OWNER) or 0)
        return False

    def _refresh(self) -> None:
        if self._user32 is None or self._process_id is None:
            self.window_updated.emit(0, QRect(), False)
            return

        matching_windows: list[tuple[int, QRect, bool]] = []
        coordinate_error: str | None = None
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def inspect_window(hwnd: int, lparam: int) -> bool:
            nonlocal coordinate_error
            if not self._user32.IsWindowVisible(hwnd):
                return True

            owner = self._user32.GetWindow(hwnd, self._GW_OWNER)
            if owner:
                return True

            window_process_id = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(
                hwnd, ctypes.byref(window_process_id)
            )
            if window_process_id.value != lparam:
                return True

            rect = wintypes.RECT()
            if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True

            physical_geometry = QRect(
                rect.left,
                rect.top,
                rect.right - rect.left,
                rect.bottom - rect.top,
            )
            try:
                geometry = physical_to_logical_rect(physical_geometry)
            except (OSError, RuntimeError, ValueError) as error:
                coordinate_error = str(error)
                return True
            if geometry.isValid():
                matching_windows.append(
                    (int(hwnd), geometry, not bool(self._user32.IsIconic(hwnd)))
                )
            return True

        callback = callback_type(inspect_window)
        process_id = self._process_id
        if process_id is None:
            return
        if not self._user32.EnumWindows(callback, process_id):
            error = ctypes.WinError(ctypes.get_last_error())
            self._timer.stop()
            self.tracking_error.emit(f"Could not enumerate game windows: {error}")
            return
        if coordinate_error is not None:
            self._timer.stop()
            self.tracking_error.emit(
                "Could not map the game window to Qt screen coordinates: "
                f"{coordinate_error}"
            )
            return

        if matching_windows:
            hwnd, geometry, is_window_restored = max(
                matching_windows,
                key=lambda item: item[1].width() * item[1].height(),
            )
            foreground = self._user32.GetForegroundWindow()
            foreground_process_id = wintypes.DWORD()
            if foreground:
                self._user32.GetWindowThreadProcessId(
                    foreground, ctypes.byref(foreground_process_id)
                )
            is_overlay_foreground = self._is_overlay_foreground(foreground)
            is_game_foreground = (
                (
                    foreground_process_id.value == process_id
                    or is_overlay_foreground
                )
                and is_window_restored
            )
            self.window_updated.emit(hwnd, geometry, is_game_foreground)
        else:
            self.window_updated.emit(0, QRect(), False)
