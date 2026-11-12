import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from launcher.main_window import MainWindow
from launcher.settings_store import AppSettings, SettingsStore
from launcher.styles.style_loader import load_stylesheet
from models.region_config import SavedRegion
from overlay.settings_window import SettingsWindow, ToggleSwitch
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class _QtUiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        # The Windows offscreen Qt plugin does not discover system fonts itself.
        font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            font_path = font_directory / filename
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
        cls.application.setFont(QFont("Segoe UI", 9))

    def _dispose_widget(self, widget) -> None:
        widget.hide()
        widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()


class LauncherRefreshTests(_QtUiTestCase):
    def setUp(self) -> None:
        self.store = Mock(spec=SettingsStore)
        self.store.path = Path("unused-test-settings.txt")
        self.store.load.return_value = AppSettings()
        with patch(
            "launcher.main_window.GlobalHotkeyManager.register", return_value=True
        ):
            self.window = MainWindow(settings_store=self.store)
        self.addCleanup(self._dispose_launcher)

    def _dispose_launcher(self) -> None:
        self.application.removeNativeEventFilter(self.window.hotkey_manager)
        self.window.hotkey_manager.unregister()
        self.window.tray_controller.hide()
        overlay = self.window.overlay_window
        overlay.stop_tracking()
        for widget in (
            overlay.region_window.hover_label,
            overlay.fishing_preview_window,
            overlay.region_test_log_window,
            overlay.region_selector,
            overlay,
        ):
            widget.hide()
            widget.deleteLater()
        self._dispose_widget(self.window)

    def _select_game(self, directory: str) -> Path:
        executable = Path(directory) / "Poke Nexus.exe"
        executable.touch()
        self.assertTrue(self.window.executable_picker.set_selected_path(executable))
        self.window.executable_picker.executable_selected.emit(executable)
        return executable

    def test_launch_action_is_visible_but_disabled_until_a_game_is_selected(self) -> None:
        self.window.show()
        self.application.processEvents()

        self.assertTrue(self.window.start_button.isVisible())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(self.window.settings_button.isEnabled())
        self.assertEqual(self.window.status_label.text(), "Choose your game")
        self.assertEqual(self.window.automation_count_label.text(), "1")

    def test_settings_can_be_opened_without_launching_the_game(self) -> None:
        self.window.process_controller.start = Mock()
        self.window.settings_button.click()
        self.application.processEvents()

        settings = self.window.overlay_window.settings_window
        self.assertTrue(settings.isVisible())
        self.window.process_controller.start.assert_not_called()
        self.assertIsNone(self.window.selected_executable)
        self.assertTrue(
            self.window.overlay_window.buttons["settings"].property("panelOpen")
        )
        settings.hide()
        self.assertFalse(
            self.window.overlay_window.buttons["settings"].property("panelOpen")
        )

    def test_background_transparency_is_restored_applied_and_saved_live(self) -> None:
        region = SavedRegion("fishing", "Fishing Bar", 10, 20, 300, 40)
        self.store.load.return_value = AppSettings(
            overlay_background_transparency=37, regions=(region,),
            pokemon_encounter_count=128, pokemon_encounter_rarity_counts={"r": 12},
            auto_fishing_press_f_on_missing_zone=True,
        )
        self.window._load_saved_settings()
        overlay = self.window.overlay_window
        settings = overlay.settings_window
        self.assertEqual(settings.overlay_background_transparency_spin.value(), 37)
        self.assertEqual(settings.overlay_background_transparency_slider.value(), 37)
        self.assertEqual(overlay.property("_overlay_background_transparency"), 37)

        self.store.save.reset_mock()
        settings.overlay_background_transparency_slider.setValue(62)

        self.store.save.assert_called_once()
        saved = self.store.save.call_args.args[0]
        self.assertEqual(saved.overlay_background_transparency, 62)
        self.assertEqual(saved.regions, (region,))
        self.assertEqual(saved.pokemon_encounter_count, 128)
        self.assertEqual(saved.pokemon_encounter_rarity_counts["r"], 12)
        self.assertTrue(saved.auto_fishing_press_f_on_missing_zone)

    def test_mouse_input_preference_defaults_to_keys_and_is_restored_and_saved_live(self) -> None:
        overlay = self.window.overlay_window
        settings = overlay.settings_window
        self.assertFalse(settings.use_mouse_clicking_toggle.isChecked())
        self.assertTrue(settings._settings_pages._pages[5].isAncestorOf(
            settings.use_mouse_clicking_toggle,
        ))
        region = SavedRegion("fight", "Fight Button", 10, 20, 40, 30)
        self.store.load.return_value = AppSettings(
            use_mouse_clicking=True, regions=(region,),
            overlay_background_transparency=37,
            pokemon_encounter_count=128, pokemon_encounter_rarity_counts={"r": 12},
            auto_fishing_press_f_on_missing_zone=True,
        )
        self.window._load_saved_settings()
        self.assertTrue(settings.use_mouse_clicking_toggle.isChecked())
        self.store.save.reset_mock()
        settings.use_mouse_clicking_toggle.setChecked(False)
        self.store.save.assert_called_once()
        saved = self.store.save.call_args.args[0]
        self.assertFalse(saved.use_mouse_clicking)
        self.assertEqual(saved.regions, (region,))
        self.assertEqual(saved.overlay_background_transparency, 37)
        self.assertEqual(saved.pokemon_encounter_count, 128)
        self.assertEqual(saved.pokemon_encounter_rarity_counts["r"], 12)
        self.assertTrue(saved.auto_fishing_press_f_on_missing_zone)

    def test_selecting_a_valid_game_updates_readiness_and_saves_the_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = self._select_game(directory)

            self.assertTrue(self.window.start_button.isEnabled())
            self.assertEqual(self.window.start_button.text(), "Launch game")
            self.assertEqual(self.window.status_label.text(), "Ready to launch")
            self.assertEqual(self.window.status_indicator.property("status"), "ready")
            self.assertEqual(
                self.store.save.call_args.args[0].executable_path, str(executable)
            )

    def test_failed_launch_restores_the_ready_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = self._select_game(directory)
            self.window.process_controller.start = Mock()
            self.window.start_button.click()

            self.window.process_controller.start.assert_called_once_with(executable)
            self.assertFalse(self.window.start_button.isEnabled())
            self.assertEqual(
                self.window.status_indicator.property("status"), "transitioning"
            )
            self.window.process_controller.running_changed.emit(False)

            self.assertTrue(self.window.start_button.isEnabled())
            self.assertTrue(self.window.executable_picker.isEnabled())
            self.assertEqual(self.window.status_label.text(), "Ready to launch")

    def test_running_action_closes_the_game_instead_of_relaunching_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._select_game(directory)
            self.window.process_controller.start = Mock()
            self.window.process_controller.stop = Mock()
            self.window.process_controller.running_changed.emit(True)

            self.assertEqual(self.window.start_button.text(), "Close game")
            self.assertFalse(self.window.executable_picker.isEnabled())
            with patch.object(
                type(self.window.process_controller),
                "is_running",
                new_callable=PropertyMock,
                return_value=True,
            ):
                self.window.start_button.click()

            self.window.process_controller.stop.assert_called_once_with()
            self.window.process_controller.start.assert_not_called()
            self.assertFalse(self.window.start_button.isEnabled())
            self.window.process_controller.running_changed.emit(False)
            self.assertEqual(self.window.start_button.text(), "Launch game")

    def test_session_overview_tracks_settings_regions_and_daily_encounters(self) -> None:
        overlay = self.window.overlay_window
        overlay.settings_window.auto_fishing_toggle.setChecked(True)
        self.assertEqual(self.window.automation_count_label.text(), "2")

        regions = (SavedRegion("fishing", "Fishing Bar", 10, 20, 300, 40),)
        overlay.region_window.set_regions(regions)
        overlay.regions_changed.emit(regions)
        self.assertEqual(self.window.regions_count_label.text(), "1")

        today = date.today().isoformat()
        overlay.set_encounter_statistics(1250, today)
        overlay.encounter_statistics_changed.emit(1250, today)
        self.assertEqual(self.window.encounter_count_label.text(), "1,250")
        self.assertEqual(self.store.save.call_args.args[0].pokemon_encounter_count, 1250)

    def test_activity_log_expands_collapses_and_clears(self) -> None:
        self.window.show()
        self.application.processEvents()
        collapsed_height = self.window.height()
        self.window.log_viewer.append("WARNING", "A test diagnostic")
        self.window.log_viewer.toggle_button.setChecked(True)
        self.application.processEvents()

        self.assertGreater(self.window.height(), collapsed_height)
        self.assertTrue(self.window.log_panel.isVisible())
        self.assertTrue(self.window.log_viewer.clear_button.isEnabled())
        self.assertIn("[WARNING] A test diagnostic", self.window.log_panel.toPlainText())
        self.window.log_viewer.clear_button.click()
        self.assertEqual(self.window.log_panel.toPlainText(), "")
        self.assertFalse(self.window.log_viewer.clear_button.isEnabled())

        self.window.log_viewer.toggle_button.setChecked(False)
        self.application.processEvents()
        self.assertEqual(self.window.height(), collapsed_height)
        self.window.log_viewer.toggle_button.setChecked(True)
        self.window.log_viewer.toggle_button.setChecked(False)
        self.assertEqual(self.window.height(), collapsed_height)

    def test_expanded_log_fits_a_short_desktop(self) -> None:
        screen = Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 1280, 720)
        with patch.object(self.window, "screen", return_value=screen):
            self.window.log_viewer.toggle_button.setChecked(True)
            self.window._fit_height_to_contents()

        self.assertLessEqual(self.window.height() + 32 + 24, 720)
        self.assertGreaterEqual(self.window.log_panel.height(), 72)

    def test_long_game_locations_do_not_resize_the_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory) / ("long-game-installation-folder-" * 4)
            nested.mkdir()
            executable = self._select_game(str(nested))
            self.window.show()
            self.application.processEvents()

            label = self.window.executable_picker.path_label
            self.assertEqual(self.window.width(), 460)
            self.assertEqual(label.toolTip(), str(executable))
            self.assertIn("…", label.text())
            self.assertTrue(label.text().endswith("Poke Nexus.exe"))

    def test_all_global_shortcuts_are_discoverable(self) -> None:
        keycaps = self.window.findChildren(QLabel, "shortcut_key")
        self.assertEqual(
            {label.text() for label in keycaps}, {"Alt+S", "Alt+T", "Alt+P", "Alt+X"}
        )

    def test_overlay_actions_have_labels_and_distinct_automation_states(self) -> None:
        overlay = self.window.overlay_window
        self.assertEqual(
            [button.text() for button in overlay.buttons.values()],
            ["Regions", "Start", "Stop", "Settings"],
        )
        overlay.set_bot_state(True)
        self.assertIn("Ready", overlay.status_label.text())
        self.assertTrue(overlay.buttons["play"].isEnabled())
        self.assertFalse(overlay.buttons["stop"].isEnabled())

        overlay._realtime_capture_requested = True
        overlay.set_bot_state(True)
        self.assertIn("Running", overlay.status_label.text())
        self.assertFalse(overlay.buttons["play"].isEnabled())
        self.assertTrue(overlay.buttons["stop"].isEnabled())

        overlay._on_stop()
        self.assertIn("Ready", overlay.status_label.text())
        self.assertFalse(overlay.buttons["stop"].isEnabled())


