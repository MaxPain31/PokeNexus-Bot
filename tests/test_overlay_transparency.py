import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QFont, QFontDatabase, QTextDocument, QTextTable
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

from launcher.settings_store import AppSettings, SettingsStore
from models.region_config import SavedRegion
from overlay.overlay_window import OverlayWindow
from overlay.region_edit_dialog import RegionEditDialog
from overlay.region_name_dialog import RegionNameDialog


class TransparencySettingsTests(unittest.TestCase):
    def test_new_and_legacy_settings_keep_original_backgrounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            store = SettingsStore(path)
            self.assertEqual(store.load().overlay_background_transparency, 0)
            path.write_text("auto_battle=false\n", encoding="utf-8")
            settings = store.load()
            self.assertEqual(settings.overlay_background_transparency, 0)
            self.assertFalse(settings.auto_battle)

    def test_transparency_round_trips_with_regions_and_automation_settings(self) -> None:
        region = SavedRegion("fishing", "Fishing Bar", 120, 240, 300, 40)
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            for value in (0, 63, 100):
                with self.subTest(value=value):
                    store.save(AppSettings(
                        overlay_background_transparency=value, regions=(region,),
                        auto_fishing=True, auto_fishing_press_f_on_missing_zone=True,
                        pokemon_encounter_count=1273,
                        pokemon_encounter_rarity_counts={"c": 900, "r": 12},
                    ))
                    loaded = store.load()
                    self.assertEqual(loaded.overlay_background_transparency, value)
                    self.assertEqual(loaded.regions, (region,))
                    self.assertTrue(loaded.auto_fishing)
                    self.assertTrue(loaded.auto_fishing_press_f_on_missing_zone)
                    self.assertEqual(loaded.pokemon_encounter_count, 1273)
                    self.assertEqual(loaded.pokemon_encounter_rarity_counts["r"], 12)

    def test_invalid_transparency_is_rejected_on_load_and_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            store = SettingsStore(path)
            for value in (-1, 101, "bad", 12.5, True):
                with self.subTest(value=value):
                    path.write_text(f"overlay_background_transparency={value}\n", encoding="utf-8")
                    with self.assertRaises(ValueError):
                        store.load()
                    with self.assertRaises(ValueError):
                        store.save(AppSettings(overlay_background_transparency=value))


class OverlayTransparencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "consola.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
        cls.application.setFont(QFont("Segoe UI", 9))

    def setUp(self) -> None:
        with patch("overlay.overlay_window.BattleHistory"):
            self.overlay = OverlayWindow()
        self.settings = self.overlay.settings_window
        self.addCleanup(self._dispose_overlay)

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

    @staticmethod
    def _pixel_alpha(widget, point: QPoint) -> int:
        image = widget.grab().toImage()
        scale = image.devicePixelRatio()
        return image.pixelColor(round(point.x() * scale), round(point.y() * scale)).alpha()

    def test_live_changes_fade_every_panel_background_without_window_opacity(self) -> None:
        panels = (
            (self.overlay, 248), (self.settings, 238),
            (self.overlay.region_window, 248),
            (self.overlay.pokemon_encounter_summary_window, 248),
            (self.overlay.battle_log_window, 248),
            (self.overlay.region_test_log_window, 248),
            (self.overlay.fishing_preview_window, 248),
        )
        for panel, _ in panels:
            panel.show()
        self.application.processEvents()
        for transparency in (0, 50, 100):
            self.settings.overlay_background_transparency_spin.setValue(transparency)
            self.application.processEvents()
            for panel, base_alpha in panels:
                with self.subTest(panel=panel.objectName(), transparency=transparency):
                    self.assertAlmostEqual(
                        self._pixel_alpha(panel, QPoint(panel.width() // 2, 7)),
                        round(base_alpha * (100 - transparency) / 100), delta=1,
                    )
                    self.assertEqual(panel.windowOpacity(), 1.0)

        image = self.overlay.grab().toImage()
        label = self.overlay.toolbar_title
        origin = label.mapTo(self.overlay, QPoint())
        scale = image.devicePixelRatio()
        text_alpha = max(
            image.pixelColor(round((origin.x() + x) * scale), round((origin.y() + y) * scale)).alpha()
            for x in range(label.width()) for y in range(label.height())
        )
        self.assertGreaterEqual(text_alpha, 250)

    def test_nested_content_backgrounds_clear_and_summary_values_are_preserved(self) -> None:
        summary = self.overlay.pokemon_encounter_summary_window
        summary.set_daily_count(1273)
        summary.set_rarity_counts({"c": 900, "r": 12})
        summary.start_encounter("Shiny Eevee", "#FACC15")
        summary._elapsed_session_ms = 5025000
        summary._update_timer_label()
        summary.show()
        battle = self.overlay.battle_log_window
        battle.append_chat_message("12:00:00 PM", "A wild Eevee appeared!")
        battle.show()
        preview = self.overlay.fishing_preview_window
        preview.show()
        regions = self.overlay.region_window
        region = SavedRegion("bag", "Bag Button", 100, 300, 80, 40)
        regions.set_regions((region,))
        regions.show_for_game(QRect(0, 0, 1000, 800))
        regions.buttons["bag"].click()
        self.settings.overlay_background_transparency_spin.setValue(100)
        self.application.processEvents()

        metric = summary.findChild(QFrame, "summary_metric_card")
        card = regions.findChild(QFrame, "region_card")
        for panel, child, point in (
            (summary, metric, QPoint(5, metric.height() // 2)),
            (battle, battle.log_view.viewport(), QPoint(6, battle.log_view.viewport().height() - 10)),
            (preview, preview.image_label, QPoint(6, 6)),
            (regions, card, QPoint(5, card.height() // 2)),
        ):
            with self.subTest(panel=panel.objectName()):
                self.assertEqual(self._pixel_alpha(panel, child.mapTo(panel, point)), 0)

        self.assertEqual(summary.daily_count_label.text(), "1,273")
        self.assertIn("Shiny Eevee", summary.current_pokemon_label.text())
        self.assertEqual(summary.timer_label.text(), "01:23:45")
        self.assertIn("Rare: 12", summary.rarity_counts_label.text())
        self.assertIn("A wild Eevee appeared!", battle.log_view.toPlainText())
        self.assertEqual(regions.regions, [region])

    def test_rarity_table_uses_real_qt_alpha_at_each_transparency_level(self) -> None:
        summary = self.overlay.pokemon_encounter_summary_window
        summary.set_rarity_counts({"r": 12})
        for transparency, expected_alpha in ((0, 255), (50, 128), (100, 0)):
            self.settings.overlay_background_transparency_spin.setValue(transparency)
            document = QTextDocument()
            document.setHtml(summary.rarity_counts_label.text())
            table = document.rootFrame().childFrames()[0]
            self.assertIsInstance(table, QTextTable)
            with self.subTest(transparency=transparency):
                self.assertEqual(table.cellAt(0, 0).format().background().color().alpha(), expected_alpha)
                self.assertIn("Rare: 12", document.toPlainText())

    def test_new_and_open_region_dialogs_follow_saved_transparency(self) -> None:
        self.settings.overlay_background_transparency_spin.setValue(60)
        region = SavedRegion("fight", "Fight Button", 100, 100, 50, 30)
        dialogs = (
            RegionNameDialog(("Fight Button",), self.overlay),
            RegionEditDialog(region, QRect(0, 0, 800, 600), self.overlay),
        )
        for dialog in dialogs:
            dialog.show()
        self.application.processEvents()
        for dialog in dialogs:
            self.assertEqual(dialog.property("_overlay_background_transparency"), 60)
            self.assertEqual(self._pixel_alpha(dialog, QPoint(dialog.width() // 2, 7)), 95)
        self.settings.overlay_background_transparency_spin.setValue(100)
        self.application.processEvents()
        for dialog in dialogs:
            self.assertEqual(self._pixel_alpha(dialog, QPoint(dialog.width() // 2, 7)), 0)
            dialog.close()

    def test_more_controls_fit_compact_settings_and_sync_keyboard_changes(self) -> None:
        bounds = QRect(100, 100, 400, 480)
        self.settings.show_at(bounds, QRect(150, 130, 296, 116))
        self.settings.settings_tabs._buttons[5].click()
        self.application.processEvents()
        slider = self.settings.overlay_background_transparency_slider
        spin = self.settings.overlay_background_transparency_spin
        viewport = self.settings.content_scroll_area.viewport()
        for control in (slider, spin):
            self.assertTrue(viewport.rect().contains(QRect(control.mapTo(viewport, QPoint()), control.size())))
        self.assertTrue(bounds.contains(self.settings.geometry()))
        self.assertGreater(self.settings.content_scroll_area.verticalScrollBar().maximum(), 0)
        original_geometry = self.settings.geometry()
        self.settings.setProperty("_overlay_user_moved", True)

        slider.setFocus()
        QTest.keyClick(slider, Qt.Key.Key_End)
        self.assertEqual(spin.value(), 100)
        self.assertGreaterEqual(spin.lineEdit().width(), spin.fontMetrics().horizontalAdvance("100%"))
        QTest.keyClick(slider, Qt.Key.Key_Home)
        self.assertEqual(spin.value(), 0)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(8, slider.height() // 2))
        QTest.mouseMove(slider, QPoint(slider.width() // 2, slider.height() // 2))
        self.assertTrue(slider.isSliderDown())
        self.assertGreater(spin.value(), 30)
        QTest.mouseMove(slider, QPoint(slider.width() - 10, slider.height() // 2))
        QTest.mouseRelease(
            slider, Qt.MouseButton.LeftButton,
            pos=QPoint(slider.width() - 10, slider.height() // 2),
        )
        self.assertGreater(spin.value(), 90)
        spin.setValue(62)
        self.assertEqual(slider.value(), 62)
        self.application.processEvents()
        self.assertEqual(self.settings.geometry(), original_geometry)
        self.assertTrue(self.settings.rect().contains(QRect(
            self.settings.ok_button.mapTo(self.settings, QPoint()), self.settings.ok_button.size(),
        )))


if __name__ == "__main__":
    unittest.main()
