import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QStyleOptionViewItem

from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.battle_action import ACTION_BUTTON_STATES
from core.pokemon_targets import parse_pokemon_targets
from launcher.main_window import MainWindow
from launcher.settings_store import AppSettings, SettingsStore
from models.region_config import (
    AUTO_BATTLE_FEATURES,
    REGION_FEATURES,
    REGION_ROLE_LABELS,
    SavedRegion,
    canonical_region_role,
    merge_missing_battle_regions,
)
from overlay.appearance import set_background_transparency
from overlay.overlay_window import OverlayWindow
from overlay.region_window import RegionWindow
from overlay.settings_window import SettingsWindow, _PokemonCheckDelegate
from overlay.window_tracker import GameWindowTracker


class SynchronizeSettingsTests(unittest.TestCase):
    def test_missing_and_legacy_settings_default_to_no_synchronize_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            store = SettingsStore(path)
            self.assertEqual(store.load().synchronize_pokemon, "")
            path.write_text("auto_battle=false\nuse_mouse_clicking=true\n", encoding="utf-8")
            loaded = store.load()
            self.assertEqual(loaded.synchronize_pokemon, "")
            self.assertFalse(loaded.auto_battle)
            self.assertTrue(loaded.use_mouse_clicking)

    def test_legacy_value_and_per_species_s_targets_round_trip_without_losing_other_settings(self) -> None:
        regions = (
            SavedRegion("synchronize", "Nature Match", 458, 174, 20, 28),
            SavedRegion("fight", "Fight Button", 510, 505, 130, 40),
            SavedRegion("fishing", "Fishing Bar", 100, 200, 300, 40),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            for name in ("Abra", "Indeedee (Female)", "Saved Custom Form", ""):
                with self.subTest(name=name):
                    settings = AppSettings(
                        synchronize_pokemon=name, regions=regions,
                        desired_pokemon="[Nature Match] Eevee, Pikachu",
                        elite_pokemon_names="[Nature Match] Dragonite, [Nature Match] Indeedee (Female)",
                        use_mouse_clicking=True, overlay_background_transparency=62,
                        auto_switch_fainted_pokemon=True, auto_fishing_press_f_on_missing_zone=True,
                        auto_catching_item="Great Ball", pokemon_encounter_count=1284,
                        pokemon_encounter_rarity_counts={"c": 900, "uc": 0, "r": 12, "vr": 0, "er": 0, "l": 0},
                    )
                    store.save(settings)
                    self.assertEqual(store.load(), settings)

    def test_synchronize_remains_outside_the_24_battle_roles(self) -> None:
        self.assertIn(("synchronize", "Synchronize"), REGION_FEATURES)
        self.assertEqual(REGION_ROLE_LABELS["synchronize"], ("Nature Match",))
        self.assertNotIn("synchronize", AUTO_BATTLE_FEATURES)
        self.assertEqual(sum(len(REGION_ROLE_LABELS[feature]) for feature in AUTO_BATTLE_FEATURES), 24)
        marker = SavedRegion("synchronize", "Nature Match", 458, 174, 20, 28)
        replacement = SavedRegion("synchronize", "Nature Match", 500, 200, 30, 35)
        fight = SavedRegion("fight", "Fight Button", 510, 505, 130, 40)
        self.assertEqual(canonical_region_role(marker), "Nature Match")
        self.assertEqual(merge_missing_battle_regions((marker,), (replacement, fight)), (marker, fight))
        self.assertEqual(merge_missing_battle_regions((), (replacement,)), ())


class SynchronizePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.settings = SettingsWindow()
        self.addCleanup(self._dispose)
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.stop_for_elite_toggle.setChecked(True)
        self.settings.desired_pokemon_advanced_toggle.setChecked(True)
        self.settings.elite_pokemon_advanced_toggle.setChecked(True)
        self.settings.show_at(QRect(0, 0, 800, 600), QRect(290, 20, 220, 68))
        self.settings.settings_tabs._buttons[1].click()
        self.pickers = (self.settings.desired_pokemon_input, self.settings.elite_pokemon_input)
        self.application.processEvents()

    def _dispose(self) -> None:
        self.settings.close()
        self.settings.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def _open_picker(self, picker) -> None:
        self.settings.content_scroll_area.ensureWidgetVisible(picker.display)
        self.application.processEvents()
        QTest.mouseClick(picker.display, Qt.MouseButton.LeftButton)
        self.application.processEvents()
        self.assertTrue(picker.popup.isVisible())

    def _click_row(self, picker, name: str, *, s_only: bool) -> None:
        item = picker._items_by_name[name.casefold()]
        picker.pokemon_list.scrollToItem(item)
        row = picker.pokemon_list.visualItemRect(item)
        option = QStyleOptionViewItem()
        option.rect = row
        checkbox = (
            _PokemonCheckDelegate._synchronize_checkbox_rect(option)
            if s_only else _PokemonCheckDelegate._checkbox_rect(option)
        )
        self.assertTrue(row.contains(checkbox))
        QTest.mouseClick(picker.pokemon_list.viewport(), Qt.MouseButton.LeftButton, pos=checkbox.center())
        self.application.processEvents()

    def test_s_only_is_confined_to_the_existing_desired_and_elite_searches(self) -> None:
        self.assertEqual(len(self.settings.findChildren(QLineEdit, "desired_pokemon_search")), 4)
        for picker in (self.settings.specific_pokemon_input, self.settings.ev_training_pokemon_input):
            self.assertFalse(picker._allow_synchronize)
            self.assertTrue(picker.synchronize_help.isHidden())
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                self.assertTrue(picker.popup.isAncestorOf(picker.synchronize_help))
                self.assertIn("green Synchronize S", picker.synchronize_help.text())
                self.assertIn("not Shiny", picker.synchronize_help.text())
                self.assertGreater(picker.pokemon_list.count(), 800)
                self.assertEqual(picker.text(), "")

    def test_mouse_s_only_selects_species_unchecking_keeps_normal_and_main_uncheck_clears_both(self) -> None:
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                changed = []
                picker.textChanged.connect(changed.append)
                self._open_picker(picker)
                picker.search_input.setText("pikachu")
                item = picker._items_by_name["pikachu"]
                self._click_row(picker, "Pikachu", s_only=True)
                self.assertEqual(item.checkState(), Qt.CheckState.Checked)
                self.assertTrue(item.data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
                self.assertEqual(picker.text(), "[Nature Match] Pikachu")
                self.assertEqual(picker.display.text(), "Pikachu (S only)")
                self.assertEqual(picker.selection_count.text(), "1 selected · 1 S only")
                self._click_row(picker, "Pikachu", s_only=True)
                self.assertEqual(item.checkState(), Qt.CheckState.Checked)
                self.assertEqual(picker.text(), "Pikachu")
                self._click_row(picker, "Pikachu", s_only=True)
                self._click_row(picker, "Pikachu", s_only=False)
                self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
                self.assertFalse(item.data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
                self.assertEqual(changed, ["[Nature Match] Pikachu", "Pikachu", "[Nature Match] Pikachu", ""])
                self.assertTrue(picker.popup.isVisible())
                picker.popup.hide()

    def test_space_and_ctrl_s_toggle_the_current_row_without_closing_popup(self) -> None:
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                self._open_picker(picker)
                picker.search_input.setText("eevee")
                item = picker._items_by_name["eevee"]
                picker.pokemon_list.setCurrentItem(item)
                picker.pokemon_list.setFocus()
                QTest.keyClick(picker.pokemon_list, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(picker.text(), "[Nature Match] Eevee")
                QTest.keyClick(picker.pokemon_list, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(picker.text(), "Eevee")
                QTest.keyClick(picker.pokemon_list, Qt.Key.Key_Space)
                self.assertEqual(picker.text(), "")
                self.assertTrue(picker.popup.isVisible())
                picker.popup.hide()

    def test_search_rarity_filter_and_multiple_selections_keep_each_s_requirement(self) -> None:
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                picker.setText("[Nature Match] Eevee, Pikachu, [Nature Match] Bulbasaur")
                changed = []
                picker.textChanged.connect(changed.append)
                self._open_picker(picker)
                QTest.keyClicks(picker.search_input, "pIkAcHu")
                self.assertFalse(picker._items_by_name["pikachu"].isHidden())
                self.assertTrue(picker._items_by_name["eevee"].isHidden())
                picker.search_input.clear()
                picker.rarity_filter.setCurrentIndex(picker.rarity_filter.findData("er"))
                self.assertFalse(picker._items_by_name["bulbasaur"].isHidden())
                self.assertTrue(picker._items_by_name["ivysaur"].isHidden())
                self.assertEqual(picker.selection_count.text(), "3 selected · 2 S only")
                self.assertEqual(len(parse_pokemon_targets(picker.text())), 3)
                self.assertEqual(picker._synchronized_names, {"eevee", "bulbasaur"})
                self.assertEqual(changed, [])
                picker.popup.hide()

    def test_restoration_forms_case_variants_and_duplicate_entries_emit_only_one_save(self) -> None:
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                changed = []
                picker.textChanged.connect(changed.append)
                selection = "[ nature match ] pIKaChu, eevee, [Nature Match] Saved Custom Form"
                picker.setText(selection)
                self.assertEqual(len(changed), 1)
                self.assertEqual(picker.text(), "[Nature Match] Pikachu, Eevee, [Nature Match] Saved Custom Form")
                self.assertNotIn("[nature match] pikachu", picker._items_by_name)
                self.assertTrue(picker._items_by_name["saved custom form"].data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
                picker.setText(selection.upper())
                self.assertEqual(len(changed), 1)
                picker.setText("[Nature Match] Pikachu, Pikachu")
                self.assertEqual(picker.text(), "Pikachu")
                self.assertEqual(picker._synchronized_names, set())
                self.assertEqual(len(changed), 2)
                picker.setText("")
                picker.setText("")
                self.assertEqual(changed[-1], "")
                self.assertEqual(len(changed), 3)

    def test_select_all_shown_preserves_hidden_s_only_and_new_selections_are_ordinary(self) -> None:
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                picker.setText("[Nature Match] Eevee, [Nature Match] Pikachu")
                self._open_picker(picker)
                picker.search_input.setText("pikachu")
                changed = []
                picker.textChanged.connect(changed.append)
                self.assertTrue(picker.select_all.isChecked())
                picker.select_all.click()
                self.assertEqual(picker.text(), "[Nature Match] Eevee")
                self.assertFalse(picker._items_by_name["pikachu"].data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
                picker.select_all.click()
                self.assertEqual(picker.text(), "Pikachu, [Nature Match] Eevee")
                self.assertEqual(changed, ["[Nature Match] Eevee", "Pikachu, [Nature Match] Eevee"])
                picker.popup.hide()

    def test_escape_tab_hide_and_disable_preserve_selected_requirements(self) -> None:
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                picker.setText("[Nature Match] Eevee")
                changed = []
                picker.textChanged.connect(changed.append)
                for dismiss in (
                    lambda: QTest.keyClick(picker.search_input, Qt.Key.Key_Escape),
                    lambda: self.settings.settings_tabs._buttons[0].click(),
                    self.settings.hide,
                    lambda: picker.setEnabled(False),
                ):
                    picker.setEnabled(True)
                    self.settings.show()
                    self.settings.settings_tabs._buttons[1].click()
                    self._open_picker(picker)
                    picker.search_input.setText("pikachu")
                    dismiss()
                    self.application.processEvents()
                    self.assertFalse(picker.popup.isVisible())
                    self.assertEqual(picker.text(), "[Nature Match] Eevee")
                self.assertEqual(changed, [])

    def test_keyboard_popups_remain_focusable_and_screen_bounded_while_settings_stays_passive(self) -> None:
        for picker in self.pickers:
            with self.subTest(picker=picker.display.accessibleName()):
                for key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
                    QTest.keyClick(picker.display, key)
                    self.application.processEvents()
                    self.assertTrue(picker.popup.isVisible())
                    self.assertEqual(picker.popup.windowType(), Qt.WindowType.Popup)
                    self.assertFalse(picker.popup.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
                    self.assertTrue(self.settings.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
                    self.assertTrue(self.settings.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
                    self.assertTrue(self.settings.isVisible())
                    bounds = picker.popup.screen().availableGeometry()
                    self.assertTrue(bounds.contains(picker.popup.geometry()))
                    for control in (picker.search_input, picker.rarity_filter, picker.synchronize_help, picker.pokemon_list):
                        self.assertTrue(picker.popup.rect().contains(QRect(control.mapTo(picker.popup, QPoint()), control.size())))
                    picker.popup.hide()

    def test_owned_search_popups_are_recognized_without_native_owners(self) -> None:
        tracker = GameWindowTracker()
        tracker._user32 = Mock()
        tracker._user32.GetWindow.return_value = 0
        tracker.set_auxiliary_window(int(self.settings.winId()))
        for picker in self.pickers:
            self._open_picker(picker)
            self.assertTrue(tracker._is_overlay_foreground(int(picker.popup.winId())))
            self.assertTrue(tracker._is_overlay_foreground(int(picker.search_input.effectiveWinId())))
            picker.popup.hide()
        with patch("overlay.window_tracker.QWidget.find", return_value=None):
            self.assertFalse(tracker._is_overlay_foreground(987654321))

    def test_both_search_popups_fit_small_high_dpi_screens_and_restore_their_normal_size(self) -> None:
        for picker in self.pickers:
            for bounds in (QRect(0, 0, 640, 360), QRect(0, 0, 280, 360)):
                with self.subTest(picker=picker.display.accessibleName(), bounds=bounds):
                    screen = Mock(availableGeometry=Mock(return_value=bounds))
                    picker.setText("")
                    picker.search_input.setText("eevee")
                    with patch.object(QApplication, "screenAt", return_value=screen):
                        self._open_picker(picker)
                    self.assertTrue(bounds.contains(picker.popup.geometry()))
                    self.assertEqual(picker.popup.size(), QSize(min(340, bounds.width()), 360))
                    self.assertGreater(picker.pokemon_list.viewport().height(), 0)
                    for control in (
                        picker.search_input, picker.rarity_filter, picker.synchronize_help,
                        picker.select_all, picker.selection_count, picker.pokemon_list,
                    ):
                        self.assertTrue(picker.popup.rect().contains(
                            QRect(control.mapTo(picker.popup, QPoint()), control.size()),
                        ))
                    self._click_row(picker, "Eevee", s_only=True)
                    self.assertEqual(picker.text(), "[Nature Match] Eevee")
                    picker.popup.hide()
                    self._open_picker(picker)
                    self.assertEqual(picker.popup.size(), QSize(340, 390))
                    picker.popup.hide()

    def test_background_transparency_fades_both_popups_but_not_selected_text(self) -> None:
        for picker in self.pickers:
            picker.setText("[Nature Match] Eevee")
        for transparency in (0, 50, 100):
            set_background_transparency(self.settings, transparency)
            for picker in self.pickers:
                with self.subTest(transparency=transparency, picker=picker.display.accessibleName()):
                    self._open_picker(picker)
                    image = picker.popup.grab().toImage()
                    dpr = image.devicePixelRatio()
                    self.assertAlmostEqual(
                        image.pixelColor(round(picker.popup.width() // 2 * dpr), round(4 * dpr)).alpha(),
                        round(250 * (100 - transparency) / 100), delta=1,
                    )
                    self.assertEqual(picker.popup.windowOpacity(), 1.0)
                    self.assertEqual(picker.text(), "[Nature Match] Eevee")
                    if transparency == 100:
                        display = picker.display.grab().toImage()
                        self.assertEqual(display.pixelColor(4, 4).alpha(), 0)
                        self.assertGreaterEqual(max(
                            display.pixelColor(x, y).alpha()
                            for x in range(display.width()) for y in range(display.height())
                        ), 250)
                    picker.popup.hide()


class SynchronizeRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = RegionWindow()
        self.addCleanup(self._dispose)
        self.panel.show_for_game(QRect(20, 40, 1000, 700))
        self.panel.buttons["synchronize"].click()
        self.application.processEvents()

    def _dispose(self) -> None:
        self.panel.hover_label.deleteLater()
        self.panel.close()
        self.panel.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def test_synchronize_region_is_manual_and_species_selection_points_to_existing_settings(self) -> None:
        self.assertTrue(self.panel.synchronize_card.isVisible())
        self.assertEqual(self.panel.size(), QSize(368, 568))
        self.assertIn("optional", self.panel.header.subtitle.text())
        self.assertFalse(hasattr(self.panel, "synchronize_pokemon_input"))
        self.assertIn("game's selected Pokemon's Nature", self.panel.synchronize_description.text())
        self.assertIn("not Shiny", self.panel.synchronize_description.text())
        self.assertIn("Desired or Elite", self.panel.synchronize_description.text())
        self.assertIn("S only", self.panel.synchronize_description.text())
        requests = []
        self.panel.add_region_requested.connect(requests.append)
        self.panel.add_region_button.click()
        self.assertEqual(requests, ["synchronize"])

    def test_region_page_and_manual_controls_fit_compact_game_windows(self) -> None:
        for width, height in ((1000, 700), (640, 480), (350, 320), (320, 240), (240, 220)):
            with self.subTest(size=(width, height)):
                bounds = QRect(20, 40, width, height)
                self.panel.show_for_game(bounds)
                self.application.processEvents()
                self.assertTrue(bounds.contains(self.panel.geometry()))
                for control in (self.panel.add_region_button, self.panel.back_button, self.panel.close_button):
                    self.assertTrue(self.panel.rect().contains(QRect(control.mapTo(self.panel, QPoint()), control.size())))
                self.assertTrue(self.panel.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)

    def test_live_status_is_relative_to_the_game_and_does_not_change_regions(self) -> None:
        region = SavedRegion("synchronize", "Nature Match", 458, 174, 20, 28)
        self.panel.set_regions((region,))
        for state, expected in (
            ({}, "Waiting"),
            ({"onBattleMode": True, "wildPokemonNatureMatch": True}, "not Shiny"),
            ({"onBattleMode": True, "wildPokemonNatureMatch": False}, "No green S detected"),
            ({"onBattleMode": True}, "unavailable"),
            ({"onBattleMode": False, "wildPokemonNatureMatch": True}, "Waiting"),
        ):
            self.panel.update_synchronize_state(state)
            self.assertIn(expected, self.panel.synchronize_status.toolTip())
            self.assertEqual(self.panel.regions, [region])


class SynchronizeLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.regions = (
            SavedRegion("synchronize", "Nature Match", 458, 174, 20, 28),
            SavedRegion("fishing", "Fishing Bar", 100, 200, 300, 40),
            SavedRegion("fight", "Fight Button", 510, 505, 130, 40),
        )
        self.initial = AppSettings(
            synchronize_pokemon="Alakazam", regions=self.regions,
            use_mouse_clicking=True, overlay_background_transparency=62,
            auto_switch_fainted_pokemon=True, auto_fishing_press_f_on_missing_zone=True,
            auto_catching_item="Great Ball", pokemon_encounter_count=1284,
            pokemon_encounter_rarity_counts={"c": 900, "r": 12},
            desired_pokemon="[Nature Match] Pikachu, Eevee",
            elite_pokemon_names="[Nature Match] Dragonite",
        )
        self.store = Mock(spec=SettingsStore)
        self.store.path = Path("unused-synchronize-test-settings.txt")
        self.store.load.return_value = self.initial
        with patch("launcher.main_window.GlobalHotkeyManager.register", return_value=True), patch(
            "overlay.overlay_window.BattleHistory",
        ):
            self.window = MainWindow(settings_store=self.store)
        self.addCleanup(self._dispose)
        self.overlay = self.window.overlay_window
        self.panel = self.overlay.region_window
        self.overlay._auto_battle_input = Mock()

    def _dispose(self) -> None:
        self.application.removeNativeEventFilter(self.window.hotkey_manager)
        self.window.hotkey_manager.unregister()
        self.window.tray_controller.hide()
        overlay = self.window.overlay_window
        overlay.stop_tracking()
        for widget in (
            overlay.region_window.hover_label, overlay.fishing_preview_window,
            overlay.region_test_log_window, overlay.region_selector, overlay, self.window,
        ):
            widget.hide()
            widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def test_startup_restores_nested_requirements_and_keeps_legacy_value_without_importing_it(self) -> None:
        settings = self.overlay.settings_window
        self.assertEqual(self.window._legacy_synchronize_pokemon, "Alakazam")
        self.assertEqual(settings.desired_pokemon_input.text(), "[Nature Match] Pikachu, Eevee")
        self.assertEqual(settings.elite_pokemon_input.text(), "[Nature Match] Dragonite")
        self.assertTrue(settings.desired_pokemon_input._items_by_name["pikachu"].data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
        self.assertTrue(settings.elite_pokemon_input._items_by_name["dragonite"].data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
        self.assertNotIn("alakazam", settings.desired_pokemon_input._selected_names)
        self.assertNotIn("alakazam", settings.elite_pokemon_input._selected_names)
        self.assertEqual(self.panel.regions, list(self.regions))
        self.assertEqual(settings.property("_overlay_background_transparency"), 62)
        self.store.save.assert_not_called()
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])

    def test_each_picker_s_only_change_and_clear_are_saved_live_without_input_or_other_changes(self) -> None:
        summary = self.overlay.pokemon_encounter_summary_window
        summary._elapsed_session_ms = 123_000
        summary.start_encounter("Eevee", "#4ADE80")
        settings = self.overlay.settings_window
        for picker, field, species, s_only, expected in (
            (settings.desired_pokemon_input, "desired_pokemon", "eevee", True,
             "[Nature Match] Pikachu, [Nature Match] Eevee"),
            (settings.elite_pokemon_input, "elite_pokemon_names", "dragonite", False, "Dragonite"),
        ):
            with self.subTest(field=field):
                self.store.save.reset_mock()
                other_field = "elite_pokemon_names" if field == "desired_pokemon" else "desired_pokemon"
                other_picker = settings.elite_pokemon_input if field == "desired_pokemon" else settings.desired_pokemon_input
                other_selection = other_picker.text()
                picker._items_by_name[species].setData(_PokemonCheckDelegate.NATURE_MATCH_ROLE, s_only)
                self.store.save.assert_called_once()
                saved = self.store.save.call_args.args[0]
                self.assertEqual(getattr(saved, field), expected)
                self.assertEqual(getattr(saved, other_field), other_selection)
                self.assertEqual(saved.synchronize_pokemon, "Alakazam")
                self.assertEqual(saved.regions, self.regions)
                for preference in (
                    "use_mouse_clicking", "overlay_background_transparency", "auto_switch_fainted_pokemon",
                    "auto_fishing_press_f_on_missing_zone", "auto_catching_item", "pokemon_encounter_count",
                ):
                    self.assertEqual(getattr(saved, preference), getattr(self.initial, preference), preference)
                self.assertEqual(saved.pokemon_encounter_rarity_counts["r"], 12)
                self.assertEqual(summary._elapsed_session_ms, 123_000)
                self.assertIn("Eevee", summary.current_pokemon_label.text())
                self.store.save.reset_mock()
                picker.setText("")
                self.store.save.assert_called_once()
                self.assertEqual(getattr(self.store.save.call_args.args[0], field), "")
                self.assertEqual(self.store.save.call_args.args[0].synchronize_pokemon, "Alakazam")
                self.assertEqual(self.store.save.call_args.args[0].regions, self.regions)
                self.assertEqual(self.overlay._auto_battle_input.method_calls, [])


class SynchronizeAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        with patch("overlay.overlay_window.BattleHistory"):
            self.overlay = OverlayWindow()
        self.addCleanup(self._dispose)
        self.overlay._target_window_id = 42
        self.overlay._target_geometry = QRect(0, 0, 800, 600)
        self.overlay._game_window_visible = True
        self.overlay._realtime_capture_requested = True
        self.overlay._auto_battle_input = Mock()
        self.overlay._play_encounter_sound = Mock()
        self.overlay.region_test_log_window.append_entry = Mock()
        self.overlay._pokemon_rarity_by_name = {"eevee": "r"}
        self.settings = self.overlay.settings_window
        self.settings.auto_battle_toggle.setChecked(False)
        self.settings.auto_close_special_pokemon_message_toggle.setChecked(False)
        self.overlay._battle_history = None
        self.now = 101.0
        clock = patch("overlay.overlay_window.time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.overlay.region_window.set_regions(tuple(
            SavedRegion(feature, label, 100 + index * 40, 400, 32, 28)
            for index, (feature, label) in enumerate((
                ("fight", "Fight Button"), ("run", "Run Button"), ("bag", "Bag Button"),
                ("moves", "Move Slot 1"), ("bag", "Use Item Button"), ("bag", "Next Button"),
                ("pokemon-team", "Pokemon Team Slot 2"),
            ))
        ))

    def _dispose(self) -> None:
        self.overlay.stop_tracking()
        for widget in (
            self.overlay.region_window.hover_label, self.overlay.fishing_preview_window,
            self.overlay.region_test_log_window, self.overlay.region_selector, self.overlay,
        ):
            widget.hide()
            widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def state(self, **changes) -> dict[str, object]:
        return {
            "captureStartedAt": self.now, "onBattleMode": True, "wildPokemonName": "[Nature Match] Eevee",
            "wildPokemonNatureMatch": True, "wildPokemonIsShiny": False, "wildPokemonIsElite": False,
            "showingSpecialPokemonMessage": False, "clickStateReliable": True, "turn": 1,
            "yourPokemonName": "Abra", "yourPokemonHealth": 100, "enemyPokemonHealth": 100,
            **{visible: True for _, visible in ACTION_BUTTON_STATES.values()},
            **{clicked: False for clicked, _ in ACTION_BUTTON_STATES.values()},
            **changes,
        }

    def push(self, **changes) -> dict[str, object]:
        self.now += 0.25
        state = self.state(**changes)
        self.overlay._on_realtime_state_updated(state)
        return state

    def input_roles(self) -> list[str]:
        if self.settings.use_mouse_clicking_toggle.isChecked():
            return [call.args[1].label for call in self.overlay._auto_battle_input.click_region.call_args_list]
        return [chr(call.args[1]) for call in self.overlay._auto_battle_input.tap_key.call_args_list]

    def logs(self) -> str:
        return "\n".join(call.args[1] for call in self.overlay.region_test_log_window.append_entry.call_args_list)

    def test_nature_match_is_not_a_shiny_elite_stop_or_catch_filter(self) -> None:
        self.settings.auto_catching_toggle.setChecked(True)
        self.settings.stop_for_shiny_toggle.setChecked(True)
        self.settings.stop_for_elite_toggle.setChecked(True)
        self.settings.auto_catching_shiny_toggle.setChecked(True)
        self.settings.auto_catching_elite_toggle.setChecked(True)
        state = self.state()
        self.assertFalse(self.overlay._is_stop_battle_encounter(state))
        self.assertFalse(self.overlay._auto_catch_matches_filters(state, "Eevee"))
        for trait in ("wildPokemonIsShiny", "wildPokemonIsElite"):
            with self.subTest(trait=trait):
                separate_trait = {**state, trait: True}
                self.assertTrue(self.overlay._is_stop_battle_encounter(separate_trait))
                self.assertTrue(self.overlay._auto_catch_matches_filters(separate_trait, "Eevee"))
        self.settings.auto_catching_shiny_toggle.setChecked(False)
        self.settings.auto_catching_elite_toggle.setChecked(False)
        self.assertTrue(self.overlay._auto_catch_matches_filters(state, "Eevee"))
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])

    def test_s_only_selection_is_not_an_ordinary_species_filter(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("Pikachu")
        self.assertIsNone(self.overlay._matched_desired_pokemon(self.state()))
        self.assertFalse(self.overlay._is_stop_battle_encounter(self.state()))
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee, Pikachu")
        self.assertEqual(self.overlay._matched_desired_pokemon(self.state()), "eevee")
        self.assertTrue(self.overlay._is_stop_battle_encounter(self.state()))
        self.assertIsNone(self.overlay._matched_desired_pokemon(self.state(wildPokemonNatureMatch=False)))
        self.assertIsNone(self.overlay._matched_desired_pokemon(self.state(wildPokemonNatureMatch=None)))
        self.assertEqual(self.overlay._matched_desired_pokemon(self.state(
            wildPokemonName="Pikachu", wildPokemonNatureMatch=False,
        )), "pikachu")
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])

    def test_elite_s_only_requires_the_selected_species_elite_and_confirmed_nature_match(self) -> None:
        self.settings.stop_for_elite_toggle.setChecked(True)
        self.settings.elite_pokemon_input.setText("[Nature Match] Eevee, Pikachu")
        for elite in (False, True):
            for nature in (False, None, True):
                with self.subTest(elite=elite, nature=nature):
                    self.assertEqual(self.overlay._should_stop_for_elite_pokemon(self.state(
                        wildPokemonIsElite=elite, wildPokemonNatureMatch=nature,
                    )), elite and nature is True)
        self.assertTrue(self.overlay._should_stop_for_elite_pokemon(self.state(
            wildPokemonName="Pikachu", wildPokemonIsElite=True, wildPokemonNatureMatch=False,
        )))
        self.assertFalse(self.overlay._should_stop_for_elite_pokemon(self.state(
            wildPokemonName="Dragonite", wildPokemonIsElite=True,
        )))

    def test_s_only_is_exact_for_forms_but_ordinary_desired_matching_is_unchanged(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Raichu")
        alolan = self.state(wildPokemonName="Raichu (Alolan)")
        self.assertIsNone(self.overlay._matched_desired_pokemon(alolan))
        self.settings.desired_pokemon_input.setText("[Nature Match] Raichu (Alolan)")
        self.assertEqual(self.overlay._matched_desired_pokemon(alolan), "raichu (alolan)")
        self.settings.desired_pokemon_input.setText("Raichu")
        self.assertEqual(self.overlay._matched_desired_pokemon(alolan), "raichu")

    def test_confirmed_s_only_targets_stop_battle_and_run_without_new_inputs_in_both_input_modes(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        for mouse in (False, True):
            for auto_run in (False, True):
                with self.subTest(mouse=mouse, auto_run=auto_run):
                    self.overlay._reset_battle_actions()
                    self.settings.use_mouse_clicking_toggle.setChecked(mouse)
                    self.settings.auto_run_toggle.setChecked(auto_run)
                    self.settings.auto_battle_toggle.setChecked(not auto_run)
                    self.push()
                    self.assertEqual(self.overlay._nature_matched_encounter_identity, "eevee")
                    self.assertEqual(self.overlay._auto_battle_input.method_calls, [])
                    self.assertIn("green S (Synchronize)", self.logs())
                    self.assertTrue((self.settings.auto_run_toggle if auto_run else self.settings.auto_battle_toggle).isChecked())

    def test_confirmed_absence_allows_ordinary_battle_or_run_with_existing_input_mapping(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        for mouse in (False, True):
            for auto_run in (False, True):
                with self.subTest(mouse=mouse, auto_run=auto_run):
                    self.overlay._reset_battle_actions()
                    self.overlay._auto_battle_input.reset_mock()
                    self.settings.use_mouse_clicking_toggle.setChecked(mouse)
                    self.settings.auto_run_toggle.setChecked(auto_run)
                    self.settings.auto_battle_toggle.setChecked(not auto_run)
                    self.push(wildPokemonNatureMatch=False)
                    if auto_run:
                        self.overlay._auto_run_timer.stop()
                        self.overlay._click_auto_run()
                    self.assertEqual(self.input_roles(), [
                        ("Run Button" if auto_run else "Fight Button") if mouse else ("4" if auto_run else "1"),
                    ])

    def test_unknown_nature_for_a_possible_target_blocks_fight_run_catch_and_replacement(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        for feature in ("battle", "run", "catch", "switch"):
            with self.subTest(feature=feature):
                self.overlay._reset_battle_actions()
                self.settings.auto_run_toggle.setChecked(feature == "run")
                self.settings.auto_battle_toggle.setChecked(feature == "battle")
                self.settings.auto_catching_toggle.setChecked(feature == "catch")
                self.settings.auto_switch_fainted_pokemon_toggle.setChecked(feature == "switch")
                state = self.push(wildPokemonNatureMatch=None, yourPokemonHealth=0 if feature == "switch" else 100)
                self.assertTrue(self.overlay._awaiting_target_nature_match(state))
                self.assertFalse(self.overlay._auto_run_timer.isActive())
                self.overlay._click_auto_run()
                self.assertEqual(self.overlay._auto_battle_input.method_calls, [])
                self.assertIn("readable Synchronize evidence", self.logs())

    def test_unknown_nature_does_not_block_other_species_forms_or_ordinary_targets(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        for selection, species in (
            ("[Nature Match] Eevee", "Pikachu"),
            ("[Nature Match] Raichu", "Raichu (Alolan)"),
            ("[Nature Match] Eevee, Eevee", "Eevee"),
            ("[Nature Match] Raichu (Alolan), Raichu", "Raichu (Alolan)"),
        ):
            with self.subTest(selection=selection, species=species):
                self.settings.desired_pokemon_input.setText(selection)
                self.assertFalse(self.overlay._awaiting_target_nature_match(self.state(
                    wildPokemonName=species, wildPokemonNatureMatch=None,
                )))

    def test_unknown_name_waits_only_when_an_enabled_s_only_stop_rule_could_apply(self) -> None:
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        unknown = self.state(wildPokemonName=None, wildPokemonNatureMatch=None)
        self.assertFalse(self.overlay._awaiting_target_nature_match(unknown))
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.assertTrue(self.overlay._awaiting_target_nature_match(unknown))
        self.settings.stop_for_desired_pokemon_toggle.setChecked(False)
        self.settings.stop_for_elite_toggle.setChecked(True)
        self.settings.elite_pokemon_input.setText("[Nature Match] Eevee")
        self.assertFalse(self.overlay._awaiting_target_nature_match(unknown))
        self.assertTrue(self.overlay._awaiting_target_nature_match({**unknown, "wildPokemonIsElite": True}))

    def test_new_s_evidence_cancels_pending_fight_and_run_before_retrying(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        for mouse in (False, True):
            for auto_run in (False, True):
                with self.subTest(mouse=mouse, auto_run=auto_run):
                    self.overlay._reset_battle_actions()
                    self.overlay._auto_battle_input.reset_mock()
                    self.settings.use_mouse_clicking_toggle.setChecked(mouse)
                    self.settings.auto_run_toggle.setChecked(auto_run)
                    self.settings.auto_battle_toggle.setChecked(not auto_run)
                    self.push(wildPokemonNatureMatch=False)
                    if auto_run:
                        self.overlay._auto_run_timer.stop()
                        self.overlay._click_auto_run()
                    pending = self.overlay._pending_battle_action
                    self.assertIsNotNone(pending)
                    self.now += 3.0
                    self.push(wildPokemonNatureMatch=None)
                    self.assertIs(self.overlay._pending_battle_action, pending)
                    self.assertEqual(len(self.input_roles()), 1)
                    self.push(wildPokemonNatureMatch=True)
                    self.assertIsNone(self.overlay._pending_battle_action)
                    self.assertEqual(len(self.input_roles()), 1)

    def test_confirmed_encounter_proof_survives_marker_loss_and_changed_ally_while_status_stays_live(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        self.settings.auto_battle_toggle.setChecked(True)
        self.push()
        for evidence in (False, None):
            state = self.push(wildPokemonName="Eevee", wildPokemonNatureMatch=evidence, yourPokemonName="Alakazam")
            self.assertIs(self.overlay._encounter_nature_match(state), True)
            self.assertEqual(self.overlay._matched_desired_pokemon(state), "eevee")
            self.assertFalse(self.overlay._awaiting_target_nature_match(state))
        self.assertIn("unavailable", self.overlay.region_window.synchronize_status.toolTip())
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])

    def test_proof_clears_after_three_exit_polls_not_dropouts_and_next_same_species_is_independent(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        self.settings.auto_battle_toggle.setChecked(True)
        self.push()
        for _ in range(2):
            self.push(onBattleMode=False, wildPokemonNatureMatch=False)
            self.assertEqual(self.overlay._nature_matched_encounter_identity, "eevee")
        restored = self.push(wildPokemonNatureMatch=False)
        self.assertIs(self.overlay._encounter_nature_match(restored), True)
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])
        for _ in range(3):
            self.push(onBattleMode=False, wildPokemonNatureMatch=False)
        self.assertIsNone(self.overlay._nature_matched_encounter_identity)
        next_encounter = self.push(wildPokemonNatureMatch=False)
        self.assertIsNone(self.overlay._matched_desired_pokemon(next_encounter))
        self.assertEqual(self.input_roles(), ["1"])

    def test_different_species_cannot_inherit_encounter_nature_proof(self) -> None:
        self.push()
        changed = self.push(wildPokemonName="Pikachu", wildPokemonNatureMatch=False)
        self.assertIsNone(self.overlay._nature_matched_encounter_identity)
        self.assertIs(self.overlay._encounter_nature_match(changed), False)

    def test_s_only_catch_filters_use_the_same_requirements_and_keep_existing_or_behavior(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee, Pikachu")
        self.settings.auto_catching_desired_toggle.setChecked(True)
        for nature in (None, False, True):
            self.assertEqual(self.overlay._auto_catch_matches_filters(self.state(wildPokemonNatureMatch=nature), "Eevee"), nature is True)
        self.assertTrue(self.overlay._auto_catch_matches_filters(self.state(wildPokemonNatureMatch=False), "Pikachu"))
        self.settings.auto_catching_desired_toggle.setChecked(False)
        self.settings.stop_for_elite_toggle.setChecked(True)
        self.settings.elite_pokemon_input.setText("[Nature Match] Eevee")
        self.settings.auto_catching_elite_toggle.setChecked(True)
        for nature, elite in ((False, True), (True, False), (True, True)):
            self.assertEqual(self.overlay._auto_catch_matches_filters(self.state(
                wildPokemonNatureMatch=nature, wildPokemonIsElite=elite,
            ), "Eevee"), nature and elite)
        self.settings.stop_for_shiny_toggle.setChecked(True)
        self.settings.auto_catching_shiny_toggle.setChecked(True)
        self.assertTrue(self.overlay._auto_catch_matches_filters(self.state(
            wildPokemonNatureMatch=False, wildPokemonIsShiny=True,
        ), "Eevee"))

    def test_s_only_catch_cycle_keeps_proof_when_the_marker_disappears_in_bag(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        self.settings.auto_catching_desired_toggle.setChecked(True)
        self.settings.auto_catching_toggle.setChecked(True)
        self.settings.auto_catching_item_selection.setCurrentText("Ultra Ball")
        for mouse in (False, True):
            with self.subTest(mouse=mouse):
                self.overlay._reset_battle_actions()
                self.overlay._auto_battle_input.reset_mock()
                self.settings.use_mouse_clicking_toggle.setChecked(mouse)
                self.push()
                self.assertEqual(self.overlay._auto_catch_phase, "opening_bag")
                self.push(
                    **{visible: False for _, visible in ACTION_BUTTON_STATES.values()},
                    clickedBagButton=True, showingBagItemMenu=True, bagItemName="Ultra Ball",
                    wildPokemonNatureMatch=False,
                )
                self.assertEqual(self.overlay._auto_catch_phase, "using_item")
                self.push(wildPokemonNatureMatch=False)
                self.push(wildPokemonNatureMatch=False)
                self.assertEqual(self.overlay._auto_catch_phase, "item_used")
                self.push(
                    onBattleMode=False, wildPokemonNatureMatch=False,
                    chatBoxMessage="A wild Eevee appeared!\nCongratulations, you caught the opposing Eevee.",
                )
                self.assertEqual(self.overlay._auto_catch_phase, "idle")
                self.assertEqual(self.input_roles(), ["Bag Button", "Use Item Button"] if mouse else ["2", "3"])
                self.assertIn("opposing Pokemon was caught", self.logs())

    def test_late_s_evidence_refreshes_desired_alert_once_without_new_counts_or_latching_live_status(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        monitor = RealtimeRegionMonitor((), 42, QRect(0, 0, 800, 600), image_capture=Mock(), text_extractor=Mock(), parent=self.overlay)
        self.overlay._realtime_capture_requested = True
        self.overlay._realtime_region_monitor = monitor
        monitor.state_updated.connect(self.overlay._on_realtime_state_updated)
        monitor.pokemon_encountered.connect(self.overlay._on_pokemon_encountered)
        monitor.pokemon_encounter_updated.connect(self.overlay._on_pokemon_encounter_updated)
        for nature in (None, True, False, None, True):
            self.now += 0.25
            monitor._publish_state(self.state(wildPokemonNatureMatch=nature), "12:00:00 PM")
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.assertEqual(self.overlay.pokemon_encounter_rarity_counts["r"], 1)
        self.overlay._play_encounter_sound.assert_called_once_with("desired")
        self.assertEqual(self.overlay._current_encounter_category, "desired")
        self.assertNotIn("Nature Match", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text())
        for _ in range(3):
            self.now += 0.25
            monitor._publish_state(self.state(onBattleMode=False, wildPokemonNatureMatch=False), "12:00:01 PM")
        self.now += 0.25
        monitor._publish_state(self.state(wildPokemonNatureMatch=False), "12:00:02 PM")
        self.assertEqual(self.overlay.pokemon_encounter_count, 2)
        self.assertIsNone(self.overlay._current_encounter_category)
        self.assertEqual(self.overlay._play_encounter_sound.call_count, 1)

    def test_pre_input_s_evidence_cannot_create_encounter_proof_or_pause_a_non_target(self) -> None:
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        self.overlay._last_input_completed_at = self.now
        self.overlay._on_realtime_state_updated(self.state())
        self.assertIsNone(self.overlay._nature_matched_encounter_identity)
        self.assertEqual(self.overlay._latest_realtime_state, {})
        self.assertIn("Waiting", self.overlay.region_window.synchronize_status.toolTip())
        self.push()
        self.assertEqual(self.overlay._nature_matched_encounter_identity, "eevee")

    def test_nature_match_keeps_species_summary_rarity_counts_and_collapsed_metrics_without_alerts(self) -> None:
        summary = self.overlay.pokemon_encounter_summary_window
        summary._elapsed_session_ms = 123_000
        self.overlay._on_pokemon_encountered("[Nature Match] Eevee", self.state())
        self.overlay._on_pokemon_encounter_updated("Eevee", self.state(wildPokemonNatureMatch=False))
        for collapsed in (True, False):
            summary.collapse_button.setChecked(collapsed)
            self.application.processEvents()
            self.assertIn("Eevee", summary.current_pokemon_label.text())
            self.assertNotIn("Shiny", summary.current_pokemon_label.text())
            self.assertNotIn("Nature Match", summary.current_pokemon_label.text())
            self.assertEqual(summary.daily_count_label.text(), "1")
            self.assertEqual(summary.timer_label.text(), "00:02:03")
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.assertEqual(self.overlay.pokemon_encounter_rarity_counts["r"], 1)
        self.overlay._play_encounter_sound.assert_not_called()
        self.overlay._on_pokemon_encounter_updated("Eevee", self.state(wildPokemonIsShiny=True))
        self.assertIn("Shiny Eevee", summary.current_pokemon_label.text())
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.overlay._play_encounter_sound.assert_called_once_with("shiny")

    def test_synchronize_status_ignores_pre_input_frames_but_updates_on_fresh_live_evidence(self) -> None:
        self.overlay._last_input_completed_at = 100.0
        self.overlay._on_realtime_state_updated(self.state())
        status = self.overlay.region_window.synchronize_status
        self.assertIn("not Shiny", status.toolTip())
        self.overlay._on_realtime_state_updated(self.state(captureStartedAt=100.0, wildPokemonNatureMatch=False))
        self.assertTrue(self.overlay._latest_realtime_state["wildPokemonNatureMatch"])
        self.assertIn("not Shiny", status.toolTip())
        self.overlay._on_realtime_state_updated(self.state(captureStartedAt=102.0, wildPokemonNatureMatch=False))
        self.assertIn("No green S", status.toolTip())
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])

    def test_nature_evidence_cannot_unblock_unknown_popup_state_or_send_a_battle_action(self) -> None:
        self.settings.auto_battle_toggle.setChecked(True)
        self.overlay._on_realtime_state_updated(self.state(
            showingSpecialPokemonMessage=None, clickStateReliable=False,
            showingFightButton=True, showingPokemonButton=True, showingRunButton=True, showingBagButton=True,
        ))
        self.assertIsNone(self.overlay._latest_realtime_state["showingSpecialPokemonMessage"])
        self.assertFalse(self.overlay._latest_realtime_state["clickStateReliable"])
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])

    def test_resetting_automation_clears_proof_but_preserves_s_targets_and_optional_region(self) -> None:
        region = SavedRegion("synchronize", "Nature Match", 458, 174, 20, 28)
        self.overlay.region_window.set_regions((region,))
        self.settings.desired_pokemon_input.setText("[Nature Match] Eevee")
        self.settings.elite_pokemon_input.setText("[Nature Match] Dragonite")
        self.overlay._nature_matched_encounter_identity = "eevee"
        self.overlay.region_window.update_synchronize_state(self.state())
        self.overlay._reset_battle_actions()
        self.assertIn("Waiting", self.overlay.region_window.synchronize_status.toolTip())
        self.assertIsNone(self.overlay._nature_matched_encounter_identity)
        self.assertEqual(self.settings.desired_pokemon_input.text(), "[Nature Match] Eevee")
        self.assertEqual(self.settings.elite_pokemon_input.text(), "[Nature Match] Dragonite")
        self.assertEqual(self.overlay.region_window.regions, [region])
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])


if __name__ == "__main__":
    unittest.main()
