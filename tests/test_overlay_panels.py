import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton, QWidget

from launcher.styles.style_loader import load_stylesheet
from models.region_config import SavedRegion
from overlay.battle_log_window import BattleLogWindow
from overlay.overlay_window import OverlayWindow
from overlay.panel_widgets import OverlayIconButton
from overlay.pokemon_encounter_summary_window import PokemonEncounterSummaryWindow
from overlay.region_test_log_window import RegionTestLogWindow
from overlay.region_window import RegionWindow
from overlay.window_tracker import GameWindowTracker
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class _PanelUiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "consola.ttf"):
            font_path = font_directory / filename
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
        cls.application.setFont(QFont("Segoe UI", 9))

    def _dispose_widget(self, widget) -> None:
        widget.hide()
        widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def _create_widget(self, widget_type):
        widget = widget_type()
        self.addCleanup(self._dispose_widget, widget)
        return widget

    def _show(self, widget) -> None:
        widget.show_for_game(QRect(100, 100, 1280, 800))
        self.application.processEvents()

    def _assert_color(self, panel, text: str, color: str) -> None:
        cursor = panel.log_view.document().find(text)
        self.assertFalse(cursor.isNull(), text)
        self.assertEqual(cursor.charFormat().foreground().color().name(), color.lower())


class OverlayButtonGeometryTests(_PanelUiTestCase):
    def _button(self, text: str, size: QSize, icon_size: QSize):
        button = self._create_widget(OverlayIconButton)
        button.setObjectName("overlay_settings_button")
        button.setStyleSheet(load_stylesheet("overlay.qss"))
        button.setFixedSize(size)
        button.setIconSize(icon_size)
        button.setIcon(load_svg_icon(application_path("assets", "nexus-mark.svg"), icon_size))
        button.setText(text)
        button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextUnderIcon
            if text else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        button.show()
        self.application.processEvents()
        return button

    def test_toolbar_icon_and_caption_have_explicit_centered_padding(self) -> None:
        button = self._button("Settings", QSize(60, 62), QSize(24, 24))
        icon, caption = button.content_rects()

        self.assertEqual(icon.size(), QSize(24, 24))
        self.assertEqual(icon.center().x(), button.rect().center().x())
        self.assertEqual(caption.center().x(), button.rect().center().x())
        self.assertEqual(caption.top() - icon.bottom() - 1, 5)
        self.assertGreaterEqual(icon.top(), 6)
        self.assertLessEqual(caption.bottom(), button.height() - 7)
        self.assertLessEqual(button.fontMetrics().horizontalAdvance(button.text()), caption.width())
        self.assertFalse(button.grab().isNull())

    def test_icon_only_clear_button_is_centered_in_both_directions(self) -> None:
        button = self._button("", QSize(32, 32), QSize(18, 18))
        icon, caption = button.content_rects()
        self.assertEqual(icon.center(), button.rect().center())
        self.assertEqual(icon.size(), QSize(18, 18))
        self.assertEqual(caption.height(), 0)
        self.assertGreaterEqual(icon.left(), 6)
        self.assertGreaterEqual(icon.top(), 6)

    def test_disabled_and_hovered_states_do_not_shift_icon_geometry(self) -> None:
        button = self._button("Start", QSize(60, 62), QSize(24, 24))
        initial = button.content_rects()
        button.setEnabled(False)
        button.grab()
        self.assertEqual(button.content_rects(), initial)
        button.setEnabled(True)
        QTest.mouseMove(button, button.rect().center())
        button.grab()
        self.assertEqual(button.content_rects(), initial)


