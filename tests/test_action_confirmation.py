import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QRect
from PySide6.QtWidgets import QApplication

from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.battle_action import ACTION_BUTTON_STATES
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from overlay.overlay_window import OverlayWindow


class _BattleActionTestCase(unittest.TestCase):
    use_mouse_clicking = False

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        with patch("overlay.overlay_window.BattleHistory"):
            self.overlay = OverlayWindow()
        self.addCleanup(self._dispose_overlay)
        self.overlay._battle_history = None
        self.overlay._auto_battle_input = Mock()
        self.overlay.region_test_log_window.append_entry = Mock()
        self.overlay._target_window_id = 42
        self.overlay._target_geometry = QRect(0, 0, 800, 600)
        self.overlay._realtime_capture_requested = True
        self.now = 100.0
        clock = patch("overlay.overlay_window.time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.regions = tuple(
            SavedRegion(feature, role, 100 + index * 50, 400, 40, 30)
            for index, (feature, role) in enumerate((
                ("fight", "Fight Button"), ("bag", "Bag Button"),
                ("run", "Run Button"), ("swap", "Pokemon/Swap Button"),
                ("moves", "Move Slot 1"), ("moves", "Move Slot 2"),
                ("moves", "Move Slot 3"), ("moves", "Move Slot 4"),
                ("bag", "Next Button"), ("bag", "Use Item Button"),
                ("pokemon-team", "Pokemon Team Slot 2"),
            ))
        )
        self.overlay.region_window.set_regions(self.regions)
        settings = self.overlay.settings_window
        settings.use_mouse_clicking_toggle.setChecked(self.use_mouse_clicking)
        settings.auto_battle_toggle.setChecked(True)
        settings.auto_switch_fainted_pokemon_toggle.setChecked(False)
        settings.auto_walk_toggle.setChecked(False)
        settings.auto_catching_toggle.setChecked(False)
        settings.stop_for_desired_pokemon_toggle.setChecked(False)
        settings.stop_for_shiny_toggle.setChecked(False)
        settings.stop_for_elite_toggle.setChecked(False)
        self.menu = {
            "onBattleMode": True, "turn": 1, "yourPokemonHealth": 100,
            "enemyPokemonHealth": 100, "yourPokemonName": "Pikachu",
            "wildPokemonName": "Pidgey", "pokemonMoves": None,
            "bagItemName": None, "chatBoxMessage": "A wild Pidgey appeared!",
            **{visible: True for _, visible in ACTION_BUTTON_STATES.values()},
            **{clicked: False for clicked, _ in ACTION_BUTTON_STATES.values()},
        }
        self.submenu = {
            **self.menu,
            **{visible: False for _, visible in ACTION_BUTTON_STATES.values()},
        }
        self.moves = {
            **self.submenu, "clickedFightButton": True,
            "pokemonMoves": (
                "Move Slot 1: Tackle (1.0x | 20/35) | "
                "Move Slot 2: Growl (- | 20/40) | "
                "Move Slot 3: Tail Whip (- | 20/30) | "
                "Move Slot 4: Thunder Wave (- | 20/20)"
            ),
        }

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

    def _state(self, state, at, *, captured_at=None):
        self.now = at
        self.overlay._on_realtime_state_updated({
            **state, "captureStartedAt": at if captured_at is None else captured_at,
        })

    def _clicks(self):
        return [
            call.args[1].label
            for call in self.overlay._auto_battle_input.click_region.call_args_list
        ]

    def _logs(self):
        return "\n".join(
            call.args[1]
            for call in self.overlay.region_test_log_window.append_entry.call_args_list
        )


