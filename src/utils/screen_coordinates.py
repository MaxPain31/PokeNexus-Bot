import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication, QScreen


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def physical_to_logical_rect(rect: QRect) -> QRect:
    """Convert a Win32 physical-pixel screen rectangle into Qt screen coordinates."""
    if not rect.isValid():
        return QRect()

    first = physical_to_logical_point(QPoint(rect.left(), rect.top()))
    last = physical_to_logical_point(QPoint(rect.right(), rect.bottom()))
    return QRect(first, last).normalized()


def physical_to_logical_point(point: QPoint) -> QPoint:
    """Map one Win32 screen point through its monitor's Qt device-pixel ratio."""
    if (
        hasattr(ctypes, "WinDLL")
        and QGuiApplication.platformName().casefold() != "offscreen"
    ):
        return _windows_physical_to_logical_point(point)
    return _qt_physical_to_logical_point(point)


def logical_to_physical_point(point: QPoint) -> QPoint:
    """Map one Qt screen point to Win32 physical-pixel coordinates."""
    if (
        hasattr(ctypes, "WinDLL")
        and QGuiApplication.platformName().casefold() != "offscreen"
    ):
        return _windows_logical_to_physical_point(point)
    for screen in QGuiApplication.screens():
        logical_geometry = screen.geometry()
        if logical_geometry.contains(point):
            scale = screen.devicePixelRatio()
            return QPoint(
                round(point.x() * scale),
                round(point.y() * scale),
            )
    raise ValueError(f"Screen point is outside all attached displays: {point}.")


