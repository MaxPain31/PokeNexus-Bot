import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QFrame
from PySide6.QtTest import QTest

from capture.realtime_region_monitor import RealtimeRegionMonitor
from launcher.settings_store import AppSettings, SettingsStore
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from overlay.overlay_window import OverlayWindow
from overlay.settings_window import SettingsWindow


class AutoCatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_auto_switch_is_checked_before_auto_catching(self) -> None:
        settings = SimpleNamespace(
            auto_walk_toggle=Mock(),
            auto_catching_toggle=Mock(),
            auto_switch_fainted_pokemon_toggle=Mock(),
        )
        settings.auto_walk_toggle.isChecked.return_value = False
        settings.auto_catching_toggle.isChecked.return_value = True
        settings.auto_switch_fainted_pokemon_toggle.isChecked.return_value = True
        overlay = SimpleNamespace(
            sender=lambda: None,
            _latest_realtime_state={},
            _last_input_completed_at=0.0,
            _cancel_queued_auto_run=Mock(),
            _handle_special_pokemon_message=Mock(return_value=False),
            _update_ev_training_progress=Mock(),
            _update_encounter_nature_match=Mock(),
            _awaiting_target_nature_match=Mock(return_value=False),
            _update_pending_battle_action=Mock(return_value=False),
            _update_fishing_battle_state=Mock(),
            region_window=Mock(),
            _update_battle_move_history=Mock(),
            _sync_auto_walk=Mock(),
            _log_battle_wait=Mock(),
            settings_window=settings,
            _realtime_capture_requested=True,
            _is_player_faint_confirmed=Mock(return_value=False),
            _handle_auto_switch=Mock(return_value=True),
            _handle_auto_catching=Mock(),
        )

        OverlayWindow._on_realtime_state_updated(
            overlay,
            {"onBattleMode": True, "yourPokemonHealth": 0},
        )

        overlay._handle_auto_switch.assert_called_once()
        overlay._handle_auto_catching.assert_not_called()

    def test_chat_confirmation_can_confirm_player_faint_when_health_is_unreadable(
        self,
    ) -> None:
        overlay = SimpleNamespace(_last_active_pokemon_name=None)

        confirmed = OverlayWindow._is_player_faint_confirmed(
            overlay,
            {
                "onBattleMode": True,
                "yourPokemonName": "Pikachu",
                "yourPokemonHealth": None,
                "chatBoxMessage": "Pikachu fainted!",
            },
        )

        self.assertTrue(confirmed)

    def test_enemy_faint_chat_does_not_confirm_player_faint(self) -> None:
        overlay = SimpleNamespace(_last_active_pokemon_name="Pikachu")

        confirmed = OverlayWindow._is_player_faint_confirmed(
            overlay,
            {
                "onBattleMode": True,
                "yourPokemonHealth": None,
                "chatBoxMessage": "Your opponent's Pikachu fainted!",
            },
        )

        self.assertFalse(confirmed)

    def test_confirmed_faint_with_unreadable_health_reaches_auto_switch(self) -> None:
        settings = SimpleNamespace(
            auto_switch_fainted_pokemon_toggle=Mock(),
        )
        settings.auto_switch_fainted_pokemon_toggle.isChecked.return_value = True
        controller = SimpleNamespace(
            update=Mock(
                return_value=SimpleNamespace(
                    exhausted=False,
                    slot=None,
                    handled=True,
                )
            )
        )
        overlay = SimpleNamespace(
            settings_window=settings,
            _auto_switch_controller=controller,
        )

        handled = OverlayWindow._handle_auto_switch(
            overlay,
            {
                "onBattleMode": True,
                "turn": 4,
                "yourPokemonHealth": None,
            },
            faint_confirmed=True,
        )

        self.assertTrue(handled)
        self.assertEqual(controller.update.call_args.kwargs["health"], 0)

    def test_auto_battle_run_switch_and_catching_can_combine(
        self,
    ) -> None:
        settings = SettingsWindow()
        settings.auto_run_toggle.setChecked(True)
        settings.auto_switch_fainted_pokemon_toggle.setChecked(True)
        settings.auto_catching_toggle.setChecked(True)

        self.assertTrue(settings.auto_battle_toggle.isEnabled())
        self.assertTrue(settings.auto_battle_toggle.isChecked())
        self.assertTrue(settings.auto_switch_fainted_pokemon_toggle.isEnabled())
        self.assertTrue(settings.auto_switch_fainted_pokemon_toggle.isChecked())
        self.assertTrue(settings.auto_run_toggle.isEnabled())
        self.assertTrue(settings.auto_run_toggle.isChecked())
        self.assertTrue(settings.auto_catching_toggle.isEnabled())
        self.assertTrue(settings.auto_catching_configuration.isHidden() is False)
        self.assertIsInstance(settings.auto_catching_configuration, QFrame)
        self.assertEqual(
            settings.auto_catching_configuration.objectName(),
            "auto_catching_configuration",
        )
        self.assertFalse(settings.auto_catching_desired_toggle.isEnabled())
        self.assertFalse(settings.auto_catching_shiny_toggle.isEnabled())
        self.assertFalse(settings.auto_catching_elite_toggle.isEnabled())
        self.assertFalse(hasattr(settings, "auto_catching_desired_input"))
        self.assertFalse(hasattr(settings, "auto_catching_elite_input"))

        settings.auto_battle_toggle.setChecked(True)
        self.assertTrue(settings.auto_battle_toggle.isChecked())
        self.assertTrue(settings.auto_run_toggle.isChecked())
        settings.auto_run_toggle.setChecked(True)
        self.assertTrue(settings.auto_battle_toggle.isChecked())
        self.assertTrue(settings.auto_switch_fainted_pokemon_toggle.isChecked())
        self.assertTrue(settings.auto_run_toggle.isChecked())
        self.assertTrue(settings.auto_catching_toggle.isChecked())
        settings.auto_switch_fainted_pokemon_toggle.setChecked(True)
        self.assertTrue(settings.auto_run_toggle.isChecked())
        self.assertTrue(settings.auto_switch_fainted_pokemon_toggle.isChecked())

        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.auto_catching_desired_toggle.setChecked(True)
        self.assertFalse(settings.desired_pokemon_advanced_toggle.isChecked())
        self.assertTrue(settings.desired_pokemon_input.isEnabled())
        self.assertFalse(settings._desired_pokemon_advanced_settings.isVisible())
        settings.stop_for_shiny_toggle.setChecked(True)
        settings.auto_catching_shiny_toggle.setChecked(True)
        settings.stop_for_elite_toggle.setChecked(True)
        settings.auto_catching_elite_toggle.setChecked(True)
        self.assertFalse(settings.elite_pokemon_advanced_toggle.isChecked())
        self.assertTrue(settings.elite_pokemon_input.isEnabled())
        self.assertFalse(settings._elite_pokemon_advanced_settings.isVisible())

        settings.stop_for_desired_pokemon_toggle.setChecked(False)
        self.assertFalse(settings.auto_catching_desired_toggle.isEnabled())
        self.assertFalse(settings.auto_catching_desired_toggle.isChecked())
        self.assertFalse(settings.desired_pokemon_input.isEnabled())

        settings.auto_catching_toggle.setChecked(False)
        self.assertTrue(settings.auto_battle_toggle.isEnabled())
        self.assertTrue(settings.auto_switch_fainted_pokemon_toggle.isEnabled())
        self.assertTrue(settings.auto_run_toggle.isEnabled())
        self.assertTrue(settings.auto_catching_configuration.isHidden())
        settings.close()

    def test_settings_height_is_fixed_and_scrolling_follows_the_active_tab(self) -> None:
        settings = SettingsWindow()
        settings.auto_catching_toggle.setChecked(True)
        settings.show_at(
            QRect(100, 100, 800, 600),
            QRect(350, 130, 216, 68),
        )
        self.application.processEvents()
        fixed_height = settings.height()

        self.assertEqual(settings.minimumHeight(), fixed_height)
        self.assertEqual(settings.maximumHeight(), fixed_height)
        self.assertLessEqual(fixed_height, 600 - 16)
        battle_scroll_maximum = settings.content_scroll_area.verticalScrollBar().maximum()
        settings.settings_tabs._buttons[4].click()
        self.application.processEvents()
        self.assertEqual(settings.content_scroll_area.verticalScrollBar().maximum(), 0)

        settings.settings_tabs._buttons[1].click()
        QTest.qWait(220)
        self.application.processEvents()

        self.assertEqual(settings.height(), fixed_height)
        self.assertGreater(settings.content_scroll_area.verticalScrollBar().maximum(), 0)

        settings.settings_tabs._buttons[0].click()
        QTest.qWait(220)
        self.application.processEvents()
        self.assertEqual(settings.height(), fixed_height)
        self.assertEqual(
            settings.content_scroll_area.verticalScrollBar().maximum(), battle_scroll_maximum,
        )
        settings.close()

    def test_settings_height_is_capped_on_tall_game_windows(self) -> None:
        settings = SettingsWindow()
        settings.show_at(
            QRect(100, 100, 1200, 1000),
            QRect(500, 130, 216, 68),
        )
        self.application.processEvents()

        self.assertEqual(settings.height(), 560)
        settings.close()

    def test_auto_catching_settings_persist(self) -> None:
        settings = AppSettings(
            auto_catching=True,
            auto_fishing=True,
            regions=(SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40),),
            auto_catching_wild_hp=65,
            auto_catching_item="Ultra Ball",
            auto_catching_desired_filter=True,
            desired_pokemon="Pikachu, Eevee",
            auto_catching_shiny_filter=True,
            auto_catching_elite_filter=True,
            elite_pokemon_names="Mewtwo",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(settings)
            loaded = store.load()
            saved_text = store.path.read_text(encoding="utf-8")

        self.assertTrue(loaded.auto_catching)
        self.assertTrue(loaded.auto_fishing)
        self.assertEqual(
            loaded.regions,
            (SavedRegion("fishing", "Fishing Bar", 10, 20, 200, 40),),
        )
        self.assertEqual(loaded.auto_catching_wild_hp, 65)
        self.assertEqual(loaded.auto_catching_item, "Ultra Ball")
        self.assertTrue(loaded.auto_catching_desired_filter)
        self.assertEqual(loaded.desired_pokemon, "Pikachu, Eevee")
        self.assertTrue(loaded.auto_catching_shiny_filter)
        self.assertTrue(loaded.auto_catching_elite_filter)
        self.assertEqual(loaded.elite_pokemon_names, "Mewtwo")
        self.assertNotIn("auto_catching_desired_pokemon=", saved_text)
        self.assertNotIn("auto_catching_elite_pokemon=", saved_text)

    def test_legacy_catch_target_names_migrate_to_advanced_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            path.write_text(
                "auto_catching_desired_pokemon=Pikachu\n"
                "auto_catching_elite_pokemon=Mewtwo\n",
                encoding="utf-8",
            )
            loaded = SettingsStore(path).load()

        self.assertEqual(loaded.desired_pokemon, "Pikachu")
        self.assertEqual(loaded.elite_pokemon_names, "Mewtwo")

    def test_bag_item_name_ocr_requires_configured_item_name_region(self) -> None:
        region = SavedRegion("bag", "Item Name", 10, 10, 100, 30)
        wrong_region = SavedRegion("bag", "Bag Button", 10, 50, 100, 30)

        self.assertEqual(
            RealtimeRegionMonitor._extract_bag_item_name(
                (
                    (wrong_region, OCRResult("Ultra Ball", 100, 30, 99, 2)),
                    (region, OCRResult(" Ultra\n Ball ", 100, 30, 99, 2)),
                )
            ),
            "Ultra Ball",
        )
        self.assertIsNone(
            RealtimeRegionMonitor._extract_bag_item_name(
                ((region, OCRResult("Ultra Ball", 100, 30, 10, 2)),)
            )
        )

    def test_auto_catch_does_not_enter_selection_when_bag_click_unconfirmed(
        self,
    ) -> None:
        region = SavedRegion("bag", "Bag Button", 10, 10, 100, 30)
        overlay = SimpleNamespace(
            region_window=SimpleNamespace(regions=(region,)),
            _submit_battle_action=Mock(return_value=False),
            _auto_battle_log_once=Mock(),
            _auto_catch_attempt_turn=None,
            _auto_catch_phase="idle",
            _auto_catch_last_item_name=None,
            _auto_catch_seen_items=set(),
        )

        self.assertFalse(
            OverlayWindow._start_auto_catch_item_selection(overlay, 1)
        )
        self.assertEqual(overlay._auto_catch_phase, "idle")
        self.assertIsNone(overlay._auto_catch_attempt_turn)

    def test_catch_confirmation_overrides_battle_mode_evidence(self) -> None:
        message = "Congratulations, you caught the opposing Ekans."

        self.assertTrue(RealtimeRegionMonitor._chat_confirms_catch(message))
        self.assertFalse(
            RealtimeRegionMonitor._is_battle_mode(
                message,
                battle_phrase_detected=True,
                enemy_health=100,
            )
        )
        self.assertFalse(
            RealtimeRegionMonitor._is_battle_mode(
                "",
                battle_phrase_detected=False,
                enemy_health=None,
            )
        )
        wild_encounter_message = "A Wild Ekans\nappeared!"
        extracted_name = RealtimeRegionMonitor._extract_wild_pokemon_name(
            wild_encounter_message
        )
        self.assertEqual(extracted_name, "Ekans")
        self.assertTrue(
            RealtimeRegionMonitor._chat_indicates_battle(
                wild_encounter_message
            )
        )
        self.assertTrue(
            RealtimeRegionMonitor._is_battle_mode(
                wild_encounter_message,
                battle_phrase_detected=True,
                enemy_health=None,
            )
        )
        self.assertTrue(
            RealtimeRegionMonitor._is_battle_mode(
                "",
                battle_phrase_detected=False,
                enemy_health=100,
            )
        )

    def test_partial_wild_pokemon_ocr_does_not_activate_battle_mode(self) -> None:
        self.assertEqual(
            RealtimeRegionMonitor._extract_wild_pokemon_name("Rol"),
            None,
        )
        self.assertFalse(
            RealtimeRegionMonitor._is_battle_mode(
                "",
                battle_phrase_detected=False,
                enemy_health=None,
            )
        )

    def test_old_catch_chat_does_not_suppress_the_next_battle_or_confirm_its_catch(self) -> None:
        caught = "Congratulations, you caught the opposing Ekans."
        for announcement in (
            "A wild Pidgey appeared!", "A Wild Ekans\nappeared!", "AWild Eevee appeared!",
        ):
            with self.subTest(announcement=announcement):
                chat = f"A wild Ekans appeared!\n{caught}\n{announcement}\nTurn 1"
                self.assertFalse(RealtimeRegionMonitor._chat_confirms_catch(chat))
                self.assertTrue(RealtimeRegionMonitor._is_battle_mode(chat, True, 100))
                self.assertFalse(OverlayWindow._auto_catch_has_new_catch_confirmation(
                    SimpleNamespace(_auto_catch_chat_before_throw=""), chat,
                ))
                self.assertTrue(RealtimeRegionMonitor._chat_confirms_catch(f"{chat}\n{caught}"))
                self.assertFalse(RealtimeRegionMonitor._is_battle_mode(f"{chat}\n{caught}", True, 100))

    def test_enabled_catch_filters_use_or_and_empty_elite_selection_means_all(
        self,
    ) -> None:
        settings = SimpleNamespace(
            auto_catching_desired_toggle=Mock(),
            stop_for_desired_pokemon_toggle=Mock(),
            desired_pokemon_input=Mock(),
            auto_catching_shiny_toggle=Mock(),
            stop_for_shiny_toggle=Mock(),
            auto_catching_elite_toggle=Mock(),
            stop_for_elite_toggle=Mock(),
            elite_pokemon_input=Mock(),
        )
        overlay = SimpleNamespace(settings_window=settings)
        overlay._match_selected_pokemon_name = (
            OverlayWindow._match_selected_pokemon_name
        )
        overlay._wild_pokemon_identity = OverlayWindow._wild_pokemon_identity
        overlay._nature_matched_encounter_identity = None
        overlay._encounter_nature_match = MethodType(OverlayWindow._encounter_nature_match, overlay)
        settings.auto_catching_desired_toggle.isChecked.return_value = True
        settings.stop_for_desired_pokemon_toggle.isChecked.return_value = True
        settings.desired_pokemon_input.text.return_value = "Pikachu"
        settings.auto_catching_shiny_toggle.isChecked.return_value = True
        settings.stop_for_shiny_toggle.isChecked.return_value = True
        settings.auto_catching_elite_toggle.isChecked.return_value = False
        settings.stop_for_elite_toggle.isChecked.return_value = False

        self.assertTrue(
            OverlayWindow._auto_catch_matches_filters(
                overlay,
                {"wildPokemonIsShiny": True},
                "Eevee",
            )
        )
        self.assertFalse(
            OverlayWindow._auto_catch_matches_filters(
                overlay,
                {"wildPokemonIsShiny": False},
                "Eevee",
            )
        )
        settings.stop_for_desired_pokemon_toggle.isChecked.return_value = False
        self.assertFalse(
            OverlayWindow._auto_catch_matches_filters(
                overlay,
                {"wildPokemonIsShiny": False},
                "Pikachu",
            )
        )

        settings.auto_catching_desired_toggle.isChecked.return_value = False
        settings.stop_for_desired_pokemon_toggle.isChecked.return_value = True
        settings.auto_catching_shiny_toggle.isChecked.return_value = False
        settings.stop_for_shiny_toggle.isChecked.return_value = False
        settings.auto_catching_elite_toggle.isChecked.return_value = True
        settings.stop_for_elite_toggle.isChecked.return_value = True
        settings.elite_pokemon_input.text.return_value = ""
        self.assertTrue(
            OverlayWindow._auto_catch_matches_filters(
                overlay,
                {"wildPokemonIsElite": True},
                "Eevee",
            )
        )
        settings.elite_pokemon_input.text.return_value = "Pikachu"
        self.assertFalse(
            OverlayWindow._auto_catch_matches_filters(
                overlay,
                {"wildPokemonIsElite": True},
                "Eevee",
            )
        )

    def test_auto_catch_priority_requires_a_matching_stop_battle_rule(self) -> None:
        settings = SimpleNamespace(
            stop_for_shiny_toggle=Mock(),
        )
        overlay = SimpleNamespace(
            settings_window=settings,
            _matched_desired_pokemon=Mock(return_value=None),
            _should_stop_for_elite_pokemon=Mock(return_value=False),
        )
        settings.stop_for_shiny_toggle.isChecked.return_value = True

        self.assertFalse(
            OverlayWindow._is_stop_battle_encounter(
                overlay,
                {"wildPokemonIsShiny": False},
            )
        )
        self.assertTrue(
            OverlayWindow._is_stop_battle_encounter(
                overlay,
                {"wildPokemonIsShiny": True},
            )
        )
        overlay._matched_desired_pokemon.return_value = "pikachu"
        self.assertTrue(
            OverlayWindow._is_stop_battle_encounter(
                overlay,
                {"wildPokemonIsShiny": False},
            )
        )

    def test_turn_one_item_cycle_browses_exact_name_uses_ball_and_detects_catch(
        self,
    ) -> None:
        settings = SimpleNamespace(
            auto_catching_toggle=Mock(),
            auto_catching_desired_toggle=Mock(),
            stop_for_desired_pokemon_toggle=Mock(),
            desired_pokemon_input=Mock(),
            auto_catching_shiny_toggle=Mock(),
            stop_for_shiny_toggle=Mock(),
            auto_catching_elite_toggle=Mock(),
            stop_for_elite_toggle=Mock(),
            elite_pokemon_input=Mock(),
            auto_catching_hp_spin=Mock(),
            auto_catching_item_selection=Mock(),
        )
        settings.auto_catching_toggle.isChecked.return_value = True
        settings.auto_catching_desired_toggle.isChecked.return_value = False
        settings.stop_for_desired_pokemon_toggle.isChecked.return_value = False
        settings.auto_catching_shiny_toggle.isChecked.return_value = True
        settings.stop_for_shiny_toggle.isChecked.return_value = True
        settings.auto_catching_elite_toggle.isChecked.return_value = False
        settings.stop_for_elite_toggle.isChecked.return_value = False
        settings.auto_catching_hp_spin.value.return_value = 100
        settings.auto_catching_item_selection.currentText.return_value = "Ultra Ball"
        regions = tuple(
            SavedRegion("bag", role, index * 40, 10, 30, 20)
            for index, role in enumerate(
                ("Bag Button", "Next Button", "Item Name", "Use Item Button")
            )
        )
        overlay = SimpleNamespace(
            settings_window=settings,
            region_window=SimpleNamespace(regions=regions),
            region_test_log_window=Mock(),
            _auto_catch_phase="idle",
            _auto_catch_identity=None,
            _auto_catch_attempt_turn=None,
            _auto_catch_last_item_name=None,
            _auto_catch_chat_before_throw=None,
            _auto_catch_seen_items=set(),
            _submit_battle_action=Mock(return_value=True),
            _auto_battle_log_once=Mock(),
            _is_stop_battle_encounter=Mock(return_value=True),
            _normalise_wild_pokemon_name=OverlayWindow._normalise_wild_pokemon_name,
            _wild_pokemon_identity=OverlayWindow._wild_pokemon_identity,
            _match_selected_pokemon_name=OverlayWindow._match_selected_pokemon_name,
            _nature_matched_encounter_identity=None,
            _normalise_item_name=OverlayWindow._normalise_item_name,
        )
        overlay._auto_catch_matches_filters = MethodType(
            OverlayWindow._auto_catch_matches_filters,
            overlay,
        )
        overlay._encounter_nature_match = MethodType(OverlayWindow._encounter_nature_match, overlay)
        overlay._start_auto_catch_item_selection = MethodType(
            OverlayWindow._start_auto_catch_item_selection,
            overlay,
        )
        overlay._auto_catch_has_new_catch_confirmation = MethodType(
            OverlayWindow._auto_catch_has_new_catch_confirmation,
            overlay,
        )
        overlay._reset_auto_catching_state = lambda: OverlayWindow._reset_auto_catching_state(
            overlay
        )

        state = {
            "onBattleMode": True,
            "turn": 2,
            "showingRunButton": False,
            "showingBagButton": True,
            "wildPokemonName": "Eevee",
            "wildPokemonIsShiny": True,
            "wildPokemonIsElite": False,
            "enemyPokemonHealth": 100,
        }
        overlay._is_stop_battle_encounter.return_value = False
        self.assertFalse(OverlayWindow._handle_auto_catching(overlay, state))
        overlay._submit_battle_action.assert_not_called()
        overlay._is_stop_battle_encounter.return_value = True

        self.assertTrue(OverlayWindow._handle_auto_catching(overlay, state))
        overlay._submit_battle_action.assert_called_once_with(
            "bag",
            2,
            "Bag Button",
            automation_name="Auto Catching",
        )
        self.assertEqual(overlay._auto_catch_phase, "opening_bag")
        # The OCR confirmation handler advances this pending phase.
        overlay._auto_catch_phase = "selecting_item"

        overlay._submit_battle_action.reset_mock()
        item_state = {
            **state, "turn": None, "bagItemName": "Poke Ball",
            "showingFightButton": False, "showingPokemonButton": False,
            "showingRunButton": False, "showingBagButton": False,
        }
        self.assertTrue(OverlayWindow._handle_auto_catching(overlay, item_state))
        overlay._submit_battle_action.assert_called_once_with(
            "bag",
            2,
            "Next Button",
            automation_name="Auto Catching",
        )

        overlay._submit_battle_action.reset_mock()
        self.assertTrue(OverlayWindow._handle_auto_catching(overlay, item_state))
        overlay._submit_battle_action.assert_not_called()

        target_item_state = {**item_state, "bagItemName": "Ultra Ball"}
        self.assertTrue(
            OverlayWindow._handle_auto_catching(overlay, target_item_state)
        )
        overlay._submit_battle_action.assert_called_once_with(
            "bag",
            2,
            "Use Item Button",
            automation_name="Auto Catching",
        )
        self.assertEqual(overlay._auto_catch_phase, "using_item")
        overlay._auto_catch_phase = "item_used"

        overlay._submit_battle_action.reset_mock()
        next_turn_state = {
            **state,
            "turn": 3,
            "showingBagButton": True,
            "enemyPokemonHealth": 75,
        }
        self.assertTrue(
            OverlayWindow._handle_auto_catching(overlay, next_turn_state)
        )
        overlay._submit_battle_action.assert_called_once_with(
            "bag",
            3,
            "Bag Button",
            automation_name="Auto Catching",
        )
        self.assertEqual(overlay._auto_catch_phase, "opening_bag")
        overlay._auto_catch_phase = "selecting_item"
        self.assertEqual(overlay._auto_catch_attempt_turn, 3)

        overlay._submit_battle_action.reset_mock()
        self.assertTrue(
            OverlayWindow._handle_auto_catching(
                overlay,
                {
                    **next_turn_state, "turn": None, "bagItemName": "Ultra Ball",
                    "showingFightButton": False, "showingPokemonButton": False,
                    "showingRunButton": False, "showingBagButton": False,
                },
            )
        )
        overlay._submit_battle_action.assert_called_once_with(
            "bag",
            3,
            "Use Item Button",
            automation_name="Auto Catching",
        )
        self.assertEqual(overlay._auto_catch_phase, "using_item")
        overlay._auto_catch_phase = "item_used"

        overlay._submit_battle_action.reset_mock()
        overlay._auto_catch_chat_before_throw = (
            "Congratulations, you caught the opposing Ekans."
        )
        previous_confirmation_state = {
            **next_turn_state,
            "turn": 3,
            "onBattleMode": True,
            "chatBoxMessage": overlay._auto_catch_chat_before_throw,
        }
        self.assertTrue(
            OverlayWindow._handle_auto_catching(
                overlay,
                previous_confirmation_state,
            )
        )
        self.assertEqual(overlay._auto_catch_phase, "item_used")
        overlay.region_test_log_window.append_entry.assert_not_called()

        confirmation_state = {
            **next_turn_state,
            "turn": 3,
            "onBattleMode": False,
            "chatBoxMessage": (
                "It shakes again...\n"
                "Congratulations, you caught the opposing Ekans.\n"
                "Congratulations, you caught the opposing Ekans."
            ),
        }
        self.assertFalse(
            OverlayWindow._handle_auto_catching(overlay, confirmation_state)
        )
        self.assertEqual(overlay._auto_catch_phase, "idle")
        overlay.region_test_log_window.append_entry.assert_called_once_with(
            "SUCCESS",
            "Auto Catching detected the battle chat confirmation that the "
            "opposing Pokemon was caught.",
        )
        overlay._submit_battle_action.assert_not_called()

        self.assertFalse(
            OverlayWindow._handle_auto_catching(
                overlay,
                {"onBattleMode": False, "turn": None},
            )
        )
        self.assertEqual(overlay._auto_catch_phase, "idle")
        overlay.region_test_log_window.append_entry.assert_called_once()


if __name__ == "__main__":
    unittest.main()
