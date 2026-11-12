import ctypes
import math
import time
from ctypes import wintypes

from PySide6.QtCore import QRect

from models.region_config import SavedRegion
from utils.screen_coordinates import logical_to_physical_point


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", _InputUnion)]


class GameInput:
    """Send foreground-guarded system mouse and keyboard input to the game."""

    _INPUT_MOUSE = 0
    _INPUT_KEYBOARD = 1
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _VK_LBUTTON = 0x01
    _CLICK_HOLD_SECONDS = 0.05
    _CLICK_SETTLE_SECONDS = 0.05
    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002

    def is_game_foreground(self, window_handle: int) -> bool:
        if not hasattr(ctypes, "WinDLL"):
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        target_process_id = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(
            window_handle,
            ctypes.byref(target_process_id),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        foreground = user32.GetForegroundWindow()
        foreground_process_id = wintypes.DWORD()
        if foreground:
            user32.GetWindowThreadProcessId(
                foreground,
                ctypes.byref(foreground_process_id),
            )
        return foreground_process_id.value == target_process_id.value

    def press_key(
        self,
        window_handle: int,
        virtual_key: int,
        *,
        extended: bool = True,
    ) -> None:
        self._send_key(
            window_handle,
            virtual_key,
            key_up=False,
            extended=extended,
        )

    def release_key(self, virtual_key: int, *, extended: bool = True) -> None:
        self._send_key(0, virtual_key, key_up=True, extended=extended)

    def tap_key(
        self,
        window_handle: int,
        virtual_key: int,
        *,
        duration_seconds: float = 0.03,
        extended: bool = True,
    ) -> None:
        if duration_seconds < 0:
            raise ValueError("Key tap duration cannot be negative.")
        self.press_key(window_handle, virtual_key, extended=extended)
        try:
            if duration_seconds > 0:
                time.sleep(duration_seconds)
        finally:
            self.release_key(virtual_key, extended=extended)

    def _send_key(
        self,
        window_handle: int,
        virtual_key: int,
        *,
        key_up: bool,
        extended: bool = True,
    ) -> None:
        if not hasattr(ctypes, "WinDLL"):
            raise OSError("Game keyboard input is only supported on Windows.")
        if not 0 < virtual_key <= 0xFF:
            raise ValueError(f"Invalid virtual-key code: {virtual_key}.")
        if not key_up and not self.is_game_foreground(window_handle):
            raise RuntimeError("Game is not the foreground application.")

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(_Input),
            ctypes.c_int,
        )
        user32.SendInput.restype = wintypes.UINT
        event = _Input()
        event.type = self._INPUT_KEYBOARD
        event.value.ki.wVk = virtual_key
        event.value.ki.dwFlags = (
            self._KEYEVENTF_EXTENDEDKEY if extended else 0
        )
        if key_up:
            event.value.ki.dwFlags |= self._KEYEVENTF_KEYUP
        error = 0
        for _ in range(2 if key_up else 1):
            ctypes.set_last_error(0)
            if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                return
            error = ctypes.get_last_error() or error
        if error:
            raise ctypes.WinError(error)
        raise OSError("Windows did not accept the keyboard input.")

    def click_region(
        self,
        window_handle: int,
        region: SavedRegion,
        game_geometry: QRect,
        *,
        settle_delay: float | None = None,
    ) -> None:
        """Click through the game's normal input path, then restore the pointer.

        Real system input is required by the game. Preserve a new cursor position
        if the user moves the mouse during the click; never activate another window.
        OCR, rather than SendInput's return value, confirms the resulting action.
        """
        if not hasattr(ctypes, "WinDLL"):
            raise OSError("Game input is only supported on Windows.")
        delay = self._CLICK_SETTLE_SECONDS if settle_delay is None else settle_delay
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Click settle delay must be finite and non-negative.")
        region_rect = QRect(region.x, region.y, region.width, region.height)
        if not region_rect.isValid() or not game_geometry.contains(region_rect):
            raise ValueError(
                f"Cannot click '{region.label}': region is outside the game window."
            )

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsWindowEnabled.argtypes = (wintypes.HWND,)
        user32.IsWindowEnabled.restype = wintypes.BOOL
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.IsIconic.restype = wintypes.BOOL
        user32.IsChild.argtypes = (wintypes.HWND, wintypes.HWND)
        user32.IsChild.restype = wintypes.BOOL
        user32.ScreenToClient.argtypes = (wintypes.HWND, ctypes.POINTER(_Point))
        user32.ScreenToClient.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user32.GetClientRect.restype = wintypes.BOOL
        user32.GetWindowDpiAwarenessContext.argtypes = (wintypes.HWND,)
        user32.GetWindowDpiAwarenessContext.restype = wintypes.HANDLE
        user32.SetThreadDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
        user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
        user32.PhysicalToLogicalPointForPerMonitorDPI.argtypes = (
            wintypes.HWND, ctypes.POINTER(_Point),
        )
        user32.PhysicalToLogicalPointForPerMonitorDPI.restype = wintypes.BOOL
        user32.WindowFromPhysicalPoint.argtypes = (_Point,)
        user32.WindowFromPhysicalPoint.restype = wintypes.HWND
        user32.GetPhysicalCursorPos.argtypes = (ctypes.POINTER(_Point),)
        user32.GetPhysicalCursorPos.restype = wintypes.BOOL
        user32.SetPhysicalCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        user32.SetPhysicalCursorPos.restype = wintypes.BOOL
        user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        user32.GetAsyncKeyState.restype = wintypes.SHORT
        user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(_Input),
            ctypes.c_int,
        )
        user32.SendInput.restype = wintypes.UINT

        if window_handle <= 0 or not user32.IsWindow(window_handle):
            raise RuntimeError("Game window is unavailable.")
        if (
            not user32.IsWindowVisible(window_handle)
            or user32.IsIconic(window_handle)
            or not user32.IsWindowEnabled(window_handle)
        ):
            raise RuntimeError("Game window is hidden, minimized, or disabled.")

        physical_point = logical_to_physical_point(region_rect.center())
        dpi_context = user32.GetWindowDpiAwarenessContext(window_handle)
        if not dpi_context:
            raise ctypes.WinError(ctypes.get_last_error())
        previous_context = user32.SetThreadDpiAwarenessContext(dpi_context)
        if not previous_context:
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            # Validate in the game's DPI space, then use physical-coordinate APIs
            # for mouse input, including bitmap-scaled games and other monitors.
            point = _Point(physical_point.x(), physical_point.y())
            if not user32.PhysicalToLogicalPointForPerMonitorDPI(
                window_handle, ctypes.byref(point)
            ) or not user32.ScreenToClient(window_handle, ctypes.byref(point)):
                raise ctypes.WinError(ctypes.get_last_error())
            client_rect = wintypes.RECT()
            if not user32.GetClientRect(window_handle, ctypes.byref(client_rect)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not (
                client_rect.left <= point.x < client_rect.right
                and client_rect.top <= point.y < client_rect.bottom
            ):
                raise ValueError(
                    f"Cannot click '{region.label}': click point is outside "
                    "the game client area."
                )
        finally:
            if not user32.SetThreadDpiAwarenessContext(previous_context):
                raise ctypes.WinError(ctypes.get_last_error())

        point = _Point(physical_point.x(), physical_point.y())

        def check_target() -> None:
            if not self.is_game_foreground(window_handle):
                raise RuntimeError("Keep Poke Nexus in the foreground for automatic clicks.")
            target = user32.WindowFromPhysicalPoint(point)
            if not target or (
                target != window_handle and not user32.IsChild(window_handle, target)
            ):
                raise RuntimeError(
                    f"Cannot click '{region.label}': the game button is covered "
                    "by another window."
                )
            if user32.GetAsyncKeyState(self._VK_LBUTTON) & 0x8000:
                raise RuntimeError("Waiting for you to release the left mouse button.")

        check_target()
        previous_position = _Point()
        if not user32.GetPhysicalCursorPos(ctypes.byref(previous_position)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not user32.SetPhysicalCursorPos(point.x, point.y):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            check_target()
            current_position = _Point()
            if not user32.GetPhysicalCursorPos(ctypes.byref(current_position)):
                raise ctypes.WinError(ctypes.get_last_error())
            if (current_position.x, current_position.y) != (point.x, point.y):
                raise RuntimeError("Mouse moved before the automatic click; retrying later.")
            self._send_mouse_button(user32, self._MOUSEEVENTF_LEFTDOWN)
            try:
                # Separate press and release across game frames.
                time.sleep(self._CLICK_HOLD_SECONDS)
            finally:
                self._send_mouse_button(user32, self._MOUSEEVENTF_LEFTUP, retry=True)
            if delay > 0:
                time.sleep(delay)
        finally:
            current_position = _Point()
            if not user32.GetPhysicalCursorPos(ctypes.byref(current_position)):
                raise ctypes.WinError(ctypes.get_last_error())
            if (current_position.x, current_position.y) == (point.x, point.y):
                if not user32.SetPhysicalCursorPos(
                    previous_position.x, previous_position.y
                ):
                    raise ctypes.WinError(ctypes.get_last_error())

    @classmethod
    def _send_mouse_button(cls, user32, flags: int, *, retry: bool = False) -> None:
        event = _Input()
        event.type = cls._INPUT_MOUSE
        event.value.mi.dwFlags = flags
        error = 0
        for _ in range(2 if retry else 1):
            ctypes.set_last_error(0)
            if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                return
            error = ctypes.get_last_error() or error
        if error:
            raise ctypes.WinError(error)
        raise OSError("Windows did not accept the mouse input.")


def region_for_role(
    regions: tuple[SavedRegion, ...],
    feature: str,
    role: str,
) -> SavedRegion | None:
    role_folded = role.casefold()
    return next(
        (
            region
            for region in regions
            if region.feature == feature
            and region.label.casefold() == role_folded
        ),
        None,
    )
