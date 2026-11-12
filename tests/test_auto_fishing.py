import unittest
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from capture.fishing_monitor import FishingMonitor
from core.auto_fishing import FishingDetection, detect_fishing_markers
from launcher.settings_store import AppSettings, SettingsStore
from models.region_config import REGION_FEATURES, REGION_ROLE_LABELS, SavedRegion
from overlay.fishing_preview_window import FishingPreviewWindow
from overlay.overlay_window import OverlayWindow
from overlay.settings_window import SettingsWindow
from PySide6.QtCore import QEvent, QRect
from PySide6.QtWidgets import QApplication


class AutoFishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_detects_green_zone_yellow_zone_and_moving_hook_separately(self) -> None:
        image = self._bar_with_markers(yellow_left=22, hook_left=55)

        detection = detect_fishing_markers(image)

        self.assertEqual(detection.target_zone, (80, 95))
        self.assertEqual(detection.yellow_zone, (22, 45))
        self.assertEqual(detection.moving_hook_x, 60)
        self.assertFalse(detection.moving_hook_overlaps_target)

    def test_detects_larger_hook_marker_on_the_track(self) -> None:
        image = self._bar_with_markers(yellow_left=22, hook_left=10)
        hook_color = cv2.cvtColor(
            np.uint8([[[100, 70, 130]]]),
            cv2.COLOR_HSV2BGR,
        )[0, 0]
        image[24:46, 40:64] = hook_color

        detection = detect_fishing_markers(image)

        self.assertEqual(detection.moving_hook_x, 52)

    def test_yellow_zone_overlapping_target_does_not_trigger(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input)
        detection = FishingDetection(
            target_zone=(80, 95),
            yellow_zone=(85, 108),
            moving_hook_x=20,
        )
        self.assertFalse(detection.moving_hook_overlaps_target)
        monitor._handle_overlap(detection)
        game_input.tap_key.assert_not_called()

    def test_hook_inside_green_zone_triggers(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input)
        detection = detect_fishing_markers(
            self._bar_with_markers(yellow_left=20, hook_left=85)
        )

        self.assertTrue(detection.moving_hook_overlaps_target)
        monitor._handle_overlap(detection)
        game_input.tap_key.assert_called_once()

    def test_hook_crossing_green_zone_between_frames_triggers(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input)
        monitor._handle_overlap(FishingDetection((80, 95), (20, 43), 70))

        monitor._handle_overlap(FishingDetection((80, 95), (20, 43), 105))

        game_input.tap_key.assert_called_once()

    def test_predictive_trigger_presses_before_green_zone(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, reaction_lead_ms=50, space_hold_ms=15)
        target = (80, 95)

        monitor._handle_overlap(FishingDetection(target, None, 70), observed_at=1.0)
        monitor._handle_overlap(FishingDetection(target, None, 75), observed_at=1.1)
        game_input.tap_key.assert_not_called()

        monitor._handle_overlap(FishingDetection(target, None, 78), observed_at=1.16)

        game_input.tap_key.assert_called_once_with(
            7,
            0x20,
            duration_seconds=0.015,
            extended=False,
        )

    def test_predictive_trigger_does_not_press_when_hook_moves_away(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, reaction_lead_ms=50)
        target = (80, 95)

        monitor._handle_overlap(FishingDetection(target, None, 70), observed_at=1.0)
        monitor._handle_overlap(FishingDetection(target, None, 65), observed_at=1.1)

        game_input.tap_key.assert_not_called()

    def test_press_f_only_after_more_than_ten_seconds_without_green(self) -> None:
        game_input = Mock()
        monitor = self._monitor(
            game_input,
            press_f_on_missing_zone=True,
        )
        missing = FishingDetection(None, None, None)

        monitor._handle_overlap(missing, observed_at=1.0)
        monitor._handle_overlap(missing, observed_at=10.99)
        game_input.tap_key.assert_not_called()

        monitor._handle_overlap(missing, observed_at=11.0)
        game_input.tap_key.assert_not_called()
        monitor._handle_overlap(missing, observed_at=11.01)
        monitor._handle_overlap(missing, observed_at=20.0)

        game_input.tap_key.assert_called_once_with(
            7,
            0x46,
            duration_seconds=0.01,
            extended=False,
        )

    def test_missing_zone_fallback_rearms_when_green_zone_returns(self) -> None:
        game_input = Mock()
        monitor = self._monitor(
            game_input,
            press_f_on_missing_zone=True,
        )
        missing = FishingDetection(None, None, None)
        visible = FishingDetection((80, 95), None, 50)

        monitor._handle_overlap(missing, observed_at=1.0)
        monitor._handle_overlap(missing, observed_at=11.01)
        monitor._handle_overlap(visible, observed_at=12.0)
        monitor._handle_overlap(missing, observed_at=13.0)
        monitor._handle_overlap(missing, observed_at=23.01)

        self.assertEqual(game_input.tap_key.call_count, 2)

    def test_green_detection_restarts_the_full_missing_zone_interval(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, press_f_on_missing_zone=True)
        missing = FishingDetection(None, None, None)

        monitor._handle_overlap(missing, observed_at=1.0)
        # A green zone alone must reset the timer, even without a readable hook.
        monitor._handle_overlap(FishingDetection((80, 95), None, None), observed_at=10.9)
        monitor._handle_overlap(missing, observed_at=11.0)
        monitor._handle_overlap(missing, observed_at=21.0)
        game_input.tap_key.assert_not_called()
        monitor._handle_overlap(missing, observed_at=21.01)
        self.assertEqual(game_input.tap_key.call_args.args[1], 0x46)

    def test_battle_pauses_both_inputs_and_restarts_the_countdown(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, press_f_on_missing_zone=True)
        missing = FishingDetection(None, None, None)
        monitor._handle_overlap(missing, observed_at=1.0)
        monitor._handle_overlap(missing, observed_at=10.9)

        monitor.set_battle_active(True)
        monitor._handle_overlap(missing, observed_at=100.0)
        monitor._handle_overlap(FishingDetection((80, 95), None, 85), observed_at=101.0)
        game_input.tap_key.assert_not_called()

        monitor.set_battle_active(False)
        monitor._handle_overlap(missing, observed_at=110.0)
        monitor.set_battle_active(False)
        monitor._handle_overlap(missing, observed_at=120.0)
        game_input.tap_key.assert_not_called()
        monitor.set_battle_active(False)
        monitor._handle_overlap(missing, observed_at=120.01)
        game_input.tap_key.assert_called_once_with(7, 0x46, duration_seconds=0.01, extended=False)

    def test_battle_rearms_a_used_fallback_even_without_a_green_frame(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, press_f_on_missing_zone=True)
        missing = FishingDetection(None, None, None)
        monitor._handle_overlap(missing, observed_at=1.0)
        monitor._handle_overlap(missing, observed_at=11.01)
        self.assertEqual(game_input.tap_key.call_count, 1)

        # Both transitions can arrive while fishing is between captures.
        monitor.set_battle_active(True)
        monitor.set_battle_active(False)
        monitor._handle_overlap(missing, observed_at=100.0)
        monitor._handle_overlap(missing, observed_at=110.0)
        self.assertEqual(game_input.tap_key.call_count, 1)
        monitor._handle_overlap(missing, observed_at=110.01)
        monitor._handle_overlap(missing, observed_at=200.0)
        self.assertEqual(game_input.tap_key.call_count, 2)

    def test_space_can_trigger_on_the_next_encounter_after_a_battle(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, press_f_on_missing_zone=True)
        overlapping = FishingDetection((80, 95), None, 85)
        monitor._handle_overlap(overlapping, observed_at=1.0)
        monitor._handle_overlap(overlapping, observed_at=1.1)
        self.assertEqual(game_input.tap_key.call_count, 1)

        monitor.set_battle_active(True)
        monitor._handle_overlap(FishingDetection(None, None, None), observed_at=30.0)
        monitor.set_battle_active(False)
        monitor._handle_overlap(overlapping, observed_at=100.0)
        monitor._handle_overlap(overlapping, observed_at=100.1)
        self.assertEqual([call.args[1] for call in game_input.tap_key.call_args_list], [0x20, 0x20])

    def test_preview_never_sends_fishing_keys_even_when_fallback_is_enabled(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, auto_fishing=False, press_f_on_missing_zone=True)
        monitor._handle_overlap(FishingDetection(None, None, None), observed_at=1.0)
        monitor._handle_overlap(FishingDetection(None, None, None), observed_at=100.0)
        monitor._handle_overlap(FishingDetection((80, 95), None, 85), observed_at=101.0)
        game_input.tap_key.assert_not_called()

    def test_stopping_during_capture_prevents_a_late_f_or_space_press(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input, press_f_on_missing_zone=True)
        missing = FishingDetection(None, None, None)
        monitor._handle_overlap(missing, observed_at=1.0)
        with patch.object(monitor, "isInterruptionRequested", return_value=True):
            monitor._handle_overlap(missing, observed_at=100.0)
            monitor._handle_overlap(FishingDetection((80, 95), None, 85), observed_at=101.0)
        game_input.tap_key.assert_not_called()

    def test_missing_zone_fallback_is_disabled_by_default(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input)
        missing = FishingDetection(None, None, None)

        monitor._handle_overlap(missing, observed_at=1.0)
        monitor._handle_overlap(missing, observed_at=12.0)

        game_input.tap_key.assert_not_called()

    def test_requires_fishing_region_and_fishing_role(self) -> None:
        self.assertIn(("fishing", "Fishing"), REGION_FEATURES)
        self.assertEqual(REGION_ROLE_LABELS["fishing"], ("Fishing Bar",))
        region = SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40)
        monitor = FishingMonitor(
            region,
            1,
            QRect(0, 0, 400, 300),
            game_input=Mock(),
        )
        self.assertEqual(monitor.region, region)

    def test_saves_unannotated_fishing_frames_to_session_directory(self) -> None:
        image = self._bar_with_markers(yellow_left=22, hook_left=10)
        with tempfile.TemporaryDirectory() as directory:
            monitor = FishingMonitor(
                SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40),
                7,
                QRect(0, 0, 400, 300),
                screenshot_directory=Path(directory),
                game_input=Mock(),
            )

            path = monitor._save_screenshot(image)

            self.assertTrue(path.is_file())
            self.assertEqual(path.parent.parent, Path(directory))
            self.assertEqual(monitor.saved_screenshot_count, 1)
            np.testing.assert_array_equal(cv2.imread(str(path)), image)

    def test_save_test_region_setting_controls_live_screenshot_saving(self) -> None:
        image = self._bar_with_markers(yellow_left=22, hook_left=10)
        with tempfile.TemporaryDirectory() as directory:
            monitor = FishingMonitor(
                SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40),
                7,
                QRect(0, 0, 400, 300),
                screenshot_directory=Path(directory),
                save_screenshots=False,
                game_input=Mock(),
            )

            self.assertIsNone(monitor._save_screenshot_if_enabled(image))
            self.assertFalse(monitor.screenshot_directory.exists())

            monitor.set_screenshot_saving_enabled(True)
            saved_path = monitor._save_screenshot_if_enabled(image)
            self.assertIsNotNone(saved_path)
            self.assertTrue(saved_path.is_file())

            monitor.set_screenshot_saving_enabled(False)
            self.assertIsNone(monitor._save_screenshot_if_enabled(image))
            self.assertEqual(monitor.saved_screenshot_count, 1)

    def test_auto_fishing_toggle_and_region_persist(self) -> None:
        settings_window = SettingsWindow()
        self.assertEqual(settings_window.auto_fishing_toggle.text(), "Auto Fishing")
        self.assertIn(
            "Fishing",
            [button.text() for button in settings_window.settings_tabs._buttons],
        )
        self.assertEqual(settings_window.auto_fishing_reaction_lead_spin.value(), 50)
        self.assertEqual(settings_window.auto_fishing_space_hold_spin.value(), 10)
        self.assertFalse(
            settings_window.auto_fishing_press_f_on_missing_zone_toggle.isChecked()
        )
        settings_window.close()

        region = SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40)
        rarity_counts = {
            "c": 100,
            "uc": 50,
            "r": 25,
            "vr": 10,
            "er": 5,
            "l": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(
                AppSettings(
                    auto_fishing=True,
                    auto_fishing_reaction_lead_ms=75,
                    auto_fishing_space_hold_ms=15,
                    auto_fishing_press_f_on_missing_zone=True,
                    pokemon_encounter_rarity_counts=rarity_counts,
                    regions=(region,),
                )
            )
            loaded = store.load()

        self.assertTrue(loaded.auto_fishing)
        self.assertEqual(loaded.auto_fishing_reaction_lead_ms, 75)
        self.assertEqual(loaded.auto_fishing_space_hold_ms, 15)
        self.assertTrue(loaded.auto_fishing_press_f_on_missing_zone)
        self.assertEqual(loaded.pokemon_encounter_rarity_counts, rarity_counts)
        self.assertEqual(loaded.regions, (region,))

    def test_daily_rarity_counts_reset_with_the_encounter_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(
                AppSettings(
                    pokemon_encounter_count=4,
                    pokemon_encounter_rarity_counts={"c": 4},
                    pokemon_encounter_date=(
                        date.today() - timedelta(days=1)
                    ).isoformat(),
                )
            )

            loaded = store.load()

        self.assertEqual(loaded.pokemon_encounter_count, 0)
        self.assertEqual(
            loaded.pokemon_encounter_rarity_counts,
            {"c": 0, "uc": 0, "r": 0, "vr": 0, "er": 0, "l": 0},
        )
        self.assertEqual(loaded.pokemon_encounter_date, date.today().isoformat())

    def test_preview_displays_live_capture_and_detection_state(self) -> None:
        preview = FishingPreviewWindow()
        detection = detect_fishing_markers(
            self._bar_with_markers(yellow_left=85, hook_left=85)
        )

        preview.set_frame(
            np.zeros((70, 180, 3), dtype=np.uint8),
            detection,
        )

        self.assertIsNotNone(preview.image_label.pixmap())
        self.assertIn("Green zone", preview.status_label.text())
        self.assertIn("Yellow zone", preview.status_label.text())
        self.assertIn("Moving hook", preview.status_label.text())
        preview.close()

    def test_auto_fishing_taps_once_while_hook_is_in_zone(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input)
        overlapping = detect_fishing_markers(
            self._bar_with_markers(yellow_left=85, hook_left=85)
        )

        monitor._handle_overlap(overlapping)
        monitor._handle_overlap(overlapping)
        game_input.tap_key.assert_called_once_with(
            7,
            0x20,
            duration_seconds=0.01,
            extended=False,
        )

    def test_auto_fishing_can_tap_again_after_hook_leaves_target(self) -> None:
        game_input = Mock()
        monitor = self._monitor(game_input)

        monitor._handle_overlap(
            detect_fishing_markers(self._bar_with_markers(85, 85))
        )
        monitor._handle_overlap(
            detect_fishing_markers(self._bar_with_markers(85, 45))
        )
        monitor._handle_overlap(
            detect_fishing_markers(self._bar_with_markers(85, 85))
        )

        self.assertEqual(game_input.tap_key.call_count, 2)

    @staticmethod
    def _monitor(
        game_input: Mock,
        *,
        auto_fishing: bool = True,
        reaction_lead_ms: int = 50,
        space_hold_ms: int = 10,
        press_f_on_missing_zone: bool = False,
    ) -> FishingMonitor:
        return FishingMonitor(
            SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40),
            7,
            QRect(0, 0, 400, 300),
            auto_fishing=auto_fishing,
            reaction_lead_ms=reaction_lead_ms,
            space_hold_ms=space_hold_ms,
            press_f_on_missing_zone=press_f_on_missing_zone,
            game_input=game_input,
        )

    @staticmethod
    def _bar_with_markers(yellow_left: int, hook_left: int) -> np.ndarray:
        image = np.zeros((70, 180, 3), dtype=np.uint8)
        rail_color = cv2.cvtColor(
            np.uint8([[[100, 220, 220]]]),
            cv2.COLOR_HSV2BGR,
        )[0, 0]
        image[25:45, 10:170] = rail_color
        yellow_color = cv2.cvtColor(
            np.uint8([[[27, 201, 255]]]),
            cv2.COLOR_HSV2BGR,
        )[0, 0]
        image[25:45, yellow_left : yellow_left + 24] = yellow_color
        image[25:45, 80:96] = (0, 255, 0)
        hook_color = cv2.cvtColor(
            np.uint8([[[100, 70, 130]]]),
            cv2.COLOR_HSV2BGR,
        )[0, 0]
        image[30:41, hook_left : hook_left + 10] = hook_color
        return image


class AutoFishingBattleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        start_patch = patch("overlay.overlay_window.FishingMonitor.start")
        start_patch.start()
        self.addCleanup(start_patch.stop)
        with patch("overlay.overlay_window.BattleHistory"):
            self.overlay = OverlayWindow()
        self.addCleanup(self._dispose_overlay)
        self.overlay._target_window_id = 7
        self.overlay._target_geometry = QRect(0, 0, 800, 600)
        self.overlay._realtime_capture_requested = True
        self.overlay.region_window.set_regions((
            SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40),
        ))
        settings = self.overlay.settings_window
        settings.auto_battle_toggle.setChecked(False)
        settings.auto_fishing_press_f_on_missing_zone_toggle.setChecked(True)
        settings.auto_fishing_toggle.setChecked(True)
        self.game_input = Mock()
        self.overlay._auto_fishing_monitor.game_input = self.game_input

    def _dispose_overlay(self) -> None:
        self.overlay.stop_tracking()
        for widget in (
            self.overlay.region_window.hover_label,
            self.overlay.fishing_preview_window,
            self.overlay.region_test_log_window,
            self.overlay.region_selector,
            self.overlay,
        ):
            widget.hide()
            widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def _state(self, battle_mode: object) -> None:
        self.overlay._on_realtime_state_updated({"onBattleMode": battle_mode})

    def _missing(self, observed_at: float) -> None:
        self.overlay._auto_fishing_monitor._handle_overlap(
            FishingDetection(None, None, None), observed_at=observed_at,
        )

    def test_fishing_waits_for_initial_realtime_state_before_counting(self) -> None:
        self._missing(1.0)
        self._missing(100.0)
        self.game_input.tap_key.assert_not_called()
        self._state(False)
        self._missing(101.0)
        self._missing(111.0)
        self.game_input.tap_key.assert_not_called()
        self._missing(111.01)
        self.assertEqual(self.game_input.tap_key.call_args.args[1], 0x46)

    def test_short_ocr_gaps_do_not_resume_fishing_during_battle(self) -> None:
        self._state(False)
        self._missing(1.0)
        self._state(True)
        for state in (False, False, None, False, False, True, False, False):
            self._state(state)
            self._missing(100.0)
        self.game_input.tap_key.assert_not_called()
        self._state(False)
        self._missing(110.0)
        self._state(False)
        self._missing(120.0)
        self.game_input.tap_key.assert_not_called()
        self._state(False)
        self._missing(120.01)
        self.assertEqual(self.game_input.tap_key.call_count, 1)

    def test_repeated_fishing_encounters_each_get_a_fresh_post_battle_timer(self) -> None:
        self._state(False)
        for start in (0.0, 200.0):
            self.overlay._auto_fishing_monitor._handle_overlap(
                FishingDetection((80, 95), None, 85), observed_at=start + 1.0,
            )
            self._missing(start + 2.0)
            self._state(True)
            self._missing(start + 90.0)
            for _ in range(3):
                self._state(False)
            count_after_space = self.game_input.tap_key.call_count
            self._missing(start + 100.0)
            self._missing(start + 110.0)
            self.assertEqual(self.game_input.tap_key.call_count, count_after_space)
            self._missing(start + 110.01)
            self._missing(start + 150.0)
        self.assertEqual(
            [call.args[1] for call in self.game_input.tap_key.call_args_list],
            [0x20, 0x46, 0x20, 0x46],
        )

    def test_fishing_battle_updates_precede_switching_and_catching_early_returns(self) -> None:
        for handler in ("switch", "catch"):
            with self.subTest(handler=handler):
                self.overlay.settings_window.auto_catching_toggle.setChecked(handler == "catch")
                with patch.object(self.overlay, "_handle_auto_switch", return_value=handler == "switch"), patch.object(
                    self.overlay, "_handle_auto_catching", return_value=True,
                ):
                    self._state(True)
                    self._missing(100.0)
                    self._missing(120.0)
                    self.game_input.tap_key.assert_not_called()
                    for _ in range(3):
                        self._state(False)
                    self._missing(130.0)
                    self._missing(140.01)
                    self.assertEqual(self.game_input.tap_key.call_count, 1)
                    self.game_input.reset_mock()

    def test_enabling_or_reconfiguring_fishing_during_battle_keeps_it_paused(self) -> None:
        self._state(True)
        self._state(False)
        settings = self.overlay.settings_window
        original = self.overlay._auto_fishing_monitor
        settings.auto_fishing_toggle.setChecked(False)
        self.assertIsNone(self.overlay._auto_fishing_monitor)
        settings.auto_fishing_toggle.setChecked(True)
        self.assertIsNot(self.overlay._auto_fishing_monitor, original)
        settings.auto_fishing_reaction_lead_spin.setValue(60)
        self.overlay._auto_fishing_monitor.game_input = self.game_input
        self._missing(1.0)
        self._missing(100.0)
        self.game_input.tap_key.assert_not_called()
        self._state(False)
        self._state(False)
        self._missing(101.0)
        self._missing(111.01)
        self.assertEqual(self.game_input.tap_key.call_count, 1)

    def test_turning_fishing_off_and_on_starts_a_new_missing_zone_cycle(self) -> None:
        self._state(False)
        self._missing(1.0)
        self._missing(11.01)
        self.assertEqual(self.game_input.tap_key.call_count, 1)
        settings = self.overlay.settings_window
        settings.auto_fishing_toggle.setChecked(False)
        settings.auto_fishing_toggle.setChecked(True)
        self.overlay._auto_fishing_monitor.game_input = self.game_input
        self._missing(100.0)
        self._missing(110.0)
        self.assertEqual(self.game_input.tap_key.call_count, 1)
        self._missing(110.01)
        self.assertEqual(self.game_input.tap_key.call_count, 2)