class OverlayLogPanelTests(_PanelUiTestCase):
    def test_empty_states_and_clear_actions_track_both_documents(self) -> None:
        for window_type in (BattleLogWindow, RegionTestLogWindow):
            with self.subTest(window=window_type.__name__):
                panel = self._create_widget(window_type)
                self._show(panel)
                self.assertTrue(panel.empty_state.isVisible())
                self.assertEqual(panel.empty_state.geometry(), panel.log_view.viewport().rect())
                self.assertEqual(panel.line_count_label.text(), "0 lines")
                self.assertFalse(panel.clear_button.isEnabled())
                self.assertTrue(panel.follow_button.isChecked())
                if isinstance(panel, BattleLogWindow):
                    panel.append_entry("A battle message")
                else:
                    panel.append_entry("INFO", "A diagnostic message")
                self.assertTrue(panel.empty_state.isHidden())
                self.assertTrue(panel.clear_button.isEnabled())
                self.assertEqual(panel.line_count_label.text(), "1 line")
                panel.follow_button.setChecked(False)
                panel.clear_button.click()
                self.assertEqual(panel.log_view.toPlainText(), "")
                self.assertEqual(panel.line_count_label.text(), "0 lines")
                self.assertTrue(panel.empty_state.isVisible())
                self.assertFalse(panel.clear_button.isEnabled())
                self.assertTrue(panel.follow_button.isChecked())

    def test_log_panels_remain_read_only_and_do_not_take_game_focus(self) -> None:
        for window_type in (BattleLogWindow, RegionTestLogWindow):
            with self.subTest(window=window_type.__name__):
                panel = self._create_widget(window_type)
                self.assertTrue(panel.log_view.isReadOnly())
                self.assertEqual(panel.log_view.focusPolicy(), Qt.FocusPolicy.NoFocus)
                self.assertEqual(panel.clear_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
                self.assertEqual(panel.follow_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
                self.assertTrue(panel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
                self.assertTrue(panel.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)

    def test_long_diagnostics_header_elides_without_crowding_the_clear_button(self) -> None:
        panel = self._create_widget(RegionTestLogWindow)
        panel.show_for_game(QRect(100, 100, 260, 260))
        self.application.processEvents()

        self.assertIn("…", panel.title.text())
        self.assertEqual(panel.title.accessibleName(), "Test Battle Configuration")
        self.assertIn("Test Battle Configuration", panel.header.toolTip())
        title = QRect(panel.title.mapTo(panel.header, QPoint()), panel.title.size())
        action = QRect(panel.clear_button.mapTo(panel.header, QPoint()), panel.clear_button.size())
        self.assertTrue(panel.header.rect().contains(action))
        self.assertLess(title.right(), action.left())

        panel.show_for_game(QRect(100, 100, 1280, 800))
        self.application.processEvents()
        self.assertEqual(panel.title.text(), panel.windowTitle())

    def test_compact_empty_state_keeps_a_short_message_visible(self) -> None:
        panel = self._create_widget(RegionTestLogWindow)
        panel.show_for_game(QRect(100, 100, 800, 600), size_limit=QSize(300, 139))
        self.application.processEvents()

        self.assertTrue(panel.empty_state.isVisible())
        self.assertEqual(panel.empty_title.text(), "Waiting for messages")
        self.assertTrue(panel.empty_description.isHidden())
        self.assertTrue(panel.empty_state.rect().contains(panel.empty_title.geometry()))
        self.assertGreaterEqual(panel.log_view.viewport().height(), panel.empty_title.fontMetrics().height())
        panel.show_for_game(QRect(100, 100, 1280, 800))
        self.application.processEvents()
        self.assertEqual(panel.empty_title.text(), "Ready to test your configuration")
        self.assertTrue(panel.empty_description.isVisible())

    def test_battle_messages_keep_text_timestamps_and_pokemon_highlighting(self) -> None:
        panel = self._create_widget(BattleLogWindow)
        panel.append_chat_message("12:34:56.78 PM", "  A wild Pikachu appeared!  ")
        panel.append_entry("Battle detection is active.")

        self.assertEqual(
            panel.log_view.toPlainText(),
            "[12:34:56.78 PM] A wild Pikachu appeared!\nBattle detection is active.",
        )
        self._assert_color(panel, "12:34:56.78 PM", "#7DD3FC")
        self._assert_color(panel, "Pikachu", "#4ADE80")
        self._assert_color(panel, "Battle detection is active.", "#E6EDF7")
        self.assertEqual(panel.line_count_label.text(), "2 lines")

    def test_blank_battle_messages_do_not_add_empty_lines(self) -> None:
        panel = self._create_widget(BattleLogWindow)
        panel.append_entry(" \n ")
        panel.append_chat_message("12:00:00.00 PM", " ")
        self.assertTrue(panel.log_view.document().isEmpty())
        self.assertEqual(panel.line_count_label.text(), "0 lines")

    def test_diagnostics_keep_timestamp_state_values_and_level_colors(self) -> None:
        panel = self._create_widget(RegionTestLogWindow)
        message = (
            "12:34:56.78 PM [onBattleMode: True, turn: 3, "
            "wildPokemonName: Pikachu, showingRunButton: False]"
        )
        panel.append_entry("INFO", message)
        self.assertEqual(panel.log_view.toPlainText(), f"[INFO] {message}")
        for text, color in (
            ("[INFO]", "#7DD3FC"), ("12:34:56.78 PM", "#7DD3FC"),
            ("True", "#5EEAD4"), ("3", "#FCD34D"),
            ("Pikachu", "#5EEAD4"), ("False", "#FDA4AF"),
        ):
            with self.subTest(text=text):
                # The turn value is selected explicitly to avoid timestamp digits.
                if text == "3":
                    cursor = panel.log_view.document().find("turn: 3")
                    self.assertEqual(cursor.charFormat().foreground().color().name(), color.lower())
                else:
                    self._assert_color(panel, text, color)
        for level, color in (
            ("SUCCESS", "#5EEAD4"), ("WARNING", "#FCD34D"), ("ERROR", "#FDA4AF"),
        ):
            panel.append_entry(level, f"A {level.lower()} diagnostic")
            self._assert_color(panel, f"[{level}]", color)

    def test_both_original_log_line_limits_are_preserved(self) -> None:
        for window_type, limit in ((BattleLogWindow, 300), (RegionTestLogWindow, 500)):
            with self.subTest(window=window_type.__name__):
                panel = self._create_widget(window_type)
                for index in range(limit + 5):
                    message = f"Entry {index:04d}"
                    if isinstance(panel, BattleLogWindow):
                        panel.append_entry(message)
                    else:
                        panel.append_entry("INFO", message)
                lines = panel.log_view.toPlainText().splitlines()
                self.assertEqual(len(lines), limit)
                self.assertTrue(lines[0].endswith("Entry 0005"))
                self.assertTrue(lines[-1].endswith(f"Entry {limit + 4:04d}"))
                self.assertEqual(panel.line_count_label.text(), f"{limit} lines")

    def test_reading_older_entries_pauses_scrolling_but_still_appends_messages(self) -> None:
        for window_type in (BattleLogWindow, RegionTestLogWindow):
            with self.subTest(window=window_type.__name__):
                panel = self._create_widget(window_type)
                self._show(panel)
                for index in range(100):
                    if isinstance(panel, BattleLogWindow):
                        panel.append_entry(f"Entry {index}")
                    else:
                        panel.append_entry("INFO", f"Entry {index}")
                self.application.processEvents()
                scrollbar = panel.log_view.verticalScrollBar()
                self.assertGreater(scrollbar.maximum(), 0)
                self.assertTrue(panel.follow_button.isChecked())
                self.assertEqual(scrollbar.value(), scrollbar.maximum())
                scrollbar.setValue(0)
                self.assertFalse(panel.follow_button.isChecked())
                self.assertEqual(panel.follow_button.text(), "Scroll paused")
                if isinstance(panel, BattleLogWindow):
                    panel.append_entry("New message while reading")
                else:
                    panel.append_entry("INFO", "New message while reading")
                self.assertEqual(scrollbar.value(), 0)
                self.assertIn("New message while reading", panel.log_view.toPlainText())
                self.assertEqual(panel.line_count_label.text(), "101 lines")
                panel.follow_button.click()
                self.assertTrue(panel.follow_button.isChecked())
                self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_resizing_a_following_panel_keeps_the_newest_message_visible(self) -> None:
        panel = self._create_widget(BattleLogWindow)
        self._show(panel)
        for index in range(60):
            panel.append_entry(f"Entry {index}")
        panel.show_for_game(QRect(100, 100, 640, 480), size_limit=QSize(280, 180))
        self.application.processEvents()
        self.assertTrue(panel.follow_button.isChecked())
        scrollbar = panel.log_view.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())


class PokemonSummaryPanelTests(_PanelUiTestCase):
    def test_summary_collapse_keeps_live_metrics_and_restores_the_rarity_breakdown(self) -> None:
        panel = self._create_widget(PokemonEncounterSummaryWindow)
        panel.set_daily_count(1234)
        panel.set_rarity_counts({"c": 1200, "r": 34})
        panel._elapsed_session_ms = 3661000
        panel.start_session_timer()
        panel.start_encounter("Eevee")
        self._show(panel)
        expanded_size = QSize(panel.size())
        self.assertFalse(panel.collapse_button.isChecked())
        self.assertEqual(panel.collapse_button.toolTip(), "Collapse Pokémon Summary")
        self.assertTrue(panel.rarity_section.isVisible())
        changes = []
        panel.preferred_size_changed.connect(lambda: changes.append(QSize(panel._preferred_size)))

        QTest.mouseClick(panel.collapse_button, Qt.MouseButton.LeftButton)
        self.application.processEvents()

        self.assertTrue(panel.collapse_button.isChecked())
        self.assertEqual(panel.collapse_button.toolTip(), "Expand Pokémon Summary")
        self.assertEqual(panel.collapse_button.accessibleName(), "Expand Pokémon Summary")
        self.assertTrue(panel.rarity_section.isHidden())
        self.assertLess(panel.height(), expanded_size.height())
        self.assertEqual(panel.width(), expanded_size.width())
        self.assertEqual(panel.content_scroll_area.verticalScrollBar().maximum(), 0)
        for label in (panel.current_pokemon_label, panel.daily_count_label, panel.timer_label):
            self.assertTrue(label.isVisible())
            viewport = panel.content_scroll_area.viewport()
            self.assertTrue(viewport.rect().contains(QRect(label.mapTo(viewport, QPoint()), label.size())))
        self.assertTrue(panel._session_timer.isValid())
        self.assertEqual(panel._elapsed_session_ms, 3661000)
        self.assertEqual(panel.timer_label.text(), "01:01:01")
        self.assertEqual(panel.session_status_label.text(), "Active")

        panel.set_daily_count(1235)
        panel.set_rarity_counts({"c": 1200, "r": 35})
        panel.start_encounter("Shiny Pikachu", "#FACC15")
        QTest.mouseClick(panel.collapse_button, Qt.MouseButton.LeftButton)
        self.application.processEvents()

        self.assertEqual(panel.size(), expanded_size)
        self.assertTrue(panel.rarity_section.isVisible())
        self.assertEqual(panel.daily_count_label.text(), "1,235")
        self.assertIn("Shiny Pikachu", panel.current_pokemon_label.text())
        self.assertIn("#FACC15", panel.current_pokemon_label.text())
        self.assertIn("Rare: 35", panel.rarity_counts_label.text())
        self.assertTrue(panel._session_timer.isValid())
        self.assertEqual(len(changes), 2)
        self.assertEqual(panel.collapse_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertTrue(panel.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(panel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))

    def test_summary_collapse_does_not_reopen_a_hidden_panel_or_reset_its_state(self) -> None:
        panel = self._create_widget(PokemonEncounterSummaryWindow)
        panel.collapse_button.setChecked(True)
        self.assertFalse(panel.isVisible())
        self._show(panel)
        collapsed_size = QSize(panel.size())
        panel.hide()
        panel.set_daily_count(42)
        panel.start_encounter("Pikachu")
        self._show(panel)

        self.assertTrue(panel.collapse_button.isChecked())
        self.assertTrue(panel.rarity_section.isHidden())
        self.assertEqual(panel.size(), collapsed_size)
        self.assertEqual(panel.daily_count_label.text(), "42")
        panel.hide()
        panel.collapse_button.setChecked(False)
        self.assertFalse(panel.isVisible())

    def test_summary_toggle_preserves_dragged_positions_and_clamps_expansion(self) -> None:
        panel = self._create_widget(PokemonEncounterSummaryWindow)
        bounds = QRect(100, 100, 640, 480)
        panel.show_for_game(bounds, size_limit=QSize(280, 250))
        panel.move(150, 200)
        panel.setProperty("_overlay_user_moved", True)
        position = QPoint(panel.pos())
        QTest.mouseClick(panel.collapse_button, Qt.MouseButton.LeftButton)
        self.assertEqual(panel.pos(), position)
        panel.move(150, bounds.bottom() - panel.height() + 1)
        QTest.mouseClick(panel.collapse_button, Qt.MouseButton.LeftButton)
        self.application.processEvents()

        self.assertTrue(bounds.contains(panel.geometry()))
        self.assertEqual(panel.height(), 250)
        self.assertEqual(panel.x(), 150)
        self.assertTrue(panel.property("_overlay_user_moved"))
        button_rect = QRect(panel.collapse_button.mapTo(panel, QPoint()), panel.collapse_button.size())
        self.assertTrue(panel.rect().contains(button_rect))

    def test_summary_metrics_and_encounter_html_are_safe_and_readable(self) -> None:
        panel = self._create_widget(PokemonEncounterSummaryWindow)
        self.assertEqual(panel.current_pokemon_label.text(), "Waiting for an encounter")
        self.assertEqual(panel.timer_label.text(), "00:00:00")
        panel.set_daily_count(1234)
        name = "Nidoran♀ <Elite> & Pikachu"
        panel.start_encounter(name, "#FACC15")

        self.assertEqual(panel.daily_count_label.text(), "1,234")
        self.assertEqual(panel.daily_count_label.accessibleName(), "Encounters today: 1,234")
        self.assertIn("&lt;Elite&gt; &amp; Pikachu", panel.current_pokemon_label.text())
        self.assertIn("#FACC15", panel.current_pokemon_label.text())
        self.assertEqual(panel.current_pokemon_label.toolTip(), name)
        self.assertEqual(panel.current_pokemon_label.accessibleName(), f"Current encounter: {name}")

    def test_timer_badge_tracks_active_and_paused_accumulated_time(self) -> None:
        panel = self._create_widget(PokemonEncounterSummaryWindow)
        self.assertEqual(panel.session_status_label.text(), "Paused")
        self.assertFalse(panel.session_status_label.property("running"))
        panel._elapsed_session_ms = 3661000
        panel.start_session_timer()
        self.assertEqual(panel.session_status_label.text(), "Active")
        self.assertTrue(panel.session_status_label.property("running"))
        panel.stop_session_timer()
        self.assertEqual(panel.timer_label.text(), "01:01:01")
        self.assertEqual(panel.session_status_label.text(), "Paused")
        panel.start_encounter("Eevee")
        self.assertEqual(panel.timer_label.text(), "01:01:01")

    def test_summary_content_scrolls_only_when_the_panel_is_compact(self) -> None:
        panel = self._create_widget(PokemonEncounterSummaryWindow)
        self._show(panel)
        scrollbar = panel.content_scroll_area.verticalScrollBar()
        self.assertEqual(scrollbar.maximum(), 0)
        panel.show_for_game(QRect(100, 100, 640, 480), size_limit=QSize(280, 180))
        self.application.processEvents()
        self.assertGreater(scrollbar.maximum(), 0)
        self.assertGreater(panel.content_scroll_area.viewport().height(), 0)
        self.assertEqual(panel.content_scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertTrue(panel.rect().contains(QRect(panel.header.pos(), panel.header.size())))


class RegionOverlayLayoutTests(_PanelUiTestCase):
    def setUp(self) -> None:
        self.panel = RegionWindow()
        self.addCleanup(self._dispose_widget, self.panel.hover_label)
        self.addCleanup(self._dispose_widget, self.panel)

    def test_feature_grid_reflows_and_scrolls_in_small_game_windows(self) -> None:
        panel = self.panel
        panel.show_for_game(QRect(100, 100, 320, 340))
        self.application.processEvents()
        self.assertTrue(QRect(100, 100, 320, 340).contains(panel.geometry()))
        self.assertGreater(panel.toolbar_scroll_area.verticalScrollBar().maximum(), 0)
        for button in panel.buttons.values():
            panel.toolbar_scroll_area.ensureWidgetVisible(button)
            self.application.processEvents()
            viewport = panel.toolbar_scroll_area.viewport()
            rect = QRect(button.mapTo(viewport, QPoint()), button.size())
            self.assertTrue(viewport.rect().contains(rect), button.text())
        self.assertEqual(panel.toolbar_scroll_area.horizontalScrollBar().maximum(), 0)
        panel.show_for_game(QRect(100, 100, 1280, 900))
        self.application.processEvents()
        self.assertEqual(panel.toolbar_scroll_area.verticalScrollBar().maximum(), 0)

    def test_feature_navigation_preserves_a_dragged_position_and_clamps_on_resize(self) -> None:
        panel = self.panel
        bounds = QRect(100, 100, 1280, 900)
        panel.show_for_game(bounds)
        panel.move(320, 240)
        panel.setProperty("_overlay_user_moved", True)
        position = QPoint(panel.pos())
        panel.buttons["fishing"].click()
        self.assertEqual(panel.pos(), position)
        panel.back_button.click()
        self.assertEqual(panel.pos(), position)
        with patch.object(panel, "raise_") as raise_window:
            panel.show_for_game(bounds)
            raise_window.assert_not_called()
        panel.show_for_game(QRect(120, 120, 340, 360))
        self.assertTrue(QRect(120, 120, 340, 360).contains(panel.geometry()))

    def test_region_cards_keep_preview_edit_remove_and_role_assignment_working(self) -> None:
        panel = self.panel
        fishing = SavedRegion("fishing", "Fishing Bar", 200, 300, 240, 50)
        chat = SavedRegion("chat-box", "Battle Chat", 150, 400, 300, 100)
        panel.set_regions((fishing, chat))
        self._show(panel)
        panel.buttons["fishing"].click()
        self.application.processEvents()
        self.assertEqual(panel.region_list.count(), 1)
        self.assertFalse(panel.empty_state.isVisible())
        self.assertFalse(panel.add_region_button.isEnabled())
        self.assertEqual(panel.buttons["fishing"].count_label.text(), "1/1")
        self.assertEqual(panel._markers[0].region, fishing)
        requested = []
        panel.test_region_requested.connect(requested.append)
        row = panel.region_list.itemWidget(panel.region_list.item(0))
        row.findChild(QPushButton, "region_test_button").click()
        self.assertEqual(requested, [fishing])
        with patch.object(panel, "edit_region") as edit:
            row.findChild(QPushButton, "region_edit_button").click()
            edit.assert_called_once_with(fishing)
        changed = []
        panel.regions_changed.connect(changed.append)
        row.findChild(QToolButton, "region_remove_button").click()
        self.application.processEvents()
        self.assertEqual(changed, [(chat,)])
        self.assertEqual(panel.regions, [chat])
        self.assertTrue(panel.empty_state.isVisible())
        self.assertTrue(panel.add_region_button.isEnabled())
        self.assertEqual(panel.buttons["fishing"].count_label.text(), "0/1")
        self.assertEqual(panel._markers, [])
        add_requests = []
        panel.add_region_requested.connect(add_requests.append)
        panel.add_region_button.click()
        self.assertEqual(add_requests, ["fishing"])

    def test_client_placement_keeps_full_capture_and_marker_coordinates(self) -> None:
        panel = self.panel
        full = QRect(100, 100, 816, 640)
        client = QRect(108, 132, 800, 600)
        region = SavedRegion("chat-box", "Battle Chat", 160, 180, 300, 100)
        panel.set_regions((region,))
        panel.show_for_game(full, ui_geometry=client)
        panel.buttons["chat-box"].click()
        self.assertTrue(client.contains(panel.geometry()))
        self.assertEqual(panel._game_geometry, full)
        self.assertEqual(panel._markers[0]._bounds, full)
        self.assertEqual(panel._markers[0].geometry(), QRect(160, 180, 300, 100))
        changed = []
        panel.regions_changed.connect(changed.append)
        # Capture/edit coordinates still use the full game-window bounds.
        panel._set_region_geometry(region, QRect(102, 104, 300, 100))
        self.assertEqual(changed[-1][0], SavedRegion("chat-box", "Battle Chat", 102, 104, 300, 100))

    def test_long_region_names_do_not_push_actions_out_of_compact_cards(self) -> None:
        panel = self.panel
        regions = tuple(
            SavedRegion("pokemon-team", f"Pokemon Team Slot {slot}", 120, 130 + slot * 20, 80, 18)
            for slot in range(1, 7)
        )
        long_region = SavedRegion("pokemon-team", "<b>Long saved region name</b> " * 8, 120, 270, 80, 18)
        panel.set_regions((*regions, long_region))
        panel.show_for_game(QRect(100, 100, 320, 340))
        panel.buttons["pokemon-team"].click()
        self.application.processEvents()
        self.assertGreater(panel.region_list.verticalScrollBar().maximum(), 0)
        last = panel.region_list.item(panel.region_list.count() - 1)
        panel.region_list.scrollToItem(last)
        self.application.processEvents()
        row = panel.region_list.itemWidget(last)
        details = row.findChild(QLabel, "region_item_details")
        self.assertEqual(details.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn(long_region.label, details.toolTip())
        for name, widget_type in (
            ("region_edit_button", QPushButton), ("region_test_button", QPushButton),
            ("region_remove_button", QToolButton),
        ):
            button = row.findChild(widget_type, name)
            self.assertTrue(row.rect().contains(QRect(button.mapTo(row, QPoint()), button.size())))
            viewport = panel.region_list.viewport()
            self.assertTrue(viewport.rect().contains(QRect(button.mapTo(viewport, QPoint()), button.size())))
        self.assertEqual(panel.region_list.horizontalScrollBar().maximum(), 0)
        for button in (panel.back_button, panel.close_button, panel.add_region_button):
            self.assertTrue(panel.rect().contains(QRect(button.mapTo(panel, QPoint()), button.size())))


class OverlayPanelPlacementTests(_PanelUiTestCase):
    def setUp(self) -> None:
        with patch("overlay.overlay_window.BattleHistory"):
            self.overlay = OverlayWindow()
        self.addCleanup(self._dispose_overlay)
        self.overlay.settings_window.show_pokemon_encounter_summary_toggle.setChecked(True)
        self.overlay.settings_window.show_chat_battle_logs_toggle.setChecked(True)
        self.overlay.settings_window.show_test_region_logs_toggle.setChecked(True)
        self.overlay._region_test_log_requested = True

    def _dispose_overlay(self) -> None:
        self.overlay.stop_tracking()
        for widget in (
            self.overlay.region_window.hover_label,
            self.overlay.fishing_preview_window,
            self.overlay.region_test_log_window,
            self.overlay.region_selector,
        ):
            widget.hide()
            widget.deleteLater()
        self._dispose_widget(self.overlay)

    def _update(self, width: int, height: int, x: int = 100, y: int = 100):
        full = QRect(x, y, width + 16, height + 40)
        client = QRect(x + 8, y + 32, width, height)
        with patch.object(self.overlay._window_tracker, "client_geometry", return_value=client):
            self.overlay._on_target_window_updated(42, full, True)
        self.application.processEvents()
        return full, client

    def _panels(self):
        return (
            self.overlay, self.overlay.pokemon_encounter_summary_window,
            self.overlay.battle_log_window, self.overlay.region_test_log_window,
        )

    def _assert_separate_and_bounded(self, bounds: QRect) -> None:
        for index, panel in enumerate(self._panels()):
            self.assertTrue(panel.isVisible(), panel.objectName())
            self.assertTrue(bounds.contains(panel.geometry()), (panel.objectName(), panel.geometry()))
            for other in self._panels()[index + 1:]:
                self.assertFalse(
                    panel.geometry().intersects(other.geometry()),
                    (panel.objectName(), other.objectName(), panel.geometry(), other.geometry()),
                )

    def test_all_four_panels_stay_separate_inside_different_client_sizes(self) -> None:
        for width, height in ((1280, 900), (1000, 700), (800, 600), (640, 480), (624, 440)):
            with self.subTest(size=(width, height)):
                _full, client = self._update(width, height)
                self._assert_separate_and_bounded(client)

    def test_toolbar_buttons_have_equal_spacing_and_padded_edges(self) -> None:
        self._update(1280, 900)
        buttons = list(self.overlay.buttons.values())
        for index, button in enumerate(buttons):
            self.assertIsInstance(button, OverlayIconButton)
            self.assertEqual(button.size(), QSize(60, 62))
            self.assertEqual(button.iconSize(), QSize(24, 24))
            self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertTrue(self.overlay.glass_container.rect().contains(button.geometry()))
            if index:
                self.assertEqual(button.y(), buttons[index - 1].y())
                self.assertEqual(button.x() - buttons[index - 1].geometry().right() - 1, 8)
        self.assertGreaterEqual(buttons[0].x(), 12)
        self.assertGreaterEqual(self.overlay.glass_container.width() - buttons[-1].geometry().right() - 1, 12)

    def test_client_ui_bounds_do_not_replace_full_window_input_coordinates(self) -> None:
        full, client = self._update(1280, 900)
        self.assertEqual(self.overlay._target_geometry, full)
        self.assertEqual(self.overlay._target_ui_geometry, client)
        self.overlay._auto_battle_input = Mock()
        region = SavedRegion("run", "Run Button", 200, 200, 40, 30)
        self.overlay.settings_window.use_mouse_clicking_toggle.setChecked(True)
        self.overlay.region_window.set_regions((region,))
        self.assertTrue(self.overlay._submit_battle_action("run", 1, "Run Button"))
        self.overlay._auto_battle_input.click_region.assert_called_once_with(
            42, region, full, settle_delay=None,
        )

    def test_region_overlay_follows_visibility_and_stays_closed_after_dismissal(self) -> None:
        full, client = self._update(640, 480)
        self.overlay.buttons["select"].click()
        panel = self.overlay.region_window
        panel.buttons["fishing"].click()
        self.assertTrue(panel.isVisible())
        self.assertTrue(client.contains(panel.geometry()))
        self.assertEqual(panel._game_geometry, full)
        self.overlay.toggle_overlay_visibility()
        self.assertFalse(panel.isVisible())
        self.overlay.toggle_overlay_visibility()
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.current_feature, "fishing")
        panel.close_button.click()
        self.assertFalse(self.overlay._region_window_requested)
        self._update(640, 480)
        self.assertFalse(panel.isVisible())
        self.assertIsNone(self.overlay._window_tracker._region_window_id)
        self.overlay.buttons["select"].click()
        self.assertTrue(panel.isVisible())

    def test_dragged_panels_keep_their_positions_and_other_panels_avoid_them(self) -> None:
        _full, client = self._update(1280, 900)
        panel = self.overlay.region_test_log_window
        panel.move(client.left() + 20, client.top() + 350)
        panel.setProperty("_overlay_user_moved", True)
        position = QPoint(panel.pos())
        self.overlay._apply_visibility()
        self.application.processEvents()
        self.assertEqual(panel.pos(), position)
        self._assert_separate_and_bounded(client)

    def test_game_moves_and_resizes_reposition_visible_logs(self) -> None:
        self._update(1280, 900)
        diagnostics_position = QPoint(self.overlay.region_test_log_window.pos())
        _full, client = self._update(800, 600, 300, 200)
        self.assertNotEqual(self.overlay.region_test_log_window.pos(), diagnostics_position)
        self._assert_separate_and_bounded(client)

    def test_hiding_the_summary_restores_log_space_and_saved_messages(self) -> None:
        self._update(800, 600)
        diagnostics = self.overlay.region_test_log_window
        diagnostics.append_entry("INFO", "Retain this diagnostic")
        compact_height = diagnostics.height()
        self.overlay.settings_window.show_pokemon_encounter_summary_toggle.setChecked(False)
        self.application.processEvents()
        self.assertFalse(self.overlay.pokemon_encounter_summary_window.isVisible())
        self.assertGreater(diagnostics.height(), compact_height)
        self.assertIn("Retain this diagnostic", diagnostics.log_view.toPlainText())

    def test_collapsing_the_summary_reflows_logs_and_survives_hiding_the_overlay(self) -> None:
        _full, client = self._update(800, 600)
        summary = self.overlay.pokemon_encounter_summary_window
        diagnostics = self.overlay.region_test_log_window
        expanded_height = summary.height()
        original_log_height = diagnostics.height()
        diagnostics.append_entry("INFO", "Keep this diagnostic while collapsing")
        QTest.mouseClick(summary.collapse_button, Qt.MouseButton.LeftButton)
        self.application.processEvents()

        self.assertLess(summary.height(), expanded_height)
        self.assertGreater(diagnostics.height(), original_log_height)
        self._assert_separate_and_bounded(client)
        self.overlay.toggle_overlay_visibility()
        self.overlay.toggle_overlay_visibility()
        _full, client = self._update(800, 600)
        self.assertTrue(summary.collapse_button.isChecked())
        self.assertTrue(summary.rarity_section.isHidden())
        self._assert_separate_and_bounded(client)
        QTest.mouseClick(summary.collapse_button, Qt.MouseButton.LeftButton)
        self.application.processEvents()
        self.assertEqual(summary.height(), expanded_height)
        self.assertEqual(diagnostics.height(), original_log_height)
        self.assertIn("Keep this diagnostic while collapsing", diagnostics.log_view.toPlainText())
        self._assert_separate_and_bounded(client)

    def test_desired_and_elite_search_popups_do_not_hide_the_overlay_when_focused(self) -> None:
        full, _client = self._update(1000, 700)
        settings = self.overlay.settings_window
        settings.stop_for_desired_pokemon_toggle.setChecked(True)
        settings.stop_for_elite_toggle.setChecked(True)
        settings.desired_pokemon_advanced_toggle.setChecked(True)
        settings.elite_pokemon_advanced_toggle.setChecked(True)
        settings._select_settings_page(1)
        self.overlay.buttons["settings"].click()
        tracker = self.overlay._window_tracker
        api = Mock()
        api.IsWindowVisible.return_value = True
        api.IsIconic.return_value = False
        api.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(
            pointer._obj, "value", 99 if hwnd == 42 else 100,
        )

        def get_window_rect(_hwnd, pointer):
            rect = pointer._obj
            rect.left, rect.top = full.left(), full.top()
            rect.right, rect.bottom = full.right() + 1, full.bottom() + 1
            return True

        api.GetWindowRect.side_effect = get_window_rect
        api.EnumWindows.side_effect = lambda callback, process_id: callback(42, process_id)
        # Qt.Popup can be ownerless on Windows. Recognize its Qt ancestry,
        # rather than depending on a mocked native owner that is not present.
        api.GetWindow.return_value = 0
        self.overlay._auto_battle_input = Mock()
        with patch.object(tracker, "_user32", api), patch.object(
            tracker, "_process_id", 99,
        ), patch("overlay.window_tracker.physical_to_logical_rect", side_effect=QRect):
            for picker in (settings.desired_pokemon_input, settings.elite_pokemon_input):
                with self.subTest(picker=picker.popup.findChild(QLabel).text()):
                    settings.content_scroll_area.ensureWidgetVisible(picker)
                    self.application.processEvents()
                    QTest.mouseClick(picker.display, Qt.MouseButton.LeftButton)
                    self.application.processEvents()
                    popup_id = int(picker.popup.winId())
                    api.GetForegroundWindow.return_value = popup_id
                    tracker._refresh()
                    self.assertTrue(self.overlay.isVisible())
                    self.assertTrue(settings.isVisible())
                    self.assertTrue(picker.popup.isVisible())
                    QTest.mouseClick(picker.search_input, Qt.MouseButton.LeftButton)
                    QTest.keyClicks(picker.search_input, "pikachu")
                    tracker._refresh()
                    self.assertTrue(settings.isVisible())
                    self.assertTrue(picker.popup.isVisible())
                    self.assertFalse(picker._items_by_name["pikachu"].isHidden())
                    self.assertTrue(picker._items_by_name["bulbasaur"].isHidden())
                    picker.rarity_filter.showPopup()
                    self.application.processEvents()
                    rarity_popup = picker.rarity_filter.view().window()
                    rarity_popup_id = int(rarity_popup.winId())
                    api.GetForegroundWindow.return_value = rarity_popup_id
                    tracker._refresh()
                    self.assertTrue(self.overlay.isVisible())
                    self.assertTrue(settings.isVisible())
                    self.assertTrue(picker.popup.isVisible())
                    self.assertTrue(rarity_popup.isVisible())
                    picker.rarity_filter.hidePopup()
                    picker.popup.hide()
            unrelated_window = self._create_widget(QWidget)
            api.GetForegroundWindow.return_value = int(unrelated_window.winId())
            tracker._refresh()
            self.assertFalse(self.overlay.isVisible())
            self.assertFalse(settings.isVisible())
        self.assertEqual(self.overlay._auto_battle_input.method_calls, [])

    def test_region_selection_hides_logs_and_restores_the_layout_afterward(self) -> None:
        _full, client = self._update(800, 600)
        self.overlay._region_selection_active = True
        self.overlay._apply_visibility()
        self.assertTrue(all(not panel.isVisible() for panel in self._panels()))
        self.assertIsNone(self.overlay._window_tracker._battle_log_window_id)
        self.assertIsNone(self.overlay._window_tracker._encounter_summary_window_id)
        self.assertIsNone(self.overlay._window_tracker._region_test_log_window_id)
        self.overlay._region_selection_active = False
        self.overlay._apply_visibility()
        self.application.processEvents()
        self._assert_separate_and_bounded(client)

    def test_overlay_visibility_applies_to_all_three_information_panels(self) -> None:
        _full, client = self._update(800, 600)
        self.overlay.toggle_overlay_visibility()
        self.assertTrue(all(not panel.isVisible() for panel in self._panels()))
        self.overlay.toggle_overlay_visibility()
        self.application.processEvents()
        self._assert_separate_and_bounded(client)

    def _start_fishing_test(self):
        full, client = self._update(1280, 900)
        region = SavedRegion("fishing", "Fishing Bar", 200, 300, 240, 50)
        with patch("overlay.overlay_window.FishingMonitor.start") as start:
            self.overlay.region_window.test_region_requested.emit(region)
            start.assert_called_once_with()
        self.application.processEvents()
        self.assertTrue(self.overlay.fishing_preview_window.isVisible())
        return self.overlay._fishing_preview_monitor, full, client

    def test_fishing_test_hides_with_the_game_and_restores_when_it_returns(self) -> None:
        monitor, full, client = self._start_fishing_test()
        preview = self.overlay.fishing_preview_window
        with patch.object(self.overlay._window_tracker, "client_geometry", return_value=client):
            self.overlay._on_target_window_updated(42, full, False)
            self.assertFalse(preview.isVisible())
            self.assertIs(self.overlay._fishing_preview_monitor, monitor)
            self.assertIsNone(self.overlay._window_tracker._fishing_preview_window_id)
            self.overlay._on_target_window_updated(42, full, True)
        self.assertTrue(preview.isVisible())
        self.assertEqual(
            self.overlay._window_tracker._fishing_preview_window_id, int(preview.winId())
        )
        self.assertTrue(client.contains(preview.geometry()))
        self.assertEqual(monitor.game_geometry, full)

    def test_fishing_test_hides_for_overlay_toggle_and_region_selection(self) -> None:
        monitor, _full, _client = self._start_fishing_test()
        preview = self.overlay.fishing_preview_window
        self.overlay.toggle_overlay_visibility()
        self.assertFalse(preview.isVisible())
        self.overlay.toggle_overlay_visibility()
        self.assertTrue(preview.isVisible())

        self.overlay._region_selection_active = True
        self.overlay._apply_visibility()
        self.assertFalse(preview.isVisible())
        self.overlay._region_selection_active = False
        self.overlay._apply_visibility()
        self.assertTrue(preview.isVisible())
        self.assertIs(self.overlay._fishing_preview_monitor, monitor)

    def test_fishing_test_keeps_its_dragged_position_without_repeated_raising(self) -> None:
        _monitor, _full, client = self._start_fishing_test()
        preview = self.overlay.fishing_preview_window
        preview.move(client.left() + 40, client.top() + 300)
        preview.setProperty("_overlay_user_moved", True)
        position = QPoint(preview.pos())
        with patch.object(preview, "raise_") as raise_window:
            self._update(1280, 900)
            self.assertEqual(preview.pos(), position)
            raise_window.assert_not_called()
        self.overlay.toggle_overlay_visibility()
        self.overlay.toggle_overlay_visibility()
        self.assertEqual(preview.pos(), position)

    def test_fishing_test_close_button_stays_reachable_with_long_status(self) -> None:
        monitor, _full, _client = self._start_fishing_test()
        preview = self.overlay.fishing_preview_window
        preview.set_capture_error("ERROR", "Could not capture the fishing region. " * 8)
        self.application.processEvents()
        close_button = preview.findChild(QToolButton)
        button_rect = QRect(close_button.mapTo(preview, QPoint()), close_button.size())
        self.assertTrue(preview.rect().contains(button_rect))
        self.assertGreater(close_button.width(), 0)
        with patch.object(monitor, "isRunning", return_value=True), patch.object(
            monitor, "requestInterruption"
        ) as interrupt, patch.object(monitor, "wait", return_value=True):
            close_button.click()
            interrupt.assert_called_once_with()
        self.assertFalse(preview.isVisible())
        self.assertIsNone(self.overlay._fishing_preview_monitor)
        self.assertIsNone(self.overlay._window_tracker._fishing_preview_window_id)
        self._update(1280, 900)
        self.assertFalse(preview.isVisible())

    def test_finished_fishing_test_does_not_leave_a_persistent_preview(self) -> None:
        monitor, _full, _client = self._start_fishing_test()
        monitor.finished.emit()
        self.assertIsNone(self.overlay._fishing_preview_monitor)
        self.assertFalse(self.overlay.fishing_preview_window.isVisible())
        self._update(1280, 900)
        self.assertFalse(self.overlay.fishing_preview_window.isVisible())

    def test_finishing_an_older_fishing_test_does_not_close_its_replacement(self) -> None:
        previous, _full, _client = self._start_fishing_test()
        replacement, _full, _client = self._start_fishing_test()
        previous.finished.emit()
        self.assertIs(self.overlay._fishing_preview_monitor, replacement)
        self.assertTrue(self.overlay.fishing_preview_window.isVisible())
        replacement.finished.emit()
        self.assertIsNone(self.overlay._fishing_preview_monitor)
        self.assertFalse(self.overlay.fishing_preview_window.isVisible())

    def test_game_disappearing_stops_fishing_test_and_prevents_reopening(self) -> None:
        _monitor, _full, _client = self._start_fishing_test()
        self.overlay._on_target_window_updated(0, QRect(), False)
        self.assertFalse(self.overlay.fishing_preview_window.isVisible())
        self.assertIsNone(self.overlay._fishing_preview_monitor)
        self._update(1280, 900)
        self.assertFalse(self.overlay.fishing_preview_window.isVisible())


class TrackerClientGeometryTests(_PanelUiTestCase):
    def _tracker(self):
        tracker = GameWindowTracker()
        self.addCleanup(tracker.deleteLater)
        find_widget = patch("overlay.window_tracker.QWidget.find", return_value=None)
        find_widget.start()
        self.addCleanup(find_widget.stop)
        api = Mock()

        def get_client_rect(_window_id, pointer):
            rect = pointer._obj
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 1264, 680
            return True

        def client_to_screen(_window_id, pointer):
            point = pointer._obj
            point.x, point.y = 108, 132
            return True

        api.GetClientRect.side_effect = get_client_rect
        api.ClientToScreen.side_effect = client_to_screen
        tracker._user32 = api
        return tracker, api

    def test_client_bounds_are_mapped_from_screen_physical_coordinates(self) -> None:
        tracker, api = self._tracker()
        full = QRect(100, 100, 1280, 720)
        client = QRect(108, 132, 1264, 680)
        with patch("overlay.window_tracker.physical_to_logical_rect", return_value=client) as convert:
            self.assertEqual(tracker.client_geometry(42, full), client)
        convert.assert_called_once_with(QRect(108, 132, 1264, 680))
        api.GetClientRect.assert_called_once()
        api.ClientToScreen.assert_called_once()

    def test_client_lookup_uses_the_same_dpi_mapping_as_the_capture_window(self) -> None:
        tracker, _api = self._tracker()
        full = QRect(50, 50, 800, 600)
        logical_client = QRect(54, 66, 632, 340)
        with patch("overlay.window_tracker.physical_to_logical_rect", return_value=logical_client):
            self.assertEqual(tracker.client_geometry(42, full), logical_client)

    def test_failed_client_lookups_fall_back_without_losing_window_bounds(self) -> None:
        full = QRect(100, 100, 1280, 720)
        for failure in ("unavailable_api", "invalid_handle", "client_rect", "screen_origin", "dpi_mapping"):
            with self.subTest(failure=failure):
                tracker, api = self._tracker()
                window_id = 42
                if failure == "unavailable_api":
                    tracker._user32 = None
                elif failure == "invalid_handle":
                    window_id = 0
                elif failure == "client_rect":
                    api.GetClientRect.side_effect = None
                    api.GetClientRect.return_value = False
                elif failure == "screen_origin":
                    api.ClientToScreen.side_effect = None
                    api.ClientToScreen.return_value = False
                with patch("overlay.window_tracker.physical_to_logical_rect", side_effect=RuntimeError("mapping unavailable")):
                    self.assertEqual(tracker.client_geometry(window_id, full), full)

    def test_empty_or_outside_client_rectangles_fall_back_to_the_game_window(self) -> None:
        tracker, _api = self._tracker()
        full = QRect(100, 100, 1280, 720)
        for invalid in (QRect(), QRect(5000, 5000, 100, 100)):
            with patch("overlay.window_tracker.physical_to_logical_rect", return_value=invalid):
                self.assertEqual(tracker.client_geometry(42, full), full)

    def test_tracking_signal_still_emits_get_window_rect_not_client_rect(self) -> None:
        tracker, api = self._tracker()
        tracker._process_id = 99
        api.IsWindowVisible.return_value = True
        api.GetWindow.return_value = 0
        api.IsIconic.return_value = False
        api.GetForegroundWindow.return_value = 42
        api.GetWindowThreadProcessId.side_effect = lambda _hwnd, pointer: setattr(pointer._obj, "value", 99)

        def get_window_rect(_hwnd, pointer):
            rect = pointer._obj
            rect.left, rect.top, rect.right, rect.bottom = 100, 100, 1380, 820
            return True

        api.GetWindowRect.side_effect = get_window_rect
        api.EnumWindows.side_effect = lambda callback, process_id: callback(42, process_id)
        updates = []
        tracker.window_updated.connect(lambda hwnd, geometry, visible: updates.append((hwnd, geometry, visible)))
        with patch("overlay.window_tracker.physical_to_logical_rect", side_effect=lambda rect: QRect(rect)):
            tracker._refresh()
        self.assertEqual(updates, [(42, QRect(100, 100, 1280, 720), True)])
        api.GetClientRect.assert_not_called()

    def test_fishing_preview_foreground_is_recognized_but_other_apps_are_not(self) -> None:
        tracker, api = self._tracker()
        tracker._process_id = 99
        tracker.set_fishing_preview_window(77)
        api.IsWindowVisible.return_value = True
        api.GetWindow.return_value = 0
        api.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(
            pointer._obj, "value", 99 if hwnd == 42 else 100
        )

        def get_window_rect(_hwnd, pointer):
            rect = pointer._obj
            rect.left, rect.top, rect.right, rect.bottom = 100, 100, 1380, 820
            return True

        api.GetWindowRect.side_effect = get_window_rect
        api.EnumWindows.side_effect = lambda callback, process_id: callback(42, process_id)
        updates = []
        tracker.window_updated.connect(
            lambda _hwnd, _geometry, visible: updates.append(visible)
        )
        for foreground, minimized, visible in (
            (77, False, True), (88, False, False), (77, True, False), (42, False, True),
        ):
            with self.subTest(foreground=foreground, minimized=minimized):
                api.GetForegroundWindow.return_value = foreground
                api.IsIconic.return_value = minimized
                with patch("overlay.window_tracker.physical_to_logical_rect", side_effect=QRect):
                    tracker._refresh()
                self.assertEqual(updates[-1], visible)

    def test_owned_settings_popups_are_trusted_only_while_their_panel_is_registered(self) -> None:
        tracker, api = self._tracker()
        tracker._process_id = 99
        tracker.set_auxiliary_window(70)
        api.IsWindowVisible.return_value = True
        api.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(
            pointer._obj, "value", 99 if hwnd == 42 else 100,
        )
        owners = {71: 70, 72: 71, 80: 81, 81: 80, 82: 83}
        api.GetWindow.side_effect = lambda hwnd, _kind: owners.get(hwnd, 0)

        def get_window_rect(_hwnd, pointer):
            rect = pointer._obj
            rect.left, rect.top, rect.right, rect.bottom = 100, 100, 1380, 820
            return True

        api.GetWindowRect.side_effect = get_window_rect
        api.EnumWindows.side_effect = lambda callback, process_id: callback(42, process_id)
        updates = []
        tracker.window_updated.connect(lambda _hwnd, _geometry, visible: updates.append(visible))
        for foreground, minimized, visible in (
            (70, False, True), (71, False, True), (72, False, True),
            (88, False, False), (82, False, False), (80, False, False),
            (None, False, False), (71, True, False), (42, False, True),
        ):
            with self.subTest(foreground=foreground, minimized=minimized):
                api.GetForegroundWindow.return_value = foreground
                api.IsIconic.return_value = minimized
                with patch("overlay.window_tracker.physical_to_logical_rect", side_effect=QRect):
                    tracker._refresh()
                self.assertEqual(updates[-1], visible)
        tracker.set_auxiliary_window(None)
        api.GetForegroundWindow.return_value = 72
        with patch("overlay.window_tracker.physical_to_logical_rect", side_effect=QRect):
            tracker._refresh()
        self.assertFalse(updates[-1])


if __name__ == "__main__":
    unittest.main()
