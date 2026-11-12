import ctypes
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal


class GlobalHotkeyManager(QObject, QAbstractNativeEventFilter):
    """Register Windows-wide Alt+S, Alt+X, Alt+T, and Alt+P shortcuts."""

    show_requested = Signal()
    quit_requested = Signal()
    overlay_requested = Signal()
    realtime_capture_stop_requested = Signal()
    registration_failed = Signal(str)

    _ALT_MODIFIER = 0x0001
    _WM_HOTKEY = 0x0312
    _SHOW_HOTKEY_ID = 1
    _QUIT_HOTKEY_ID = 2
    _OVERLAY_HOTKEY_ID = 3
    _REALTIME_CAPTURE_STOP_HOTKEY_ID = 4

    def __init__(self, parent: QObject | None = None) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._registered_ids: list[int] = []
        self._user32: Any = None

    def register(self) -> bool:
        windows_api = getattr(ctypes, "WinDLL", None)
        if windows_api is None:
            self.registration_failed.emit(
                "Global hotkeys are only supported on Windows."
            )
            return False

        self._user32 = windows_api("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = (
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        )
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self._user32.UnregisterHotKey.restype = wintypes.BOOL

        shortcuts = (
            (self._SHOW_HOTKEY_ID, ord("S")),
            (self._QUIT_HOTKEY_ID, ord("X")),
            (self._OVERLAY_HOTKEY_ID, ord("T")),
            (self._REALTIME_CAPTURE_STOP_HOTKEY_ID, ord("P")),
        )
        for hotkey_id, key in shortcuts:
            registered = self._user32.RegisterHotKey(
                None, hotkey_id, self._ALT_MODIFIER, key
            )
            if not registered:
                error = ctypes.WinError(ctypes.get_last_error())
                self.unregister()
                self.registration_failed.emit(
                    f"Could not register Alt+{chr(key)}: {error}"
                )
                return False
            self._registered_ids.append(hotkey_id)

        return True

    def unregister(self) -> None:
        if self._user32 is None:
            return

        for hotkey_id in self._registered_ids:
            if not self._user32.UnregisterHotKey(None, hotkey_id):
                error = ctypes.WinError(ctypes.get_last_error())
                self.registration_failed.emit(
                    f"Could not unregister hotkey {hotkey_id}: {error}"
                )
        self._registered_ids.clear()

    def nativeEventFilter(self, eventType: bytes, message: int) -> tuple[bool, int]:
        if eventType not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0

        native_message = ctypes.cast(
            ctypes.c_void_p(int(message)), ctypes.POINTER(wintypes.MSG)
        ).contents
        if native_message.message != self._WM_HOTKEY:
            return False, 0

        if native_message.wParam == self._SHOW_HOTKEY_ID:
            self.show_requested.emit()
            return True, 0

        if native_message.wParam == self._QUIT_HOTKEY_ID:
            self.quit_requested.emit()
            return True, 0

        if native_message.wParam == self._OVERLAY_HOTKEY_ID:
            self.overlay_requested.emit()
            return True, 0

        if native_message.wParam == self._REALTIME_CAPTURE_STOP_HOTKEY_ID:
            self.realtime_capture_stop_requested.emit()
            return True, 0

        return False, 0
