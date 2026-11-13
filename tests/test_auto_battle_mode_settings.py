import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.auto_battle import AUTO_BATTLE_MODES
from core.ev_training import EVProgress, default_ev_values, load_ev_training_data, validate_ev_values
from launcher.main_window import MainWindow
from launcher.settings_store import AppSettings, SettingsStore
from models.region_config import SavedRegion
from overlay.settings_window import SettingsWindow, _PokemonCheckDelegate


class AutoBattleModePersistenceTests(unittest.TestCase):
    def test_new_and_legacy_files_default_to_normal_battle_without_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            store = SettingsStore(path)
            for exists in (False, True):
                if exists:
                    path.write_text("auto_battle=true\nauto_run=true\n", encoding="utf-8")
                settings = store.load()
                self.assertEqual(settings.auto_battle_mode, "Default")
                self.assertEqual(settings.auto_battle_specific_pokemon, "")
                self.assertEqual(settings.ev_training_pokemon, "")
                self.assertEqual(settings.ev_training_category, "")
                self.assertEqual(settings.ev_training_search, "")
                self.assertEqual(settings.ev_training_current, default_ev_values())
                self.assertEqual(settings.ev_training_targets, default_ev_values())
                self.assertFalse(settings.auto_battle_elite_only)
                self.assertTrue(settings.auto_battle)
                if exists:
                    self.assertTrue(settings.auto_run)

    def test_all_modes_and_simultaneous_automations_round_trip_with_other_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(AppSettings(
                desired_pokemon="[Nature Match] Eevee", elite_pokemon_names="[Nature Match] Dragonite",
                synchronize_pokemon="Alakazam", use_mouse_clicking=True,
                auto_switch_fainted_pokemon=True, auto_run=True, auto_battle=True,
                auto_fishing=True, auto_fishing_press_f_on_missing_zone=True,
                regions=(SavedRegion("synchronize", "Nature Match", 10, 20, 25, 30),),
            ))
            original = store.load()
            for mode in AUTO_BATTLE_MODES:
                with self.subTest(mode=mode):
                    expected = replace(
                        original, auto_battle_mode=mode,
                        auto_battle_specific_pokemon="Raichu (Alolan), Rattata",
                        auto_battle_elite_only=True,
                        ev_training_pokemon="Rattata, Pidgey",
                        ev_training_category="Attack + Speed", ev_training_search="pidge",
                        ev_training_current={**default_ev_values(), "hp": 4, "attack": 40, "speed": 24},
                        ev_training_targets={**default_ev_values(), "hp": 6, "attack": 252, "speed": 252},
                    )
                    store.save(expected)
                    self.assertEqual(store.load(), expected)

    def test_invalid_modes_categories_flags_and_ev_progress_are_reported(self) -> None:
        defaults = default_ev_values()
        invalid = {
            "auto_battle_mode": ("Everything",),
            "ev_training_category": ("Sp. Atk", "Unknown"),
            "auto_battle_elite_only": ("maybe",),
        }
        for key in ("ev_training_current", "ev_training_targets"):
            invalid[key] = (
                "not JSON", "true", "[]", "{}",
                json.dumps({**defaults, "hp": 252, "attack": 252, "speed": 7}),
                *(json.dumps({**defaults, "hp": value}) for value in (
                    -1, 253, False, 1.5, "6", [0, 6],
                )),
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            for key, values in invalid.items():
                for value in values:
                    with self.subTest(key=key, value=value):
                        path.write_text(f"{key}={value}\n", encoding="utf-8")
                        with self.assertRaisesRegex(ValueError, key):
                            SettingsStore(path).load()

    def test_invalid_save_does_not_overwrite_the_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(AppSettings())
            before = store.path.read_bytes()
            for changes in (
                {"auto_battle_mode": "Invalid"}, {"ev_training_category": "Invalid"},
                {"ev_training_current": {**default_ev_values(), "attack": 253}},
                {"ev_training_targets": {**default_ev_values(), "hp": 252, "attack": 252, "speed": 7}},
            ):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    store.save(replace(AppSettings(), **changes))
                self.assertEqual(store.path.read_bytes(), before)

    def test_legacy_yield_ranges_do_not_become_accumulated_progress_or_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            path.write_text(
                "auto_battle_mode=EV Training Only\nev_training_pokemon=Exploud, Pidgey\n"
                "ev_training_category=HP\n"
                f"ev_training_ranges={json.dumps({key: [0, 252] for key in default_ev_values()})}\n",
                encoding="utf-8",
            )
            store = SettingsStore(path)
            settings = store.load()
            self.assertEqual(settings.ev_training_pokemon, "Exploud, Pidgey")
            self.assertEqual(settings.ev_training_current, default_ev_values())
            self.assertEqual(settings.ev_training_targets, default_ev_values())
            store.save(settings)
            self.assertNotIn("ev_training_ranges=", path.read_text(encoding="utf-8"))


class AutoBattleModePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.settings = SettingsWindow()
        self.addCleanup(self._dispose)
        self.settings.show_at(QRect(0, 0, 800, 600), QRect(290, 20, 220, 68))
        self.application.processEvents()

    def _dispose(self) -> None:
        self.settings.close()
        self.settings.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def _open(self, picker) -> None:
        self.settings.content_scroll_area.ensureWidgetVisible(picker.display)
        self.application.processEvents()
        QTest.mouseClick(picker.display, Qt.MouseButton.LeftButton)
        self.application.processEvents()
        self.assertTrue(picker.popup.isVisible())

    def test_modes_show_only_their_configuration_and_keep_all_automation_toggles_independent(self) -> None:
        settings = self.settings
        self.assertEqual(
            tuple(settings.auto_battle_mode_selection.itemText(i) for i in range(3)), AUTO_BATTLE_MODES,
        )
        for mode in AUTO_BATTLE_MODES:
            settings.auto_battle_mode_selection.setCurrentText(mode)
            self.application.processEvents()
            self.assertEqual(settings.specific_battle_settings.isVisible(), mode == "Specific Only")
            self.assertEqual(settings.ev_training_settings.isVisible(), mode == "EV Training Only")
        settings.auto_run_toggle.setChecked(True)
        settings.auto_switch_fainted_pokemon_toggle.setChecked(True)
        settings.auto_catching_toggle.setChecked(True)
        self.assertTrue(settings.auto_battle_toggle.isChecked())
        settings.auto_battle_toggle.setChecked(False)
        self.assertFalse(settings.auto_battle_configuration.isEnabled())
        self.assertTrue(settings.auto_run_toggle.isChecked())
        self.assertTrue(settings.auto_switch_fainted_pokemon_toggle.isChecked())
        self.assertTrue(settings.auto_catching_toggle.isChecked())

    def test_battle_pickers_have_no_s_only_controls_and_right_side_clicks_select_normally(self) -> None:
        for mode, picker in (
            ("Specific Only", self.settings.specific_pokemon_input),
            ("EV Training Only", self.settings.ev_training_pokemon_input),
        ):
            with self.subTest(mode=mode):
                self.settings.auto_battle_mode_selection.setCurrentText(mode)
                picker.setText("[Nature Match] Pidgey")
                self.assertEqual(picker.text(), "Pidgey")
                self.assertTrue(picker.synchronize_help.isHidden())
                self.assertFalse(picker.pokemon_list.itemDelegate().allow_synchronize)
                self._open(picker)
                picker.search_input.setText("pidgey")
                item = picker._items_by_name["pidgey"]
                picker.pokemon_list.setCurrentItem(item)
                QTest.keyClick(picker.pokemon_list, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(picker.text(), "Pidgey")
                row = picker.pokemon_list.visualItemRect(item)
                QTest.mouseClick(
                    picker.pokemon_list.viewport(), Qt.MouseButton.LeftButton,
                    pos=QPoint(row.right() - 20, row.center().y()),
                )
                self.assertEqual(picker.text(), "")
                self.assertFalse(item.data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
                picker.popup.hide()

    def test_desired_and_battle_selections_require_manual_unchecking_in_both_directions(self) -> None:
        settings = self.settings
        desired, specific, training = (
            settings.desired_pokemon_input, settings.specific_pokemon_input, settings.ev_training_pokemon_input,
        )
        desired.setText("[Nature Match] Pidgey")
        for picker in (specific, training):
            item = picker._items_by_name["pidgey"]
            self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsEnabled)
            self.assertIn("Uncheck it there first", item.toolTip())
            picker.setText("Pidgey")
            item.setCheckState(Qt.CheckState.Checked)
            self.assertEqual(picker.text(), "")
            self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
        self.assertEqual(desired.text(), "[Nature Match] Pidgey")
        desired.setText("")
        specific.setText("Pidgey")
        training.setText("Pidgey")
        self.assertEqual(specific.text(), training.text())
        desired_item = desired._items_by_name["pidgey"]
        self.assertFalse(desired_item.flags() & Qt.ItemFlag.ItemIsEnabled)
        desired.setText("[Nature Match] Pidgey")
        desired_item.setData(_PokemonCheckDelegate.NATURE_MATCH_ROLE, True)
        self.assertEqual(desired.text(), "")
        self.assertFalse(desired_item.data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
        specific.setText("")
        self.assertFalse(desired_item.flags() & Qt.ItemFlag.ItemIsEnabled)
        training.setText("")
        self.assertTrue(desired_item.flags() & Qt.ItemFlag.ItemIsEnabled)
        desired.setText("[Nature Match] Pidgey")
        self.assertEqual(desired.text(), "[Nature Match] Pidgey")

    def test_select_all_and_keyboard_cannot_select_visible_conflicting_species(self) -> None:
        self.settings.desired_pokemon_input.setText("[Nature Match] Pidgey")
        for mode, picker in (
            ("Specific Only", self.settings.specific_pokemon_input),
            ("EV Training Only", self.settings.ev_training_pokemon_input),
        ):
            with self.subTest(mode=mode):
                self.settings.auto_battle_mode_selection.setCurrentText(mode)
                self._open(picker)
                picker.search_input.setText("pidgey")
                item = picker._items_by_name["pidgey"]
                self.assertFalse(item.isHidden())
                self.assertFalse(picker.select_all.isEnabled())
                picker.pokemon_list.setCurrentItem(item)
                QTest.keyClick(picker.pokemon_list, Qt.Key.Key_Space)
                self.assertEqual(picker.text(), "")
                picker.search_input.setText("pidge")
                picker.select_all.click()
                self.assertNotIn("pidgey", picker._selected_names)
                self.assertIn("pidgeotto", picker._selected_names)
                picker.popup.hide()

    def test_hidden_ev_selections_still_block_desired_until_cleared(self) -> None:
        settings = self.settings
        settings.ev_training_pokemon_input.setText("Pidgey")
        settings.ev_training_target_inputs["speed"].setValue(252)
        settings.ev_training_category_filter.setCurrentIndex(settings.ev_training_category_filter.findData("HP"))
        self.assertTrue(settings.ev_training_pokemon_input._items_by_name["pidgey"].isHidden())
        self.assertEqual(settings.ev_training_target_names(), "Pidgey")
        self.assertEqual(settings.ev_training_pokemon_input.text(), "Pidgey")
        self.assertFalse(settings.desired_pokemon_input._items_by_name["pidgey"].flags() & Qt.ItemFlag.ItemIsEnabled)
        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")
        settings.ev_training_reset_button.click()
        self.assertTrue(settings.desired_pokemon_input._items_by_name["pidgey"].flags() & Qt.ItemFlag.ItemIsEnabled)

    def test_elite_only_disables_elite_stop_and_catch_filter_while_preserving_its_species(self) -> None:
        settings = self.settings
        settings.stop_for_elite_toggle.setChecked(True)
        settings.auto_catching_elite_toggle.setChecked(True)
        settings.elite_pokemon_input.setText("[Nature Match] Dragonite")
        settings.auto_battle_mode_selection.setCurrentText("Specific Only")
        settings.auto_battle_elite_only_toggle.setChecked(True)
        for toggle in (settings.stop_for_elite_toggle, settings.auto_catching_elite_toggle):
            self.assertFalse(toggle.isChecked())
            self.assertFalse(toggle.isEnabled())
        self.assertFalse(settings.elite_pokemon_input.isEnabled())
        self.assertEqual(settings.elite_pokemon_input.text(), "[Nature Match] Dragonite")
        settings.auto_battle_elite_only_toggle.setChecked(False)
        self.assertTrue(settings.stop_for_elite_toggle.isEnabled())
        self.assertFalse(settings.stop_for_elite_toggle.isChecked())
        settings.auto_battle_elite_only_toggle.setChecked(True)
        settings.auto_battle_mode_selection.setCurrentText("Default")
        self.assertTrue(settings.stop_for_elite_toggle.isEnabled())

    def test_ev_catalog_is_unique_filters_categories_and_uses_search_only_for_discovery(self) -> None:
        settings = self.settings
        picker = settings.ev_training_pokemon_input
        entries = load_ev_training_data()
        self.assertEqual(picker.pokemon_list.count(), len({entry.pokemon for entry in entries}))
        self.assertEqual(settings.ev_training_category_filter.count(), 1 + len({entry.training_category for entry in entries}))
        self.assertTrue(picker.rarity_filter.isHidden())
        picker.setText("Pidgey, Gyarados, Unknown Pokemon")
        settings.set_ev_training_progress(
            default_ev_values(), {**default_ev_values(), "attack": 252, "speed": 252},
        )
        self.assertNotIn("unknown pokemon", picker._selected_names)
        settings.ev_training_category_filter.setCurrentIndex(settings.ev_training_category_filter.findData("Attack"))
        self.assertEqual(set(settings.ev_training_target_names().split(", ")), {"Pidgey", "Gyarados"})
        picker.search_input.setText("pidgey")
        self.assertTrue(picker._items_by_name["gyarados"].isHidden())
        self.assertEqual(set(settings.ev_training_target_names().split(", ")), {"Pidgey", "Gyarados"})
        settings.set_ev_training_progress(
            {**default_ev_values(), "attack": 251, "speed": 252},
            {**default_ev_values(), "attack": 252, "speed": 252},
        )
        self.assertEqual(settings.ev_training_target_names(), "")
        self.assertEqual(picker._selected_names, {"pidgey", "gyarados"})

    def test_current_and_target_fields_share_their_510_budgets_and_emit_valid_configurations(self) -> None:
        snapshots = []
        settings = self.settings
        settings.auto_battle_configuration_changed.connect(lambda: snapshots.append(settings.ev_training_progress()))
        for inputs in (settings.ev_training_current_inputs, settings.ev_training_target_inputs):
            inputs["attack"].setValue(252)
            inputs["speed"].setValue(252)
            self.assertEqual(inputs["hp"].maximum(), 6)
            inputs["hp"].setValue(999)
            self.assertEqual(inputs["hp"].value(), 6)
            for key in ("defense", "sp_attack", "sp_defense"):
                self.assertEqual(inputs[key].maximum(), 0)
        self.assertEqual(len(snapshots), 6)
        for snapshot in snapshots:
            validate_ev_values(snapshot.current)
            validate_ev_values(snapshot.targets)
        self.assertTrue(settings.ev_training_progress().complete)
        self.assertIn("510", settings.ev_training_status.text())
        self.assertTrue(all(label.text() == "0" for label in settings.ev_training_remaining_labels.values()))

    def test_every_current_and_target_field_accepts_typed_numbers_in_passive_settings(self) -> None:
        settings = self.settings
        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")
        for inputs, number in ((settings.ev_training_current_inputs, 24), (settings.ev_training_target_inputs, 60)):
            for key, spin in inputs.items():
                with self.subTest(stat=key, number=number):
                    settings.content_scroll_area.ensureWidgetVisible(spin)
                    self.application.processEvents()
                    QTest.mouseClick(spin.lineEdit(), Qt.MouseButton.LeftButton)
                    self.application.processEvents()
                    self.assertTrue(spin.popup.isVisible())
                    self.assertFalse(spin.popup.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
                    self.assertTrue(settings.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
                    spin.editor.selectAll()
                    QTest.keyClicks(spin.editor, str(number))
                    QTest.keyClick(spin.editor, Qt.Key.Key_Return)
                    self.assertFalse(spin.popup.isVisible())
                    self.assertEqual(spin.value(), number)
        self.assertEqual(tuple(settings.ev_training_progress().remaining.values()), (36,) * 6)
        self.assertTrue(all(label.text() == "36" for label in settings.ev_training_remaining_labels.values()))

    def test_ev_numeric_editor_escape_and_parent_disable_do_not_apply_unfinished_changes(self) -> None:
        settings = self.settings
        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")
        spin = settings.ev_training_target_inputs["hp"]
        spin.setValue(6)
        for close in ("escape", "disable"):
            settings.auto_battle_toggle.setChecked(True)
            spin._show_editor()
            spin.editor.selectAll()
            QTest.keyClicks(spin.editor, "252")
            if close == "escape":
                QTest.keyClick(spin.editor, Qt.Key.Key_Escape)
            else:
                settings.auto_battle_toggle.setChecked(False)
            self.assertFalse(spin.popup.isVisible())
            self.assertEqual(spin.value(), 6)

    def test_launcher_number_entry_commits_inline_without_closing_settings(self) -> None:
        settings = self.settings
        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")
        settings.show_at(QRect(0, 0, 800, 600), QRect(290, 20, 220, 68), activate=True)
        spin = settings.ev_training_current_inputs["attack"]
        settings.content_scroll_area.ensureWidgetVisible(spin)
        self.application.processEvents()
        QTest.mouseClick(spin.lineEdit(), Qt.MouseButton.LeftButton)
        QTest.keyClick(spin, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(spin, "40")
        QTest.keyClick(spin, Qt.Key.Key_Return)
        self.assertEqual(spin.value(), 40)
        self.assertTrue(settings.isVisible())
        self.assertFalse(spin.popup.isVisible())

    def test_reset_clears_ev_search_filters_and_selection_in_one_change(self) -> None:
        settings = self.settings
        settings.auto_battle_mode_selection.setCurrentText("EV Training Only")
        settings.specific_pokemon_input.setText("Pikachu")
        settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        settings.elite_pokemon_input.setText("[Nature Match] Dragonite")
        settings.ev_training_pokemon_input.setText("Rattata, Pidgey")
        settings.ev_training_pokemon_input.search_input.setText("pidge")
        settings.ev_training_category_filter.setCurrentIndex(settings.ev_training_category_filter.findData("Speed"))
        settings.set_ev_training_progress(
            {key: 1 for key in default_ev_values()}, {key: 2 for key in default_ev_values()},
        )
        changes = []
        settings.auto_battle_configuration_changed.connect(lambda: changes.append(settings.ev_training_progress()))
        settings.ev_training_reset_button.click()
        self.assertEqual(changes, [EVProgress()])
        self.assertEqual(settings.ev_training_pokemon_input.text(), "")
        self.assertEqual(settings.ev_training_pokemon_input.search_input.text(), "")
        self.assertEqual(settings.ev_training_category_filter.currentData(), "")
        self.assertEqual(settings.ev_training_progress(), EVProgress())
        self.assertEqual(settings.auto_battle_mode_selection.currentText(), "EV Training Only")
        self.assertEqual(settings.specific_pokemon_input.text(), "Pikachu")
        self.assertEqual(settings.desired_pokemon_input.text(), "[Nature Match] Eevee")
        self.assertEqual(settings.elite_pokemon_input.text(), "[Nature Match] Dragonite")

    def test_new_popups_keep_search_focus_screen_bounds_and_dismiss_on_parent_disable(self) -> None:
        for mode, picker in (
            ("Specific Only", self.settings.specific_pokemon_input),
            ("EV Training Only", self.settings.ev_training_pokemon_input),
        ):
            with self.subTest(mode=mode):
                self.settings.auto_battle_toggle.setChecked(True)
                self.settings.auto_battle_mode_selection.setCurrentText(mode)
                self._open(picker)
                self.assertTrue(self.settings.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
                self.assertFalse(picker.popup.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
                self.assertTrue(picker.popup.screen().availableGeometry().contains(picker.popup.geometry()))
                QTest.keyClicks(picker.search_input, "pidgey")
                self.assertFalse(picker._items_by_name["pidgey"].isHidden())
                self.settings.auto_battle_toggle.setChecked(False)
                self.assertFalse(picker.popup.isVisible())

    def test_mode_and_ev_controls_remain_reachable_in_a_short_game_window(self) -> None:
        settings = self.settings
        bounds = QRect(20, 40, 350, 320)
        settings.show_at(bounds, QRect(80, 52, 216, 68))
        for mode in AUTO_BATTLE_MODES:
            with self.subTest(mode=mode):
                settings.auto_battle_mode_selection.setCurrentText(mode)
                self.application.processEvents()
                controls = [settings.auto_battle_mode_selection]
                if mode == "Specific Only":
                    controls += [settings.specific_pokemon_input.display, settings.auto_battle_elite_only_toggle]
                elif mode == "EV Training Only":
                    controls += [
                        settings.ev_training_category_filter, settings.ev_training_pokemon_input.display,
                        *settings.ev_training_current_inputs.values(), *settings.ev_training_target_inputs.values(),
                        settings.ev_training_reset_button,
                    ]
                area = settings.content_scroll_area
                self.assertTrue(bounds.contains(settings.geometry()))
                self.assertGreaterEqual(area.viewport().height(), max(control.height() for control in controls))
                for control in controls:
                    center = control.mapTo(area.widget(), control.rect().center())
                    area.ensureVisible(center.x(), center.y(), control.width() // 2, control.height() // 2 + 4)
                    self.application.processEvents()
                    rect = QRect(control.mapTo(area.viewport(), QPoint()), control.size())
                    self.assertTrue(area.viewport().rect().contains(rect), (control.accessibleName(), rect))


class AutoBattleModeLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _create_window(self, mode="EV Training Only") -> tuple[MainWindow, Mock, AppSettings]:
        initial = AppSettings(
            auto_battle=True, auto_run=True, auto_switch_fainted_pokemon=True,
            auto_battle_mode=mode, auto_battle_specific_pokemon="Pidgey, Raichu (Alolan)",
            auto_battle_elite_only=True, desired_pokemon="[Nature Match] Pidgey, Eevee",
            ev_training_pokemon="Pidgey, Rattata, Gastly", ev_training_category="Speed",
            ev_training_current={**default_ev_values(), "speed": 24},
            ev_training_targets={**default_ev_values(), "speed": 252}, ev_training_search="rattata",
            stop_auto_battle_for_elite=True, auto_catching_elite_filter=True,
            elite_pokemon_names="[Nature Match] Dragonite", synchronize_pokemon="Alakazam",
            use_mouse_clicking=True, overlay_background_transparency=62,
            regions=(SavedRegion("synchronize", "Nature Match", 10, 20, 25, 30),),
        )
        store = Mock(spec=SettingsStore, path=Path("unused-auto-battle-settings.txt"))
        store.load.return_value = initial
        with patch("launcher.main_window.GlobalHotkeyManager.register", return_value=True), patch(
            "overlay.overlay_window.BattleHistory",
        ), patch("overlay.overlay_window.GameInput"):
            window = MainWindow(settings_store=store)
        self.addCleanup(self._dispose, window)
        return window, store, initial

    def _dispose(self, window) -> None:
        self.application.removeNativeEventFilter(window.hotkey_manager)
        window.hotkey_manager.unregister()
        window.tray_controller.hide()
        overlay = window.overlay_window
        overlay.stop_tracking()
        for widget in (
            overlay.region_window.hover_label, overlay.fishing_preview_window,
            overlay.region_test_log_window, overlay.region_selector, overlay, window,
        ):
            widget.hide()
            widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def test_startup_restores_configuration_and_resolves_conflicts_in_favor_of_desired(self) -> None:
        window, store, initial = self._create_window()
        settings = window.overlay_window.settings_window
        self.assertTrue(settings.auto_battle_toggle.isChecked() and settings.auto_run_toggle.isChecked())
        self.assertTrue(settings.auto_switch_fainted_pokemon_toggle.isChecked())
        self.assertEqual(settings.auto_battle_mode_selection.currentText(), "EV Training Only")
        self.assertEqual(settings.desired_pokemon_input.text(), "[Nature Match] Pidgey, Eevee")
        self.assertEqual(settings.specific_pokemon_input.text(), "Raichu (Alolan)")
        self.assertEqual(settings.ev_training_pokemon_input._selected_names, {"rattata", "gastly"})
        self.assertEqual(settings.ev_training_target_names(), "Rattata")
        self.assertEqual(settings.ev_training_progress(), EVProgress(initial.ev_training_current, initial.ev_training_targets))
        self.assertEqual(settings.ev_training_pokemon_input.search_input.text(), "rattata")
        self.assertEqual(settings.ev_training_category_filter.currentData(), "Speed")
        self.assertTrue(settings.auto_battle_elite_only_toggle.isChecked())
        self.assertEqual(settings.elite_pokemon_input.text(), "[Nature Match] Dragonite")
        store.save.assert_not_called()
        self.assertEqual(window.overlay_window._auto_battle_input.method_calls, [])

    def test_saved_elite_only_configuration_disables_incompatible_elite_stop_on_startup(self) -> None:
        window, store, _initial = self._create_window("Specific Only")
        settings = window.overlay_window.settings_window
        self.assertFalse(settings.stop_for_elite_toggle.isChecked())
        self.assertFalse(settings.stop_for_elite_toggle.isEnabled())
        self.assertFalse(settings.auto_catching_elite_toggle.isChecked())
        self.assertEqual(settings.elite_pokemon_input.text(), "[Nature Match] Dragonite")
        store.save.assert_not_called()

    def test_each_live_edit_and_reset_save_complete_valid_configuration_without_game_input(self) -> None:
        window, store, initial = self._create_window()
        settings = window.overlay_window.settings_window
        edits = (
            (lambda: settings.auto_battle_mode_selection.setCurrentText("Default"), "auto_battle_mode", "Default"),
            (lambda: settings.specific_pokemon_input.setText("Pikachu"), "auto_battle_specific_pokemon", "Pikachu"),
            (lambda: settings.auto_battle_elite_only_toggle.setChecked(False), "auto_battle_elite_only", False),
            (lambda: settings.ev_training_pokemon_input.setText("Rattata"), "ev_training_pokemon", "Rattata"),
            (lambda: settings.ev_training_pokemon_input.search_input.setText("rat"), "ev_training_search", "rat"),
            (lambda: settings.ev_training_category_filter.setCurrentIndex(0), "ev_training_category", ""),
            (lambda: settings.ev_training_current_inputs["hp"].setValue(2), "ev_training_current",
             {**initial.ev_training_current, "hp": 2}),
            (lambda: settings.ev_training_target_inputs["hp"].setValue(6), "ev_training_targets",
             {**initial.ev_training_targets, "hp": 6}),
        )
        for change, field, expected in edits:
            with self.subTest(field=field):
                store.save.reset_mock()
                change()
                store.save.assert_called_once()
                saved = store.save.call_args.args[0]
                self.assertEqual(getattr(saved, field), expected)
                validate_ev_values(saved.ev_training_current)
                validate_ev_values(saved.ev_training_targets)
                self.assertEqual(saved.regions, initial.regions)
                self.assertEqual(saved.synchronize_pokemon, "Alakazam")
                self.assertEqual(saved.use_mouse_clicking, initial.use_mouse_clicking)
                self.assertEqual(saved.overlay_background_transparency, 62)
                self.assertTrue(saved.auto_battle and saved.auto_run and saved.auto_switch_fainted_pokemon)
        store.save.reset_mock()
        settings._reset_ev_training()
        store.save.assert_called_once()
        saved = store.save.call_args.args[0]
        self.assertEqual((saved.ev_training_pokemon, saved.ev_training_search, saved.ev_training_category), ("", "", ""))
        self.assertEqual(saved.ev_training_current, default_ev_values())
        self.assertEqual(saved.ev_training_targets, default_ev_values())
        self.assertEqual(saved.auto_battle_specific_pokemon, "Pikachu")
        self.assertEqual(saved.desired_pokemon, "[Nature Match] Pidgey, Eevee")
        self.assertEqual(window.overlay_window._auto_battle_input.method_calls, [])

    def test_confirmed_defeat_live_saves_accumulated_progress_once(self) -> None:
        window, store, initial = self._create_window()
        overlay = window.overlay_window
        overlay._realtime_capture_requested = True
        alive = {
            "captureStartedAt": 100.0, "onBattleMode": True, "wildPokemonName": "Rattata",
            "yourPokemonName": "Pikachu", "yourPokemonHealth": 100, "enemyPokemonHealth": 100,
            "chatBoxMessage": "A wild Rattata appeared!",
        }
        overlay._update_ev_training_progress(alive)
        store.save.assert_not_called()
        defeated = {
            **alive, "captureStartedAt": 100.1, "enemyPokemonHealth": 0,
            "chatBoxMessage": "A wild Rattata appeared!\nThe opposing Rattata fainted!",
        }
        overlay._update_ev_training_progress(defeated)
        overlay._update_ev_training_progress({**defeated, "captureStartedAt": 100.2})
        store.save.assert_called_once()
        saved = store.save.call_args.args[0]
        self.assertEqual(saved.ev_training_current, {**initial.ev_training_current, "speed": 25})
        self.assertEqual(saved.ev_training_targets, initial.ev_training_targets)
        self.assertEqual(overlay.settings_window.ev_training_remaining_labels["speed"].text(), "227")
        self.assertEqual(saved.regions, initial.regions)
        self.assertEqual(saved.synchronize_pokemon, "Alakazam")
        self.assertEqual(overlay._auto_battle_input.method_calls, [])


if __name__ == "__main__":
    unittest.main()
