import re
import threading
import time
import unicodedata
import ctypes
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, wait
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
from core.health_text import parse_health_text
from core.ev_training import chat_confirms_opponent_defeat
from core.move_text import parse_move_text
from core.special_pokemon_message import (
    has_special_pokemon_message_hint,
    special_pokemon_message_kind,
)
from core.pokemon_name import (
    is_elite_wild_pokemon_name,
    is_nature_matched_wild_pokemon_name,
    is_shiny_wild_pokemon_name,
    pokemon_species_name,
)
from models.ocr_result import OCRResult
from models.region_config import SavedRegion, canonical_region_role
from ocr.region_ocr import PreparedOCR, RegionOCR, RegionOCRReading
from ocr.text_extractor import TextExtractor
from utils.screen_coordinates import physical_to_logical_point


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

    def update_regions(self, regions: tuple[SavedRegion, ...]) -> None:
        """Replace click hitboxes atomically and discard clicks on the old layout."""
        action_regions = frozenset(
            region for region in regions
            if region.label.casefold() == _ACTION_BUTTON_ROLES.get(region.feature)
        )
        button_regions = {
            feature: tuple(
                QRect(region.x, region.y, region.width, region.height)
                for region in action_regions if region.feature == feature
            )
            for feature in _ACTION_BUTTON_ROLES
        }
        with self._lock:
            self._action_regions = action_regions
            self._button_regions = button_regions
            self._pending_click_feature = None
            self._pending_click_at = 0.0

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
        with self._lock:
            matching_features = {
                feature
                for feature, regions in self._button_regions.items()
                if any(region.contains(x, y) for region in regions)
            }
            if len(matching_features) != 1:
                return
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
    pokemon_encountered = Signal(str, object)
    pokemon_encounter_updated = Signal(str, object)

    _MINIMUM_STATE_CONFIDENCE = 50.0
    _POLL_INTERVAL_SECONDS = 0.25
    _BATTLE_POLL_INTERVAL_SECONDS = 0.10
    _BATTLE_END_CONFIRMATION_POLLS = 3
    _MAX_OCR_WORKERS = 4
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
        self._configuration_lock = threading.Lock()
        self._pending_region_configuration: tuple[tuple[SavedRegion, ...], QRect] | None = None
        self.window_handle = window_handle
        self._click_listener = _GameRegionClickListener(regions, window_handle)
        self.game_geometry = QRect(game_geometry)
        self.image_capture = image_capture or ImageCapture()
        self.region_cropper = region_cropper or RegionCropper()
        self.image_processor = image_processor or ImageProcessor()
        self.text_extractor = text_extractor or TextExtractor()
        self.region_ocr = RegionOCR(self.image_processor, self.text_extractor)
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
        self._recovering_battle_menu = False
        self._ocr_cache: dict[SavedRegion, tuple[bytes, OCRResult, int]] = {}
        self._nature_match_color_evidence: dict[SavedRegion, bool] = {}
        self._last_chat_message: str | None = None
        self._encounter_active = False
        self._encounter_battle_active = False
        self._non_battle_poll_count = 0
        self._encounter_pokemon_name: str | None = None
        self._encounter_is_shiny = False
        self._encounter_is_elite = False
        self._last_encounter_nature_match: bool | None = None

    def record_automated_click(self, region: SavedRegion) -> None:
        """Queue an input attempt; a later OCR frame must confirm the response."""
        self._click_listener.record_automated_click(region)

    def record_automated_action(self, feature: str, role: str) -> None:
        """Track a submitted key or click by its menu action, without coordinates."""
        self._click_listener.record_automated_action(feature, role)

    def update_regions(self, regions: tuple[SavedRegion, ...], game_geometry: QRect) -> None:
        """Queue a new layout; only the capture thread mutates OCR/menu state."""
        if not game_geometry.isValid():
            raise ValueError("The game window bounds are invalid.")
        with self._configuration_lock:
            self._pending_region_configuration = (tuple(regions), QRect(game_geometry))

    def _apply_region_configuration(self) -> None:
        with self._configuration_lock:
            configuration = self._pending_region_configuration
            self._pending_region_configuration = None
        if configuration is None:
            return
        self.regions, self.game_geometry = configuration
        self._regions_by_feature = {}
        for region in self.regions:
            self._regions_by_feature.setdefault(region.feature, []).append(region)
        self._ocr_cache.clear()
        self._click_listener.update_regions(self.regions)
        self._clear_action_clicks()
        self._action_turn = None
        self._action_menu_visible = False
        self._recovering_battle_menu = True
        self._click_non_battle_polls = 0
        self._non_battle_poll_count = 0
        # Do not restart encounter tracking, lose confirmed shiny/elite traits,
        # or count the same battle again just because an outline was adjusted.

    def run(self) -> None:
        click_listener_started = False
        click_listener_attempted = False
        poll_interval_seconds = self._POLL_INTERVAL_SECONDS
        try:
            while not self.isInterruptionRequested():
                self._apply_region_configuration()
                if not click_listener_attempted and self._click_listener._action_regions:
                    click_listener_attempted = True
                    if getattr(ctypes, "WinDLL", None) is not None:
                        try:
                            self._click_listener.start()
                            click_listener_started = True
                        except Exception as error:
                            self.log_message.emit("ERROR", f"Game button click listener failed: {error}")
                    else:
                        self.log_message.emit(
                            "ERROR", "Manual game button click tracking is only supported on Windows.",
                        )
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
                    with self._configuration_lock:
                        layout_current = self._pending_region_configuration is None
                    if layout_current and not self.isInterruptionRequested():
                        self._publish_state(state, captured_at)
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

    def _publish_state(self, state: dict[str, object], captured_at: str) -> None:
        state = dict(state)
        battle_active = state.get("onBattleMode") is True
        pokemon_name = pokemon_species_name(state.get("wildPokemonName")) or None
        was_in_battle = self._encounter_battle_active
        new_encounter = self._is_new_pokemon_encounter(
            pokemon_name,
            battle_active or (
                self._encounter_battle_active
                and state.get("showingSpecialPokemonMessage", False) is not False
            ),
        )
        if not was_in_battle or not self._encounter_battle_active:
            self._encounter_pokemon_name = None
            self._encounter_is_shiny = False
            self._encounter_is_elite = False
            self._last_encounter_nature_match = None
        if new_encounter:
            self._encounter_pokemon_name = pokemon_name
        previous_traits = (self._encounter_is_shiny, self._encounter_is_elite)
        nature_match_arrived = (
            battle_active and state.get("wildPokemonNatureMatch") is True
            and self._last_encounter_nature_match is not True
        )
        if battle_active:
            # This tracks the live observation only, not a latched trait. A
            # later readable S can refresh an S-only Desired alert without
            # counting another encounter or changing the live Nature state.
            evidence = state.get("wildPokemonNatureMatch")
            self._last_encounter_nature_match = evidence if type(evidence) is bool else None
        if self._encounter_battle_active and battle_active:
            # A readable shiny marker can arrive after the announcement, or
            # disappear briefly behind a popup. Keep confirmed traits until
            # the battle ends, for both automation and encounter notifications.
            self._encounter_is_shiny |= state.get("wildPokemonIsShiny") is True
            self._encounter_is_elite |= state.get("wildPokemonIsElite") is True
            state["wildPokemonIsShiny"] = self._encounter_is_shiny
            # Keep positive Elite proof, but do not turn an unreadable name
            # into a negative result for Specific Only's Elite filter.
            if self._encounter_is_elite:
                state["wildPokemonIsElite"] = True

        self.log_message.emit("INFO", f"{captured_at} {self._format_state(state)}")
        self.state_updated.emit(state)
        chat_message = state.get("chatBoxMessage")
        if isinstance(chat_message, str) and chat_message:
            new_message = self._new_chat_message(self._last_chat_message, chat_message)
            if new_message and battle_active:
                for line in new_message.splitlines():
                    if line.strip():
                        self.chat_message.emit(captured_at, line.strip())
            if self._last_chat_message is None or new_message:
                self._last_chat_message = chat_message
        elif not battle_active:
            self._last_chat_message = None

        # Carry the actual capture with the event. The UI may correctly reject
        # this frame for battle input while its encounter evidence is still useful.
        if self._encounter_pokemon_name is None:
            return
        if new_encounter:
            self.pokemon_encountered.emit(self._encounter_pokemon_name, state)
        elif self._encounter_active and battle_active and (
            nature_match_arrived or previous_traits != (
                self._encounter_is_shiny, self._encounter_is_elite,
            )
        ):
            self.pokemon_encounter_updated.emit(self._encounter_pokemon_name, state)

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
        # Include layout application in this frame's freshness boundary. A
        # configuration queued between application and capture must not make an
        # old-layout frame appear newer than the UI's reconfiguration cutoff.
        frame_started_at = time.monotonic()
        self._apply_region_configuration()
        self._action_ocr_failed = False
        self._nature_match_color_evidence.clear()
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

        special_message, showing_special_message = self._read_special_pokemon_message(
            screenshot, timings,
        )
        special_message_blocking = showing_special_message is not False

        # These observations are independent, but must all describe the same
        # screenshot. Group them so the long chat read does not delay names/HP.
        observation_results = self._read_regions(
            screenshot,
            tuple(
                region
                for feature in ("chat-box", "pokemon-name", "health", "synchronize")
                for region in self._regions_by_feature.get(feature, ())
            ),
            timings,
        )
        chat_results = [
            item for item in observation_results if item[0].feature == "chat-box"
        ]
        chat_message = "\n".join(
            self._normalize_chat_snapshot(result.text)
            for _, result in chat_results
            if result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
            and result.text.strip()
        )
        chat_wild_pokemon_name = self._extract_wild_pokemon_name(chat_message)
        battle_phrase_detected = self._chat_indicates_battle(chat_message)

        pokemon_name_results = [
            item for item in observation_results if item[0].feature == "pokemon-name"
        ]
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
        wild_pokemon_is_shiny = any(
            is_shiny_wild_pokemon_name(name)
            for name in (selected_wild_pokemon_name, chat_wild_pokemon_name)
        )
        wild_pokemon_is_elite = any(
            is_elite_wild_pokemon_name(name)
            for name in (selected_wild_pokemon_name, chat_wild_pokemon_name)
        )
        if not wild_pokemon_is_elite:
            wild_name_configured = any(
                canonical_region_role(region) == "Wild Pokemon"
                for region in self._regions_by_feature.get("pokemon-name", ())
            )
            readable_name = (
                selected_wild_pokemon_name if wild_name_configured else chat_wild_pokemon_name
            )
            if not any(character.isalpha() for character in pokemon_species_name(readable_name)):
                wild_pokemon_is_elite = None
        # Nature match describes the game's selected Pokemon. Keep the live
        # observation separate from Shiny/Elite, including an unknown result
        # when a green glyph cannot be read. S-only targets must not treat an
        # unreadable crop as proof that the encounter has no green S.
        wild_pokemon_nature_match = self._read_nature_match(observation_results)

        your_health: int | None = None
        enemy_health: int | None = None
        health_results = [
            item for item in observation_results if item[0].feature == "health"
        ]
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

        battle_mode = showing_special_message is True or self._is_battle_mode(
            chat_message,
            battle_phrase_detected,
            enemy_health,
        )
        button_states: dict[str, bool | None] = {
            state: None if special_message_blocking else False
            for state in self._BUTTON_FEATURES
        }
        bag_item_name: str | None = None
        showing_bag_item_menu = False
        turn_number: int | None = None

        if battle_mode and not special_message_blocking:
            # Previous/Next are input targets, not state evidence. Item/menu
            # crops are only useful after the main action menu disappears.
            action_results = self._read_regions(
                screenshot,
                tuple(
                    region
                    for feature in ("fight", "swap", "run", "bag", "turn")
                    for region in self._regions_by_feature.get(feature, ())
                    if feature == "turn"
                    or region.label.casefold() == _ACTION_BUTTON_ROLES[feature]
                ),
                timings,
            )
            for state_name, features in self._BUTTON_FEATURES.items():
                feature, expected_texts = features
                button_results = [
                    result for region, result in action_results
                    if region.feature == feature
                    and region.label.casefold() == _ACTION_BUTTON_ROLES[feature]
                ]
                button_states[state_name] = any(
                    result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
                    and any(
                        self._contains_text(result.text, expected_text)
                        for expected_text in expected_texts
                    )
                    for result in button_results
                )
                if not button_results or all(
                    result.text.strip() and result.mean_confidence < self._MINIMUM_STATE_CONFIDENCE
                    for result in button_results
                ):
                    button_states[state_name] = None
            turn_number = next(
                (
                    number
                    for region, result in action_results
                    if region.feature == "turn"
                    and result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
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
        if special_message_blocking:
            if showing_special_message is True:
                self._clear_action_clicks()
                self._action_menu_visible = False
                self._recovering_battle_menu = True
            self._click_non_battle_polls = 0
        elif battle_mode:
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
            use_item_regions = tuple(
                region
                for region in self._regions_by_feature.get("bag", ())
                if region.label.casefold() == "use item button"
            )
            item_name_regions = tuple(
                region
                for region in self._regions_by_feature.get("bag", ())
                if region.label.casefold() == "item name"
            )
            has_use_item_region = bool(use_item_regions)
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
            read_move_menu = all_action_buttons_absent and (
                self._pending_action_feature == "fight"
                or self._clicked_button_states["clickedFightButton"]
                or self._recovering_battle_menu
            )
            read_item_name = all_action_buttons_absent and (
                bag_context or (not has_use_item_region and not other_menu_context)
            )
            submenu_results: list[tuple[SavedRegion, OCRResult]] = []
            if not all_action_buttons_detected:
                submenu_results = self._read_regions(
                    screenshot,
                    (
                        *use_item_regions,
                        *(item_name_regions if read_item_name else ()),
                    ),
                    timings,
                )
            use_item_visible = any(
                region.label.casefold() == "use item button"
                and result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
                and self._contains_text(result.text, "use item")
                for region, result in submenu_results
            )
            # A fresh Use Item label can reveal a manually opened Bag despite
            # old Fight tracking or unknown main-button OCR. Keep that recovery
            # without recognizing overlapping Item Name crops on every move.
            if use_item_visible and not read_item_name:
                submenu_results.extend(self._read_regions(
                    screenshot, item_name_regions, timings,
                ))
            bag_item_name = self._extract_bag_item_name(submenu_results)
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
                self._recovering_battle_menu = False
                for name in self._clicked_button_states:
                    self._clicked_button_states[name] = False
                self._action_transition_polls = 0
                if frame_started_at - self._pending_action_at >= ACTION_RETRY_SECONDS:
                    self._pending_action_feature = None
            elif all_action_buttons_absent or showing_bag_item_menu:
                if showing_bag_item_menu:
                    self._recovering_battle_menu = False
                if not showing_bag_item_menu and read_move_menu:
                    pokemon_moves = self._join_move_ocr_text(self._read_regions(
                        screenshot, self._regions_by_feature.get("moves", ()), timings,
                    ))
                    # Closing a modal invalidates old input tracking. Recover
                    # an already-open Fight menu from fresh move/PP evidence.
                    if self._recovering_battle_menu and has_move_details(pokemon_moves):
                        self._clicked_button_states["clickedFightButton"] = True
                        self._recovering_battle_menu = False
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
                self._recovering_battle_menu = False

        if not showing_bag_item_menu:
            bag_item_name = None
        state: dict[str, object] = {
            "captureStartedAt": frame_started_at,
            "clickStateReliable": not self._action_ocr_failed and not special_message_blocking,
            "onBattleMode": battle_mode,
            "specialPokemonMessage": special_message,
            "showingSpecialPokemonMessage": showing_special_message,
            **button_states,
            **{name: value and battle_mode for name, value in self._clicked_button_states.items()},
            "turn": turn_number,
            "chatBoxMessage": chat_message,
            "wildPokemonName": wild_pokemon_name,
            "wildPokemonIsShiny": wild_pokemon_is_shiny,
            "wildPokemonIsElite": wild_pokemon_is_elite,
            "wildPokemonNatureMatch": wild_pokemon_nature_match,
            "showingBagItemMenu": showing_bag_item_menu,
            "bagItemName": bag_item_name,
            "yourPokemonName": your_pokemon_name,
            "pokemonMoves": pokemon_moves,
            "yourPokemonHealth": your_health,
            "enemyPokemonHealth": enemy_health,
        }
        return state, captured_at

    def _read_special_pokemon_message(
        self,
        screenshot: np.ndarray,
        timings: dict[str, float],
    ) -> tuple[str | None, bool | None]:
        regions = tuple(
            region for region in self._regions_by_feature.get("special-pokemon-message", ())
            if region.label.casefold() == "message box"
        )
        if not regions:
            return None, False
        results = self._read_regions(screenshot, regions, timings)
        uncertain = len(results) != len(regions)
        message = None
        for _region, result in results:
            if special_pokemon_message_kind(result.text) is not None:
                message = re.sub(r"\s+", " ", result.text).strip()
                if result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE:
                    return message, True
                uncertain = True
            elif (
                result.mean_confidence < self._MINIMUM_STATE_CONFIDENCE
                and has_special_pokemon_message_hint(result.text)
            ):
                message = re.sub(r"\s+", " ", result.text).strip()
                uncertain = True
        # Map sprites routinely produce low-confidence OCR in this saved box.
        # Only dialog-like text or an actual read failure makes it unknown;
        # unrelated background must allow startup and post-popup resumption.
        return message, None if uncertain else False

    def _read_nature_match(
        self, observations: Sequence[tuple[SavedRegion, OCRResult]],
    ) -> bool | None:
        results = [
            (region, result) for region, result in observations
            if region.feature == "synchronize" or (
                region.feature == "pokemon-name" and canonical_region_role(region) == "Wild Pokemon"
            )
        ]
        if any(
            result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
            and is_nature_matched_wild_pokemon_name(result.text)
            for _region, result in results
        ):
            return True
        relevant_regions = (
            *self._regions_by_feature.get("synchronize", ()),
            *(
                region for region in self._regions_by_feature.get("pokemon-name", ())
                if canonical_region_role(region) == "Wild Pokemon"
            ),
        )
        if any(self._nature_match_color_evidence.get(region) is True for region in relevant_regions):
            return None
        marker_regions = self._regions_by_feature.get("synchronize", ())
        observed_regions = {region for region, _result in results}
        if marker_regions:
            # A failed optional marker crop/read must not be overridden by a
            # name crop that may not include the S at all. Positive S evidence
            # above is sufficient; absent evidence requires every saved marker.
            return False if all(
                region in observed_regions
                and self._nature_match_color_evidence.get(region) is False
                for region in marker_regions
            ) else None
        if any(
            region.feature == "pokemon-name"
            and self._nature_match_color_evidence.get(region) is False
            and result.mean_confidence >= self._MINIMUM_STATE_CONFIDENCE
            and bool(pokemon_species_name(result.text))
            for region, result in results
        ):
            return False
        return None

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
        results: dict[SavedRegion, OCRResult] = {}
        jobs: dict[SavedRegion, tuple[PreparedOCR, ...]] = {}
        for region in regions:
            if self.isInterruptionRequested():
                break
            if region in results or region in jobs:
                continue
            try:
                cropped = self.region_cropper.crop(
                    screenshot, region, self.game_geometry
                )
            except Exception as error:
                self._record_region_read_failure(region, "crop", error)
                continue

            if self.isInterruptionRequested():
                continue
            cached_result = self._ocr_cache.get(region)
            try:
                phase_started_at = time.perf_counter()
                try:
                    prepared = self.region_ocr.prepare(cropped, region)
                    if region.feature == "synchronize" or (
                        region.feature == "pokemon-name" and canonical_region_role(region) == "Wild Pokemon"
                    ):
                        self._nature_match_color_evidence[region] = any(
                            "green" in candidate.name_prefix_colors for candidate in prepared
                        )
                    # Only thresholds used by the previous reading can affect
                    # its result. Unused fallbacks may see animated scenery.
                    image_fingerprint = self.region_ocr.fingerprint(
                        prepared[:cached_result[2]] if cached_result else prepared,
                    )
                finally:
                    timings["image_process"] += time.perf_counter() - phase_started_at

                if cached_result is not None and cached_result[0] == image_fingerprint:
                    results[region] = cached_result[1]
                else:
                    jobs[region] = prepared
            except Exception as error:
                self._record_region_read_failure(region, "OCR", error)
                continue

        readings = self._extract_prepared_regions(jobs, timings)
        if self.isInterruptionRequested():
            return []
        # Only the capture thread owns cache/menu state. Workers recognize
        # independent crops, finish before this method returns, and cannot
        # publish a partial frame or race a queued layout change.
        for region, reading in readings.items():
            self._ocr_cache[region] = (
                self.region_ocr.fingerprint(jobs[region][:reading.variant_count]),
                reading.result,
                reading.variant_count,
            )
            results[region] = reading.result
        return [(region, results[region]) for region in regions if region in results]

    def _extract_prepared_regions(
        self,
        jobs: dict[SavedRegion, tuple[PreparedOCR, ...]],
        timings: dict[str, float],
    ) -> dict[SavedRegion, RegionOCRReading]:
        readings: dict[SavedRegion, RegionOCRReading] = {}
        if not jobs or self.isInterruptionRequested():
            return readings
        phase_started_at = time.perf_counter()
        try:
            if len(jobs) == 1:
                region, prepared = next(iter(jobs.items()))
                try:
                    readings[region] = self.region_ocr.extract(prepared, region)
                except Exception as error:
                    self._record_region_read_failure(region, "OCR", error)
                return readings

            executor = ThreadPoolExecutor(
                max_workers=min(self._MAX_OCR_WORKERS, len(jobs)),
                thread_name_prefix="battle-region-ocr",
            )
            try:
                futures = {
                    region: executor.submit(self.region_ocr.extract, prepared, region)
                    for region, prepared in jobs.items()
                    if not self.isInterruptionRequested()
                }
                for region, future in futures.items():
                    while not future.done() and not self.isInterruptionRequested():
                        wait((future,), timeout=0.05)
                    if self.isInterruptionRequested():
                        break
                    try:
                        readings[region] = future.result()
                    except Exception as error:
                        self._record_region_read_failure(region, "OCR", error)
            finally:
                # Cancel queued reads on Stop; allow only already-running OCR
                # calls to finish before releasing the screenshot/monitor.
                executor.shutdown(wait=True, cancel_futures=True)
        finally:
            # Report elapsed OCR time, not the sum of overlapping worker times.
            timings["text_extraction"] += time.perf_counter() - phase_started_at
        return readings

    def _record_region_read_failure(
        self, region: SavedRegion, phase: str, error: Exception,
    ) -> None:
        if region.feature in (*self._CLICKED_BUTTON_STATES, "moves", "turn", "pokemon-team"):
            self._action_ocr_failed = True
        self.log_message.emit(
            "ERROR", f"Realtime {phase} failed for {region.label}: {error}",
        )

    @staticmethod
    def _chat_indicates_battle(text: str) -> bool:
        return RealtimeRegionMonitor._extract_wild_pokemon_name(text) is not None

    @staticmethod
    def _chat_confirms_catch(text: str) -> bool:
        confirmations = list(re.finditer(
            r"\bcongratulations,\s*you\s+caught\s+the\s+opposing\b",
            text,
            flags=re.IGNORECASE,
        ))
        if not confirmations:
            return False
        # Chat can retain the previous catch above a new wild announcement.
        # Only a catch after the latest encounter can end the current battle.
        return not RealtimeRegionMonitor._chat_indicates_battle(
            text[confirmations[-1].end():],
        )

    @staticmethod
    def _is_battle_mode(
        chat_message: str,
        battle_phrase_detected: bool,
        enemy_health: int | None,
    ) -> bool:
        if RealtimeRegionMonitor._chat_confirms_catch(chat_message):
            return False
        if enemy_health in (None, 0) and chat_confirms_opponent_defeat(chat_message):
            # The old wild announcement may remain in chat after a knockout.
            # Its explicit terminal message can end this battle; disappearance
            # is still confirmed by three polls in the encounter/action trackers.
            return False
        return battle_phrase_detected or enemy_health is not None

    @staticmethod
    def _extract_wild_pokemon_name(text: str) -> str | None:
        matches = list(re.finditer(
            r"\ba\s+wild\s+(.+?)\s+appeared\b",
            text,
            flags=re.IGNORECASE,
        ))
        match = matches[-1] if matches else None
        if match is None:
            normalized = RealtimeRegionMonitor._normalize_text(text)
            matches = list(re.finditer(
                r"\ba\s*wild\s+([\w'-]+(?:\s+[\w'-]+)*)\s+appeared\b",
                normalized,
            ))
            match = matches[-1] if matches else None
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
            self._encounter_battle_active = True
            self._non_battle_poll_count = 0
        elif self._encounter_battle_active:
            self._non_battle_poll_count += 1
            if self._non_battle_poll_count >= self._BATTLE_END_CONFIRMATION_POLLS:
                self._encounter_active = False
                self._encounter_battle_active = False
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
        return parse_move_text(text)

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
        return parse_health_text(text)

    @staticmethod
    def _format_state(state: dict[str, object]) -> str:
        fields = ", ".join(
            f"{name}: {value}"
            for name, value in state.items()
            if name not in ("chatBoxMessage", "captureStartedAt")
        )
        return f"[{fields}]"
