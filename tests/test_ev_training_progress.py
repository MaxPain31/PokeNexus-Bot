from unittest.mock import Mock

from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.ev_training import EVTrainingEntry, default_ev_values
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from test_action_confirmation import _BattleActionTestCase


class _EVProgressIntegrationTests:
    def _configure(self, *, current=None, targets=None, names="Exploud, Whismur, Marill, Gyarados, Pidgey"):
        self.overlay._reset_battle_actions()
        self.overlay._auto_battle_input.reset_mock()
        self.overlay.region_test_log_window.append_entry.reset_mock()
        self.overlay._realtime_capture_requested = True
        settings = self.overlay.settings_window
        settings.auto_battle_toggle.setChecked(True)
        settings.auto_run_toggle.setChecked(False)
        settings.auto_catching_toggle.setChecked(False)
        settings.ev_training_pokemon_input.setText(names)
        settings.set_ev_training_progress(
            current or default_ev_values(),
            targets if targets is not None else {**default_ev_values(), "hp": 6},
        )
        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")

    def _frame(self, name, *, moves=False, defeated=False):
        frame = {
            **(self.submenu if defeated else self.moves if moves else self.menu),
            "wildPokemonName": name, "chatBoxMessage": f"A wild {name} appeared!",
        }
        if defeated:
            frame.update(enemyPokemonHealth=0, chatBoxMessage=frame["chatBoxMessage"] + f"\nThe opposing {name} fainted!")
        return frame

    def _end(self, at):
        for offset in (0.0, 0.1, 0.2):
            self._state({
                **self.submenu, "onBattleMode": False, "turn": None,
                "wildPokemonName": None, "enemyPokemonHealth": None, "chatBoxMessage": "",
            }, at + offset)

    def _defeat(self, name, at):
        self._state(self._frame(name), at)
        self._state(self._frame(name, moves=True), at + 0.1)
        self._state(self._frame(name, defeated=True), at + 0.2)

    def _flush_run(self):
        self.overlay._auto_run_timer.stop()
        self.overlay._click_auto_run()

    def _assert_inputs(self, *roles):
        if self.use_mouse_clicking:
            self.assertEqual(self._clicks(), list(roles))
            self.overlay._auto_battle_input.tap_key.assert_not_called()
        else:
            keys = {"Fight Button": "1", "Move Slot 1": "1", "Run Button": "4", "Bag Button": "2"}
            self.assertEqual(
                [chr(call.args[1]) for call in self.overlay._auto_battle_input.tap_key.call_args_list],
                [keys[role] for role in roles],
            )
            self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_two_confirmed_exploud_defeats_reach_six_and_each_is_counted_once(self):
        self._configure()
        settings = self.overlay.settings_window
        for at, expected in ((100.0, 3), (102.0, 6)):
            self._state(self._frame("Exploud"), at)
            self.assertEqual(settings.ev_training_progress().current["hp"], expected - 3)
            self._state(self._frame("Exploud", moves=True), at + 0.1)
            self.assertEqual(settings.ev_training_progress().current["hp"], expected - 3)
            self._state(self._frame("Exploud", defeated=True), at + 0.2)
            for offset in (0.3, 0.4, 0.5):
                self._state(self._frame("Exploud", defeated=True), at + offset)
                self._flush_run()
            self.assertEqual(settings.ev_training_progress().current["hp"], expected)
            self.assertEqual(settings.ev_training_remaining_labels["hp"].text(), str(6 - expected))
            self._end(at + 0.6)
        self._state(self._frame("Exploud"), 104.0)
        self._flush_run()
        self._assert_inputs("Fight Button", "Move Slot 1", "Fight Button", "Move Slot 1")
        self.assertIsNone(self.overlay._pending_battle_action)
        self.assertTrue(settings.ev_training_progress().complete)
        self.assertEqual(self._logs().count("Exploud defeated — +3 HP"), 2)

    def test_oversized_hp_yield_runs_automatically_and_other_stats_continue_across_categories(self):
        self._configure(
            current={**default_ev_values(), "hp": 4, "attack": 40, "speed": 24},
            targets={**default_ev_values(), "hp": 6, "attack": 252, "speed": 252},
        )
        settings = self.overlay.settings_window
        settings.ev_training_category_filter.setCurrentIndex(settings.ev_training_category_filter.findData("HP"))
        self._state(self._frame("Exploud"), 100.0)
        self._flush_run()
        self._assert_inputs("Run Button")
        self.assertFalse(settings.auto_run_toggle.isChecked())
        self._end(100.2)
        self._defeat("Gyarados", 101.0)
        self.assertEqual(settings.ev_training_progress().current["attack"], 42)
        self._end(101.3)
        self._defeat("Pidgey", 102.0)
        self.assertEqual(settings.ev_training_progress().current["speed"], 25)
        self._end(102.3)
        self._defeat("Whismur", 103.0)
        self.assertEqual(settings.ev_training_progress().current["hp"], 5)
        self._assert_inputs(
            "Run Button", "Fight Button", "Move Slot 1", "Fight Button", "Move Slot 1",
            "Fight Button", "Move Slot 1",
        )

    def test_total_cap_rejects_a_partial_yield_then_stops_at_510(self):
        self._configure(
            current={**default_ev_values(), "hp": 4, "attack": 252, "speed": 252},
            targets={**default_ev_values(), "hp": 6, "attack": 252, "speed": 252},
        )
        self._state(self._frame("Exploud"), 100.0)
        self._flush_run()
        self._end(100.2)
        self._defeat("Marill", 101.0)
        progress = self.overlay.settings_window.ev_training_progress()
        self.assertEqual((progress.total, progress.current["hp"]), (510, 6))
        self._end(101.3)
        self._state(self._frame("Whismur"), 102.0)
        self._flush_run()
        self._assert_inputs("Run Button", "Fight Button", "Move Slot 1")

    def test_a_completed_stat_is_skipped_while_another_stat_still_needs_evs(self):
        self._configure(
            current={**default_ev_values(), "hp": 251},
            targets={**default_ev_values(), "hp": 252, "speed": 1},
        )
        self._defeat("Whismur", 100.0)
        self._end(100.3)
        self._state(self._frame("Whismur"), 101.0)
        self._flush_run()
        self._end(101.2)
        self._defeat("Pidgey", 102.0)
        self._assert_inputs("Fight Button", "Move Slot 1", "Run Button", "Fight Button", "Move Slot 1")
        progress = self.overlay.settings_window.ev_training_progress()
        self.assertEqual((progress.current["hp"], progress.current["speed"]), (252, 1))
        self.assertTrue(progress.complete)

    def test_secondary_stat_overflow_rejects_the_entire_encounter_and_updates_both_stats_when_it_fits(self):
        self._configure(
            targets={**default_ev_values(), "hp": 2, "sp_defense": 1, "speed": 252}, names="Whismur",
        )
        settings = self.overlay.settings_window
        settings._ev_training_entries = (
            EVTrainingEntry("Whismur", "HP + Special Defense", "Test", "Test", "1 HP + 2 Special Defense", (1, 0, 0, 0, 2, 0)),
        )
        self._state(self._frame("Whismur"), 100.0)
        self._flush_run()
        self._assert_inputs("Run Button")
        settings.ev_training_target_inputs["sp_defense"].setValue(2)
        self.assertIsNone(self.overlay._pending_battle_action)
        self._defeat("Whismur", 101.0)
        self.assertEqual(tuple(settings.ev_training_progress().current.values()), (1, 0, 0, 0, 2, 0))
        self._end(101.3)
        self._state(self._frame("Whismur"), 102.0)
        self._flush_run()
        self._assert_inputs("Run Button", "Fight Button", "Move Slot 1", "Run Button")

    def test_stale_unknown_and_repeated_faint_frames_do_not_change_progress(self):
        self._configure()
        self._state(self._frame("Exploud"), 100.0)
        self._state(self._frame("Exploud", moves=True), 100.1)
        fainted = self._frame("Exploud", defeated=True)
        self._state(fainted, 100.2, captured_at=100.1)
        self._state({**fainted, "clickStateReliable": False}, 100.3)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 0)
        self._state(fainted, 100.4)
        self._state(fainted, 100.5, captured_at=100.4)
        self._state(fainted, 100.6)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)
        self._assert_inputs("Fight Button", "Move Slot 1")

    def test_new_faint_chat_with_no_remaining_hud_is_counted_before_battle_cleanup(self):
        self._configure()
        self._state(self._frame("Exploud"), 100.0)
        self._state({
            **self._frame("Exploud", defeated=True), "onBattleMode": False,
            "enemyPokemonHealth": None, "wildPokemonName": None, "yourPokemonHealth": None,
        }, 100.1)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)

    def test_defeat_accounting_preserves_the_final_move_damage_observation(self):
        self._configure()
        history = Mock()
        history.average_damage_by_move.return_value = {}
        history.record_damage.return_value = (1, 100.0)
        self.overlay._battle_history = history
        self._defeat("Exploud", 100.0)
        history.record_damage.assert_called_once_with("pikachu", "exploud", "Tackle", 100)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)

    def test_battle_disappearance_running_catching_or_player_faint_never_awards_evs(self):
        for outcome in ("missing", "run", "catch", "player faint"):
            with self.subTest(outcome=outcome):
                self._configure()
                self._state(self._frame("Exploud"), 100.0)
                frame = {**self._frame("Exploud"), "onBattleMode": False, "enemyPokemonHealth": None}
                if outcome == "run":
                    frame.update(clickedRunButton=True, chatBoxMessage="Got away safely!")
                elif outcome == "catch":
                    frame.update(enemyPokemonHealth=0, chatBoxMessage="Congratulations, you caught the opposing Exploud!")
                elif outcome == "player faint":
                    frame.update(yourPokemonHealth=0, chatBoxMessage="Pikachu fainted!")
                for at in (100.1, 100.2, 100.3):
                    self._state(frame, at)
                self.assertEqual(self.overlay.settings_window.ev_training_progress().current, default_ev_values())

    def test_two_fresh_zero_hp_frames_confirm_once_and_hp_bounce_is_not_a_new_encounter(self):
        self._configure()
        self._state(self._frame("Exploud"), 100.0)
        self._state(self._frame("Exploud", moves=True), 100.1)
        zero = {**self._frame("Exploud", moves=True), "enemyPokemonHealth": 0}
        self._state(zero, 100.2)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 0)
        self._state(zero, 100.3)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)
        for at, frame in ((100.4, self._frame("Exploud")), (100.5, zero), (100.6, zero)):
            self._state(frame, at)
        self._assert_inputs("Fight Button", "Move Slot 1")
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)

    def test_retained_chat_with_new_announcement_trains_a_second_encounter_without_reusing_turn_flags(self):
        self._configure()
        self._defeat("Exploud", 100.0)
        previous = self._frame("Exploud", defeated=True)["chatBoxMessage"]
        next_chat = previous + "\nA wild Exploud appeared!"
        self._state({**self._frame("Exploud"), "chatBoxMessage": next_chat}, 100.3)
        self._state({**self._frame("Exploud", moves=True), "chatBoxMessage": next_chat}, 100.4)
        self._state({
            **self._frame("Exploud", defeated=True), "chatBoxMessage": next_chat + "\nThe opposing Exploud fainted!",
        }, 100.5)
        self._assert_inputs("Fight Button", "Move Slot 1", "Fight Button", "Move Slot 1")
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 6)

    def test_restarting_capture_on_terminal_chat_does_not_recount_saved_progress(self):
        self._configure()
        self._defeat("Exploud", 100.0)
        self.overlay._reset_battle_actions()
        self._state(self._frame("Exploud", defeated=True), 101.0)
        self._state(self._frame("Exploud", defeated=True), 101.1)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)
        self._assert_inputs("Fight Button", "Move Slot 1")

    def test_target_edit_after_submitting_a_move_keeps_actual_defeat_accounting(self):
        self._configure()
        self._state(self._frame("Exploud"), 100.0)
        self._state(self._frame("Exploud", moves=True), 100.1)
        self.overlay.settings_window.ev_training_target_inputs["hp"].setValue(0)
        self.assertIsNone(self.overlay._pending_battle_action)
        self._state(self._frame("Exploud", defeated=True), 100.2)
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)
        self._assert_inputs("Fight Button", "Move Slot 1")

    def test_confirmed_manual_defeat_of_unselected_catalog_species_is_still_accounted(self):
        self._configure(names="Pidgey")
        self._state(self._frame("Exploud"), 100.0)
        self._state(self._frame("Exploud", defeated=True), 100.1)
        self._flush_run()
        self._assert_inputs()
        self.assertEqual(self.overlay.settings_window.ev_training_progress().current["hp"], 3)

    def test_completion_stops_run_walking_and_fishing_until_targets_are_extended(self):
        self._configure(current={**default_ev_values(), "hp": 6})
        settings = self.overlay.settings_window
        settings.auto_run_toggle.setChecked(True)
        self.overlay._auto_walk_controller = Mock()
        fishing = Mock()
        self.overlay._auto_fishing_monitor = fishing
        settings.auto_walk_toggle.setChecked(True)
        self._end(100.0)
        self._state(self._frame("Exploud"), 101.0)
        self._flush_run()
        self._assert_inputs()
        self.overlay._auto_walk_controller.update.assert_not_called()
        self.overlay._auto_walk_controller.stop.assert_called()
        fishing.set_configuration_paused.assert_called_with(True)
        self.assertTrue(settings.auto_battle_toggle.isChecked())
        settings.ev_training_target_inputs["hp"].setValue(9)
        self._assert_inputs()
        self._state(self._frame("Exploud"), 101.1)
        self._assert_inputs("Fight Button")
        fishing.set_configuration_paused.assert_called_with(False)