class _CatchingRecoveryTests:
    def _enable_catching(self) -> None:
        settings = self.overlay.settings_window
        settings.auto_battle_toggle.setChecked(False)
        settings.auto_catching_toggle.setChecked(True)
        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.desired_pokemon_input.setText("Pidgey")
        settings.auto_catching_item_selection.setCurrentText("Ultra Ball")

    def _assert_catching_inputs(self, *roles: str) -> None:
        if self.use_mouse_clicking:
            self.assertEqual(self._clicks(), list(roles))
            self.overlay._auto_battle_input.tap_key.assert_not_called()
        else:
            keys = {
                "Bag Button": "2", "Next Button": "2", "Use Item Button": "3",
                "Pokemon Team Slot 2": "2",
            }
            self.assertEqual(self._keys(), [keys[role] for role in roles])
            self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_matching_item_is_used_when_catching_starts_with_bag_already_open(self) -> None:
        self._enable_catching()
        self._state({**self.submenu, "bagItemName": " Ultra\n Ball "}, 100.0)

        self._assert_catching_inputs("Use Item Button")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")
        self.assertEqual(self.overlay._pending_battle_action.action_name, "Use Item Button")

    def test_matching_item_is_used_without_a_latched_bag_clicked_flag(self) -> None:
        self._enable_catching()
        self._state(self.menu, 100.0)
        self._state({**self.submenu, "turn": None, "bagItemName": "Ultra Ball"}, 100.2)

        self._assert_catching_inputs("Bag Button", "Use Item Button")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")

    def test_turn_change_while_opening_bag_does_not_strand_the_matching_item(self) -> None:
        self._enable_catching()
        self._state(self.menu, 100.0)
        self._state({**self.submenu, "turn": 2, "bagItemName": "Ultra Ball"}, 100.2)

        self._assert_catching_inputs("Bag Button", "Use Item Button")
        self.assertEqual(self.overlay._auto_catch_attempt_turn, 2)
        self.assertEqual(self.overlay._pending_battle_action.turn, 2)

    def test_turn_change_while_browsing_uses_the_matching_item_instead_of_waiting(self) -> None:
        self._enable_catching()
        self._state(self.menu, 100.0)
        bag = {**self.submenu, "clickedBagButton": True, "bagItemName": "Poke Ball"}
        self._state(bag, 100.2)
        self._assert_catching_inputs("Bag Button", "Next Button")
        self._state({
            **bag, "turn": 2, "clickedBagButton": False, "bagItemName": "Ultra Ball",
        }, 100.4)

        self._assert_catching_inputs("Bag Button", "Next Button", "Use Item Button")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")
        self.assertEqual(self.overlay._pending_battle_action.turn, 2)

    def test_monitor_turn_reset_and_partial_main_button_ocr_still_use_the_matching_item(self) -> None:
        self._enable_catching()
        regions = (
            *self.regions,
            SavedRegion("bag", "Item Name", 100, 350, 150, 30),
            SavedRegion("turn", "Turn Number", 100, 100, 60, 20),
            SavedRegion("chat-box", "Battle Chat", 100, 200, 300, 100),
            SavedRegion("health", "Enemy Health", 600, 150, 60, 20),
            SavedRegion("health", "Ally Health", 600, 300, 60, 20),
        )
        monitor = RealtimeRegionMonitor(
            regions, 42, self.overlay._target_geometry,
            image_capture=Mock(), text_extractor=Mock(), parent=self.overlay,
        )
        self.overlay._realtime_region_monitor = monitor
        display = {"main_menu": True, "turn": 1}

        def read_regions(screenshot, requested, timings):
            results = []
            for region in requested:
                confidence = 95.0
                if region.feature == "chat-box":
                    text = "A wild Pidgey appeared!"
                elif region.feature == "health":
                    text = "100%"
                elif region.feature == "turn":
                    text = f"Turn {display['turn']}"
                elif region.label == "Item Name":
                    text = "" if display["main_menu"] else "Ultra Ball"
                elif region.label == "Use Item Button":
                    text = "" if display["main_menu"] else "Use Item"
                elif region.feature in ("fight", "swap", "run", "bag"):
                    text = region.label if display["main_menu"] else ""
                    if region.feature == "run" and not display["main_menu"]:
                        text, confidence = "?", 10.0
                else:
                    text = ""
                results.append((region, OCRResult(text, 100, 30, confidence)))
            return results

        monitor._read_regions = Mock(side_effect=read_regions)

        def capture(at):
            self.now = at
            return monitor._capture_state({
                "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
            })[0]

        self.overlay._on_realtime_state_updated(capture(100.0))
        self._assert_catching_inputs("Bag Button")
        # The capture thread consumes the notification before a subsequent
        # turn change clears the monitor's pending main-menu action.
        capture(100.1)
        display.update(main_menu=False, turn=2)
        state = capture(100.2)
        self.assertFalse(state["clickedBagButton"])
        self.overlay._on_realtime_state_updated(state)

        self._assert_catching_inputs("Bag Button", "Use Item Button")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")
        self.assertEqual(self.overlay._pending_battle_action.turn, 2)

    def test_catching_resumes_in_the_open_bag_after_a_faint_and_replacement(self) -> None:
        self._enable_catching()
        self.overlay.settings_window.auto_switch_fainted_pokemon_toggle.setChecked(True)
        self._state(self.menu, 100.0)
        fainted = {**self.submenu, "yourPokemonHealth": 0}
        self._state(fainted, 100.2)
        self._state(fainted, 102.2)
        self._assert_catching_inputs("Bag Button", "Pokemon Team Slot 2")
        self._state({
            **self.submenu, "turn": 2, "yourPokemonHealth": 80,
            "bagItemName": "Ultra Ball",
        }, 102.4)

        self._assert_catching_inputs("Bag Button", "Pokemon Team Slot 2", "Use Item Button")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")

    def test_a_stale_matching_item_in_the_main_menu_never_sends_use_item(self) -> None:
        self._enable_catching()
        self.overlay._auto_catch_identity = "pidgey"
        self.overlay._auto_catch_attempt_turn = 1
        self.overlay._auto_catch_phase = "selecting_item"
        self._state({**self.menu, "bagItemName": "Ultra Ball"}, 100.0)

        self._assert_catching_inputs()
        self.assertIsNone(self.overlay._pending_battle_action)

    def test_unknown_or_move_menus_never_use_stale_matching_item_text(self) -> None:
        self._enable_catching()
        self.overlay._auto_catch_identity = "pidgey"
        self.overlay._auto_catch_attempt_turn = 1
        self.overlay._auto_catch_phase = "selecting_item"
        unknown = {
            **self.submenu, "bagItemName": "Ultra Ball",
            **{visible: None for _, visible in ACTION_BUTTON_STATES.values()},
        }
        self._state(unknown, 100.0)
        self._state({**self.moves, "bagItemName": "Ultra Ball"}, 100.2)

        self._assert_catching_inputs()
        self.assertIsNone(self.overlay._pending_battle_action)

    def test_item_use_never_retries_three_after_the_visible_item_changes(self) -> None:
        self._enable_catching()
        self._state(self.menu, 100.0)
        bag = {**self.submenu, "clickedBagButton": True, "bagItemName": "Ultra Ball"}
        self._state(bag, 100.2)
        self._assert_catching_inputs("Bag Button", "Use Item Button")
        self._state({**bag, "bagItemName": "Great Ball"}, 102.3)

        self._assert_catching_inputs("Bag Button", "Use Item Button", "Next Button")
        self.assertEqual(self.overlay._auto_catch_phase, "selecting_item")
        self.assertEqual(self.overlay._pending_battle_action.action_name, "Next Button")


