from core.auto_battle import AUTO_BATTLE_MODES
from core.ev_training import default_ev_values
from test_action_confirmation import _BattleActionTestCase


class _AutoBattleModeTests:
    def _configure(
        self, mode="Specific Only", *, specific="Pidgey", training="Pidgey",
        category="", current=None, targets=None, elite_only=False, auto_run=True,
    ) -> None:
        self.overlay._reset_battle_actions()
        self.overlay._auto_battle_input.reset_mock()
        self.overlay.region_test_log_window.append_entry.reset_mock()
        self.overlay._realtime_capture_requested = True
        self.overlay._region_selection_active = False
        settings = self.overlay.settings_window
        settings.specific_pokemon_input.setText("")
        settings.ev_training_pokemon_input.setText("")
        settings.desired_pokemon_input.setText("")
        settings.elite_pokemon_input.setText("")
        settings.auto_battle_elite_only_toggle.setChecked(False)
        settings.auto_battle_mode_selection.setCurrentText(mode)
        settings.auto_battle_toggle.setChecked(True)
        settings.auto_run_toggle.setChecked(auto_run)
        settings.auto_switch_fainted_pokemon_toggle.setChecked(False)
        settings.auto_catching_toggle.setChecked(False)
        settings.stop_for_desired_pokemon_toggle.setChecked(False)
        settings.stop_for_shiny_toggle.setChecked(False)
        settings.stop_for_elite_toggle.setChecked(False)
        settings.specific_pokemon_input.setText(specific)
        settings.ev_training_pokemon_input.setText(training)
        settings.ev_training_category_filter.setCurrentIndex(
            settings.ev_training_category_filter.findData(category),
        )
        settings.set_ev_training_progress(
            current or default_ev_values(),
            targets if targets is not None else {**default_ev_values(), "speed": 252},
        )
        settings.ev_training_pokemon_input.search_input.clear()
        settings.auto_battle_elite_only_toggle.setChecked(elite_only)

    def _flush_run(self) -> None:
        self.overlay._auto_run_timer.stop()
        self.overlay._click_auto_run()

    def _assert_inputs(self, *roles: str) -> None:
        if self.use_mouse_clicking:
            self.assertEqual(self._clicks(), list(roles))
            self.overlay._auto_battle_input.tap_key.assert_not_called()
        else:
            keys = {
                "Fight Button": "1", "Move Slot 1": "1", "Bag Button": "2",
                "Run Button": "4", "Pokemon Team Slot 2": "2",
            }
            self.assertEqual(
                [chr(call.args[1]) for call in self.overlay._auto_battle_input.tap_key.call_args_list],
                [keys[role] for role in roles],
            )
            self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_default_battles_without_selections_even_with_auto_run_enabled(self) -> None:
        self._configure("Default", specific="", training="")
        self._state({**self.menu, "wildPokemonName": None}, 100.0)
        self._flush_run()
        self._assert_inputs("Fight Button")
        self.assertEqual(self.overlay._pending_battle_action.action_name, "Fight Button")
        self._state(self.moves, 100.2)
        self._flush_run()
        self._assert_inputs("Fight Button", "Move Slot 1")
        self.assertTrue(self.overlay.settings_window.auto_run_toggle.isChecked())

    def test_specific_mode_matches_exact_species_and_forms_before_auto_run(self) -> None:
        for wild_name, target, expected in (
            ("[S] [E] Pidgey Lv. 20", "Pidgey", "Fight Button"),
            ("Pidgeotto", "Pidgey", "Run Button"),
            ("Raichu (Alolan)", "Raichu", "Run Button"),
            ("Raichu (Alolan)", "Raichu (Alolan)", "Fight Button"),
        ):
            with self.subTest(wild_name=wild_name, target=target):
                self._configure(specific=target)
                self._state({**self.menu, "wildPokemonName": wild_name}, 100.0)
                self._flush_run()
                self._assert_inputs(expected)
                self.assertEqual(self.overlay._pending_battle_action.action_name, expected)

    def test_elite_only_requires_positive_elite_evidence_for_selected_species(self) -> None:
        for name, elite, expected in (
            ("Pidgey", True, "Fight Button"),
            ("Pidgey", False, "Run Button"),
            ("Pidgey", None, None),
            ("Rattata", None, "Run Button"),
        ):
            with self.subTest(name=name, elite=elite):
                self._configure(elite_only=True)
                self._state({**self.menu, "wildPokemonName": name, "wildPokemonIsElite": elite}, 100.0)
                self._flush_run()
                self._assert_inputs(*([expected] if expected else []))

    def test_ev_training_uses_selected_eligible_species_independently_of_search(self) -> None:
        for name, expected in (
            ("Pidgey", "Fight Button"),
            ("Rattata", "Run Button"),  # Eligible, but not selected.
            ("Gyarados", "Fight Button"),  # Selected from another browsing category.
        ):
            with self.subTest(name=name):
                self._configure(
                    "EV Training Only", training="Pidgey, Gyarados", category="Speed",
                    targets={**default_ev_values(), "speed": 1, "attack": 2},
                )
                self.overlay.settings_window.ev_training_pokemon_input.search_input.setText("gastly")
                self._state({**self.menu, "wildPokemonName": name}, 100.0)
                self._flush_run()
                self._assert_inputs(expected)

    def test_no_selected_species_uses_configured_run_or_ev_modes_automatic_run(self) -> None:
        for mode in AUTO_BATTLE_MODES[1:]:
            for auto_run in (False, True):
                with self.subTest(mode=mode, auto_run=auto_run):
                    self._configure(mode, specific="", training="", auto_run=auto_run)
                    self._state(self.menu, 100.0)
                    self._flush_run()
                    self._assert_inputs(*(["Run Button"] if auto_run or mode == "EV Training Only" else []))

    def test_nonmatches_wait_when_fallback_run_is_disabled(self) -> None:
        self._configure("Specific Only", specific="Rattata", auto_run=False)
        self._state(self.menu, 100.0)
        self._flush_run()
        self._assert_inputs()
        self.assertIsNone(self.overlay._pending_battle_action)
        self.assertIn("Auto Run is off", self._logs())

    def test_disabling_auto_battle_allows_auto_run_without_its_target_evidence(self) -> None:
        self._configure(elite_only=True)
        self.overlay.settings_window.auto_battle_toggle.setChecked(False)
        self._state({**self.menu, "wildPokemonName": None, "wildPokemonIsElite": None}, 100.0)
        self._flush_run()
        self._assert_inputs("Run Button")
        self.assertTrue(self.overlay.settings_window.auto_run_toggle.isChecked())

    def test_unreadable_names_do_not_fall_through_to_auto_run(self) -> None:
        for mode in AUTO_BATTLE_MODES[1:]:
            for name in (None, "", "???"):
                with self.subTest(mode=mode, name=name):
                    self._configure(mode)
                    self._state({**self.menu, "wildPokemonName": name}, 100.0)
                    self._flush_run()
                    self._assert_inputs()
                    self.assertFalse(self.overlay._auto_run_pending)

    def test_unknown_target_evidence_pauses_fight_move_and_run_retries(self) -> None:
        for role, frame, targets in (
            ("Fight Button", self.menu, "Pidgey"),
            ("Move Slot 1", self.moves, "Pidgey"),
            ("Run Button", self.menu, "Rattata"),
        ):
            with self.subTest(role=role):
                self._configure(specific=targets)
                self._state(frame, 100.0)
                self._flush_run()
                attempt = self.overlay._pending_battle_action
                self._state({**frame, "wildPokemonName": None}, 103.0)
                self._flush_run()
                self._assert_inputs(role)
                self.assertIs(self.overlay._pending_battle_action, attempt)
                self._state(frame, 103.2)
                self._assert_inputs(role, role)
                self.assertIs(self.overlay._pending_battle_action, attempt)

    def test_live_target_and_mode_changes_cancel_a_queued_run_until_a_fresh_frame(self) -> None:
        settings = self.overlay.settings_window
        for edit in (
            lambda: settings.specific_pokemon_input.setText("Pidgey"),
            lambda: settings.auto_battle_mode_selection.setCurrentText("Default"),
            lambda: settings.auto_battle_mode_selection.setCurrentText("EV Training Only"),
        ):
            with self.subTest(edit=edit):
                self._configure(specific="Rattata")
                self._state(self.menu, 100.0)
                self.assertTrue(self.overlay._auto_run_pending)
                edit()
                self.assertFalse(self.overlay._auto_run_pending)
                self._flush_run()
                self._assert_inputs()
                self._state(self.menu, 100.2)
                self._flush_run()
                self._assert_inputs("Fight Button")

    def test_fresh_elite_evidence_cancels_the_previous_frames_queued_run(self) -> None:
        self._configure(elite_only=True)
        self._state({**self.menu, "wildPokemonIsElite": False}, 100.0)
        self.assertTrue(self.overlay._auto_run_pending)
        self._state({**self.menu, "wildPokemonIsElite": True}, 100.2)
        self._flush_run()
        self._assert_inputs("Fight Button")

    def test_live_target_changes_cancel_obsolete_fight_and_move_retries(self) -> None:
        settings = self.overlay.settings_window
        for role, frame in (("Fight Button", self.menu), ("Move Slot 1", self.moves)):
            for change in ("selection", "mode", "toggle"):
                with self.subTest(role=role, change=change):
                    self._configure(training="Rattata")
                    self._state(frame, 100.0)
                    self._assert_inputs(role)
                    if change == "selection":
                        settings.specific_pokemon_input.setText("Rattata")
                    elif change == "mode":
                        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")
                    else:
                        settings.auto_battle_toggle.setChecked(False)
                    self.assertIsNone(self.overlay._pending_battle_action)
                    self.assertIsNone(self.overlay._pending_battle_move)
                    self._flush_run()
                    self._assert_inputs(role)
                    self._state(self.menu, 103.0)
                    self._flush_run()
                    self._assert_inputs(role, "Run Button")

    def test_ev_selection_progress_target_and_reset_changes_cancel_obsolete_battle(self) -> None:
        settings = self.overlay.settings_window
        for change in ("selection", "target", "current", "reset"):
            with self.subTest(change=change):
                self._configure(
                    "EV Training Only", category="Speed",
                    targets={**default_ev_values(), "speed": 252, "attack": 252},
                )
                self._state(self.menu, 100.0)
                if change == "selection":
                    settings.ev_training_pokemon_input.setText("Gyarados")
                elif change == "target":
                    settings.ev_training_target_inputs["speed"].setValue(0)
                elif change == "current":
                    settings.ev_training_current_inputs["speed"].setValue(252)
                else:
                    settings._reset_ev_training()
                self.assertIsNone(self.overlay._pending_battle_action)
                self._assert_inputs("Fight Button")
                self._state(self.menu, 103.0)
                self._flush_run()
                self._assert_inputs("Fight Button", *([] if change == "reset" else ["Run Button"]))

    def test_increasing_ev_target_cancels_pending_run_and_battles_on_the_next_frame(self) -> None:
        self._configure("EV Training Only", targets={**default_ev_values(), "sp_attack": 252})
        self._state(self.menu, 100.0)
        self._flush_run()
        self._assert_inputs("Run Button")
        self.overlay.settings_window.ev_training_target_inputs["speed"].setValue(252)
        self.assertIsNone(self.overlay._pending_battle_action)
        self._assert_inputs("Run Button")
        self._state(self.menu, 100.2)
        self._assert_inputs("Run Button", "Fight Button")

    def test_ev_search_and_category_changes_leave_an_eligible_battle_retry_active(self) -> None:
        self._configure("EV Training Only")
        self._state(self.menu, 100.0)
        attempt = self.overlay._pending_battle_action
        self.overlay.settings_window.ev_training_pokemon_input.search_input.setText("gastly")
        self.overlay.settings_window.ev_training_category_filter.setCurrentIndex(
            self.overlay.settings_window.ev_training_category_filter.findData("HP"),
        )
        self.assertIs(self.overlay._pending_battle_action, attempt)
        self._state(self.menu, 102.1)
        self._assert_inputs("Fight Button", "Fight Button")

    def test_enabling_elite_only_pauses_unknown_fight_then_reroutes_a_confirmed_nonelite(self) -> None:
        self._configure()
        self._state(self.menu, 100.0)
        attempt = self.overlay._pending_battle_action
        self.overlay.settings_window.auto_battle_elite_only_toggle.setChecked(True)
        self._state({**self.menu, "wildPokemonIsElite": None}, 103.0)
        self._flush_run()
        self._assert_inputs("Fight Button")
        self.assertIs(self.overlay._pending_battle_action, attempt)
        self._state({**self.menu, "wildPokemonIsElite": False}, 103.2)
        self._flush_run()
        self._assert_inputs("Fight Button", "Run Button")

    def test_fresh_target_evidence_cancels_submitted_run_before_it_can_retry(self) -> None:
        for elite_only in (False, True):
            with self.subTest(elite_only=elite_only):
                self._configure(elite_only=elite_only)
                before = {
                    **self.menu, "wildPokemonName": "Pidgey" if elite_only else "Rattata",
                    "wildPokemonIsElite": False,
                }
                self._state(before, 100.0)
                self._flush_run()
                self._state({**self.menu, "wildPokemonIsElite": True}, 103.0)
                self._flush_run()
                self._assert_inputs("Run Button", "Fight Button")
                self.assertEqual(self.overlay._pending_battle_action.automation_name, "Auto Battle")

    def test_stop_rules_precede_battle_and_run_in_every_mode(self) -> None:
        settings = self.overlay.settings_window
        for mode in AUTO_BATTLE_MODES:
            for stop in ("desired", "shiny", "elite"):
                with self.subTest(mode=mode, stop=stop):
                    self._configure(mode, specific="Rattata", training="Rattata")
                    frame = {**self.menu, "wildPokemonNatureMatch": True}
                    if stop == "desired":
                        settings.desired_pokemon_input.setText("[Nature Match] Pidgey")
                        settings.stop_for_desired_pokemon_toggle.setChecked(True)
                    elif stop == "shiny":
                        settings.stop_for_shiny_toggle.setChecked(True)
                        frame["wildPokemonIsShiny"] = True
                    else:
                        settings.elite_pokemon_input.setText("[Nature Match] Pidgey")
                        settings.stop_for_elite_toggle.setChecked(True)
                        frame["wildPokemonIsElite"] = True
                    self._state(frame, 100.0)
                    self._flush_run()
                    self._assert_inputs()
                    self.assertEqual(self.overlay._auto_battle_stopped_for_desired_pokemon, "pidgey")

    def test_late_stop_or_catch_evidence_preempts_queued_and_submitted_inputs(self) -> None:
        settings = self.overlay.settings_window
        for prior in ("queued run", "Run Button", "Fight Button"):
            for catch in (False, True):
                with self.subTest(prior=prior, catch=catch):
                    self._configure(specific="Pidgey" if prior == "Fight Button" else "Rattata")
                    settings.stop_for_shiny_toggle.setChecked(True)
                    settings.auto_catching_toggle.setChecked(catch)
                    settings.auto_catching_shiny_toggle.setChecked(catch)
                    settings.auto_catching_hp_spin.setValue(100)
                    self._state(self.menu, 100.0)
                    if prior != "queued run":
                        self._flush_run()
                    self._state({**self.menu, "wildPokemonIsShiny": True}, 103.0)
                    self._flush_run()
                    expected = [] if prior == "queued run" else [prior]
                    self._assert_inputs(*expected, *(["Bag Button"] if catch else []))
                    if catch:
                        self.assertEqual(self.overlay._pending_battle_action.automation_name, "Auto Catching")
                    else:
                        self.assertIsNone(self.overlay._pending_battle_action)

    def test_faint_replacement_preempts_battle_and_run_and_keeps_the_two_second_wait(self) -> None:
        for prior in ("queued run", "Run Button", "Fight Button"):
            with self.subTest(prior=prior):
                self._configure(specific="Pidgey" if prior == "Fight Button" else "Rattata")
                self.overlay.settings_window.auto_switch_fainted_pokemon_toggle.setChecked(True)
                self._state(self.menu, 100.0)
                if prior != "queued run":
                    self._flush_run()
                expected = [] if prior == "queued run" else [prior]
                fainted = {**self.submenu, "yourPokemonHealth": 0}
                self._state(fainted, 100.2)
                self._flush_run()
                self.assertIsNone(self.overlay._pending_battle_action)
                self._state(fainted, 102.19)
                self._assert_inputs(*expected)
                self._state(fainted, 102.2)
                self._assert_inputs(*expected, "Pokemon Team Slot 2")
                self.assertEqual(self.overlay._pending_battle_action.automation_name, "Auto Switch")

    def test_fresh_blocking_frames_discard_a_queued_run_before_any_callback(self) -> None:
        for changes in (
            {"wildPokemonName": None}, {"clickStateReliable": False},
            {"showingSpecialPokemonMessage": None}, {"onBattleMode": False},
            {"turn": None},
        ):
            with self.subTest(changes=changes):
                self._configure(specific="Rattata")
                self._state(self.menu, 100.0)
                self.assertTrue(self.overlay._auto_run_pending)
                self._state({**self.menu, **changes}, 100.2)
                self._flush_run()
                self._assert_inputs()
                self.assertFalse(self.overlay._auto_run_pending)

    def test_run_callback_rechecks_live_guards_without_sending_input(self) -> None:
        settings = self.overlay.settings_window
        for guard in ("capture", "freshness", "run disabled", "battle mode", "stop", "catch", "region selection"):
            with self.subTest(guard=guard):
                self._configure(specific="Rattata")
                settings.auto_catching_toggle.setChecked(True)
                self._state({**self.menu, "wildPokemonIsShiny": True}, 100.0)
                self.assertTrue(self.overlay._auto_run_pending)
                if guard == "capture":
                    self.overlay._realtime_capture_requested = False
                elif guard == "freshness":
                    self.overlay._last_input_completed_at = 100.0
                elif guard == "run disabled":
                    settings.auto_run_toggle.setChecked(False)
                elif guard == "battle mode":
                    settings.auto_battle_mode_selection.setCurrentText("Default")
                elif guard == "stop":
                    settings.stop_for_shiny_toggle.setChecked(True)
                elif guard == "catch":
                    self.overlay._auto_catch_phase = "ready"
                else:
                    self.overlay._region_selection_active = True
                self._flush_run()
                self._assert_inputs()

    def test_stale_frames_cannot_reclassify_or_confirm_a_pending_battle_or_run(self) -> None:
        for role, target, next_name in (
            ("Fight Button", "Pidgey", "Rattata"),
            ("Run Button", "Rattata", "Rattata"),
        ):
            with self.subTest(role=role):
                self._configure(specific=target)
                self._state(self.menu, 100.0)
                self._flush_run()
                attempt = self.overlay._pending_battle_action
                self._state({**self.menu, "wildPokemonName": next_name}, 103.0, captured_at=100.0)
                self._flush_run()
                self._assert_inputs(role)
                self.assertIs(self.overlay._pending_battle_action, attempt)
                self._state({**self.menu, "wildPokemonName": next_name}, 103.2)
                self._flush_run()
                self._assert_inputs(role, "Run Button" if role == "Fight Button" else "Fight Button")

    def test_fallback_run_requires_three_distinct_reliable_battle_end_polls(self) -> None:
        self._configure(specific="Rattata")
        self._state(self.menu, 100.0)
        self._flush_run()
        self._state(self.menu, 102.1)
        self._assert_inputs("Run Button", "Run Button")
        ended = {**self.submenu, "onBattleMode": False, "turn": None, "wildPokemonName": None}
        for at, changes in (
            (102.2, {}), (102.3, {}), (102.4, {"onBattleMode": None}),
            (102.5, {}), (102.6, {"clickStateReliable": False}),
            (102.7, {}), (102.8, {}),
        ):
            self._state({**ended, **changes}, at)
            self.assertIsNotNone(self.overlay._pending_battle_action)
        self._state(ended, 102.9, captured_at=102.8)
        self.assertIsNotNone(self.overlay._pending_battle_action)
        self._state(ended, 103.0)
        self.assertIsNone(self.overlay._pending_battle_action)
        self.assertIn("confirmed Run Button", self._logs())
        self._assert_inputs("Run Button", "Run Button")


class AutoBattleModeKeyboardTests(_AutoBattleModeTests, _BattleActionTestCase):
    pass


class AutoBattleModeMouseTests(_AutoBattleModeTests, _BattleActionTestCase):
    use_mouse_clicking = True