class SettingsRefreshTests(_QtUiTestCase):
    def setUp(self) -> None:
        self.settings = SettingsWindow()
        self.addCleanup(self._dispose_widget, self.settings)

    def _show_settings(self) -> None:
        self.settings.show_at(
            QRect(100, 100, 1000, 800), QRect(400, 130, 264, 96)
        )
        self.application.processEvents()

    def _open_picker(self):
        self.settings.stop_for_desired_pokemon_toggle.setChecked(True)
        self.settings.desired_pokemon_advanced_toggle.setChecked(True)
        self.settings.settings_tabs._buttons[1].click()
        self._show_settings()
        return self.settings.desired_pokemon_input

    def test_category_tabs_support_arrow_home_and_end_keys(self) -> None:
        self._show_settings()
        buttons = self.settings.settings_tabs._buttons
        buttons[0].setFocus()
        QTest.keyClick(buttons[0], Qt.Key.Key_Right)
        self.assertEqual(self.settings._settings_pages._current_index, 1)
        self.assertEqual(
            self.settings.page_description.text(), self.settings._PAGE_DESCRIPTIONS[1]
        )
        QTest.keyClick(buttons[1], Qt.Key.Key_End)
        self.assertEqual(self.settings._settings_pages._current_index, 5)
        QTest.keyClick(buttons[5], Qt.Key.Key_Home)
        self.assertEqual(self.settings._settings_pages._current_index, 0)
        QTest.keyClick(buttons[0], Qt.Key.Key_Left)
        self.assertEqual(self.settings._settings_pages._current_index, 5)

    def test_pokemon_picker_opens_and_toggles_selection_using_the_keyboard(self) -> None:
        picker = self._open_picker()
        picker.display.setFocus()
        QTest.keyClick(picker.display, Qt.Key.Key_Space)
        self.application.processEvents()
        self.assertTrue(picker.popup.isVisible())

        item = picker._items_by_name["pikachu"]
        picker.pokemon_list.setCurrentItem(item)
        picker.pokemon_list.scrollToItem(item)
        picker.pokemon_list.setFocus()
        QTest.keyClick(picker.pokemon_list, Qt.Key.Key_Space)
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)
        self.assertEqual(picker.text(), "Pikachu")

    def test_empty_pokemon_filters_have_a_message_and_disable_select_all(self) -> None:
        picker = self._open_picker()
        picker._show_picker()
        picker.search_input.setText("no-matching-pokemon-zzzz")
        self.application.processEvents()

        self.assertTrue(picker.empty_state.isVisible())
        self.assertFalse(picker.select_all.isEnabled())
        picker.search_input.clear()
        self.assertTrue(picker.empty_state.isHidden())
        self.assertTrue(picker.select_all.isEnabled())

    def test_long_toggle_labels_wrap_and_the_whole_row_is_clickable(self) -> None:
        toggle = ToggleSwitch(
            "Automatically press F if green zone is missing for 10 seconds"
        )
        toggle.setStyleSheet(load_stylesheet("settings.qss"))
        toggle.setFixedWidth(220)
        toggle.show()
        self.addCleanup(self._dispose_widget, toggle)
        self.application.processEvents()

        self.assertGreater(toggle.height(), 46)
        self.assertEqual(toggle.height(), toggle.heightForWidth(220))
        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
        self.assertTrue(toggle.isChecked())
        QTest.keyClick(toggle, Qt.Key.Key_Space)
        self.assertFalse(toggle.isChecked())
        toggle.setEnabled(False)
        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        self.assertFalse(toggle.isChecked())

    def test_expanding_advanced_settings_preserves_a_dragged_position(self) -> None:
        self._show_settings()
        self.settings.move(150, 250)
        self.settings.setProperty("_overlay_user_moved", True)
        previous_position = self.settings.pos()

        self.settings.desired_pokemon_advanced_toggle.setChecked(True)
        self.application.processEvents()
        self.assertEqual(self.settings.pos(), previous_position)

    def test_settings_stay_within_a_smaller_game_window(self) -> None:
        bounds = QRect(100, 100, 640, 480)
        self.settings.show_at(bounds, QRect(300, 130, 264, 96))
        self.application.processEvents()

        self.assertTrue(bounds.contains(self.settings.geometry()))
        self.assertLessEqual(self.settings.height(), bounds.height() - 16)
        self.assertGreater(self.settings.content_scroll_area.viewport().height(), 0)


