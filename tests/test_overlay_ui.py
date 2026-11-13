import unittest
from unittest.mock import Mock

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QToolButton,
    QWidget,
)

from models.region_config import REGION_FEATURES, SavedRegion
from overlay.pokemon_encounter_summary_window import (
    PokemonEncounterSummaryWindow,
)
from overlay.region_marker import RegionMarkerWindow
from overlay.region_window import RegionWindow
from overlay.window_tracker import GameWindowTracker
from overlay.region_edit_dialog import RegionEditDialog
from overlay.region_name_dialog import RegionNameDialog
from overlay.settings_window import SettingsWindow
from overlay.overlay_window import OverlayWindow


class OverlayUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def _open_desired_pokemon_advanced_settings(settings: SettingsWindow) -> None:
        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.desired_pokemon_advanced_toggle.setChecked(True)

    def test_region_role_dialog_is_not_covered_by_region_window(self) -> None:
        parent = QWidget()
        region_window = RegionWindow(parent)
        region_window._game_geometry = QRect(100, 100, 800, 600)
        region_window.show()
        dialog_state: list[tuple[bool, bool]] = []

        def cancel_dialog() -> None:
            dialog = QApplication.activeModalWidget()
            self.assertIsInstance(dialog, QDialog)
            self.assertIs(dialog.parentWidget(), parent)
            self.assertTrue(
                dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            )
            self.assertTrue(
                dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
            )
            self.assertIsNotNone(
                dialog.findChild(QWidget, "region_name_panel")
            )
            dialog_state.append((region_window.isVisible(), dialog.isVisible()))
            region_window.show_for_game(region_window._game_geometry)
            self.assertFalse(region_window.isVisible())
            game_center = region_window._game_geometry.center()
            dialog_center = dialog.geometry().center()
            self.assertLessEqual(abs(dialog_center.x() - game_center.x()), 2)
            self.assertLessEqual(abs(dialog_center.y() - game_center.y()), 2)
            buttons = dialog.findChild(QDialogButtonBox)
            self.assertIsNotNone(buttons)
            buttons.button(QDialogButtonBox.StandardButton.Cancel).click()

        QTimer.singleShot(0, cancel_dialog)
        region_window.prompt_region_name("chat-box", QRect(0, 0, 30, 30))

        self.assertEqual(dialog_state, [(False, True)])
        self.assertTrue(region_window.isVisible())
        region_window.close()
        parent.close()

    def test_region_name_dialog_uses_glass_panel_styling(self) -> None:
        dialog = RegionNameDialog(("Chat Box", "Battle"))

        self.assertIsNotNone(dialog.findChild(QWidget, "region_name_panel"))
        self.assertIn(
            "background-color: rgba(17, 24, 39, 238)",
            dialog.styleSheet(),
        )
        self.assertTrue(
            dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        )
        dialog.close()

    def test_settings_are_grouped_into_glass_sections(self) -> None:
        settings = SettingsWindow()
        headings = [
            label.text()
            for label in settings.findChildren(QLabel, "settings_section_title")
        ]

        self.assertEqual(
            headings,
            [
                "Battle automation",
                "Special encounter popup",
                "Encounter rules",
                "Auto Catching Configuration",
                "Fishing automation",
                "Encounter alerts",
                "Walking options",
                "Overlay appearance",
                "Battle input",
                "Overlay and diagnostics",
                "Planned features",
            ],
        )
        self.assertEqual(
            settings.auto_battle_toggle.parentWidget().objectName(),
            "settings_section",
        )
        self.assertEqual(
            settings.show_chat_battle_logs_toggle.parentWidget().objectName(),
            "settings_section",
        )
        settings.close()

    def test_desired_pokemon_picker_is_first_in_encounter_rules(self) -> None:
        settings = SettingsWindow()
        encounter_layout = settings.stop_for_desired_pokemon_toggle.parentWidget().layout()
        self.assertIsNotNone(encounter_layout)
        section_widgets = [
            encounter_layout.itemAt(index).widget()
            for index in range(encounter_layout.count())
        ]
        self.assertLess(
            section_widgets.index(settings.stop_for_desired_pokemon_toggle),
            section_widgets.index(settings.desired_pokemon_advanced_toggle),
        )
        self.assertIs(
            settings.desired_pokemon_input.parentWidget(),
            settings._desired_pokemon_advanced_settings,
        )
        self.assertTrue(settings._desired_pokemon_advanced_settings.isHidden())
        settings.close()

    def test_pokemon_filter_fields_require_their_stop_toggle(self) -> None:
        settings = SettingsWindow()

        self.assertTrue(settings._desired_pokemon_advanced_settings.isHidden())
        self.assertTrue(settings._elite_pokemon_advanced_settings.isHidden())
        self.assertFalse(settings.desired_pokemon_input.isEnabled())
        self.assertFalse(settings.elite_pokemon_input.isEnabled())
        settings.desired_pokemon_advanced_toggle.setChecked(True)
        settings.elite_pokemon_advanced_toggle.setChecked(True)
        self.assertFalse(settings._desired_pokemon_advanced_settings.isHidden())
        self.assertFalse(settings._elite_pokemon_advanced_settings.isHidden())
        self.assertFalse(settings.desired_pokemon_input.isEnabled())
        self.assertFalse(settings.elite_pokemon_input.isEnabled())

        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.stop_for_elite_toggle.setChecked(True)
        self.assertTrue(settings.desired_pokemon_input.isEnabled())
        self.assertTrue(settings.elite_pokemon_input.isEnabled())
        settings.stop_for_desired_pokemon_toggle.setChecked(False)
        settings.stop_for_elite_toggle.setChecked(False)
        self.assertFalse(settings.desired_pokemon_input.isEnabled())
        self.assertFalse(settings.elite_pokemon_input.isEnabled())

        settings.close()

    def test_auto_walk_advanced_controls_are_disabled_when_auto_walk_is_off(self) -> None:
        settings = SettingsWindow()
        settings.auto_walk_advanced_toggle.setChecked(True)

        self.assertFalse(settings.auto_walk_toggle.isChecked())
        self.assertFalse(settings._auto_walk_advanced_settings.isHidden())
        self.assertFalse(settings._auto_walk_advanced_settings.isEnabled())
        self.assertFalse(settings.auto_walk_direction_selection.isEnabled())
        self.assertFalse(settings.auto_walk_duration_mode_selection.isEnabled())
        self.assertFalse(settings.auto_walk_duration_spin.isEnabled())

        settings.auto_walk_toggle.setChecked(True)
        self.assertTrue(settings._auto_walk_advanced_settings.isEnabled())
        self.assertTrue(settings.auto_walk_direction_selection.isEnabled())
        self.assertTrue(settings.auto_walk_duration_mode_selection.isEnabled())
        self.assertTrue(settings.auto_walk_duration_spin.isEnabled())

        settings.auto_walk_toggle.setChecked(False)
        self.assertFalse(settings._auto_walk_advanced_settings.isEnabled())
        settings.close()

    def test_enabling_desired_stop_without_selection_does_not_open_picker(
        self,
    ) -> None:
        overlay = Mock()
        overlay.settings_window.desired_pokemon_input.text.return_value = ""

        OverlayWindow._on_desired_pokemon_stop_toggled(overlay, True)

        overlay.settings_window.stop_for_desired_pokemon_toggle.setChecked.assert_not_called()
        overlay.settings_window.desired_pokemon_advanced_toggle.setChecked.assert_not_called()
        overlay.settings_window.desired_pokemon_input.setFocus.assert_not_called()
        overlay.region_test_log_window.append_entry.assert_called_once()

    def test_bag_region_configuration_offers_bag_controls(self) -> None:
        region_window = RegionWindow()

        self.assertEqual(
            region_window._available_role_names("bag"),
            (
                "Bag Button",
                "Previous Button",
                "Next Button",
                "Item Name",
                "Use Item Button",
            ),
        )
        self.assertEqual(
            RegionWindow._canonical_role_name(
                SavedRegion("bag", "Item Name", 10, 10, 20, 20)
            ),
            "Item Name",
        )

        region_window.set_regions(
            (
                SavedRegion("bag", "Next Button", 10, 10, 20, 20),
            )
        )
        self.assertEqual(
            region_window._available_role_names("bag"),
            (
                "Bag Button",
                "Previous Button",
                "Item Name",
                "Use Item Button",
            ),
        )
        region_window.close()

    def test_settings_dialog_has_glass_frame_and_footer_actions(self) -> None:
        settings = SettingsWindow()

        self.assertIsInstance(settings, QDialog)
        self.assertTrue(
            settings.windowFlags() & Qt.WindowType.FramelessWindowHint
        )
        self.assertTrue(
            settings.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        )
        self.assertIsNone(
            settings.findChild(QWidget, "settings_close_button")
        )
        self.assertIsNotNone(
            settings.findChild(QFrame, "settings_container")
        )
        buttons = settings.findChild(QDialogButtonBox, "settings_button_box")
        self.assertIsNotNone(buttons)
        self.assertEqual(settings.ok_button.text(), "Okay")
        self.assertEqual(settings.cancel_button.text(), "Cancel")
        self.assertIn(
            "background-color: rgba(17, 24, 39, 238)",
            settings.styleSheet(),
        )

        settings.show()
        settings.cancel_button.click()
        self.assertFalse(settings.isVisible())
        settings.close()

    def test_desired_pokemon_picker_uses_asset_catalog_and_persists_selection(self) -> None:
        settings = SettingsWindow()
        picker = settings.desired_pokemon_input
        emitted: list[str] = []
        picker.textChanged.connect(emitted.append)

        self.assertIn("bulbasaur", picker._items_by_name)
        self.assertIn("zeraora", picker._items_by_name)
        self.assertGreater(picker.pokemon_list.count(), 800)
        self.assertEqual(picker.display.height(), 36)
        self.assertIsNone(picker.findChild(QToolButton, "desired_pokemon_open_button"))
        self.assertTrue(
            picker.popup.windowFlags()
            & Qt.WindowType.WindowStaysOnTopHint
        )
        self.assertEqual(
            [picker.rarity_filter.itemData(i) for i in range(1, 7)],
            ["c", "uc", "r", "vr", "er", "l"],
        )

        picker.setText("Pancham, Meditite")
        self.assertEqual(picker.text(), "Meditite, Pancham")
        pancham_index = picker.pokemon_list.indexFromItem(
            picker._items_by_name["pancham"]
        )
        self.assertTrue(
            picker.pokemon_list.itemDelegate()._is_checked(
                pancham_index.data(Qt.ItemDataRole.CheckStateRole)
            )
        )
        self.assertEqual(
            picker._items_by_name["pancham"].checkState(),
            Qt.CheckState.Checked,
        )
        self.assertEqual(emitted[-1], "Meditite, Pancham")
        picker._items_by_name["zeraora"].setCheckState(Qt.CheckState.Checked)
        self.assertEqual(
            picker.text(),
            "Meditite, Pancham, Zeraora",
        )

        picker.search_input.setText("zera")
        self.assertFalse(picker._items_by_name["zeraora"].isHidden())
        self.assertTrue(picker._items_by_name["bulbasaur"].isHidden())
        picker.search_input.clear()
        picker.rarity_filter.setCurrentIndex(
            picker.rarity_filter.findData("er")
        )
        self.assertFalse(picker._items_by_name["bulbasaur"].isHidden())
        self.assertTrue(picker._items_by_name["ivysaur"].isHidden())
        self.assertEqual(
            picker.pokemon_list.itemDelegate().__class__.__name__,
            "_PokemonCheckDelegate",
        )
        settings.close()

    def test_desired_pokemon_picker_opens_below_field(self) -> None:
        settings = SettingsWindow()
        self._open_desired_pokemon_advanced_settings(settings)
        picker = settings.desired_pokemon_input
        settings.show()
        settings.settings_tabs._buttons[1].click()
        QTest.qWait(220)
        self.application.processEvents()

        picker._show_picker()
        self.application.processEvents()

        field_top_left = picker.mapToGlobal(QPoint(0, 0))
        field_bottom_left = picker.mapToGlobal(QPoint(0, picker.height()))
        popup_position = picker.popup.geometry().topLeft()
        screen = QApplication.screenAt(field_bottom_left)
        self.assertIsNotNone(screen)
        bounds = screen.availableGeometry()
        expected_x = max(
            bounds.left(),
            min(field_top_left.x(), bounds.right() - picker.popup.width() + 1),
        )
        self.assertEqual(popup_position.x(), expected_x)
        if (
            field_bottom_left.y() + 8 + picker.popup.height()
            <= bounds.bottom() + 1
        ):
            self.assertGreaterEqual(popup_position.y(), field_bottom_left.y())
        else:
            self.assertLess(popup_position.y(), field_top_left.y())
        settings.close()

    def test_disabled_pokemon_picker_cannot_open_or_keep_popup_visible(self) -> None:
        settings = SettingsWindow()
        for picker in (
            settings.desired_pokemon_input,
            settings.elite_pokemon_input,
        ):
            picker.setEnabled(True)
            picker._show_picker()
            self.application.processEvents()
            self.assertTrue(picker.popup.isVisible())

            picker.setEnabled(False)
            self.assertFalse(picker.popup.isVisible())
            picker._show_picker()
            self.assertFalse(picker.popup.isVisible())
        settings.close()

    def test_desired_pokemon_checkbox_click_toggles_and_updates_selection(self) -> None:
        settings = SettingsWindow()
        self._open_desired_pokemon_advanced_settings(settings)
        picker = settings.desired_pokemon_input
        picker._show_picker()
        self.application.processEvents()
        item = picker._items_by_name["bulbasaur"]
        row = picker.pokemon_list.visualItemRect(item)
        click_position = QPoint(row.left() + 17, row.center().y())

        self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
        QTest.mouseClick(
            picker.pokemon_list.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            click_position,
        )
        self.application.processEvents()

        self.assertEqual(item.checkState(), Qt.CheckState.Checked)
        self.assertEqual(picker.text(), "Bulbasaur")
        self.assertIn("Bulbasaur", picker.display.text())

        QTest.mouseClick(
            picker.pokemon_list.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            click_position,
        )
        self.application.processEvents()
        self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
        self.assertEqual(picker.text(), "")
        settings.close()

    def test_clicking_pokemon_name_toggles_checkbox(self) -> None:
        settings = SettingsWindow()
        self._open_desired_pokemon_advanced_settings(settings)
        picker = settings.desired_pokemon_input
        picker._show_picker()
        self.application.processEvents()
        item = picker._items_by_name["bulbasaur"]
        row = picker.pokemon_list.visualItemRect(item)
        name_position = QPoint(row.left() + 55, row.center().y())

        QTest.mouseClick(
            picker.pokemon_list.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            name_position,
        )
        self.application.processEvents()
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)
        self.assertEqual(picker.text(), "Bulbasaur")

        QTest.mouseClick(
            picker.pokemon_list.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            name_position,
        )
        self.application.processEvents()
        self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
        settings.close()

    def test_select_all_only_changes_visible_filtered_pokemon(self) -> None:
        settings = SettingsWindow()
        self._open_desired_pokemon_advanced_settings(settings)
        picker = settings.desired_pokemon_input
        picker.setText("Ivysaur")
        picker.rarity_filter.setCurrentIndex(
            picker.rarity_filter.findData("r")
        )
        self.application.processEvents()
        visible_items = [
            item
            for item in picker._items_by_name.values()
            if not item.isHidden()
        ]
        hidden_selected = picker._items_by_name["ivysaur"]
        self.assertGreater(len(visible_items), 0)
        self.assertTrue(hidden_selected.isHidden())
        self.assertEqual(hidden_selected.checkState(), Qt.CheckState.Checked)
        visible_items[0].setCheckState(Qt.CheckState.Checked)
        self.assertFalse(picker.select_all.isTristate())
        self.assertFalse(picker.select_all.isChecked())
        changed: list[str] = []
        picker.textChanged.connect(changed.append)

        QTest.mouseClick(picker.select_all, Qt.MouseButton.LeftButton)
        self.application.processEvents()
        self.assertTrue(picker.select_all.isChecked())
        self.assertTrue(
            all(item.checkState() == Qt.CheckState.Checked for item in visible_items)
        )
        self.assertEqual(hidden_selected.checkState(), Qt.CheckState.Checked)
        self.assertEqual(len(changed), 1)

        QTest.mouseClick(picker.select_all, Qt.MouseButton.LeftButton)
        self.application.processEvents()
        self.assertFalse(picker.select_all.isChecked())
        self.assertTrue(
            all(item.checkState() == Qt.CheckState.Unchecked for item in visible_items)
        )
        self.assertEqual(hidden_selected.checkState(), Qt.CheckState.Checked)
        self.assertEqual(len(changed), 2)
        settings.close()

    def test_settings_category_tabs_switch_immediately(self) -> None:
        settings = SettingsWindow()
        settings.show()
        self.application.processEvents()

        self.assertEqual(len(settings.settings_tabs._buttons), 6)
        settings.settings_tabs._buttons[1].click()

        self.assertEqual(settings._settings_pages._current_index, 1)
        self.assertTrue(settings.settings_tabs._buttons[1].isChecked())
        settings.settings_tabs._buttons[2].click()
        self.assertEqual(settings._settings_pages._current_index, 2)
        settings.settings_tabs._buttons[4].click()
        self.assertEqual(settings._settings_pages._current_index, 4)
        settings.settings_tabs._buttons[0].click()
        self.assertEqual(settings._settings_pages._current_index, 0)
        settings.close()

    def test_settings_page_section_stays_below_header_without_vertical_gap(self) -> None:
        settings = SettingsWindow()
        settings.show_at(
            QRect(100, 100, 800, 600),
            QRect(350, 130, 216, 68),
        )
        self.application.processEvents()
        section_title = settings.findChild(QLabel, "settings_section_title")
        self.assertIsNotNone(section_title)

        title_position = section_title.mapTo(settings, QPoint(0, 0))
        self.assertLess(title_position.y(), 190)
        settings.close()

    def test_region_toolbar_shows_all_labeled_features_without_clipping(self) -> None:
        region_window = RegionWindow()
        region_window.show_for_game(QRect(100, 100, 800, 600))
        self.application.processEvents()
        viewport = region_window.toolbar_scroll_area.viewport()
        for feature, label in REGION_FEATURES:
            button = region_window.buttons[feature]
            button_rect = QRect(button.mapTo(viewport, QPoint()), button.size())
            self.assertTrue(viewport.rect().contains(button_rect), feature)
            self.assertEqual(button.text(), label)
            icon, caption = button.content_rects()
            self.assertTrue(button.rect().contains(icon))
            self.assertTrue(button.rect().contains(caption))
            self.assertFalse(icon.intersects(button.count_label.geometry()))
            for line in button.caption_lines():
                self.assertLessEqual(button.fontMetrics().horizontalAdvance(line), caption.width())
        self.assertEqual(region_window.toolbar_scroll_area.verticalScrollBar().maximum(), 0)
        region_window.close()

    def test_auto_run_waits_for_turn_one_and_visible_run_button(self) -> None:
        battle_state = {
            "onBattleMode": True,
            "turn": 1,
            "showingRunButton": True,
        }

        self.assertTrue(OverlayWindow._auto_run_ready(battle_state))
        self.assertFalse(
            OverlayWindow._auto_run_ready({**battle_state, "turn": 0})
        )
        self.assertFalse(
            OverlayWindow._auto_run_ready({**battle_state, "turn": True})
        )
        self.assertFalse(
            OverlayWindow._auto_run_ready(
                {**battle_state, "showingRunButton": False}
            )
        )

    def test_auto_run_is_scheduled_without_pre_click_delay(self) -> None:
        overlay = Mock()
        overlay._auto_run_clicked = False
        overlay._auto_run_pending = False
        overlay._auto_run_allowed.return_value = True
        overlay._auto_run_ready.return_value = True
        overlay._stop_auto_run_for_encounter.return_value = False

        OverlayWindow._handle_auto_run(
            overlay,
            {"onBattleMode": True, "turn": 1, "showingRunButton": True},
        )

        overlay._auto_run_timer.start.assert_called_once_with(0)

    def test_battle_chat_is_appended_only_while_battle_mode_is_true(self) -> None:
        battle_log_window = Mock()
        overlay = Mock()
        overlay._latest_realtime_state = {"onBattleMode": False}
        overlay.battle_log_window = battle_log_window

        OverlayWindow._append_battle_chat_message(
            overlay,
            "12:00:00.00 PM",
            "A wild Pokemon appeared!",
        )
        battle_log_window.append_chat_message.assert_not_called()

        overlay._latest_realtime_state = {"onBattleMode": True}
        OverlayWindow._append_battle_chat_message(
            overlay,
            "12:00:01.00 PM",
            "The opposing Pokemon appeared!",
        )
        battle_log_window.append_chat_message.assert_called_once_with(
            "12:00:01.00 PM",
            "The opposing Pokemon appeared!",
        )

    def test_auto_run_click_uses_normal_settle_delay(self) -> None:
        region = SavedRegion("run", "Run Button", 120, 130, 40, 30)
        input_device = Mock()
        overlay = Mock()
        overlay._target_window_id = 12
        overlay._target_geometry = QRect(100, 100, 800, 600)
        overlay._auto_battle_input = input_device
        overlay.region_test_log_window = Mock()
        overlay._auto_battle_move_clicked = False
        overlay.region_window.regions = (region,)
        overlay.settings_window.use_mouse_clicking_toggle.isChecked.return_value = True

        self.assertTrue(
            OverlayWindow._send_battle_action(
                overlay,
                "run",
                1,
                "Run Button",
                automation_name="Auto Run",
                settle_delay=None,
            )
        )
        input_device.click_region.assert_called_once_with(
            12,
            region,
            overlay._target_geometry,
            settle_delay=None,
        )

    def test_back_to_toolbar_keeps_feature_panel_position(self) -> None:
        region_window = RegionWindow()
        region_window._game_geometry = QRect(100, 100, 800, 600)
        region_window._set_panel_size(toolbar=False)
        region_window.setProperty("_overlay_user_moved", True)
        region_window.move(320, 240)
        region_window.stack.setCurrentWidget(region_window.feature_page)
        position_before_back = region_window.pos()

        region_window._close_feature()

        expected_y = min(
            position_before_back.y(),
            region_window._game_geometry.bottom()
            - region_window.height()
            + 1,
        )
        self.assertEqual(
            region_window.pos(),
            QPoint(position_before_back.x(), expected_y),
        )
        region_window.close()

    def test_clicking_region_label_opens_exact_position_and_size_editor(self) -> None:
        parent = QWidget()
        region_window = RegionWindow(parent)
        region_window._game_geometry = QRect(100, 100, 800, 600)
        region_window.current_feature = "chat-box"
        region_window.stack.setCurrentWidget(region_window.feature_page)
        region = SavedRegion("chat-box", "Battle Chat", 160, 180, 300, 240)
        region_window.set_regions((region,))
        region_window.show()
        region_window._sync_markers()
        changed_regions: list[tuple[SavedRegion, ...]] = []
        region_window.regions_changed.connect(changed_regions.append)
        details = region_window.findChild(QLabel, "region_item_details")
        self.assertIsNotNone(details)

        def edit_geometry() -> None:
            dialog = QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            self.assertEqual(dialog.objectName(), "region_edit_dialog")
            self.assertTrue(
                dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
            )
            self.assertIsNotNone(
                dialog.findChild(QWidget, "region_edit_panel")
            )
            dialog.x_input.setValue(170)
            self.assertEqual(region_window._markers[0].x(), 170)
            dialog.y_input.setValue(190)
            self.assertEqual(region_window._markers[0].y(), 190)
            dialog.width_input.setValue(320)
            dialog.height_input.setValue(260)
            dialog.accept()

        QTimer.singleShot(0, edit_geometry)
        details.clicked.emit()

        self.assertEqual(
            region_window.regions,
            [SavedRegion("chat-box", "Battle Chat", 170, 190, 320, 260)],
        )
        self.assertEqual(changed_regions[-1], tuple(region_window.regions))
        region_window.close()
        parent.close()

    def test_dragging_region_marker_moves_outline_within_game_bounds(self) -> None:
        region = SavedRegion("chat-box", "Battle Chat", 120, 130, 100, 80)
        marker = RegionMarkerWindow(region, QRect(100, 100, 300, 250))
        marker.show()
        moved: list[tuple[SavedRegion, QRect]] = []
        marker.move_requested.connect(lambda item, rect: moved.append((item, rect)))

        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(130, 140),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        marker.mousePressEvent(press)
        move = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(210, 190),
            QPointF(330, 320),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        marker.mouseMoveEvent(move)
        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPointF(210, 190),
            QPointF(330, 320),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        marker.mouseReleaseEvent(release)

        self.assertEqual(marker.geometry(), QRect(300, 270, 100, 80))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0][1], QRect(300, 270, 100, 80))
        marker.close()

    def test_marker_click_enables_keyboard_focus_for_arrow_movement(self) -> None:
        region = SavedRegion("chat-box", "Battle Chat", 120, 130, 100, 80)
        marker = RegionMarkerWindow(region, QRect(100, 100, 300, 250))
        marker.show()
        self.application.processEvents()
        self.assertFalse(
            marker.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        )

        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(130, 140),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        marker.mousePressEvent(press)
        self.application.processEvents()

        self.assertTrue(marker.hasFocus())
        marker.keyPressEvent(
            QKeyEvent(
                QKeyEvent.Type.KeyPress,
                Qt.Key.Key_Right,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertEqual(marker.x(), 121)
        marker.close()

    def test_tracker_accepts_active_region_marker_as_overlay_window(self) -> None:
        tracker = GameWindowTracker()
        tracker.set_region_marker_windows((123, 456))
        self.assertEqual(tracker._region_marker_window_ids, {123, 456})
        tracker.set_region_marker_windows(())
        self.assertEqual(tracker._region_marker_window_ids, set())

    def test_region_edit_dialog_has_visible_styled_glass_panel(self) -> None:
        dialog = RegionEditDialog(
            SavedRegion("chat-box", "Battle Chat", 120, 130, 100, 80),
            QRect(100, 100, 300, 250),
        )
        self.assertIsNotNone(dialog.findChild(QWidget, "region_edit_panel"))
        self.assertIn(
            "background-color: rgba(17, 24, 39, 238)",
            dialog.styleSheet(),
        )
        self.assertTrue(
            dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        )
        dialog.close()

    def test_arrow_keys_nudge_selected_region_by_one_or_ten_pixels(self) -> None:
        region = SavedRegion("chat-box", "Battle Chat", 120, 130, 100, 80)
        marker = RegionMarkerWindow(region, QRect(100, 100, 300, 250))
        marker.show()
        moved: list[tuple[SavedRegion, QRect]] = []
        marker.move_requested.connect(
            lambda selected_region, rect: moved.append((selected_region, rect))
        )

        marker.keyPressEvent(
            QKeyEvent(
                QKeyEvent.Type.KeyPress,
                Qt.Key.Key_Right,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        marker.keyPressEvent(
            QKeyEvent(
                QKeyEvent.Type.KeyPress,
                Qt.Key.Key_Down,
                Qt.KeyboardModifier.ShiftModifier,
            )
        )

        self.assertEqual(marker.geometry(), QRect(121, 140, 100, 80))
        self.assertEqual(
            moved,
            [
                (region, QRect(121, 130, 100, 80)),
                (region, QRect(121, 140, 100, 80)),
            ],
        )
        marker.close()

    def test_session_timer_stops_and_resumes_accumulated_time(self) -> None:
        summary = PokemonEncounterSummaryWindow()
        summary.start_session_timer()
        QTimer.singleShot(1100, self.application.quit)
        self.application.exec()
        summary.stop_session_timer()
        stopped_text = summary.timer_label.text()
        self.assertNotEqual(stopped_text, "00:00:00")

        QTimer.singleShot(1100, self.application.quit)
        self.application.exec()
        summary._update_timer_label()
        self.assertEqual(summary.timer_label.text(), stopped_text)

        summary.start_session_timer()
        QTimer.singleShot(1100, self.application.quit)
        self.application.exec()
        summary.stop_session_timer()
        self.assertNotEqual(summary.timer_label.text(), stopped_text)
        summary.close()

    def test_encounter_summary_shows_colored_rarity_totals(self) -> None:
        summary = PokemonEncounterSummaryWindow()

        summary.set_rarity_counts(
            {"c": 100, "uc": 50, "r": 25, "vr": 5, "er": 3, "l": 1}
        )

        text = summary.rarity_counts_label.text()
        for expected in (
            "Common: 100",
            "Uncommon: 50",
            "Rare: 25",
            "Very Rare: 5",
            "Extremely Rare: 3",
            "Legendary: 1",
            "#22C55E",
            "#A3E635",
            "#3B82F6",
            "#D946EF",
            "#F97316",
            "#EF4444",
        ):
            self.assertIn(expected, text)
        summary.close()


if __name__ == "__main__":
    unittest.main()