def _windows_logical_to_physical_point(point: QPoint) -> QPoint:
    screen = next(
        (
            candidate
            for candidate in QGuiApplication.screens()
            if candidate.geometry().contains(point)
        ),
        None,
    )
    if screen is None:
        raise ValueError(f"Screen point is outside all attached displays: {point}.")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplayMonitors.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        ),
        wintypes.LPARAM,
    )
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = (
        wintypes.HMONITOR,
        ctypes.POINTER(_MonitorInfoEx),
    )
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    physical_geometry: QRect | None = None
    best_geometry: QRect | None = None
    best_overlap = 0
    ambiguous_overlap = False
    size_matches: list[QRect] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def inspect_monitor(
        monitor: int,
        device_context: int,
        monitor_rect: int,
        lparam: int,
    ) -> bool:
        del device_context, monitor_rect, lparam
        nonlocal physical_geometry, best_geometry
        nonlocal best_overlap, ambiguous_overlap
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return True
        monitor_geometry = QRect(
            info.rcMonitor.left,
            info.rcMonitor.top,
            info.rcMonitor.right - info.rcMonitor.left,
            info.rcMonitor.bottom - info.rcMonitor.top,
        )
        if _normalize_device_name(info.szDevice) == _normalize_device_name(
            screen.name()
        ):
            physical_geometry = monitor_geometry
            return True

        screen_geometry = _screen_physical_geometry(screen)
        if screen_geometry.size() == monitor_geometry.size():
            size_matches.append(monitor_geometry)
        intersection = screen_geometry.intersected(monitor_geometry)
        overlap = intersection.width() * intersection.height()
        if overlap > best_overlap:
            best_geometry = monitor_geometry
            best_overlap = overlap
            ambiguous_overlap = False
        elif overlap and overlap == best_overlap:
            ambiguous_overlap = True
        return True

    callback = callback_type(inspect_monitor)
    if not user32.EnumDisplayMonitors(None, None, callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if physical_geometry is None:
        if len(size_matches) == 1:
            physical_geometry = size_matches[0]
        elif best_geometry is not None and not ambiguous_overlap:
            physical_geometry = best_geometry
        elif best_geometry is not None:
            raise RuntimeError(
                f"Could not uniquely match Qt screen {screen.name()!r} "
                "to a Windows display."
            )
    if physical_geometry is None:
        raise RuntimeError(
            f"Could not find physical bounds for display {screen.name()!r}."
        )

    logical_geometry = screen.geometry()
    scale = screen.devicePixelRatio()
    return QPoint(
        physical_geometry.left()
        + round((point.x() - logical_geometry.left()) * scale),
        physical_geometry.top()
        + round((point.y() - logical_geometry.top()) * scale),
    )


def _windows_physical_to_logical_point(point: QPoint) -> QPoint:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MonitorFromPoint.argtypes = (_Point, wintypes.DWORD)
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = (
        wintypes.HMONITOR,
        ctypes.POINTER(_MonitorInfoEx),
    )
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    monitor = user32.MonitorFromPoint(_Point(point.x(), point.y()), 2)
    if not monitor:
        raise ctypes.WinError(ctypes.get_last_error())

    monitor_info = _MonitorInfoEx()
    monitor_info.cbSize = ctypes.sizeof(_MonitorInfoEx)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
        raise ctypes.WinError(ctypes.get_last_error())

    physical = monitor_info.rcMonitor
    physical_geometry = QRect(
        physical.left,
        physical.top,
        physical.right - physical.left,
        physical.bottom - physical.top,
    )
    screen = _screen_for_device(monitor_info.szDevice, physical_geometry)
    return _map_point(
        point,
        physical_geometry,
        screen.geometry(),
        screen.devicePixelRatio(),
    )


def _qt_physical_to_logical_point(point: QPoint) -> QPoint:
    for screen in QGuiApplication.screens():
        physical_geometry = _screen_physical_geometry(screen)
        scale = screen.devicePixelRatio()
        if physical_geometry.contains(point):
            return _map_point(
                point, physical_geometry, screen.geometry(), scale
            )
    raise ValueError(f"Screen point is outside all attached displays: {point}.")


def _screen_for_device(device_name: str, physical_geometry: QRect) -> QScreen:
    normalized = _normalize_device_name(device_name)
    screens = QGuiApplication.screens()
    for screen in screens:
        if _normalize_device_name(screen.name()) == normalized:
            return screen

    size_matches = [
        screen
        for screen in screens
        if _screen_physical_geometry(screen).size() == physical_geometry.size()
    ]
    if len(size_matches) == 1:
        return size_matches[0]

    overlaps: list[tuple[int, QScreen]] = []
    for screen in screens:
        candidate_geometry = _screen_physical_geometry(screen)
        intersection = candidate_geometry.intersected(physical_geometry)
        area = intersection.width() * intersection.height()
        if area > 0:
            overlaps.append((area, screen))

    if overlaps:
        largest_overlap = max(area for area, _ in overlaps)
        best_matches = [
            screen for area, screen in overlaps if area == largest_overlap
        ]
        if len(best_matches) == 1:
            return best_matches[0]
    elif len(screens) == 1:
        return screens[0]

    raise RuntimeError(
        f"Could not match Windows display {device_name!r} to a Qt screen. "
        f"Qt screens: {[screen.name() for screen in screens]!r}."
    )


def _normalize_device_name(device_name: str) -> str:
    normalized = device_name.strip().casefold()
    windows_device_prefix = "\\\\.\\"
    if normalized.startswith(windows_device_prefix):
        return normalized[len(windows_device_prefix) :]
    return normalized


def _screen_physical_geometry(screen: QScreen) -> QRect:
    logical_geometry = screen.geometry()
    scale = screen.devicePixelRatio()
    return QRect(
        round(logical_geometry.x() * scale),
        round(logical_geometry.y() * scale),
        round(logical_geometry.width() * scale),
        round(logical_geometry.height() * scale),
    )


def _map_point(
    point: QPoint,
    physical_geometry: QRect,
    logical_geometry: QRect,
    scale: float,
) -> QPoint:
    return QPoint(
        logical_geometry.left()
        + round((point.x() - physical_geometry.left()) / scale),
        logical_geometry.top()
        + round((point.y() - physical_geometry.top()) / scale),
    )
