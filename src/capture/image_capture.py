import ctypes
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RgbQuad(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", wintypes.BYTE),
        ("rgbGreen", wintypes.BYTE),
        ("rgbRed", wintypes.BYTE),
        ("rgbReserved", wintypes.BYTE),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", _RgbQuad * 1)]


class ImageCapture:

    def capture(self, source: str | Path | np.ndarray) -> np.ndarray:
        # Case 1: If Actual Image Screenshot then return only its pixel data
        if isinstance(source, np.ndarray):
            return source

        # Case 2: The input is a file path
        path = Path(source)

        if not path.is_file():
            raise FileNotFoundError(f"Screenshot not found: {path}")

        image = cv2.imread(str(path))

        if image is None:
            raise ValueError(f"Could not decode image: {path}")

        return image

    def capture_window(self, window_handle: int) -> np.ndarray:
        """Capture a Windows top-level window as a BGR image."""
        if not hasattr(ctypes, "WinDLL"):
            raise OSError("Window capture is only supported on Windows.")
        if window_handle <= 0:
            raise ValueError("A valid Windows window handle is required.")

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
        user32.PrintWindow.restype = wintypes.BOOL
        user32.GetWindowDC.argtypes = (wintypes.HWND,)
        user32.GetWindowDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
        user32.ReleaseDC.restype = ctypes.c_int

        gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleBitmap.argtypes = (
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
        )
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = (wintypes.HDC,)
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.BitBlt.argtypes = (
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        )
        gdi32.BitBlt.restype = wintypes.BOOL
        gdi32.GetDIBits.argtypes = (
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
        )
        gdi32.GetDIBits.restype = ctypes.c_int

        hwnd = wintypes.HWND(window_handle)
        if not user32.IsWindow(hwnd):
            raise ValueError(f"Invalid window handle: {window_handle}.")
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            raise RuntimeError("The game window must be visible and not minimized.")

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise RuntimeError("The game window has invalid capture dimensions.")

        window_dc = user32.GetWindowDC(hwnd)
        if not window_dc:
            raise ctypes.WinError(ctypes.get_last_error())

        memory_dc = None
        bitmap = None
        previous_object = None
        try:
            memory_dc = gdi32.CreateCompatibleDC(window_dc)
            if not memory_dc:
                raise ctypes.WinError(ctypes.get_last_error())

            bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
            if not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())

            previous_object = gdi32.SelectObject(memory_dc, bitmap)
            if not previous_object:
                raise ctypes.WinError(ctypes.get_last_error())

            # Render full content when supported; copy the visible window DC otherwise.
            captured = bool(user32.PrintWindow(hwnd, memory_dc, 2))
            if not captured:
                captured = bool(
                    gdi32.BitBlt(
                        memory_dc,
                        0,
                        0,
                        width,
                        height,
                        window_dc,
                        0,
                        0,
                        0x00CC0020,
                    )
                )
            if not captured:
                raise ctypes.WinError(ctypes.get_last_error())

            gdi32.SelectObject(memory_dc, previous_object)
            previous_object = None

            bitmap_info = _BitmapInfo()
            bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            bitmap_info.bmiHeader.biWidth = width
            bitmap_info.bmiHeader.biHeight = -height
            bitmap_info.bmiHeader.biPlanes = 1
            bitmap_info.bmiHeader.biBitCount = 32
            bitmap_info.bmiHeader.biCompression = 0
            pixels = (ctypes.c_ubyte * (width * height * 4))()
            rows = gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                height,
                ctypes.cast(pixels, ctypes.c_void_p),
                ctypes.byref(bitmap_info),
                0,
            )
            if rows != height:
                raise ctypes.WinError(ctypes.get_last_error())

            bgra = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
            return bgra[:, :, :3].copy()
        finally:
            if previous_object:
                gdi32.SelectObject(memory_dc, previous_object)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(hwnd, window_dc)