class EVTrainingProgressKeyboardTests(_EVProgressIntegrationTests, _BattleActionTestCase):
    pass


class EVTrainingProgressMouseTests(_EVProgressIntegrationTests, _BattleActionTestCase):
    use_mouse_clicking = True


class EVTrainingMonitorTests(_BattleActionTestCase):
    def test_captured_terminal_chat_ends_battle_and_two_encounters_award_six_hp(self):
        settings = self.overlay.settings_window
        settings.ev_training_pokemon_input.setText("Exploud")
        settings.set_ev_training_progress(default_ev_values(), {**default_ev_values(), "hp": 6})
        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")
        regions = (
            *self.regions,
            SavedRegion("chat-box", "Battle Chat", 0, 0, 200, 100),
            SavedRegion("pokemon-name", "Wild Pokemon", 200, 0, 100, 30),
            SavedRegion("pokemon-name", "Your Pokemon", 200, 200, 100, 30),
            SavedRegion("health", "Enemy Health", 300, 0, 100, 30),
            SavedRegion("health", "Ally Health", 300, 200, 100, 30),
            SavedRegion("turn", "Turn Number", 400, 0, 100, 30),
        )
        monitor = RealtimeRegionMonitor(regions, 42, self.overlay._target_geometry, image_capture=Mock(), text_extractor=Mock())
        self.overlay._realtime_region_monitor = monitor
        monitor.state_updated.connect(self.overlay._on_realtime_state_updated)
        display = {"phase": "menu"}

        def read_regions(_screenshot, requested, _timings):
            results = []
            for region in requested:
                text = ""
                if region.feature == "chat-box":
                    text = "A wild Exploud appeared!"
                    if display["phase"] == "fainted":
                        text += "\nThe opposing Exploud fainted!"
                elif region.feature == "health":
                    text = "0%" if region.label == "Enemy Health" and display["phase"] == "fainted" else "100%"
                elif region.feature == "pokemon-name":
                    text = "Exploud" if region.label == "Wild Pokemon" else "Pikachu"
                elif region.feature == "turn":
                    text = "Turn 1"
                elif region.feature == "moves" and display["phase"] == "moves":
                    text = "Tackle 1.0x 20/35"
                elif region.feature in ("fight", "swap", "run", "bag") and display["phase"] == "menu":
                    text = region.label
                results.append((region, OCRResult(text, 100, 30, 95.0)))
            return results

        monitor._read_regions = Mock(side_effect=read_regions)
        for battle in range(2):
            for index, phase in enumerate(("menu", "moves", "fainted", "fainted", "fainted")):
                self.now = 100.0 + battle + index / 10
                display["phase"] = phase
                state, captured_at = monitor._capture_state({"capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0})
                self.assertEqual(state["onBattleMode"], phase != "fainted")
                monitor._publish_state(state, captured_at)
            self.assertEqual(settings.ev_training_progress().current["hp"], (battle + 1) * 3)
        self.assertEqual(len(self.overlay._auto_battle_input.tap_key.call_args_list), 4)
        self.assertTrue(settings.ev_training_progress().complete)