class _SpecialPokemonMessageTests:
    _popup_message = (
        "Special Encounter!\n"
        "You have encountered a special pokemon!\n"
        "Click the Close button to continue\n"
        "Window will Auto-Close in: 02:54"
    )

    def _configure_special_message(self) -> None:
        self.message_box = SavedRegion(
            "special-pokemon-message", "Message Box", 220, 160, 300, 120,
        )
        self.close_button = SavedRegion(
            "special-pokemon-message", "Close Button", 460, 250, 50, 25,
        )
        self.overlay.region_window.set_regions((*self.regions, self.message_box, self.close_button))

    def _popup(self, kind=None, **changes):
        return {
            **self.menu, "showingSpecialPokemonMessage": True,
            "specialPokemonMessage": (
                f"You encountered a {kind} Pokemon!" if kind else self._popup_message
            ),
            **changes,
        }

    def _assert_special_inputs(self, *roles):
        keys = {
            "Fight Button": "1", "Move Slot 1": "1", "Bag Button": "2",
            "Use Item Button": "3", "Pokemon Team Slot 2": "2", "Run Button": "4",
        }
        expected = [
            ("click_region", role) if role == "Close Button" or self.use_mouse_clicking
            else ("tap_key", keys[role])
            for role in roles
        ]
        actual = [
            (call[0], call.args[1].label if call[0] == "click_region" else chr(call.args[1]))
            for call in self.overlay._auto_battle_input.method_calls
        ]
        self.assertEqual(actual, expected)

    def test_all_special_popup_types_close_before_monitor_driven_fight_and_move(self) -> None:
        self._configure_special_message()
        regions = (
            *self.overlay.region_window.regions,
            SavedRegion("chat-box", "Battle Chat", 50, 200, 300, 100),
            SavedRegion("turn", "Turn Button", 100, 100, 50, 20),
        )
        messages = (
            ("Shiny", "You encountered a Shiny Pokemon!"),
            ("Extremely Rare", "You encountered an Extremely Rare Pokemon!"),
            ("Legendary", "You encountered a Legendary Pokemon!"),
            ("Special Encounter", self._popup_message),
        )
        for index, (kind, message) in enumerate(messages):
            with self.subTest(kind=kind):
                self.overlay._reset_battle_actions()
                self.overlay._auto_battle_input.reset_mock()
                self.overlay.region_test_log_window.append_entry.reset_mock()
                monitor = RealtimeRegionMonitor(
                    regions, 42, self.overlay._target_geometry,
                    image_capture=Mock(), text_extractor=Mock(), parent=self.overlay,
                )
                self.overlay._realtime_region_monitor = monitor
                display = {"popup": kind, "main_menu": True}

                def read_regions(screenshot, requested, timings):
                    results = []
                    for region in requested:
                        if region == self.message_box:
                            text = message if display["popup"] else ""
                        elif display["popup"] and kind != "Special Encounter":
                            text = ""
                        elif region.feature == "chat-box":
                            text = "A wild Pidgey appeared!"
                        elif region.feature == "turn":
                            text = "Turn 1"
                        elif region.feature == "moves":
                            text = "Tackle 1.0x 20/35" if not display["main_menu"] else "Fight"
                        else:
                            text = region.label if display["main_menu"] else ""
                        results.append((region, OCRResult(text, 100, 30, 95.0)))
                    return results

                monitor._read_regions = Mock(side_effect=read_regions)

                def capture(at):
                    self.now = at
                    state = monitor._capture_state({
                        "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
                    })[0]
                    self.overlay._on_realtime_state_updated(state)
                    return state

                start = 100.0 + index * 10.0
                blocked = capture(start)
                self.assertTrue(blocked["onBattleMode"])
                self.assertIsNone(blocked["turn"])
                self._assert_special_inputs("Close Button")
                capture(start + 0.1)
                self._assert_special_inputs("Close Button")
                display["popup"] = None
                capture(start + 0.2)
                self._assert_special_inputs("Close Button")
                capture(start + 0.3)
                self._assert_special_inputs("Close Button", "Fight Button")
                display["main_menu"] = False
                capture(start + 0.4)
                self._assert_special_inputs("Close Button", "Fight Button", "Move Slot 1")
                self.assertIn(f"detected ({kind})", self._logs())
                self.assertEqual(self._logs().count("dismissal confirmed"), 1)
                self.assertEqual(
                    self.overlay._auto_battle_input.click_region.call_args_list[0].args,
                    (42, self.close_button, self.overlay._target_geometry),
                )

    def test_auto_close_off_interrupts_battle_and_waits_for_manual_or_timed_dismissal(self) -> None:
        self._configure_special_message()
        self.overlay.region_window.set_regions((*self.regions, self.message_box))
        self.overlay.settings_window.auto_close_special_pokemon_message_toggle.setChecked(False)
        self._state(self.menu, 100.0)
        self._state(self._popup(), 100.1)
        self.assertIsNone(self.overlay._pending_battle_action)
        for at in (101.0, 105.0, 270.0):
            self._state(self._popup(), at)
        self._assert_special_inputs("Fight Button")
        self.assertTrue(self.overlay._special_pokemon_message_controller.blocked)
        self.assertIn("auto-close is off", self._logs())
        self.assertNotIn("requires its Close Button region", self._logs())

        self._state(self.menu, 280.0)
        self._state(self.menu, 280.1, captured_at=280.0)
        self._assert_special_inputs("Fight Button")
        self._state(self.menu, 280.2)
        self._assert_special_inputs("Fight Button", "Fight Button")
        self.assertFalse(self.overlay._special_pokemon_message_controller.blocked)

    def test_auto_close_takes_effect_live_without_reusing_stale_frames_or_clearing_the_pause(self) -> None:
        self._configure_special_message()
        toggle = self.overlay.settings_window.auto_close_special_pokemon_message_toggle
        toggle.setChecked(False)
        self._state(self._popup(), 100.0)
        self._state(self._popup(), 101.0)
        self._assert_special_inputs()
        toggle.setChecked(True)
        self._state(self._popup(), 101.1, captured_at=101.0)
        self._assert_special_inputs()
        self._state(self._popup(), 101.2)
        self._assert_special_inputs("Close Button")

        toggle.setChecked(False)
        self._state(self._popup(), 103.3)
        self._assert_special_inputs("Close Button")
        self.assertTrue(self.overlay._special_pokemon_message_controller.blocked)
        toggle.setChecked(True)
        self._state(self._popup(), 103.4, captured_at=103.3)
        self._assert_special_inputs("Close Button")
        self._state(self._popup(), 103.5)
        self._assert_special_inputs("Close Button", "Close Button")

        toggle.setChecked(False)
        self._state(self.menu, 103.6)
        self._assert_special_inputs("Close Button", "Close Button")
        self._state(self.menu, 103.7)
        self._assert_special_inputs("Close Button", "Close Button", "Fight Button")

    def test_failed_close_is_paced_and_stale_duplicate_or_unknown_frames_cannot_resume(self) -> None:
        self._configure_special_message()
        popup = self._popup()
        self.overlay._auto_battle_input.click_region.side_effect = RuntimeError("Game is covered")
        self._state(popup, 100.0)
        self._state(popup, 101.99)
        self._assert_special_inputs("Close Button")
        self._state(popup, 102.0)
        self._state(self.menu, 102.3, captured_at=101.8)
        self._state(popup, 102.4)
        self._assert_special_inputs("Close Button", "Close Button")
        self.assertEqual(self._logs().count("could not click Close Button"), 1)

        self.overlay._auto_battle_input.click_region.side_effect = None
        self._state(popup, 104.0)
        self._state(self.menu, 104.1, captured_at=103.9)
        self._state(self.menu, 104.2)
        self._state(self.menu, 104.3, captured_at=104.2)
        self._state({**self.menu, "showingSpecialPokemonMessage": None}, 104.4)
        self._state(self.menu, 104.5)
        self._assert_special_inputs("Close Button", "Close Button", "Close Button")
        self.assertNotIn("dismissal confirmed", self._logs())
        self._state(self.menu, 104.6)
        self._assert_special_inputs("Close Button", "Close Button", "Close Button", "Fight Button")

    def test_missing_close_target_blocks_battle_until_it_is_configured(self) -> None:
        self._configure_special_message()
        self.overlay.region_window.set_regions((*self.regions, self.message_box))
        self._state(self._popup(), 100.0)
        self._state(self._popup(), 102.1)
        self._assert_special_inputs()
        self.assertEqual(self._logs().count("requires its Close Button region"), 1)
        self.assertIsNone(self.overlay._pending_battle_action)
        self.overlay.region_window.set_regions((*self.regions, self.message_box, self.close_button))
        self._state(self._popup(), 104.2)
        self._state(self.menu, 104.4)
        self._assert_special_inputs("Close Button")
        self._state(self.menu, 104.6)
        self._assert_special_inputs("Close Button", "Fight Button")

    def test_popup_preempts_a_pending_move_without_confirming_it_or_recording_damage(self) -> None:
        self._configure_special_message()
        self._state(self.moves, 100.0)
        self.assertIsNotNone(self.overlay._pending_battle_move)
        self._state(self._popup(enemyPokemonHealth=40), 100.1)
        self._assert_special_inputs("Move Slot 1", "Close Button")
        self.assertIsNone(self.overlay._pending_battle_action)
        self.assertIsNone(self.overlay._pending_battle_move)
        self.assertFalse(self.overlay._auto_battle_move_clicked)
        self.assertNotIn("confirmed Move Slot", self._logs())
        self._state(self.menu, 100.2)
        self._state(self.menu, 100.3)
        self._assert_special_inputs("Move Slot 1", "Close Button", "Fight Button")

    def test_popup_restarts_interrupted_catching_from_the_visible_bag(self) -> None:
        self._configure_special_message()
        self._enable_catching()
        self._state(self.menu, 100.0)
        self._state(self._popup(), 100.1)
        self.assertEqual(self.overlay._auto_catch_phase, "ready")
        self.assertIsNone(self.overlay._pending_battle_action)
        bag = {**self.submenu, "bagItemName": "Ultra Ball"}
        self._state(bag, 100.2)
        self._assert_special_inputs("Bag Button", "Close Button")
        self._state(bag, 100.3)
        self._assert_special_inputs("Bag Button", "Close Button", "Use Item Button")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")

    def test_popup_interruption_preserves_the_replacement_slot_and_two_second_delay(self) -> None:
        self._configure_special_message()
        self.overlay.settings_window.auto_battle_toggle.setChecked(False)
        self.overlay.settings_window.auto_switch_fainted_pokemon_toggle.setChecked(True)
        fainted = {**self.submenu, "yourPokemonHealth": 0}
        self._state(fainted, 100.0)
        self._state(fainted, 102.0)
        self._state(self._popup(yourPokemonHealth=0), 102.1)
        self._state(fainted, 102.2)
        self._state(fainted, 102.3)
        self._state(fainted, 104.29)
        self._assert_special_inputs("Pokemon Team Slot 2", "Close Button")
        self.assertEqual(self.overlay._auto_switch_controller._next_slot, 2)
        self._state(fainted, 104.3)
        self._assert_special_inputs("Pokemon Team Slot 2", "Close Button", "Pokemon Team Slot 2")
        self._state({**self.submenu, "turn": 2, "yourPokemonHealth": 80}, 104.5)
        self.assertEqual(self.overlay._auto_switch_controller._next_slot, 3)

    def test_queued_run_and_walking_wait_until_popup_dismissal_is_confirmed(self) -> None:
        self._configure_special_message()
        self.overlay.settings_window.auto_battle_toggle.setChecked(False)
        self.overlay.settings_window.auto_run_toggle.setChecked(True)
        self.overlay._auto_walk_controller.stop = Mock()
        self._state(self.menu, 100.0)
        self.assertTrue(self.overlay._auto_run_timer.isActive())
        self._state(self._popup(), 100.1)
        self.assertFalse(self.overlay._auto_run_timer.isActive())
        self.overlay._auto_walk_controller.stop.assert_called()
        self.overlay._click_auto_run()
        self._state(self.menu, 100.2)
        self.overlay._click_auto_run()
        self._assert_special_inputs("Close Button")
        self._state(self.menu, 100.3)
        self.overlay._auto_run_timer.stop()
        self.overlay._click_auto_run()
        self._assert_special_inputs("Close Button", "Run Button")

    def test_closing_a_shiny_popup_still_honors_stop_for_shiny_in_battle_and_run(self) -> None:
        self._configure_special_message()
        self.overlay.settings_window.stop_for_shiny_toggle.setChecked(True)
        for index, auto_run in enumerate((False, True)):
            with self.subTest(auto_run=auto_run):
                self.overlay._reset_battle_actions()
                self.overlay._auto_battle_input.reset_mock()
                self.overlay.settings_window.auto_run_toggle.setChecked(auto_run)
                start = 100.0 + index * 10.0
                self._state(self._popup(wildPokemonIsShiny=True), start)
                shiny = {**self.menu, "wildPokemonIsShiny": True}
                self._state(shiny, start + 0.1)
                self._state(shiny, start + 0.2)
                self._assert_special_inputs("Close Button")
                self.assertIsNone(self.overlay._pending_battle_action)
                self.assertIn("stopped for this encounter", self._logs())

    def test_shiny_catching_uses_existing_filters_only_after_popup_confirmation(self) -> None:
        self._configure_special_message()
        settings = self.overlay.settings_window
        settings.stop_for_shiny_toggle.setChecked(True)
        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.desired_pokemon_input.setText("Eevee")
        settings.auto_catching_toggle.setChecked(True)
        settings.auto_catching_desired_toggle.setChecked(True)
        settings.auto_catching_shiny_toggle.setChecked(False)
        settings.auto_catching_elite_toggle.setChecked(False)
        settings.auto_catching_item_selection.setCurrentText("Ultra Ball")
        settings.auto_run_toggle.setChecked(True)
        self._state(self._popup(wildPokemonIsShiny=True), 100.0)
        shiny = {**self.menu, "wildPokemonIsShiny": True}
        self._state(shiny, 100.1)
        self._state(shiny, 100.2)
        self._assert_special_inputs("Close Button")
        self.assertIsNone(self.overlay._pending_battle_action)

        settings.auto_catching_shiny_toggle.setChecked(True)
        self._state(shiny, 100.3)
        self._assert_special_inputs("Close Button", "Bag Button")
        self._state({
            **self.submenu, "wildPokemonIsShiny": True,
            "clickedBagButton": True, "bagItemName": "Ultra Ball",
        }, 100.4)
        self._assert_special_inputs("Close Button", "Bag Button", "Use Item Button")

    def test_stop_and_monitor_restart_discard_queued_popup_closes(self) -> None:
        self._configure_special_message()
        regions = tuple(self.overlay.region_window.regions)
        monitor = RealtimeRegionMonitor(
            regions, 42, self.overlay._target_geometry,
            image_capture=Mock(), text_extractor=Mock(), parent=self.overlay,
        )
        monitor.state_updated.connect(self.overlay._on_realtime_state_updated)
        self.overlay._realtime_region_monitor = monitor
        self._state(self._popup(), 100.0)
        self.overlay.stop_realtime_region_capture()
        self.assertFalse(self.overlay._special_pokemon_message_controller.blocked)
        self.now = 103.0
        monitor.state_updated.emit({**self._popup(), "captureStartedAt": self.now})
        self._assert_special_inputs("Close Button")
        self.overlay._on_realtime_monitor_finished(monitor)
        self.overlay._realtime_capture_requested = True
        monitor.state_updated.emit({**self._popup(), "captureStartedAt": self.now})
        self._assert_special_inputs("Close Button")
        self._state(self._popup(), 103.2)
        self._assert_special_inputs("Close Button", "Close Button")
        self.assertEqual(tuple(self.overlay.region_window.regions), regions)

    def test_battle_automations_start_after_old_catch_chat_and_noisy_popup_background(self) -> None:
        self._configure_special_message()
        regions = (
            *self.overlay.region_window.regions,
            SavedRegion("chat-box", "Battle Chat", 50, 100, 300, 150),
            SavedRegion("turn", "Turn Button", 600, 100, 100, 30),
            SavedRegion("health", "Ally Health", 600, 350, 60, 20),
            SavedRegion("health", "Enemy Health", 600, 150, 60, 20),
        )
        settings = self.overlay.settings_window
        for index, (automation, role, key) in enumerate((
            ("battle", "Fight Button", "1"), ("run", "Run Button", "4"),
            ("catch", "Bag Button", "2"), ("switch", "Pokemon Team Slot 2", "2"),
        )):
            with self.subTest(automation=automation):
                self.overlay._reset_battle_actions()
                self.overlay._auto_battle_input.reset_mock()
                settings.auto_run_toggle.setChecked(False)
                settings.auto_battle_toggle.setChecked(automation == "battle")
                settings.auto_switch_fainted_pokemon_toggle.setChecked(automation == "switch")
                settings.auto_catching_toggle.setChecked(automation == "catch")
                settings.auto_run_toggle.setChecked(automation == "run")
                settings.stop_for_desired_pokemon_toggle.setChecked(automation == "catch")
                settings.desired_pokemon_input.setText("Pidgey")
                monitor = RealtimeRegionMonitor(
                    regions, 42, self.overlay._target_geometry,
                    image_capture=Mock(), text_extractor=Mock(), parent=self.overlay,
                )
                self.overlay._realtime_region_monitor = monitor

                def read_regions(screenshot, requested, timings):
                    results = []
                    for region in requested:
                        confidence = 95.0
                        if region.label == "Message Box":
                            text, confidence = "fh . a sew BH OU furs |__|", 32.41
                        elif region.feature == "chat-box":
                            text = (
                                "Congratulations, you caught the opposing Ekans.\n"
                                "A wild Pidgey appeared!\nGo! Pikachu!\nTurn 1"
                            )
                        elif region.feature == "turn":
                            text = "Turn 1"
                        elif region.feature == "health":
                            text = "0%" if automation == "switch" and region.label == "Ally Health" else "100%"
                        elif region.label in ("Fight Button", "Bag Button", "Pokemon/Swap Button", "Run Button"):
                            text = region.label if automation != "switch" else ""
                        else:
                            text = ""
                        results.append((region, OCRResult(text, 100, 30, confidence)))
                    return results

                monitor._read_regions = Mock(side_effect=read_regions)

                def capture(at):
                    self.now = at
                    state = monitor._capture_state({
                        "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
                    })[0]
                    self.assertIs(state["onBattleMode"], True)
                    self.assertIs(state["showingSpecialPokemonMessage"], False)
                    self.overlay._on_realtime_state_updated(state)

                start = 100.0 + index * 10.0
                capture(start)
                if automation == "switch":
                    capture(start + 1.99)
                    self.overlay._auto_battle_input.tap_key.assert_not_called()
                    self.overlay._auto_battle_input.click_region.assert_not_called()
                    capture(start + 2.0)
                elif automation == "run":
                    self.overlay._auto_run_timer.stop()
                    self.overlay._click_auto_run()
                if self.use_mouse_clicking:
                    self.assertEqual(self._clicks(), [role])
                    self.overlay._auto_battle_input.tap_key.assert_not_called()
                else:
                    self.assertEqual(self._keys(), [key])
                    self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_unreadable_turn_and_menu_explain_the_wait_then_start_from_a_fresh_menu(self) -> None:
        for at in (100.0, 101.0):
            self._state({**self.menu, "turn": None}, at)
        self.assertEqual(self._logs().count("a readable turn number in the Turn Button region"), 1)
        for at in (102.0, 103.0):
            self._state({**self.menu, "showingRunButton": None}, at)
        self.assertEqual(self._logs().count("Run=None"), 1)
        self._state({**self.menu, "clickStateReliable": False}, 104.0)
        self.assertIn("readable region OCR before sending an action", self._logs())
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

        self._state(self.menu, 105.0)
        if self.use_mouse_clicking:
            self.assertEqual(self._clicks(), ["Fight Button"])
        else:
            self.assertEqual(self._keys(), ["1"])


