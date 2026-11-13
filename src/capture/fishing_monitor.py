import time
from datetime import datetime
from pathlib import Path
from threading import Event, Lock

import cv2
import numpy as np
from PySide6.QtCore import QObject, QRect, QThread, Signal

from capture.game_input import GameInput
from capture.image_capture import ImageCapture
from capture.region_cropper import RegionCropper
from core.auto_fishing import (
    FishingDetection,
    detect_fishing_markers,
    draw_fishing_detection,
)
from models.region_config import SavedRegion


class FishingMonitor(QThread):
    """Capture and track the fishing bar, optionally tapping Space on overlap."""

    frame_updated = Signal(object, object)
    log_message = Signal(str, str)
    _ERROR_RETRY_INTERVAL_SECONDS = 0.1
    _SPACE_VIRTUAL_KEY = 0x20
    _F_VIRTUAL_KEY = 0x46
    _MISSING_GREEN_ZONE_TIMEOUT_SECONDS = 10.0
    _FALLBACK_KEY_HOLD_SECONDS = 0.01

    def __init__(
        self,
        region: SavedRegion,
        window_handle: int,
        game_geometry: QRect,
        *,
        auto_fishing: bool = False,
        reaction_lead_ms: int = 50,
        space_hold_ms: int = 10,
        press_f_on_missing_zone: bool = False,
        publish_frames: bool = True,
        screenshot_directory: Path | None = None,
        save_screenshots: bool = True,
        image_capture: ImageCapture | None = None,
        region_cropper: RegionCropper | None = None,
        game_input: GameInput | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if region.feature != "fishing":
            raise ValueError("Fishing monitor requires a fishing region.")
        if not 0 <= reaction_lead_ms <= 500:
            raise ValueError("Fishing reaction lead must be between 0 and 500 ms.")
        if not 0 <= space_hold_ms <= 100:
            raise ValueError("Fishing Space hold must be between 0 and 100 ms.")
        self.region = region
        self.window_handle = window_handle
        self.game_geometry = QRect(game_geometry)
        self.auto_fishing = auto_fishing
        self.reaction_lead_ms = reaction_lead_ms
        self.space_hold_ms = space_hold_ms
        self.press_f_on_missing_zone = press_f_on_missing_zone
        self.publish_frames = publish_frames
        self.screenshot_directory = (
            screenshot_directory
            / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            if screenshot_directory is not None
            else None
        )
        self._save_screenshots = Event()
        if self.screenshot_directory is not None and save_screenshots:
            self._save_screenshots.set()
        self.saved_screenshot_count = 0
        self.image_capture = image_capture or ImageCapture()
        self.region_cropper = region_cropper or RegionCropper()
        self.game_input = game_input or GameInput()
        self._input_state_lock = Lock()
        self._battle_active = False
        self._configuration_paused = False
        self._configuration_pause_started: float | None = None
        self._configuration_resumed_at: float | None = None
        self._overlap_latched = False
        self._previous_hook_x: int | None = None
        self._previous_detection_time: float | None = None
        self._green_zone_missing_since: float | None = None
        self._missing_zone_fallback_pressed = False
        self._last_capture_error: str | None = None

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                capture_started_at = time.perf_counter()
                screenshot = self.image_capture.capture_window(self.window_handle)
                cropped = self.region_cropper.crop(
                    screenshot,
                    self.region,
                    self.game_geometry,
                )
                try:
                    self._save_screenshot_if_enabled(cropped)
                except (OSError, cv2.error) as error:
                    self._save_screenshots.clear()
                    self.log_message.emit(
                        "ERROR",
                        f"Fishing screenshot saving stopped: {error}",
                    )
                detection = detect_fishing_markers(cropped)
                self._handle_overlap(detection, capture_started_at=capture_started_at)
                if self.publish_frames:
                    self.frame_updated.emit(
                        draw_fishing_detection(cropped, detection),
                        detection,
                    )
                self._last_capture_error = None
            except (OSError, RuntimeError, ValueError, cv2.error) as error:
                message = str(error)
                if message != self._last_capture_error:
                    self.log_message.emit("ERROR", f"Fishing capture failed: {error}")
                    self._last_capture_error = message
                with self._input_state_lock:
                    if not self._configuration_paused:
                        self._reset_fishing_state()
                time.sleep(self._ERROR_RETRY_INTERVAL_SECONDS)

    def set_battle_active(self, active: bool) -> None:
        """Hand off battle transitions from the UI without racing a fishing input."""
        with self._input_state_lock:
            if self._battle_active == active:
                return
            self._battle_active = active
            self._reset_fishing_state()

    def set_configuration_paused(self, paused: bool) -> None:
        """Pause input without treating a region adjustment as a new cycle."""
        with self._input_state_lock:
            if self._configuration_paused == paused:
                return
            now = time.perf_counter()
            if paused:
                self._configuration_pause_started = now
            elif self._configuration_pause_started is not None:
                if self._green_zone_missing_since is not None:
                    # The strict ten-second timeout counts active observation,
                    # not time spent in an adjustment or reset dialog.
                    self._green_zone_missing_since += max(
                        0.0, now - self._configuration_pause_started,
                    )
                self._configuration_pause_started = None
                self._configuration_resumed_at = now
            self._configuration_paused = paused
            self._previous_hook_x = None
            self._previous_detection_time = None

    def _reset_missing_zone_timer(self) -> None:
        self._green_zone_missing_since = None
        self._missing_zone_fallback_pressed = False

    def _reset_fishing_state(self) -> None:
        self._overlap_latched = False
        self._previous_hook_x = None
        self._previous_detection_time = None
        self._reset_missing_zone_timer()

    def set_screenshot_saving_enabled(self, enabled: bool) -> None:
        if enabled and self.screenshot_directory is None:
            raise ValueError("Fishing screenshot saving has no output directory.")
        if enabled:
            self._save_screenshots.set()
        else:
            self._save_screenshots.clear()

    def _save_screenshot_if_enabled(self, image: np.ndarray) -> Path | None:
        if not self._save_screenshots.is_set():
            return None
        return self._save_screenshot(image)

    def _save_screenshot(self, image: np.ndarray) -> Path:
        if self.screenshot_directory is None:
            raise RuntimeError("Fishing screenshot saving is not configured.")
        self.screenshot_directory.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_directory / (
            f"frame_{self.saved_screenshot_count + 1:06d}.png"
        )
        if not cv2.imwrite(str(path), image):
            raise OSError(f"Could not write fishing screenshot: {path}")
        self.saved_screenshot_count += 1
        return path

    def _handle_overlap(
        self,
        detection: FishingDetection,
        observed_at: float | None = None,
        *,
        capture_started_at: float | None = None,
    ) -> None:
        # Capture stays outside this lock. Only the short key tap and its state
        # changes are serialized with battle entry/exit from the UI thread.
        with self._input_state_lock:
            if self._configuration_paused:
                return
            if (
                capture_started_at is not None
                and self._configuration_resumed_at is not None
                and capture_started_at <= self._configuration_resumed_at
            ):
                # A slow capture begun behind the dialog may finish after fresh
                # realtime evidence resumes fishing. Wait for a new gauge too.
                return
            if (
                not self.auto_fishing
                or self._battle_active
                or self.isInterruptionRequested()
            ):
                self._reset_fishing_state()
                return
            self._process_detection(detection, observed_at)

    def _process_detection(
        self,
        detection: FishingDetection,
        observed_at: float | None,
    ) -> None:
        current_time = time.perf_counter() if observed_at is None else observed_at
        self._handle_missing_green_zone(detection, current_time)
        target_zone = detection.target_zone
        hook_x = detection.moving_hook_x
        previous_hook_x = self._previous_hook_x
        previous_detection_time = self._previous_detection_time
        previous_hook_overlaps = (
            target_zone is not None
            and previous_hook_x is not None
            and target_zone[0] <= previous_hook_x <= target_zone[1]
        )
        crossed_target = (
            target_zone is not None
            and previous_hook_x is not None
            and hook_x is not None
            and not previous_hook_overlaps
            and not detection.moving_hook_overlaps_target
            and min(previous_hook_x, hook_x) <= target_zone[1]
            and max(previous_hook_x, hook_x) >= target_zone[0]
        )
        hook_overlaps = detection.moving_hook_overlaps_target
        predicted_arrival = False
        if (
            target_zone is not None
            and hook_x is not None
            and previous_hook_x is not None
            and previous_detection_time is not None
            and not hook_overlaps
        ):
            elapsed = current_time - previous_detection_time
            if elapsed > 0:
                velocity = (hook_x - previous_hook_x) / elapsed
                distance: int | None = None
                if hook_x < target_zone[0] and velocity > 0:
                    distance = target_zone[0] - hook_x
                elif hook_x > target_zone[1] and velocity < 0:
                    distance = hook_x - target_zone[1]
                if distance is not None:
                    time_to_target = distance / abs(velocity)
                    predicted_arrival = (
                        0 <= time_to_target <= self.reaction_lead_ms / 1000
                    )

        self._previous_hook_x = hook_x
        self._previous_detection_time = current_time if hook_x is not None else None
        should_press = hook_overlaps or crossed_target or predicted_arrival

        if hook_x is not None and not should_press:
            self._overlap_latched = False
        if not should_press:
            return
        if self._overlap_latched:
            return
        try:
            self.game_input.tap_key(
                self.window_handle,
                self._SPACE_VIRTUAL_KEY,
                duration_seconds=self.space_hold_ms / 1000,
                extended=False,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.log_message.emit(
                "WARNING",
                f"Auto Fishing could not press Space: {error}",
            )
            return
        self._overlap_latched = True
        self._reset_missing_zone_timer()
        self.log_message.emit(
            "SUCCESS",
            (
                "Auto Fishing pressed Space based on predicted hook arrival."
                if predicted_arrival
                else "Auto Fishing pressed Space because the moving hook reached "
                "the green zone."
            ),
        )

    def _handle_missing_green_zone(
        self,
        detection: FishingDetection,
        observed_at: float,
    ) -> None:
        if detection.target_zone is not None or not self.press_f_on_missing_zone:
            self._reset_missing_zone_timer()
            return
        if self._missing_zone_fallback_pressed:
            return
        if self._green_zone_missing_since is None:
            self._green_zone_missing_since = observed_at
            return
        if (
            observed_at - self._green_zone_missing_since
            <= self._MISSING_GREEN_ZONE_TIMEOUT_SECONDS
        ):
            return
        try:
            self.game_input.tap_key(
                self.window_handle,
                self._F_VIRTUAL_KEY,
                duration_seconds=self._FALLBACK_KEY_HOLD_SECONDS,
                extended=False,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.log_message.emit(
                "WARNING",
                f"Auto Fishing could not press F after the green zone was missing: {error}",
            )
            return
        self._missing_zone_fallback_pressed = True
        self.log_message.emit(
            "SUCCESS",
            "Auto Fishing pressed F after the green zone was missing for more than 10 seconds.",
        )
