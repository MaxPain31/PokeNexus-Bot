import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QRect
from PySide6.QtWidgets import QApplication

from capture.game_input import GameInput
from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.auto_walk import AutoWalkController
from core.battle_action import PendingBattleAction
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from overlay.overlay_window import OverlayWindow


class AutoWalkControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.game_input = Mock(spec=GameInput)
        self.game_input.is_game_foreground.return_value = True
        self.log_message = Mock()
        self.controller = AutoWalkController(self.game_input, self.log_message)
        self.addCleanup(self.controller.stop)
        self.options = dict(
            enabled=True, on_battle_mode=False, window_handle=42,
            direction="Vertical", duration_mode="Default", default_seconds=0.5,
            minimum_seconds=0.5, maximum_seconds=3.0,
        )

    def test_foreground_return_starts_walking_without_waiting_out_the_retry(self) -> None:
        self.game_input.is_game_foreground.return_value = False
        self.controller.update(**self.options)
        self.controller.update(**self.options)
        self.game_input.press_key.assert_not_called()
        self.assertTrue(self.controller._timer.isActive())
        self.log_message.assert_called_once_with("INFO", "Auto Walk paused: game is not focused.")

        self.game_input.is_game_foreground.return_value = True
        self.controller.update(**self.options)
        self.game_input.press_key.assert_called_once()
        self.assertIn(self.game_input.press_key.call_args.args[1], (0x26, 0x28))
        self.log_message.assert_called_with("INFO", "Auto Walk started.")

    def test_direction_and_duration_changes_replace_the_current_segment_immediately(self) -> None:
        self.controller.update(**{**self.options, "default_seconds": 3600.0})
        held = self.game_input.press_key.call_args.args[1]
        self.controller.update(**{
            **self.options, "direction": "Horizontal", "default_seconds": 0.5,
        })
        self.game_input.release_key.assert_called_once_with(held)
        self.assertEqual(self.game_input.press_key.call_count, 2)
        self.assertIn(self.game_input.press_key.call_args.args[1], (0x25, 0x27))
        self.assertEqual(self.controller._timer.interval(), 500)

    def test_unchanged_state_does_not_restart_a_held_segment(self) -> None:
        for _ in range(4):
            self.controller.update(**self.options)
        self.game_input.press_key.assert_called_once()
        self.game_input.release_key.assert_not_called()

    def test_battle_or_unknown_state_releases_the_key_and_cancels_retries(self) -> None:
        for battle in (True, None):
            with self.subTest(battle=battle):
                self.controller.update(**self.options)
                self.game_input.reset_mock()
                self.controller.update(**{**self.options, "on_battle_mode": battle})
                self.game_input.release_key.assert_called_once()
                self.assertFalse(self.controller._timer.isActive())
                self.controller._finish_segment()
                self.game_input.press_key.assert_not_called()


class AutoWalkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.game_input = Mock(spec=GameInput)
        self.game_input.is_game_foreground.return_value = True
        with patch("overlay.overlay_window.BattleHistory"), patch(
            "overlay.overlay_window.GameInput", return_value=self.game_input,
        ):
            self.overlay = OverlayWindow()
        self.addCleanup(self._dispose)
        self.overlay.region_test_log_window.append_entry = Mock()
        self.overlay._target_window_id = 42
        self.overlay._target_geometry = QRect(0, 0, 800, 600)
        self.overlay._realtime_capture_requested = True
        self.settings = self.overlay.settings_window
        self.settings.auto_battle_toggle.setChecked(False)
        self.settings.auto_walk_direction_selection.setCurrentText("Vertical")
        self._popup_monitor = None

    def _dispose(self) -> None:
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

    def _frame(self, at=100.0, **changes) -> None:
        self.overlay._on_realtime_state_updated({
            "captureStartedAt": at, "onBattleMode": False,
            "showingSpecialPokemonMessage": False, "clickStateReliable": True,
            **changes,
        })

    def _popup_capture(self, text, confidence, at):
        if self._popup_monitor is None:
            self._popup_monitor = RealtimeRegionMonitor(
                (SavedRegion("special-pokemon-message", "Message Box", 220, 160, 300, 120),),
                42, self.overlay._target_geometry, image_capture=Mock(), text_extractor=Mock(),
                parent=self.overlay,
            )
        self._popup_monitor._read_regions = Mock(side_effect=lambda screenshot, regions, timings: [
            (region, OCRResult(text, 300, 120, confidence)) for region in regions
        ])
        with patch("capture.realtime_region_monitor.time.monotonic", return_value=at):
            state = self._popup_monitor._capture_state({
                "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
            })[0]
        self.overlay._on_realtime_state_updated(state)
        return state

    def test_enabling_auto_walk_uses_the_latest_non_battle_state(self) -> None:
        self._frame()
        self.game_input.press_key.assert_not_called()
        self.settings.auto_walk_toggle.setChecked(True)
        self.game_input.press_key.assert_called_once()
        self.assertIn(self.game_input.press_key.call_args.args[1], (0x26, 0x28))
        self.settings.auto_walk_toggle.setChecked(False)
        self.game_input.release_key.assert_called_once()

    def test_saved_auto_walk_starts_when_first_non_battle_capture_arrives(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self.game_input.press_key.assert_not_called()
        self._frame()
        self.game_input.press_key.assert_called_once()

    def test_map_background_ocr_noise_starts_walking_without_a_popup_latch(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        state = self._popup_capture("fh . a sew BH OU furs |__|", 32.41, 100.0)
        self.assertIs(state["onBattleMode"], False)
        self.game_input.press_key.assert_called_once()
        self.assertFalse(self.overlay._special_pokemon_message_controller.blocked)
        self.assertIs(self.overlay._fishing_battle_active, False)
        self.game_input.click_region.assert_not_called()

    def test_walking_resumes_when_a_popup_is_replaced_by_noisy_map_background(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self.settings.auto_close_special_pokemon_message_toggle.setChecked(False)
        self._frame()
        self._popup_capture("Special Encounter!", 95.0, 100.1)
        self.game_input.release_key.assert_called_once()
        self.game_input.press_key.reset_mock()

        # A partly read dialog must keep walking paused and never authorize a click.
        self._popup_capture("SpeciaI Encounler!", 25.0, 100.2)
        self.game_input.press_key.assert_not_called()
        for at in (100.3, 100.4, 100.5, 100.6):
            self._popup_capture("fh . a sew BH OU furs |__|", 32.41, at)
            self.game_input.press_key.assert_not_called()
        self._popup_capture("fh . a sew BH OU furs |__|", 32.41, 100.7)
        self.game_input.press_key.assert_called_once()
        self.game_input.click_region.assert_not_called()

    def test_non_battle_walking_is_not_blocked_by_battle_button_ocr(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self._frame(clickStateReliable=False)
        self.game_input.press_key.assert_called_once()

    def test_walking_stops_before_a_pending_battle_action_or_unreadable_menu_returns(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self._frame()
        held = self.game_input.press_key.call_args.args[1]
        self.settings.auto_battle_toggle.setChecked(True)
        self.overlay._pending_battle_action = PendingBattleAction(
            "fight", 1, "Fight Button", "Auto Battle", 100.1,
            {"onBattleMode": True, "turn": 1},
        )
        self._frame(100.2, onBattleMode=True, turn=1, clickStateReliable=False)
        self.game_input.release_key.assert_called_once_with(held)
        self.assertFalse(self.overlay._auto_walk_controller._timer.isActive())

    def test_walking_resumes_even_if_a_previous_battle_action_is_still_waiting(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self.settings.auto_run_toggle.setChecked(True)
        self.overlay._pending_battle_action = PendingBattleAction(
            "run", 1, "Run Button", "Auto Run", 99.0,
            {"onBattleMode": True, "turn": 1},
        )
        self._frame(clickStateReliable=False)
        self.game_input.press_key.assert_called_once()

    def test_resume_waits_for_a_confirmed_battle_end_even_with_unreadable_buttons(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self._frame(onBattleMode=True)
        for at in (100.1, 100.2):
            self._frame(at, clickStateReliable=False)
            self.game_input.press_key.assert_not_called()
        self._frame(100.3, clickStateReliable=False)
        self.game_input.press_key.assert_called_once()

    def test_pause_logs_explain_a_false_battle_flag_without_repeating_each_poll(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self.overlay.region_test_log_window.append_entry.reset_mock()
        for at in (100.0, 100.1, 100.2):
            self._frame(at, showingSpecialPokemonMessage=None)
        self.game_input.press_key.assert_not_called()
        self.overlay.region_test_log_window.append_entry.assert_called_once_with(
            "INFO", "Auto Walk paused: Special Pokemon Message OCR is uncertain.",
        )
        for at in (100.3, 100.4, 100.5, 100.6, 100.7, 100.8):
            self._frame(at)
        logs = [
            call.args[1]
            for call in self.overlay.region_test_log_window.append_entry.call_args_list
        ]
        self.assertEqual(logs, [
            "Auto Walk paused: Special Pokemon Message OCR is uncertain.",
            "Auto Walk paused: confirming Special Pokemon Message dismissal.",
            "Auto Walk paused: waiting for battle-end confirmation (onBattleMode=false).",
            "Auto Walk started.",
        ])
        self.game_input.press_key.assert_called_once()

    def test_popup_pauses_walking_until_dismissal_and_battle_end_are_confirmed(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self.settings.auto_close_special_pokemon_message_toggle.setChecked(False)
        self._frame()
        self._frame(100.1, showingSpecialPokemonMessage=True)
        self.game_input.release_key.assert_called_once()
        self.game_input.press_key.reset_mock()
        for at in (100.2, 100.3, 100.4, 100.5):
            self._frame(at)
            self.game_input.press_key.assert_not_called()
        self._frame(100.6)
        self.game_input.press_key.assert_called_once()
        self.game_input.click_region.assert_not_called()

    def test_live_movement_options_change_the_running_controller(self) -> None:
        self.settings.auto_walk_toggle.setChecked(True)
        self._frame()
        self.settings.auto_walk_direction_selection.setCurrentText("Horizontal")
        self.assertEqual(self.game_input.press_key.call_count, 2)
        self.assertIn(self.game_input.press_key.call_args.args[1], (0x25, 0x27))
        self.settings.auto_walk_duration_spin.setValue(2.0)
        self.assertEqual(self.overlay._auto_walk_controller._timer.interval(), 2000)

    def test_popup_and_unknown_battle_state_prevent_live_toggle_startup(self) -> None:
        for changes in (
            {"onBattleMode": None},
            {"onBattleMode": True},
            {"showingSpecialPokemonMessage": None},
        ):
            with self.subTest(changes=changes):
                self.settings.auto_walk_toggle.setChecked(False)
                self._frame(**changes)
                self.settings.auto_walk_toggle.setChecked(True)
                self.game_input.press_key.assert_not_called()

    def test_stopped_automation_cannot_be_started_by_toggling_movement(self) -> None:
        self._frame()
        self.overlay.stop_realtime_region_capture()
        self.settings.auto_walk_toggle.setChecked(True)
        self.settings.auto_walk_direction_selection.setCurrentText("Horizontal")
        self.game_input.press_key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
