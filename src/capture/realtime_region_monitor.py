import hashlib
import re
import threading
import time
import unicodedata
import ctypes
from collections.abc import Sequence
from datetime import datetime
from ctypes import wintypes

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QThread, Signal

from capture.image_capture import ImageCapture
from capture.image_processor import ImageProcessor
from capture.region_cropper import RegionCropper
from core.battle_action import (
    ACTION_BUTTON_ROLES as _ACTION_BUTTON_ROLES,
    ACTION_BUTTON_STATES,
    ACTION_RETRY_SECONDS,
    has_move_details,
)
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from ocr.text_extractor import TextExtractor
from utils.screen_coordinates import physical_to_logical_point


def is_shiny_wild_pokemon_name(name: str | None) -> bool:
    return bool(name and re.search(r"(?<!\w)\[S\]\s*", name, re.IGNORECASE))


def is_elite_wild_pokemon_name(name: str | None) -> bool:
    return bool(
        name
        and re.search(
            r"^\s*(?:\[S\]\s*)?(?:\[E\](?!\w)|Elite\b)",
            name,
            re.IGNORECASE,
        )
    )


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _MousePoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _LowLevelMouseEvent(ctypes.Structure):
    _fields_ = [
        ("pt", _MousePoint),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _GameRegionClickListener:
    """Track the latest manual click or automated main-menu action."""

    _WH_MOUSE_LL = 14
    _WM_LBUTTONDOWN = 0x0201
    _WM_QUIT = 0x0012
    _LLMHF_INJECTED = 0x0001

    def __init__(
        self,
        regions: tuple[SavedRegion, ...],
        window_handle: int,
    ) -> None:
        self._action_regions = frozenset(
            region for region in regions
            if region.label.casefold() == _ACTION_BUTTON_ROLES.get(region.feature)
        )
        self._button_regions = {
            feature: tuple(
                QRect(region.x, region.y, region.width, region.height)
                for region in self._action_regions
                if region.feature == feature
            )
            for feature in _ACTION_BUTTON_ROLES
        }
        self._window_handle = window_handle
        self._target_process_id = 0
        self._pending_click_feature: str | None = None
        self._pending_click_at = 0.0
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._startup_error: Exception | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="game-region-click-listener",
            daemon=True,
        )
        self._thread.start()
        self._started.wait()
        if self._startup_error is not None:
            raise self._startup_error

    def stop(self) -> None:
        if self._thread is None:
            return
        if self._thread_id:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW.argtypes = (
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.PostThreadMessageW.restype = wintypes.BOOL
            if not user32.PostThreadMessageW(self._thread_id, self._WM_QUIT, 0, 0):
                raise ctypes.WinError(ctypes.get_last_error())
        self._thread.join()

    def consume_clicks(self, *, before: float | None = None) -> set[str]:
        with self._lock:
            if before is not None and self._pending_click_at >= before:
                return set()
            feature = self._pending_click_feature
            self._pending_click_feature = None
            return {feature} if feature is not None else set()

    def record_automated_click(self, region: SavedRegion) -> None:
        if region not in self._action_regions:
            return
        self.record_automated_action(region.feature, region.label)

    def record_automated_action(self, feature: str, role: str) -> None:
        # Key meanings depend on the current menu. Only opening a main action
        # menu starts tracking; Move 1 and Bag Previous must not become Fight.
        if role.casefold() != _ACTION_BUTTON_ROLES.get(feature):
            return
        with self._lock:
            self._pending_click_feature = feature
            self._pending_click_at = time.monotonic()

    def _record_click(
        self,
        x: int,
        y: int,
        process_id: int,
        foreground_process_id: int,
    ) -> None:
        if (
            process_id != self._target_process_id
            or foreground_process_id != self._target_process_id
        ):
            return
        matching_features = {
            feature
            for feature, regions in self._button_regions.items()
            if any(region.contains(x, y) for region in regions)
        }
        if len(matching_features) != 1:
            return
        with self._lock:
            self._pending_click_feature = next(iter(matching_features))
            self._pending_click_at = time.monotonic()

    def _run(self) -> None:
        hook: int | None = None
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            callback_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.SetWindowsHookExW.argtypes = (
                ctypes.c_int,
                callback_type,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            )
            user32.SetWindowsHookExW.restype = wintypes.HANDLE
            user32.CallNextHookEx.argtypes = (
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.WindowFromPoint.argtypes = (_MousePoint,)
            user32.WindowFromPoint.restype = wintypes.HWND
            user32.GetForegroundWindow.argtypes = ()
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.GetWindowThreadProcessId.argtypes = (
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            )
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.GetMessageW.argtypes = (
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            )
            user32.GetMessageW.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = (
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            )
            user32.PeekMessageW.restype = wintypes.BOOL
            user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
            user32.TranslateMessage.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
            user32.DispatchMessageW.restype = ctypes.c_ssize_t

            target_process_id = wintypes.DWORD()
            if not user32.GetWindowThreadProcessId(
                self._window_handle,
                ctypes.byref(target_process_id),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self._target_process_id = target_process_id.value

            def on_mouse_event(code: int, message: int, data: int) -> int:
                if code >= 0 and message == self._WM_LBUTTONDOWN:
                    event = ctypes.cast(
                        data, ctypes.POINTER(_LowLevelMouseEvent)
                    ).contents
                    # Automated clicks are reported explicitly after release.
                    # In particular, a move slot can overlap the Fight region.
                    if event.flags & self._LLMHF_INJECTED:
                        return user32.CallNextHookEx(None, code, message, data)
                    point = event.pt
                    target_window = user32.WindowFromPoint(point)
                    foreground_window = user32.GetForegroundWindow()
                    click_process_id = wintypes.DWORD()
                    foreground_process_id = wintypes.DWORD()
                    if target_window:
                        user32.GetWindowThreadProcessId(
                            target_window,
                            ctypes.byref(click_process_id),
                        )
                    if foreground_window:
                        user32.GetWindowThreadProcessId(
                            foreground_window,
                            ctypes.byref(foreground_process_id),
                        )
                    if (
                        click_process_id.value != self._target_process_id
                        or foreground_process_id.value != self._target_process_id
                    ):
                        return user32.CallNextHookEx(None, code, message, data)
                    try:
                        logical_point = physical_to_logical_point(
                            QPoint(point.x, point.y)
                        )
                    except (OSError, RuntimeError, ValueError):
                        return user32.CallNextHookEx(None, code, message, data)
                    self._record_click(
                        logical_point.x(),
                        logical_point.y(),
                        click_process_id.value,
                        foreground_process_id.value,
                    )
                return user32.CallNextHookEx(None, code, message, data)

            callback = callback_type(on_mouse_event)
            self._thread_id = kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            user32.PeekMessageW(
                ctypes.byref(message),
                None,
                0,
                0,
                0,
            )
            hook = user32.SetWindowsHookExW(
                self._WH_MOUSE_LL,
                callback,
                kernel32.GetModuleHandleW(None),
                0,
            )
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception as error:
            self._startup_error = error
            self._started.set()
            return

        self._started.set()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(hook)


class RealtimeRegionMonitor(QThread):
    """Continuously sample configured game regions and report OCR-derived state."""

    log_message = Signal(str, str)
    state_updated = Signal(object)
    chat_message = Signal(str, str)
    pokemon_encountered = Signal(str)

    _MINIMUM_STATE_CONFIDENCE = 50.0
    _POLL_INTERVAL_SECONDS = 0.25
    _BATTLE_POLL_INTERVAL_SECONDS = 0.10
    _BATTLE_END_CONFIRMATION_POLLS = 3
    _THRESHOLD_METHODS = {
        "chat-box": "otsu_inverted",
        "pokemon-name": "binary_230",
        "pokemon-team": "binary_230",
        "fight": "binary_230",
        "bag": "binary_230",
        "swap": "binary_230",
        "run": "binary_230",
        "turn": "binary_230",
        "moves": "binary_230",
        "health": "otsu_inverted",
    }
    _MORPHOLOGY_OPERATIONS = {
        "chat-box": "close",
        "health": "close",
    }
    _MINIMUM_CHAT_LINE_CONFIDENCE = 60.0
    _BUTTON_FEATURES = {
        "showingFightButton": ("fight", ("fight",)),
        "showingPokemonButton": ("swap", ("pokemon", "swap")),
        "showingRunButton": ("run", ("run",)),
        "showingBagButton": ("bag", ("bag",)),
    }
    _CLICKED_BUTTON_STATES = ACTION_BUTTON_STATES

    def __init__(
        self,
        regions: tuple[SavedRegion, ...],
        window_handle: int,
        game_geometry: QRect,
        image_capture: ImageCapture | None = None,
        region_cropper: RegionCropper | None = None,
        image_processor: ImageProcessor | None = None,
        text_extractor: TextExtractor | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.regions = regions
        self.window_handle = window_handle
        self._click_listener = _GameRegionClickListener(regions, window_handle)
        self.game_geometry = QRect(game_geometry)
        self.image_capture = image_capture or ImageCapture()
        self.region_cropper = region_cropper or RegionCropper()
        self.image_processor = image_processor or ImageProcessor()
        self.text_extractor = text_extractor or TextExtractor()
        self._regions_by_feature: dict[str, list[SavedRegion]] = {}
        for region in regions:
            self._regions_by_feature.setdefault(region.feature, []).append(region)
        self._clicked_button_states = {
            state_name: False for state_name, _ in self._CLICKED_BUTTON_STATES.values()
        }
        self._pending_action_feature: str | None = None
        self._pending_action_at = 0.0
        self._action_menu_visible = False
        self._action_transition_polls = 0
        self._action_turn: int | None = None
        self._click_non_battle_polls = 0
        self._action_ocr_failed = False
        self._ocr_cache: dict[SavedRegion, tuple[bytes, OCRResult]] = {}
        self._last_chat_message: str | None = None
        self._encounter_active = False
        self._non_battle_poll_count = 0

    def record_automated_click(self, region: SavedRegion) -> None:
        """Queue an input attempt; a later OCR frame must confirm the response."""
        self._click_listener.record_automated_click(region)

    def record_automated_action(self, feature: str, role: str) -> None:
        """Track a submitted key or click by its menu action, without coordinates."""
        self._click_listener.record_automated_action(feature, role)

    def run(self) -> None:
        click_listener_started = False
        poll_interval_seconds = self._POLL_INTERVAL_SECONDS
        try:
            if self._click_listener._action_regions:
                if getattr(ctypes, "WinDLL", None) is not None:
                    try:
                        self._click_listener.start()
                        click_listener_started = True
                    except Exception as error:
                        self.log_message.emit(
                            "ERROR", f"Game button click listener failed: {error}"
                        )
                else:
                    self.log_message.emit(
                        "ERROR",
                        "Manual game button click tracking is only supported on Windows.",
                    )

            while not self.isInterruptionRequested():
                started_at = time.perf_counter()
                memory_before: tuple[int, int, int, int] | None = None
                memory_error: OSError | None = None
                try:
                    memory_before = self._get_memory_usage()
                except OSError as error:
                    memory_error = error
                timings = {
                    "capture_window": 0.0,
                    "image_process": 0.0,
                    "text_extraction": 0.0,
                }
                try:
                    state, captured_at = self._capture_state(timings)
                except Exception as error:
                    self.log_message.emit("ERROR", f"Realtime capture failed: {error}")
                else:
                    battle_active = state.get("onBattleMode") is True
                    poll_interval_seconds = (
                        self._BATTLE_POLL_INTERVAL_SECONDS
                        if battle_active
                        else self._POLL_INTERVAL_SECONDS
                    )
                    self.log_message.emit(
                        "INFO",
                        f"{captured_at} {self._format_state(state)}",
                    )
                    self.state_updated.emit(state)
                    chat_message = state.get("chatBoxMessage")
                    pokemon_name: str | None = None
                    if isinstance(chat_message, str) and chat_message:
                        new_message = self._new_chat_message(
                            self._last_chat_message,
                            chat_message,
                        )
                        if new_message:
                            if battle_active:
                                for line in new_message.splitlines():
                                    if line.strip():
                                        self.chat_message.emit(
                                            captured_at,
                                            line.strip(),
                                        )
                            pokemon_name = self._extract_wild_pokemon_name(new_message)
                        if self._last_chat_message is None or new_message:
                            self._last_chat_message = chat_message
                    else:
                        if not battle_active:
                            self._last_chat_message = None
                    if self._is_new_pokemon_encounter(
                        pokemon_name,
                        battle_active,
                    ):
                        if pokemon_name is not None:
                            self.pokemon_encountered.emit(pokemon_name)
                finally:
                    elapsed = time.perf_counter() - started_at
                    try:
                        memory_after = self._get_memory_usage()
                    except OSError as error:
                        memory_after = None
                        memory_error = error
                    memory_summary = self._format_memory_usage(
                        memory_before,
                        memory_after,
                        memory_error,
                    )
                    print(
                        "[Result] "
                        f"capture_window: {timings['capture_window']:.4f}s | "
                        f"image_process: {timings['image_process']:.4f}s | "
                        f"text_extraction: {timings['text_extraction']:.4f}s | "
                        f"Overall: {elapsed:.4f}s | {memory_summary}",
                        flush=True,
                    )

                if self.isInterruptionRequested():
                    break
                self.msleep(
                    max(
                        1,
                        round(max(0.0, poll_interval_seconds - elapsed) * 1000),
                    )
                )
        finally:
            if click_listener_started:
                self._click_listener.stop()
            self.log_message.emit("INFO", "Realtime region capture stopped cleanly.")

    @staticmethod
    def _get_memory_usage() -> tuple[int, int, int, int]:
        windows_api = getattr(ctypes, "WinDLL", None)
        if windows_api is None:
            raise OSError("Memory usage reporting is only supported on Windows.")

        kernel32 = windows_api("kernel32", use_last_error=True)
        psapi = windows_api("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(_MemoryStatusEx),)
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCountersEx),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        process = _ProcessMemoryCountersEx()
        process.cb = ctypes.sizeof(process)
        if not psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(),
            ctypes.byref(process),
            process.cb,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        system = _MemoryStatusEx()
        system.dwLength = ctypes.sizeof(system)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(system)):
            raise ctypes.WinError(ctypes.get_last_error())

        return (
            process.WorkingSetSize,
            process.PrivateUsage,
            system.ullTotalPhys - system.ullAvailPhys,
            system.ullTotalPhys,
        )

    @staticmethod
    def _format_memory_usage(
        before: tuple[int, int, int, int] | None,
        after: tuple[int, int, int, int] | None,
        error: OSError | None,
    ) -> str:
        if after is None:
            return f"Memory: unavailable ({error})"

        rss, private_bytes, system_used, system_total = after
        memory_summary = (
            f"Memory: process RSS {rss / (1024 ** 2):.1f} MB | "
            f"private {private_bytes / (1024 ** 2):.1f} MB"
        )
        if before is not None:
            private_delta = private_bytes - before[1]
            memory_summary += f" (change {private_delta / (1024 ** 2):+.1f} MB)"
        elif error is not None:
            memory_summary += f" (change unavailable: {error})"

        memory_summary += (
            f" | system {system_used / (1024 ** 3):.1f}/"
            f"{system_total / (1024 ** 3):.1f} GB"
        )
        return memory_summary

    def _capture_state(
        self,
        timings: dict[str, float],
        clicked_features: set[str] | None = None,
    ) -> tuple[dict[str, object], str]:
        self._action_ocr_failed = False
        frame_started_at = time.monotonic()
        phase_started_at = time.perf_counter()
        try:
            screenshot = self.image_capture.capture_window(self.window_handle)
        finally:
            timings["capture_window"] += time.perf_counter() - phase_started_at
        # Keep attempts across capture failures, and never confirm an action using
        # an image whose capture began before its input finished.
        if clicked_features is None:
            clicked_features = self._click_listener.consume_clicks(before=frame_started_at)
        local_capture_time = datetime.now().astimezone()
        captured_at = (
            f"{local_capture_time.strftime('%I:%M:%S')}."
            f"{local_capture_time.microsecond // 10000:02d} "
            f"{local_capture_time.strftime('%p')}"
        )

        chat_results = self._read_regions(
            screenshot,
            self._regions_by_feature.get("chat-box", ()),
            timings,
        )
        chat_message = "\n".join(
            self._normalize_chat_snapshot(result.text)
            for _, result in chat_results
            if result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
            and result.text.strip()
        )
        chat_wild_pokemon_name = self._extract_wild_pokemon_name(chat_message)
        battle_phrase_detected = self._chat_indicates_battle(chat_message)

        pokemon_name_results = self._read_regions(
            screenshot,
            self._regions_by_feature.get("pokemon-name", ()),
            timings,
        )
        your_pokemon_name = self._join_ocr_text(
            [
                (region, result)
                for region, result in pokemon_name_results
                if "your" in region.label.casefold()
            ]
        )
        selected_wild_pokemon_name = self._join_ocr_text(
            [
                (region, result)
                for region, result in pokemon_name_results
                if "wild" in region.label.casefold()
                and result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
            ]
        )
        wild_pokemon_name = (
            chat_wild_pokemon_name or selected_wild_pokemon_name
        )
        wild_pokemon_is_shiny = is_shiny_wild_pokemon_name(selected_wild_pokemon_name)
        wild_pokemon_is_elite = is_elite_wild_pokemon_name(selected_wild_pokemon_name)

        your_health: int | None = None
        enemy_health: int | None = None
        health_results = self._read_regions(
            screenshot,
            self._regions_by_feature.get("health", ()),
            timings,
        )
        health_regions = sorted(
            health_results,
            key=lambda item: item[0].y,
        )
        for region, result in health_regions:
            if result.mean_confidence < self._MINIMUM_STATE_CONFIDENCE:
                continue
            value = self._parse_health(result.text)
            if value is None:
                continue
            label = region.label.casefold()
            if any(token in label for token in ("ally", "your", "player")):
                your_health = value
            elif "enemy" in label or "opponent" in label:
                enemy_health = value
            elif region.y + region.height // 2 < self.game_geometry.center().y():
                enemy_health = value
            else:
                your_health = value

        battle_mode = self._is_battle_mode(
            chat_message,
            battle_phrase_detected,
            enemy_health,
        )
        button_states: dict[str, bool | None] = {
            state: False for state in self._BUTTON_FEATURES
        }
        bag_item_name: str | None = None
        showing_bag_item_menu = False
        turn_number: int | None = None

        if battle_mode:
            feature_results: dict[str, list[tuple[SavedRegion, OCRResult]]] = {}
            for feature in ("fight", "swap", "run", "bag", "turn"):
                feature_results[feature] = self._read_regions(
                    screenshot,
                    self._regions_by_feature.get(feature, ()),
                    timings,
                )

            bag_item_name = self._extract_bag_item_name(
                feature_results["bag"]
            )
            for state_name, features in self._BUTTON_FEATURES.items():
                feature, expected_texts = features
                action_results = [
                    result for region, result in feature_results.get(feature, ())
                    if region.label.casefold() == _ACTION_BUTTON_ROLES[feature]
                ]
                button_states[state_name] = any(
                    result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
                    and any(
                        self._contains_text(result.text, expected_text)
                        for expected_text in expected_texts
                    )
                    for result in action_results
                )
                if not action_results or all(
                    result.text.strip() and result.mean_confidence < self._MINIMUM_STATE_CONFIDENCE
                    for result in action_results
                ):
                    button_states[state_name] = None
            turn_number = next(
                (
                    number
                    for _, result in feature_results["turn"]
                    if result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
                    and (number := self._extract_turn_number(result.text)) is not None
                ),
                None,
            )

        clicked_feature = next(
            (feature for feature in self._CLICKED_BUTTON_STATES
             if feature in clicked_features),
            None,
        )
        pokemon_moves = None
        if battle_mode:
            self._click_non_battle_polls = 0
            if turn_number is not None and turn_number != self._action_turn:
                self._clear_action_clicks()
                self._action_turn = turn_number
            all_action_buttons_detected = all(value is True for value in button_states.values())
            if clicked_feature is not None and (
                all_action_buttons_detected or self._action_menu_visible
                or not any(self._clicked_button_states.values())
            ):
                self._clear_action_clicks()
                self._pending_action_feature = clicked_feature
                self._pending_action_at = frame_started_at

            all_action_buttons_absent = all(value is False for value in button_states.values())
            use_item_results = [
                result for region, result in feature_results["bag"]
                if region.label.casefold() == "use item button"
            ]
            use_item_visible = any(
                result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
                and self._contains_text(result.text, "use item")
                for result in use_item_results
            )
            has_use_item_region = any(
                region.label.casefold() == "use item button"
                for region in self._regions_by_feature.get("bag", ())
            )
            bag_context = (
                self._pending_action_feature == "bag"
                or self._clicked_button_states["clickedBagButton"]
            )
            other_menu_context = (
                self._pending_action_feature in ("fight", "swap", "run")
                or any(
                    self._clicked_button_states[name]
                    for name in ("clickedFightButton", "clickedSwapButton", "clickedRunButton")
                )
            )
            # Use Item plus a readable name positively identifies the Bag even
            # if overlapping main-button OCR is unknown or turn tracking reset.
            # Without a saved Use Item region, keyboard mode can still use the
            # hidden main menu and item OCR, but never a known move/team menu.
            showing_bag_item_menu = bool(bag_item_name) and not all_action_buttons_detected and (
                use_item_visible
                or (
                    all_action_buttons_absent
                    and not has_move_details(bag_item_name)
                    and (bag_context or (not has_use_item_region and not other_menu_context))
                )
            )

            if all_action_buttons_detected:
                for name in self._clicked_button_states:
                    self._clicked_button_states[name] = False
                self._action_transition_polls = 0
                if frame_started_at - self._pending_action_at >= ACTION_RETRY_SECONDS:
                    self._pending_action_feature = None
            elif all_action_buttons_absent or showing_bag_item_menu:
                if not showing_bag_item_menu and all_action_buttons_absent and (
                    self._pending_action_feature == "fight"
                    or self._clicked_button_states["clickedFightButton"]
                ):
                    pokemon_moves = self._join_move_ocr_text(self._read_regions(
                        screenshot, self._regions_by_feature.get("moves", ()), timings,
                    ))
                self._action_transition_polls = (
                    self._action_transition_polls + 1 if all_action_buttons_absent else 0
                )
                pending = self._pending_action_feature
                response_detected = (
                    (pending == "fight" and has_move_details(pokemon_moves))
                    or (pending == "bag" and showing_bag_item_menu)
                    or (pending in ("swap", "run") and self._action_transition_polls >= 2)
                )
                if response_detected:
                    clicked_state, _ = self._CLICKED_BUTTON_STATES[pending]
                    self._clicked_button_states[clicked_state] = True
                    self._pending_action_feature = None
            else:
                self._action_transition_polls = 0
            self._action_menu_visible = all_action_buttons_detected
        else:
            self._click_non_battle_polls += 1
            if self._click_non_battle_polls >= self._BATTLE_END_CONFIRMATION_POLLS:
                self._clear_action_clicks()
                self._action_turn = None
                self._action_menu_visible = False

        if not showing_bag_item_menu:
            bag_item_name = None
        state: dict[str, object] = {
            "captureStartedAt": frame_started_at,
            "clickStateReliable": not self._action_ocr_failed,
            "onBattleMode": battle_mode,
            **button_states,
            **{name: value and battle_mode for name, value in self._clicked_button_states.items()},
            "turn": turn_number,
            "chatBoxMessage": chat_message,
            "wildPokemonName": wild_pokemon_name,
            "wildPokemonIsShiny": wild_pokemon_is_shiny,
            "wildPokemonIsElite": wild_pokemon_is_elite,
            "showingBagItemMenu": showing_bag_item_menu,
            "bagItemName": bag_item_name,
            "yourPokemonName": your_pokemon_name,
            "pokemonMoves": pokemon_moves,
            "yourPokemonHealth": your_health,
            "enemyPokemonHealth": enemy_health,
        }
        return state, captured_at

    def _clear_action_clicks(self) -> None:
        for name in self._clicked_button_states:
            self._clicked_button_states[name] = False
        self._pending_action_feature = None
        self._action_transition_polls = 0

    def _read_regions(
        self,
        screenshot: np.ndarray,
        regions: Sequence[SavedRegion],
        timings: dict[str, float],
    ) -> list[tuple[SavedRegion, OCRResult]]:
        results: list[tuple[SavedRegion, OCRResult]] = []
        for region in regions:
            if self.isInterruptionRequested():
                break
            try:
                cropped = self.region_cropper.crop(
                    screenshot, region, self.game_geometry
                )
            except Exception as error:
                if region.feature in (*self._CLICKED_BUTTON_STATES, "moves", "turn", "pokemon-team"):
                    self._action_ocr_failed = True
                self.log_message.emit(
                    "ERROR", f"Realtime crop failed for {region.label}: {error}"
                )
                continue

            if self.isInterruptionRequested():
                continue
            threshold_method = self._THRESHOLD_METHODS[region.feature]
            morphology_operation = self._MORPHOLOGY_OPERATIONS.get(
                region.feature,
                "erode",
            )
            try:
                phase_started_at = time.perf_counter()
                try:
                    processed = self.image_processor.process(
                        cropped,
                        threshold_method=threshold_method,
                    )
                    processed = self.image_processor.imageMorphology(
                        processed, morphology_operation
                    )
                finally:
                    timings["image_process"] += time.perf_counter() - phase_started_at

                image_fingerprint = hashlib.blake2b(
                    processed.tobytes(),
                    digest_size=16,
                ).digest()
                cached_result = self._ocr_cache.get(region)
                if cached_result is not None and cached_result[0] == image_fingerprint:
                    result = cached_result[1]
                else:
                    phase_started_at = time.perf_counter()
                    try:
                        is_chat_region = region.feature == "chat-box"
                        result = self.text_extractor.extract(
                            processed,
                            preserve_lines=is_chat_region,
                            minimum_line_confidence=(
                                self._MINIMUM_CHAT_LINE_CONFIDENCE
                                if is_chat_region
                                else 0.0
                            ),
                        )
                    finally:
                        timings["text_extraction"] += (
                            time.perf_counter() - phase_started_at
                        )
                    self._ocr_cache[region] = (image_fingerprint, result)
            except Exception as error:
                if region.feature in (*self._CLICKED_BUTTON_STATES, "moves", "turn", "pokemon-team"):
                    self._action_ocr_failed = True
                self.log_message.emit(
                    "ERROR",
                    f"Realtime OCR failed for {region.label} "
                    f"({threshold_method}): {error}",
                )
                continue

            results.append((region, result))
        return results

    @staticmethod
    def _chat_indicates_battle(text: str) -> bool:
        return RealtimeRegionMonitor._extract_wild_pokemon_name(text) is not None

    @staticmethod
    def _chat_confirms_catch(text: str) -> bool:
        return re.search(
            r"\bcongratulations,\s*you\s+caught\s+the\s+opposing\b",
            text,
            flags=re.IGNORECASE,
        ) is not None

    @staticmethod
    def _is_battle_mode(
        chat_message: str,
        battle_phrase_detected: bool,
        enemy_health: int | None,
    ) -> bool:
        if RealtimeRegionMonitor._chat_confirms_catch(chat_message):
            return False
        return battle_phrase_detected or enemy_health is not None

    @staticmethod
    def _extract_wild_pokemon_name(text: str) -> str | None:
        match = re.search(
            r"\ba\s+wild\s+(.+?)\s+appeared\b",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            normalized = RealtimeRegionMonitor._normalize_text(text)
            match = re.search(
                r"\ba\s*wild\s+([\w'-]+(?:\s+[\w'-]+)*)\s+appeared\b",
                normalized,
            )
            if match is not None:
                return match.group(1).title()
        if match is None:
            return None
        pokemon_name = re.sub(r"\s+", " ", match.group(1)).strip(" .,!?:;")
        return pokemon_name or None

    @staticmethod
    def _join_ocr_text(
        results: Sequence[tuple[SavedRegion, OCRResult]],
    ) -> str | None:
        recognized_text = " | ".join(
            result.text.strip() for _, result in results if result.text.strip()
        )
        return recognized_text or None

    @staticmethod
    def _extract_bag_item_name(
        results: Sequence[tuple[SavedRegion, OCRResult]],
    ) -> str | None:
        return next(
            (
                re.sub(r"\s+", " ", result.text).strip()
                for region, result in results
                if region.label.casefold() == "item name"
                and result.mean_confidence
                >= RealtimeRegionMonitor._MINIMUM_STATE_CONFIDENCE
                and result.text.strip()
            ),
            None,
        )

    @staticmethod
    def _new_chat_message(previous: str | None, current: str) -> str:
        current = RealtimeRegionMonitor._normalize_chat_snapshot(current)
        if previous is None:
            return current

        previous = RealtimeRegionMonitor._normalize_chat_snapshot(previous)
        if current == previous or current in previous:
            return ""

        previous_position = current.rfind(previous)
        if previous_position >= 0:
            return current[previous_position + len(previous) :].strip(" |")

        previous_lines = previous.splitlines()
        current_lines = current.splitlines()
        maximum_overlap = min(len(previous_lines), len(current_lines))
        for overlap_length in range(maximum_overlap, 0, -1):
            if previous_lines[-overlap_length:] == current_lines[:overlap_length]:
                return "\n".join(current_lines[overlap_length:])
        return current

    @staticmethod
    def _normalize_chat_snapshot(text: str) -> str:
        return "\n".join(
            normalized_line
            for line in text.splitlines()
            if (normalized_line := re.sub(r"[ \t]+", " ", line).strip())
        )

    def _is_new_pokemon_encounter(
        self,
        pokemon_name: str | None,
        battle_active: bool,
    ) -> bool:
        if battle_active:
            self._non_battle_poll_count = 0
        elif self._encounter_active:
            self._non_battle_poll_count += 1
            if self._non_battle_poll_count >= self._BATTLE_END_CONFIRMATION_POLLS:
                self._encounter_active = False
                self._non_battle_poll_count = 0

        if pokemon_name is None or not battle_active or self._encounter_active:
            return False

        self._encounter_active = True
        return True

    @staticmethod
    def _join_move_ocr_text(
        results: Sequence[tuple[SavedRegion, OCRResult]],
    ) -> str | None:
        move_entries: list[tuple[int, str]] = []
        for region, result in results:
            match = re.fullmatch(
                r"Move Slot ([1-4])",
                region.label,
                flags=re.IGNORECASE,
            )
            if match is None:
                continue
            if result.mean_confidence < RealtimeRegionMonitor._MINIMUM_STATE_CONFIDENCE:
                continue
            parsed_move = RealtimeRegionMonitor._parse_move_text(result.text)
            if parsed_move is None:
                continue
            move_name, effectiveness, pp = parsed_move
            details = [effectiveness]
            if pp is not None:
                details.append(pp)
            move_entries.append(
                (
                    int(match.group(1)),
                    f"Move Slot {match.group(1)}: {move_name} "
                    f"({' | '.join(details)})",
                )
            )
        move_entries.sort(key=lambda entry: entry[0])
        return " | ".join(entry for _, entry in move_entries) or None

    @staticmethod
    def _parse_move_text(text: str) -> tuple[str, str, str | None] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return None

        effectiveness_match = re.search(
            r"(?<![\w.])(\d+(?:\.\d+)?)\s*[xX](?!\w)",
            normalized,
        )
        pp_match = re.search(r"(?<!\d)(\d+\s*/\s*\d+)(?!\d)", normalized)
        pp = re.sub(r"\s+", "", pp_match.group(1)) if pp_match else None
        metadata_spans = sorted(
            (
                match.span()
                for match in (effectiveness_match, pp_match)
                if match is not None
            ),
            reverse=True,
        )
        move_name = normalized
        for start, end in metadata_spans:
            move_name = move_name[:start] + " " + move_name[end:]
        move_name = re.sub(r"\s+", " ", move_name).strip(" ,;:-")
        if not move_name:
            return None

        effectiveness = (
            f"{effectiveness_match.group(1)}x"
            if effectiveness_match is not None
            else "-"
        )
        return move_name, effectiveness, pp

    @staticmethod
    def _extract_turn_number(text: str) -> int | None:
        match = re.search(r"\bturn\s*(\d+)\b", text, flags=re.IGNORECASE)
        return int(match.group(1)) if match is not None else None

    @staticmethod
    def _contains_text(text: str, expected: str) -> bool:
        normalized = RealtimeRegionMonitor._normalize_text(text)
        return bool(
            re.search(
                rf"(?<!\w){re.escape(expected)}(?!\w)",
                normalized,
            )
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text.casefold())
        without_marks = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
        )
        return re.sub(r"[^\w'-]+", " ", without_marks).strip()

    @staticmethod
    def _parse_health(text: str) -> int | None:
        normalized = re.sub(
            r"(?<=\d)[Oo](?=\d)|(?<!\w)[Oo](?=\s*[/|]\s*\d)",
            "0",
            text,
        )
        percent = re.search(r"(?<!\d)(\d{1,3})\s*%", normalized)
        if percent:
            return min(100, int(percent.group(1)))

        fraction = re.search(r"(?<!\d)(\d{1,3})\s*[/|]\s*(\d{1,3})(?!\d)", normalized)
        if fraction:
            current, maximum = (int(value) for value in fraction.groups())
            if maximum > 0:
                return min(100, round(current / maximum * 100))

        values = re.findall(r"(?<!\d)(\d{1,3})(?!\d)", normalized)
        if len(values) == 1:
            return min(100, int(values[0]))
        return None

    @staticmethod
    def _format_state(state: dict[str, object]) -> str:
        fields = ", ".join(
            f"{name}: {value}"
            for name, value in state.items()
            if name not in ("chatBoxMessage", "captureStartedAt", "clickStateReliable")
        )
        return f"[{fields}]"