class StyleAssetTests(_QtUiTestCase):
    def test_stylesheet_asset_urls_are_absolute_and_resolved(self) -> None:
        for filename in ("settings.qss", "overlay.qss"):
            with self.subTest(filename=filename):
                stylesheet = load_stylesheet(filename)
                self.assertNotIn("{{ASSETS_DIR}}", stylesheet)
                self.assertIn(application_path("assets").as_posix(), stylesheet)

    def test_stylesheet_asset_urls_support_a_bundled_application(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bot bundle ") as directory:
            with patch("utils.runtime_paths.sys.frozen", True, create=True), patch(
                "utils.runtime_paths.sys._MEIPASS", directory, create=True
            ):
                stylesheet = load_stylesheet("settings.qss")
            self.assertIn((Path(directory) / "assets").as_posix(), stylesheet)
            self.assertNotIn("{{ASSETS_DIR}}", stylesheet)

    def test_new_svg_assets_render_as_nonempty_icons(self) -> None:
        for filename in (
            "nexus-mark.svg",
            "folder-open.svg",
            "chevron-up.svg",
            "chevron-down.svg",
            "check.svg",
        ):
            with self.subTest(filename=filename):
                icon = load_svg_icon(application_path("assets", filename), QSize(24, 24))
                self.assertFalse(icon.isNull())
                self.assertFalse(icon.pixmap(QSize(24, 24)).isNull())


if __name__ == "__main__":
    unittest.main()