class _BattleLatencyTests:
    def _assert_fast_battle_inputs(self, *roles: str) -> None:
        if self.use_mouse_clicking:
            self.assertEqual(self._clicks(), list(roles))
            self.overlay._auto_battle_input.tap_key.assert_not_called()
        else:
            self.assertEqual(self._keys(), ["1" for _role in roles])
            self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_confirmed_fight_selects_a_move_immediately_without_waiting_for_retry_delay(self) -> None:
        self._state(self.menu, 100.0)
        self._state(self.moves, 100.005, captured_at=99.99)
        self._assert_fast_battle_inputs("Fight Button")

        self._state(self.moves, 100.01)

        self._assert_fast_battle_inputs("Fight Button", "Move Slot 1")
        self.assertIn("confirmed Fight Button", self._logs())
        self.assertEqual(self.overlay._pending_battle_action.feature, "moves")
        self.assertFalse(self.overlay._auto_battle_move_clicked)

    def test_fast_fight_confirmation_still_waits_for_all_four_move_and_pp_readings(self) -> None:
        self._state(self.menu, 100.0)
        partial = {
            **self.moves, "pokemonMoves": "Move Slot 1: Tackle (1.0x | 20/35)",
        }
        self._state(partial, 100.01)

        self._assert_fast_battle_inputs("Fight Button")
        self.assertIsNone(self.overlay._pending_battle_action)
        self.assertIn("all four move slots", self._logs())
        self._state(self.moves, 100.02)
        self._assert_fast_battle_inputs("Fight Button", "Move Slot 1")


