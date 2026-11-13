import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect

from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.auto_battle import choose_move_slot, has_complete_move_set
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from overlay.overlay_window import OverlayWindow


class RealtimeClickTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actions = {
            feature: SavedRegion(feature, label, 100 + index * 60, 400, 50, 30)
            for index, (feature, label) in enumerate((
                ("fight", "Fight Button"), ("bag", "Bag Button"),
                ("swap", "Pokemon/Swap Button"), ("run", "Run Button"),
            ))
        }
        self.other_regions = (
            SavedRegion("chat-box", "Battle Chat", 100, 200, 300, 100),
            SavedRegion("moves", "Move Slot 1", 100, 300, 100, 30),
            SavedRegion("bag", "Next Button", 300, 300, 50, 30),
            SavedRegion("bag", "Use Item Button", 360, 300, 50, 30),
            SavedRegion("bag", "Item Name", 100, 350, 150, 30),
            SavedRegion("turn", "Turn Number", 100, 100, 60, 20),
        )
        self.monitor = RealtimeRegionMonitor(
            (*self.actions.values(), *self.other_regions), 42, QRect(0, 0, 800, 600),
            image_capture=Mock(), region_cropper=Mock(), image_processor=Mock(),
            text_extractor=Mock(),
        )
        self.in_battle = True
        self.main_menu = True
        self.turn = 1
        self.bag_item = ""
        self.bag_controls = False
        self.now = 100.0
        clock = patch("capture.realtime_region_monitor.time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.monitor._read_regions = Mock(side_effect=self._read_regions)

    def _read_regions(self, screenshot, regions, timings):
        result = []
        for region in regions:
            if region.feature == "chat-box":
                text = "A wild Eevee appeared!" if self.in_battle else ""
            elif region in self.actions.values():
                text = region.label if self.main_menu else ""
            elif region.feature == "moves":
                text = "Tackle 1.0x 20/35" if not self.main_menu else "Fight"
            elif region.feature == "turn":
                text = f"Turn {self.turn}"
            elif region.label == "Item Name":
                text = self.bag_item
            elif region.label == "Use Item Button":
                text = "Use Item" if self.bag_controls and not self.main_menu else ""
            else:
                text = ""
            result.append((region, OCRResult(text, 100, 30, 95.0)))
        return result

    def _capture(self):
        self.now += 0.1
        return self.monitor._capture_state({
            "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
        })[0]

    def _requested_regions(self):
        return [
            region
            for call in self.monitor._read_regions.call_args_list
            for region in call.args[1]
        ]

    def test_main_menu_skips_item_navigation_item_name_and_move_ocr(self) -> None:
        self.bag_item = "Ultra Ball"
        state = self._capture()

        requested = self._requested_regions()
        self.assertTrue(all(state[name] for name in self.monitor._BUTTON_FEATURES))
        self.assertTrue(all(region in requested for region in self.actions.values()))
        self.assertFalse(any(
            region.feature == "moves"
            or region.label in ("Previous Button", "Next Button", "Item Name", "Use Item Button")
            for region in requested
        ))
        self.assertIsNone(state["bagItemName"])

    def test_first_fresh_fight_response_reads_all_four_moves_together_without_item_name(self) -> None:
        self.monitor._regions_by_feature["moves"].extend(
            SavedRegion("moves", f"Move Slot {slot}", 100 + slot * 60, 300, 100, 30)
            for slot in range(2, 5)
        )

        def read_moves(screenshot, regions, timings):
            return [
                (region, OCRResult(
                    "Discharge 4x 24/24" if region.label == "Move Slot 2" else result.text,
                    100, 30, 95.0,
                ))
                for region, result in self._read_regions(screenshot, regions, timings)
            ]

        self.monitor._read_regions.side_effect = read_moves
        self.monitor.record_automated_action("fight", "Fight Button")
        self._capture()
        self.monitor._read_regions.reset_mock()
        self.main_menu = False
        state = self._capture()

        self.assertTrue(state["clickedFightButton"])
        self.assertTrue(has_complete_move_set(state["pokemonMoves"]))
        self.assertEqual(choose_move_slot(state["pokemonMoves"]), 2)
        move_batches = [
            [region.label for region in call.args[1] if region.feature == "moves"]
            for call in self.monitor._read_regions.call_args_list
            if any(region.feature == "moves" for region in call.args[1])
        ]
        self.assertEqual(move_batches, [[f"Move Slot {slot}" for slot in range(1, 5)]])
        self.assertFalse(any(
            region.label in ("Previous Button", "Next Button", "Item Name")
            for region in self._requested_regions()
        ))

    def test_bag_navigation_targets_are_never_recognized_to_detect_the_item_menu(self) -> None:
        self.monitor._regions_by_feature["bag"].append(
            SavedRegion("bag", "Previous Button", 240, 300, 50, 30)
        )
        self.main_menu = False
        self.bag_item = "Ultra Ball"
        self.bag_controls = True
        state = self._capture()

        self.assertTrue(state["showingBagItemMenu"])
        self.assertEqual(state["bagItemName"], "Ultra Ball")
        self.assertFalse(any(
            region.label in ("Previous Button", "Next Button")
            for region in self._requested_regions()
        ))

    def test_fresh_use_item_controls_override_old_fight_tracking(self) -> None:
        self.main_menu = False
        self.monitor.record_automated_action("fight", "Fight Button")
        self.assertTrue(self._capture()["clickedFightButton"])
        self.bag_item = "Ultra Ball"
        self.bag_controls = True
        state = self._capture()

        self.assertTrue(state["showingBagItemMenu"])
        self.assertEqual(state["bagItemName"], "Ultra Ball")
        self.assertIsNone(state["pokemonMoves"])

    def test_confirmed_bag_does_not_depend_on_move_ocr_during_menu_recovery(self) -> None:
        self.monitor._recovering_battle_menu = True
        self.main_menu = False
        self.bag_item = "Ultra Ball"
        self.bag_controls = True
        state = self._capture()

        self.assertTrue(state["showingBagItemMenu"])
        self.assertTrue(state["clickStateReliable"])
        self.assertFalse(any(region.feature == "moves" for region in self._requested_regions()))

    def test_submitted_fight_click_enables_move_ocr_without_a_mouse_hook(self) -> None:
        self.main_menu = False
        self.assertIsNone(self._capture()["pokemonMoves"])
        self.monitor.record_automated_click(self.actions["fight"])

        state = self._capture()

        self.assertTrue(state["clickedFightButton"])
        self.assertEqual(state["pokemonMoves"], "Move Slot 1: Tackle (1.0x | 20/35)")

    def test_click_survives_delayed_menu_transition_and_resets_when_menu_returns(self) -> None:
        self.monitor.record_automated_click(self.actions["fight"])
        for _ in range(3):
            state = self._capture()
            self.assertFalse(state["clickedFightButton"])
            self.assertIsNone(state["pokemonMoves"])
        self.main_menu = False
        self.assertIn("Tackle", self._capture()["pokemonMoves"])
        self.assertTrue(self._capture()["clickedFightButton"])
        self.main_menu = True

        state = self._capture()
        self.assertFalse(state["clickedFightButton"])
        self.assertIsNone(state["pokemonMoves"])

    def test_ignored_click_never_reads_the_action_menu_as_move_slots(self) -> None:
        self.monitor.record_automated_click(self.actions["fight"])
        for _ in range(25):
            state = self._capture()
            self.assertFalse(state["clickedFightButton"])
            self.assertIsNone(state["pokemonMoves"])
        self.assertIsNone(self.monitor._pending_action_feature)
        self.assertFalse(any(
            region.feature == "moves"
            for call in self.monitor._read_regions.call_args_list
            for region in call.args[1]
        ))

    def test_bag_click_requires_the_item_menu_and_hides_stale_item_ocr(self) -> None:
        self.bag_item = "Ultra Ball"
        self.monitor.record_automated_click(self.actions["bag"])
        state = self._capture()
        self.assertFalse(state["clickedBagButton"])
        self.assertIsNone(state["bagItemName"])
        self.main_menu = False
        state = self._capture()
        self.assertTrue(state["clickedBagButton"])
        self.assertEqual(state["bagItemName"], "Ultra Ball")

    def test_open_bag_is_detected_without_a_recorded_bag_click(self) -> None:
        self.main_menu = False
        self.bag_item = "Ultra Ball"
        self.bag_controls = True
        state = self._capture()

        self.assertTrue(state["showingBagItemMenu"])
        self.assertEqual(state["bagItemName"], "Ultra Ball")
        self.assertFalse(state["clickedBagButton"])

    def test_readable_use_item_and_name_confirm_bag_despite_unknown_main_button_ocr(self) -> None:
        self.main_menu = False
        self.bag_item = "Ultra Ball"
        self.bag_controls = True
        self.monitor.record_automated_action("bag", "Bag Button")

        def unreadable_run(screenshot, regions, timings):
            return [
                (region, OCRResult("?", 100, 30, 10.0))
                if region == self.actions["run"] else (region, result)
                for region, result in self._read_regions(screenshot, regions, timings)
            ]

        self.monitor._read_regions.side_effect = unreadable_run
        state = self._capture()

        self.assertIsNone(state["showingRunButton"])
        self.assertEqual(state["bagItemName"], "Ultra Ball")
        self.assertTrue(state["showingBagItemMenu"])
        self.assertTrue(state["clickedBagButton"])

    def test_move_pp_at_the_item_name_coordinates_is_not_a_bag_item(self) -> None:
        self.main_menu = False
        self.bag_item = "wet 24/24"
        self.monitor.record_automated_action("fight", "Fight Button")
        state = self._capture()

        self.assertTrue(state["clickedFightButton"])
        self.assertIsNone(state["bagItemName"])
        self.assertFalse(state["showingBagItemMenu"])

    def test_unknown_submenu_text_is_not_a_bag_when_use_item_region_is_configured(self) -> None:
        self.main_menu = False
        self.bag_item = "Waiting for opponent"
        state = self._capture()

        self.assertIsNone(state["bagItemName"])
        self.assertFalse(state["showingBagItemMenu"])

    def test_open_bag_detection_does_not_require_saved_mouse_targets(self) -> None:
        self.main_menu = False
        self.bag_item = "Ultra Ball"
        self.monitor._regions_by_feature["bag"] = [
            region for region in self.monitor._regions_by_feature["bag"]
            if region.label not in ("Use Item Button", "Next Button")
        ]
        state = self._capture()

        self.assertEqual(state["bagItemName"], "Ultra Ball")
        self.assertTrue(state["showingBagItemMenu"])

    def test_turn_reset_does_not_hide_an_open_bag_with_readable_controls(self) -> None:
        self.monitor.record_automated_action("bag", "Bag Button")
        self._capture()
        self.turn = 2
        self.main_menu = False
        self.bag_item = "Ultra Ball"
        self.bag_controls = True
        state = self._capture()

        self.assertFalse(state["clickedBagButton"])
        self.assertEqual(state["bagItemName"], "Ultra Ball")
        self.assertTrue(state["showingBagItemMenu"])

    def test_run_requires_consecutive_menu_transition_frames(self) -> None:
        self.monitor.record_automated_click(self.actions["run"])
        self.assertFalse(self._capture()["clickedRunButton"])
        self.main_menu = False
        self.assertFalse(self._capture()["clickedRunButton"])
        self.main_menu = True
        self.assertFalse(self._capture()["clickedRunButton"])
        self.main_menu = False
        self.assertFalse(self._capture()["clickedRunButton"])
        self.assertTrue(self._capture()["clickedRunButton"])

    def test_missing_button_ocr_is_unknown_instead_of_a_confirmed_hidden_button(self) -> None:
        self.monitor.record_automated_click(self.actions["run"])
        self._capture()
        self.main_menu = False

        def missing_bag(screenshot, regions, timings):
            return [
                (region, result) for region, result in self._read_regions(screenshot, regions, timings)
                if region != self.actions["bag"]
            ]

        self.monitor._read_regions.side_effect = missing_bag
        for _ in range(3):
            state = self._capture()
            self.assertIsNone(state["showingBagButton"])
            self.assertFalse(state["clickedRunButton"])

    def test_only_configured_action_button_roles_are_recorded(self) -> None:
        listener = self.monitor._click_listener
        for region in (
            *self.other_regions,
            SavedRegion("fight", "Fight Button", 999, 999, 20, 20),
        ):
            self.monitor.record_automated_click(region)
        self.assertEqual(listener.consume_clicks(), set())

        for feature, region in self.actions.items():
            with self.subTest(feature=feature):
                self.monitor.record_automated_click(region)
                self.assertEqual(listener.consume_clicks(), {feature})
                self.assertEqual(listener.consume_clicks(), set())

    def test_keyboard_notifications_use_action_roles_instead_of_shared_numbers(self) -> None:
        listener = self.monitor._click_listener
        listener._action_regions = frozenset()
        self.monitor.record_automated_action("fight", "Fight Button")
        self.assertEqual(listener.consume_clicks(), {"fight"})

        for feature, role in (
            ("moves", "Move Slot 1"), ("bag", "Previous Button"),
            ("bag", "Next Button"), ("bag", "Use Item Button"),
            ("pokemon-team", "Pokemon Team Slot 3"),
        ):
            with self.subTest(role=role):
                self.monitor.record_automated_action(feature, role)
                self.assertEqual(listener.consume_clicks(), set())

        self.monitor.record_automated_action("bag", "Bag Button")
        self.assertEqual(listener.consume_clicks(), {"bag"})
        self.monitor.record_automated_action("swap", "Pokemon/Swap Button")
        self.assertEqual(listener.consume_clicks(), {"swap"})
        self.monitor.record_automated_action("run", "Run Button")
        self.assertEqual(listener.consume_clicks(), {"run"})

    def test_manual_and_automated_clicks_share_latest_click_ordering(self) -> None:
        listener = self.monitor._click_listener
        listener._target_process_id = 100
        self.monitor.record_automated_click(self.actions["fight"])
        bag = self.actions["bag"]
        listener._record_click(bag.x + 5, bag.y + 5, 100, 100)
        self.assertEqual(listener.consume_clicks(), {"bag"})

        listener._record_click(bag.x + 5, bag.y + 5, 100, 100)
        self.monitor.record_automated_click(self.actions["fight"])
        self.assertEqual(listener.consume_clicks(), {"fight"})
        listener._record_click(bag.x + 5, bag.y + 5, 200, 100)
        self.assertEqual(listener.consume_clicks(), set())

    def test_click_submitted_from_another_thread_during_ocr_waits_for_next_frame(self) -> None:
        original_read = self._read_regions

        def read_and_post(screenshot, regions, timings):
            self.monitor._read_regions.side_effect = original_read
            producer = threading.Thread(
                target=self.monitor.record_automated_click, args=(self.actions["fight"],)
            )
            producer.start()
            producer.join(timeout=2)
            self.assertFalse(producer.is_alive())
            return original_read(screenshot, regions, timings)

        self.monitor._read_regions.side_effect = read_and_post
        self.assertFalse(self._capture()["clickedFightButton"])
        self.main_menu = False
        self.assertIn("Tackle", self._capture()["pokemonMoves"])

    def test_click_arriving_during_capture_cannot_confirm_using_that_image(self) -> None:
        self.main_menu = False

        def capture_and_click(_handle):
            self.monitor.image_capture.capture_window.side_effect = None
            self.now += 0.01
            self.monitor.record_automated_click(self.actions["fight"])
            return object()

        self.monitor.image_capture.capture_window.side_effect = capture_and_click
        self.assertFalse(self._capture()["clickedFightButton"])
        self.assertTrue(self._capture()["clickedFightButton"])

    def test_pending_click_is_not_lost_when_capture_fails(self) -> None:
        self.monitor.record_automated_click(self.actions["fight"])
        self.monitor.image_capture.capture_window.side_effect = [OSError("Capture failed"), object()]
        with self.assertRaises(OSError):
            self._capture()
        self.main_menu = False
        self.assertIn("Tackle", self._capture()["pokemonMoves"])

    def test_battle_end_clears_clicked_state(self) -> None:
        self.main_menu = False
        self.monitor.record_automated_click(self.actions["fight"])
        self.assertTrue(self._capture()["clickedFightButton"])
        self.in_battle = False
        state = self._capture()
        self.assertFalse(state["clickedFightButton"])
        self.assertIsNone(state["pokemonMoves"])
        self._capture()
        self._capture()
        self.assertFalse(any(self.monitor._clicked_button_states.values()))

    def test_short_battle_ocr_gap_preserves_the_open_move_menu(self) -> None:
        self.main_menu = False
        self.monitor.record_automated_click(self.actions["fight"])
        self.assertTrue(self._capture()["clickedFightButton"])
        self.in_battle = False
        self._capture()
        self._capture()
        self.in_battle = True
        self.assertIn("Tackle", self._capture()["pokemonMoves"])

    def test_new_turn_clears_an_unconfirmed_old_action(self) -> None:
        self.monitor.record_automated_click(self.actions["fight"])
        self._capture()
        self.turn = 2
        state = self._capture()
        self.assertFalse(state["clickedFightButton"])
        self.assertIsNone(self.monitor._pending_action_feature)

    def test_overlay_notifies_monitor_only_after_successful_click_to_same_game(self) -> None:
        overlay = SimpleNamespace(
            _target_window_id=42, _target_geometry=QRect(0, 0, 800, 600),
            _auto_battle_input=Mock(), _auto_battle_log_once=Mock(),
            _ev_training_enabled=Mock(return_value=False),
            _realtime_region_monitor=self.monitor, region_test_log_window=Mock(),
            settings_window=SimpleNamespace(
                use_mouse_clicking_toggle=Mock(isChecked=Mock(return_value=True)),
            ),
            region_window=SimpleNamespace(regions=tuple(self.actions.values())),
        )
        def submit():
            return OverlayWindow._send_battle_action(
                overlay, "fight", 1, "Fight Button", automation_name="Auto Battle",
                settle_delay=None,
            )

        overlay._auto_battle_input.click_region.side_effect = OSError("Input rejected")
        self.assertFalse(submit())
        self.assertEqual(self.monitor._click_listener.consume_clicks(), set())

        overlay._auto_battle_input.click_region.side_effect = None
        self.assertTrue(submit())
        self.main_menu = False
        self.assertIn("Tackle", self._capture()["pokemonMoves"])

        overlay._target_window_id = 43
        self.assertTrue(submit())
        self.assertEqual(self.monitor._click_listener.consume_clicks(), set())


    def test_successful_key_submission_enables_ocr_then_move_one_keeps_its_context(self) -> None:
        overlay = SimpleNamespace(
            _target_window_id=42,
            _auto_battle_input=Mock(), _auto_battle_log_once=Mock(),
            _ev_training_enabled=Mock(return_value=False),
            _realtime_region_monitor=self.monitor, region_test_log_window=Mock(),
            settings_window=SimpleNamespace(
                use_mouse_clicking_toggle=Mock(isChecked=Mock(return_value=False)),
            ),
        )

        def submit(feature="fight", role="Fight Button"):
            return OverlayWindow._send_battle_action(
                overlay, feature, 1, role, automation_name="Auto Battle", settle_delay=None,
            )

        overlay._auto_battle_input.tap_key.side_effect = OSError("Input rejected")
        self.assertFalse(submit())
        self.assertEqual(self.monitor._click_listener.consume_clicks(), set())

        overlay._auto_battle_input.tap_key.side_effect = None
        self.assertTrue(submit())
        self.assertFalse(self._capture()["clickedFightButton"])
        self.main_menu = False
        state = self._capture()
        self.assertTrue(state["clickedFightButton"])
        self.assertIn("Tackle", state["pokemonMoves"])

        self.assertTrue(submit("moves", "Move Slot 1"))
        self.assertEqual(self.monitor._click_listener.consume_clicks(), set())
        self.assertTrue(self._capture()["clickedFightButton"])
        self.assertEqual(
            [call.args[1] for call in overlay._auto_battle_input.tap_key.call_args_list],
            [0x31, 0x31, 0x31],
        )
        overlay._auto_battle_input.click_region.assert_not_called()
        overlay._target_window_id = 43
        self.assertTrue(submit())
        self.assertEqual(self.monitor._click_listener.consume_clicks(), set())


if __name__ == "__main__":
    unittest.main()