class ActionConfirmationTests(_BattleLatencyTests, _SpecialPokemonMessageTests, _CatchingRecoveryTests, _BattleActionTestCase):
    use_mouse_clicking = True

    def test_ignored_fight_retries_then_advances_only_after_the_move_menu_appears(self) -> None:
        self._state(self.menu, 100.0)
        self.assertEqual(self._clicks(), ["Fight Button"])
        self.assertFalse(self.overlay._auto_battle_fight_clicked)
        self.assertNotIn("confirmed", self._logs())
        for at in (100.2, 101.0, 101.99):
            self._state(self.menu, at)
        self.assertEqual(self._clicks(), ["Fight Button"])

        self._state(self.menu, 102.01)
        self.assertEqual(self._clicks(), ["Fight Button", "Fight Button"])
        self.assertIn("retrying", self._logs())
        self.assertNotIn("waiting for readable move", self._logs())
        self._state(self.moves, 102.3)
        self.assertEqual(self._clicks(), ["Fight Button", "Fight Button", "Move Slot 1"])
        self.assertIn("confirmed Fight Button", self._logs())
        self.assertFalse(self.overlay._auto_battle_move_clicked)

        self._state(self.submenu, 102.5)
        self.assertFalse(self.overlay._auto_battle_move_clicked)
        self._state(self.submenu, 102.7)
        self.assertTrue(self.overlay._auto_battle_move_clicked)
        self._state({**self.menu, "turn": 2}, 103.0)
        self.assertEqual(self._clicks()[-1], "Fight Button")
        self.assertEqual(len(self._clicks()), 4)

    def test_old_or_duplicate_frames_cannot_confirm_or_repeat_a_click(self) -> None:
        self._state(self.menu, 100.0)
        self._state(self.moves, 101.0, captured_at=99.9)
        self._state(self.menu, 104.0, captured_at=100.0)
        self.assertEqual(self._clicks(), ["Fight Button"])
        self.assertNotIn("confirmed", self._logs())
        self._state(self.moves, 104.2)
        self.assertEqual(self._clicks(), ["Fight Button", "Move Slot 1"])
        self._state(self.submenu, 104.4)
        self._state(self.submenu, 104.5, captured_at=104.4)
        self.assertFalse(self.overlay._auto_battle_move_clicked)

    def test_visible_fight_menu_is_not_move_selection_even_with_an_old_clicked_flag(self) -> None:
        self._state({
            **self.menu, "clickedFightButton": True,
            "pokemonMoves": (
                "Move Slot 1: Fight (-) | Move Slot 2: Bag (-) | "
                "Move Slot 3: Pokemon (-) | Move Slot 4: Run (-)"
            ),
        }, 100.0)
        self.assertEqual(self._clicks(), ["Fight Button"])
        self.assertNotIn("waiting for readable move", self._logs())

    def test_move_click_retries_a_readable_unchanged_menu_without_latching_success(self) -> None:
        self._state(self.moves, 100.0)
        self.assertEqual(self._clicks(), ["Move Slot 1"])
        self._state(self.moves, 101.0)
        self.assertFalse(self.overlay._auto_battle_move_clicked)
        self._state(self.moves, 102.1)
        self.assertEqual(self._clicks(), ["Move Slot 1", "Move Slot 1"])
        self._state({**self.menu, "turn": 2}, 102.3)
        self.assertIn("confirmed Move Slot 1", self._logs())
        self.assertEqual(self._clicks()[-1], "Fight Button")

    def test_unreadable_menu_does_not_trigger_a_blind_retry(self) -> None:
        self._state(self.menu, 100.0)
        unknown = {
            **self.submenu,
            **{visible: None for _, visible in ACTION_BUTTON_STATES.values()},
        }
        for at in (103.0, 104.0, 105.0):
            self._state(unknown, at)
        self.assertEqual(self._clicks(), ["Fight Button"])
        self.assertNotIn("confirmed", self._logs())

    def test_failed_move_ocr_is_not_a_confirmed_animation_transition(self) -> None:
        self._state(self.moves, 100.0)
        for at in (100.2, 100.4, 100.6):
            self._state({**self.submenu, "clickStateReliable": False}, at)
        self.assertFalse(self.overlay._auto_battle_move_clicked)
        self.assertIsNotNone(self.overlay._pending_battle_action)
        self._state(self.submenu, 100.8)
        self.assertFalse(self.overlay._auto_battle_move_clicked)
        self._state(self.submenu, 101.0)
        self.assertTrue(self.overlay._auto_battle_move_clicked)

    def test_failed_retry_remains_pending_and_is_paced(self) -> None:
        self._state(self.menu, 100.0)
        self.overlay._auto_battle_input.click_region.side_effect = RuntimeError("Game is covered")
        self._state(self.menu, 102.1)
        self._state(self.menu, 102.2)
        self._state(self.menu, 104.0)
        self.assertEqual(len(self._clicks()), 2)
        self.assertIsNotNone(self.overlay._pending_battle_action)
        self.overlay._auto_battle_input.click_region.side_effect = None
        self._state(self.menu, 104.2)
        self.assertEqual(len(self._clicks()), 3)

    def test_turn_change_discards_the_old_fight_attempt(self) -> None:
        self._state(self.menu, 100.0)
        self._state({**self.menu, "turn": 2}, 100.5)
        self.assertEqual(self._clicks(), ["Fight Button", "Fight Button"])
        self.assertEqual(self.overlay._pending_battle_action.turn, 2)
        self.assertNotIn("confirmed Fight Button on turn 1", self._logs())

    def test_disabling_automation_cancels_pending_retries(self) -> None:
        self._state(self.menu, 100.0)
        self.overlay.settings_window.auto_battle_toggle.setChecked(False)
        self._state(self.menu, 105.0)
        self.assertEqual(self._clicks(), ["Fight Button"])
        self.assertIsNone(self.overlay._pending_battle_action)

    def test_stop_and_monitor_restart_discard_pending_actions_and_queued_states(self) -> None:
        monitor = RealtimeRegionMonitor(
            self.regions, 42, self.overlay._target_geometry,
            image_capture=Mock(), text_extractor=Mock(), parent=self.overlay,
        )
        monitor.state_updated.connect(self.overlay._on_realtime_state_updated)
        self.overlay._realtime_region_monitor = monitor
        self._state(self.menu, 100.0)
        self.overlay.stop_realtime_region_capture()
        self.assertIsNone(self.overlay._pending_battle_action)
        self.now = 105.0
        monitor.state_updated.emit({**self.menu, "captureStartedAt": self.now})
        self.assertEqual(self._clicks(), ["Fight Button"])

        self.overlay._on_realtime_monitor_finished(monitor)
        self.overlay._realtime_capture_requested = True
        monitor.state_updated.emit({**self.moves, "captureStartedAt": self.now})
        self.assertEqual(self._clicks(), ["Fight Button"])
        self._state(self.menu, 105.2)
        self.assertEqual(self._clicks(), ["Fight Button", "Fight Button"])
        self.assertEqual(tuple(self.overlay.region_window.regions), self.regions)

    def test_bag_browsing_and_item_use_wait_for_confirmed_responses_and_retry(self) -> None:
        settings = self.overlay.settings_window
        settings.auto_battle_toggle.setChecked(False)
        settings.auto_catching_toggle.setChecked(True)
        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.desired_pokemon_input.setText("Pidgey")
        settings.auto_catching_item_selection.setCurrentText("Ultra Ball")
        self._state(self.menu, 100.0)
        self.assertEqual(self.overlay._auto_catch_phase, "opening_bag")
        self._state(self.menu, 102.1)
        self.assertEqual(self._clicks(), ["Bag Button", "Bag Button"])
        bag = {
            **self.submenu, "turn": None, "clickedBagButton": True,
            "bagItemName": "Poke Ball",
        }
        self._state(bag, 102.3)
        self.assertEqual(self.overlay._auto_catch_phase, "selecting_item")
        self.assertEqual(self._clicks()[-1], "Next Button")
        self._state(bag, 104.4)
        self.assertEqual(self._clicks()[-2:], ["Next Button", "Next Button"])
        target = {**bag, "bagItemName": "Ultra Ball"}
        self._state(target, 104.6)
        self.assertEqual(self._clicks()[-1], "Use Item Button")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")
        self._state(target, 106.7)
        self.assertEqual(self._clicks()[-2:], ["Use Item Button", "Use Item Button"])
        self._state(self.submenu, 106.9)
        self._state(self.submenu, 107.1)
        self.assertEqual(self.overlay._auto_catch_phase, "item_used")
        self._state({
            **self.submenu, "onBattleMode": False,
            "chatBoxMessage": "Congratulations, you caught the opposing Pidgey.",
        }, 107.3)
        self.assertEqual(self.overlay._auto_catch_phase, "idle")
        self.assertIn("opposing Pokemon was caught", self._logs())

    def test_run_waits_for_confirmed_battle_end_and_retries_an_ignored_click(self) -> None:
        self.overlay.settings_window.auto_battle_toggle.setChecked(False)
        self.overlay.settings_window.auto_run_toggle.setChecked(True)
        self._state(self.menu, 100.0)
        self.overlay._auto_run_timer.stop()
        self.overlay._click_auto_run()
        self.assertEqual(self._clicks(), ["Run Button"])
        self.assertFalse(self.overlay._auto_run_clicked)
        self._state(self.menu, 102.1)
        self.assertEqual(self._clicks(), ["Run Button", "Run Button"])
        ended = {**self.submenu, "onBattleMode": False, "turn": None}
        self._state(ended, 102.3)
        self._state(ended, 102.5)
        self.assertNotIn("confirmed Run Button", self._logs())
        self._state(ended, 102.7)
        self.assertIn("confirmed Run Button", self._logs())
        self.assertIsNone(self.overlay._pending_battle_action)

    def test_confirmed_faint_preempts_fight_and_switch_waits_for_recovery(self) -> None:
        self.overlay.settings_window.auto_switch_fainted_pokemon_toggle.setChecked(True)
        self._state(self.menu, 100.0)
        fainted = {**self.submenu, "yourPokemonHealth": 0}
        self._state(fainted, 100.2)
        self.assertIsNone(self.overlay._pending_battle_action)
        for at in (101.7, 102.19):
            self._state(fainted, at)
            self.assertEqual(self._clicks(), ["Fight Button"])
        self._state(fainted, 102.2)
        self.assertEqual(self._clicks(), ["Fight Button", "Pokemon Team Slot 2"])
        self.assertEqual(self.overlay._auto_switch_controller._next_slot, 2)
        self._state(fainted, 104.19)
        self.assertEqual(self._clicks(), ["Fight Button", "Pokemon Team Slot 2"])
        self._state(fainted, 104.2)
        self.assertEqual(self._clicks()[-2:], ["Pokemon Team Slot 2", "Pokemon Team Slot 2"])
        self._state({**self.submenu, "yourPokemonHealth": 80}, 104.4)
        self.assertEqual(self.overlay._auto_switch_controller._next_slot, 3)
        self.assertIn("confirmed Pokemon Team Slot 2", self._logs())


class KeyboardActionConfirmationTests(_BattleLatencyTests, _SpecialPokemonMessageTests, _CatchingRecoveryTests, _BattleActionTestCase):
    def _keys(self):
        return [
            chr(call.args[1])
            for call in self.overlay._auto_battle_input.tap_key.call_args_list
        ]

    def test_fight_and_move_one_are_separate_confirmed_key_presses(self) -> None:
        self._state(self.menu, 100.0)
        self.assertEqual(self._keys(), ["1"])
        self.assertFalse(self.overlay._auto_battle_fight_clicked)
        self._state(self.menu, 100.1)
        self.assertEqual(self._keys(), ["1", "1"])
        self._state(self.menu, 100.15, captured_at=100.1)
        self.assertEqual(self._keys(), ["1", "1"])
        self._state(self.moves, 100.2)
        self.assertEqual(self._keys(), ["1", "1", "1"])
        self.assertIn("confirmed Fight Button", self._logs())
        self.assertIn("sent key 1 for Move Slot 1", self._logs())
        self.assertFalse(self.overlay._auto_battle_move_clicked)

        self._state(self.submenu, 100.3)
        self.assertFalse(self.overlay._auto_battle_move_clicked)
        self._state(self.submenu, 100.4)
        self.assertTrue(self.overlay._auto_battle_move_clicked)
        self._state({**self.menu, "turn": 2}, 100.5)
        self.assertEqual(self._keys(), ["1", "1", "1", "1"])
        self.overlay._auto_battle_input.click_region.assert_not_called()
        for call in self.overlay._auto_battle_input.tap_key.call_args_list:
            self.assertEqual(call.args[0], 42)
            self.assertEqual(call.kwargs, {"duration_seconds": 0.05, "extended": False})

    def test_each_selected_move_uses_its_own_number(self) -> None:
        for slot in range(1, 5):
            with self.subTest(slot=slot):
                self.overlay._reset_battle_actions()
                moves = " | ".join(
                    f"Move Slot {candidate}: Tackle (1.0x | "
                    f"{20 if candidate == slot else 0}/35)"
                    for candidate in range(1, 5)
                )
                self._state({**self.moves, "pokemonMoves": moves}, 100.0 + slot)
                self.assertEqual(self._keys()[-1], str(slot))
                self.assertEqual(
                    self.overlay._pending_battle_action.action_name, f"Move Slot {slot}",
                )
        self.assertEqual(self._keys(), ["1", "2", "3", "4"])
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_old_unreadable_and_duplicate_frames_cannot_confirm_keys(self) -> None:
        self._state(self.menu, 100.0)
        self._state(self.moves, 101.0, captured_at=99.9)
        self._state(self.menu, 103.0, captured_at=100.0)
        self._state({**self.moves, "clickStateReliable": False}, 103.1)
        self.assertEqual(self._keys(), ["1"])
        self.assertNotIn("confirmed", self._logs())
        self._state(self.moves, 103.3)
        self.assertEqual(self._keys(), ["1", "1"])
        self._state(self.submenu, 103.5)
        self._state(self.submenu, 103.7, captured_at=103.5)
        self.assertFalse(self.overlay._auto_battle_move_clicked)
        self._state(self.submenu, 103.9)
        self.assertTrue(self.overlay._auto_battle_move_clicked)

    def test_catching_opens_bag_browses_and_uses_item_without_click_targets(self) -> None:
        self.overlay.region_window.set_regions(tuple(
            region for region in self.regions
            if region.label not in ("Next Button", "Use Item Button")
        ))
        settings = self.overlay.settings_window
        settings.auto_battle_toggle.setChecked(False)
        settings.auto_catching_toggle.setChecked(True)
        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.desired_pokemon_input.setText("Pidgey")
        settings.auto_catching_item_selection.setCurrentText("Ultra Ball")
        self._state(self.menu, 100.0)
        self.assertEqual(self._keys(), ["2"])
        self.assertEqual(self.overlay._auto_catch_phase, "opening_bag")
        self._state(self.menu, 100.1)
        self.assertEqual(self._keys(), ["2", "2"])
        bag = {
            **self.submenu, "turn": None, "clickedBagButton": True,
            "bagItemName": "Poke Ball",
        }
        self._state(bag, 100.2)
        self.assertEqual(self._keys(), ["2", "2", "2"])
        self.assertIn("sent key 2 for Next Button", self._logs())
        self._state(bag, 100.3)
        self.assertEqual(self._keys(), ["2", "2", "2", "2"])
        target = {**bag, "bagItemName": "Ultra Ball"}
        self._state(target, 100.4)
        self.assertEqual(self._keys()[-1], "3")
        self.assertEqual(self.overlay._auto_catch_phase, "using_item")
        self._state(target, 100.5)
        self.assertEqual(self._keys()[-2:], ["3", "3"])
        self._state(self.submenu, 100.6)
        self._state(self.submenu, 100.7)
        self.assertEqual(self.overlay._auto_catch_phase, "item_used")
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_previous_uses_one_and_confirms_an_item_change(self) -> None:
        self.overlay.settings_window.auto_catching_toggle.setChecked(True)
        bag = {**self.submenu, "clickedBagButton": True, "bagItemName": "Ultra Ball"}
        self.overlay._latest_realtime_state = bag
        self.assertTrue(self.overlay._submit_battle_action(
            "bag", 1, "Previous Button", automation_name="Auto Catching",
        ))
        self.assertEqual(self._keys(), ["1"])
        self._state(bag, 100.1)
        self.assertEqual(self._keys(), ["1", "1"])
        self._state({**bag, "bagItemName": "Great Ball"}, 100.2)
        self.assertIsNone(self.overlay._pending_battle_action)
        self.assertIn("confirmed Previous Button", self._logs())
        self.assertFalse(self.overlay._auto_battle_fight_clicked)

    def test_swap_uses_three_and_waits_for_the_pokemon_menu(self) -> None:
        self.overlay._latest_realtime_state = self.menu
        self.assertTrue(self.overlay._submit_battle_action(
            "swap", 1, "Pokemon/Swap Button",
        ))
        self._state(self.submenu, 100.1)
        self.assertIsNotNone(self.overlay._pending_battle_action)
        self.assertEqual(self._keys(), ["3"])
        self._state({**self.submenu, "clickedSwapButton": True}, 100.2)
        self.assertIsNone(self.overlay._pending_battle_action)
        self.assertIn("confirmed Pokemon/Swap Button", self._logs())

    def test_faints_select_slots_two_through_six_directly_and_wait_for_recovery(self) -> None:
        self.overlay.region_window.set_regions(tuple(
            region for region in self.regions if region.feature != "pokemon-team"
        ))
        settings = self.overlay.settings_window
        settings.auto_battle_toggle.setChecked(False)
        settings.auto_switch_fainted_pokemon_toggle.setChecked(True)
        for slot in range(2, 7):
            with self.subTest(slot=slot):
                at = 100.0 + slot * 10
                fainted = {**self.submenu, "turn": slot, "yourPokemonHealth": 0}
                self._state(fainted, at)
                self.assertEqual(len(self._keys()), (slot - 2) * 2)
                for elapsed in (1.5, 1.99):
                    self._state(fainted, at + elapsed)
                    self.assertEqual(len(self._keys()), (slot - 2) * 2)
                self._state(fainted, at + 2.0)
                self.assertEqual(self._keys()[-1], str(slot))
                self.assertEqual(self.overlay._auto_switch_controller._next_slot, slot)
                for elapsed in (2.1, 3.99):
                    self._state(fainted, at + elapsed)
                    self.assertEqual(len(self._keys()), (slot - 2) * 2 + 1)
                self._state(fainted, at + 4.0)
                self.assertEqual(self._keys()[-2:], [str(slot), str(slot)])
                self._state({**fainted, "yourPokemonHealth": 80}, at + 4.2)
                self.assertEqual(self.overlay._auto_switch_controller._next_slot, slot + 1)
        self._state({**self.submenu, "turn": 7, "yourPokemonHealth": 0}, 170.0)
        self._state({**self.submenu, "turn": 7, "yourPokemonHealth": 0}, 172.0)
        self.assertEqual(self._keys(), ["2", "2", "3", "3", "4", "4", "5", "5", "6", "6"])
        self.assertIn("no remaining Pokemon slots", self._logs())
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_replacement_turn_change_waits_for_health_recovery_before_advancing(self) -> None:
        self.overlay.settings_window.auto_battle_toggle.setChecked(False)
        self.overlay.settings_window.auto_switch_fainted_pokemon_toggle.setChecked(True)
        fainted = {**self.submenu, "yourPokemonHealth": 0}
        self._state(fainted, 100.0)
        self._state(fainted, 102.0)
        self._state({**fainted, "turn": 2}, 102.1)
        self.assertEqual(self._keys(), ["2"])
        self.assertEqual(self.overlay._auto_switch_controller._next_slot, 2)
        self.assertIsNotNone(self.overlay._pending_battle_action)
        self._state({**fainted, "turn": 2}, 104.0)
        self.assertEqual(self._keys(), ["2", "2"])
        self._state({**self.submenu, "turn": 2, "yourPokemonHealth": 80}, 104.2)
        self.assertEqual(self.overlay._auto_switch_controller._next_slot, 3)

    def test_slot_one_and_invalid_actions_are_never_submitted_in_either_mode(self) -> None:
        for mouse_mode in (False, True):
            self.overlay.settings_window.use_mouse_clicking_toggle.setChecked(mouse_mode)
            for feature, role in (
                ("pokemon-team", "Pokemon Team Slot 1"),
                ("pokemon-team", "Pokemon Team Slot 7"),
                ("moves", "Move Slot 5"),
            ):
                with self.subTest(mouse_mode=mouse_mode, role=role):
                    self.assertFalse(self.overlay._submit_battle_action(feature, 1, role))
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_run_uses_four_and_requires_a_confirmed_response(self) -> None:
        self.overlay.settings_window.auto_battle_toggle.setChecked(False)
        self.overlay.settings_window.auto_run_toggle.setChecked(True)
        self._state(self.menu, 100.0)
        self.overlay._auto_run_timer.stop()
        self.overlay._click_auto_run()
        self.assertEqual(self._keys(), ["4"])
        self.assertFalse(self.overlay._auto_run_clicked)
        self._state(self.menu, 100.1)
        self.assertEqual(self._keys(), ["4", "4"])
        ended = {**self.submenu, "onBattleMode": False, "turn": None}
        self._state(ended, 100.2)
        self._state(ended, 100.3)
        self.assertNotIn("confirmed Run Button", self._logs())
        self._state(ended, 100.4)
        self.assertIn("confirmed Run Button", self._logs())
        self.assertIsNone(self.overlay._pending_battle_action)

    def test_switching_input_mode_applies_its_retry_timing_to_the_pending_action(self) -> None:
        self._state(self.menu, 100.0)
        pending = self.overlay._pending_battle_action
        self.overlay.settings_window.use_mouse_clicking_toggle.setChecked(True)
        self.assertIs(self.overlay._pending_battle_action, pending)
        self._state(self.menu, 101.9)
        self.assertEqual(self._clicks(), [])
        self._state(self.menu, 102.1)
        self.assertEqual(self._clicks(), ["Fight Button"])
        self.assertEqual(self._keys(), ["1"])
        self.overlay.settings_window.use_mouse_clicking_toggle.setChecked(False)
        self._state(self.menu, 102.2)
        self.assertIs(self.overlay._pending_battle_action, pending)
        self.assertEqual(self._keys(), ["1", "1"])
        self._state(self.moves, 102.3)
        self.assertEqual(self._keys(), ["1", "1", "1"])
        self.assertEqual(self._clicks(), ["Fight Button"])
        self.assertIn("confirmed Fight Button", self._logs())

    def test_mouse_retry_waits_for_a_saved_target_and_can_resume_with_keys(self) -> None:
        self.overlay.region_window.set_regions(())
        self._state(self.moves, 100.0)
        self.assertEqual(self._keys(), ["1"])
        self.overlay.settings_window.use_mouse_clicking_toggle.setChecked(True)
        self._state(self.moves, 102.1)
        self.assertEqual(self._clicks(), [])
        self.assertIn("configure its region first", self._logs())
        self.assertIsNotNone(self.overlay._pending_battle_action)
        self.overlay.settings_window.use_mouse_clicking_toggle.setChecked(False)
        self._state(self.moves, 102.2)
        self.assertEqual(self._keys(), ["1", "1"])

    def test_failed_key_retry_waits_only_for_a_new_reading_and_stopping_discards_it(self) -> None:
        self._state(self.menu, 100.0)
        self.overlay._auto_battle_input.tap_key.side_effect = RuntimeError("Game is not active")
        self._state(self.menu, 100.1)
        self._state(self.menu, 100.15, captured_at=100.1)
        self.assertEqual(self._keys(), ["1", "1"])
        self._state(self.menu, 100.2)
        self.assertEqual(self._keys(), ["1", "1", "1"])
        self._state({**self.menu, "clickStateReliable": False}, 100.3)
        self.assertEqual(self._keys(), ["1", "1", "1"])
        self.overlay._auto_battle_input.tap_key.side_effect = None
        self._state(self.menu, 100.4)
        self.assertEqual(self._keys(), ["1", "1", "1", "1"])
        self.assertIsNotNone(self.overlay._pending_battle_action)
        self.assertNotIn("confirmed", self._logs())
        self.assertEqual(self._logs().count("retrying the action"), 1)
        self.overlay.stop_realtime_region_capture()
        self.assertIsNone(self.overlay._pending_battle_action)


if __name__ == "__main__":
    unittest.main()
