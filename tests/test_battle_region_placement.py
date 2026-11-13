import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRect, QThread, QTimer, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from capture.battle_region_detector import BattlePanelNotFound, BattleRegionDetector, BattleRegionPlacement
from capture.battle_region_placement_task import BattleRegionPlacementService, BattleRegionPlacementTask
from capture.image_processor import ImageProcessor
from capture.fishing_monitor import FishingMonitor
from capture.realtime_region_monitor import RealtimeRegionMonitor
from capture.region_cropper import RegionCropper
from core.auto_battle import choose_move_slot, has_complete_move_set
from core.auto_fishing import FishingDetection
from core.pokemon_name import is_nature_matched_wild_pokemon_name, is_shiny_wild_pokemon_name
from launcher.settings_store import AppSettings, SettingsStore
from models.ocr_result import OCRResult
from models.region_config import AUTO_BATTLE_FEATURES, REGION_FEATURES, REGION_ROLE_LABELS, SavedRegion, merge_missing_battle_regions
from ocr.region_ocr import RegionOCR
from ocr.text_extractor import TextExtractor
from overlay.overlay_window import OverlayWindow


FIXTURES = Path(__file__).parent / "fixtures" / "battle_placement"
GEOMETRY = QRect(0, 0, 1382, 784)
PANEL = QRect(490, 191, 678, 422)
UI_SCALE_GEOMETRY = QRect(0, 0, 1365, 767)
UI_SCALE_PANEL = QRect(449, 158, 847, 527)
TEAM_NAMES = ("Latios", "Golurk", "Zapdos", "Slaking", "Reuniclus", "Alakazam")


def fixture(name="main.png"):
    image = cv2.imread(str(FIXTURES / name))
    if image is None:
        raise AssertionError(f"Missing battle-placement fixture: {name}")
    return image


def placement():
    return BattleRegionPlacement(tuple(
        SavedRegion(feature, role, rect.x(), rect.y(), rect.width(), rect.height())
        for feature, role, rect in BattleRegionDetector._pixel_regions(PANEL)
    ), QRect(PANEL))


def timings():
    return {"capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0}


class BattleRegionScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.reader = RegionOCR(ImageProcessor(), TextExtractor())
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))
        cls.detector = BattleRegionDetector(cls.reader)

    def read(self, image, region, geometry=GEOMETRY):
        cropped = RegionCropper().crop(image, region, geometry)
        return self.reader.extract(self.reader.prepare(cropped, region), region).result

    def test_main_team_and_moves_find_the_same_frame_and_all_24_battle_roles(self):
        expected = {(feature, role) for feature in AUTO_BATTLE_FEATURES for role in REGION_ROLE_LABELS[feature]}
        for name in ("main.png", "team.png", "moves.png"):
            with self.subTest(menu=name):
                found = self.detector.detect(fixture(name), GEOMETRY)
                self.assertEqual(found.panel, PANEL)
                self.assertEqual({(region.feature, region.label) for region in found.regions}, expected)
                self.assertEqual(len(found.regions), 24)
                self.assertTrue(all(GEOMETRY.contains(QRect(r.x, r.y, r.width, r.height)) for r in found.regions))

    def test_automatically_placed_names_hp_turn_and_main_buttons_read_exactly(self):
        image = fixture()
        found = self.detector.detect(image, GEOMETRY)
        expected = {
            "Your Pokemon": "Zapdos", "Wild Pokemon": "Gyarados", "Ally Health": "69%",
            "Enemy Health": "100%", "Turn Button": "Turn 3", "Fight Button": "Fight",
            "Bag Button": "Bag", "Pokemon/Swap Button": "Pokemon", "Run Button": "Run",
        }
        for region in found.regions:
            if region.label in expected:
                with self.subTest(role=region.label):
                    reading = self.read(image, region)
                    self.assertEqual(reading.text, expected[region.label])
                    self.assertGreaterEqual(reading.mean_confidence, 85)

    def test_automatically_placed_team_buttons_read_all_six_icons_and_names(self):
        image = fixture("team.png")
        found = self.detector.detect(image, GEOMETRY)
        for region, name in zip((r for r in found.regions if r.feature == "pokemon-team"), TEAM_NAMES):
            with self.subTest(role=region.label):
                reading = self.read(image, region)
                self.assertEqual(reading.text, name)
                self.assertGreaterEqual(reading.mean_confidence, 85)

    def test_game_rendered_ui_scale_finds_all_roles_and_keeps_the_marker_gutter(self):
        found = self.detector.detect(fixture("ui_scale.png"), UI_SCALE_GEOMETRY)
        self.assertEqual(found.panel, UI_SCALE_PANEL)
        self.assertEqual(len(found.regions), 24)
        self.assertEqual(
            {(region.feature, region.label) for region in found.regions},
            {(feature, role) for feature in AUTO_BATTLE_FEATURES for role in REGION_ROLE_LABELS[feature]},
        )
        self.assertTrue(all(
            UI_SCALE_GEOMETRY.contains(QRect(r.x, r.y, r.width, r.height)) for r in found.regions
        ))
        expected = {
            "Wild Pokemon": (458, 174, 225, 28), "Turn Button": (1182, 177, 94, 31),
            "Battle Chat": (47, 157, 386, 530), "Fight Button": (468, 546, 166, 57),
        }
        for region in found.regions:
            if region.label in expected:
                with self.subTest(role=region.label):
                    self.assertEqual((region.x, region.y, region.width, region.height), expected[region.label])

    def test_game_rendered_ui_scale_names_hp_turn_chat_and_main_buttons_read_exactly(self):
        image = fixture("ui_scale.png")
        found = self.detector.detect(image, UI_SCALE_GEOMETRY)
        expected = {
            "Your Pokemon": "Latios", "Wild Pokemon": "[Nature Match] Linoone", "Ally Health": "26%",
            "Enemy Health": "100%", "Turn Button": "Turn 1", "Fight Button": "Fight",
            "Bag Button": "Bag", "Pokemon/Swap Button": "Pokemon", "Run Button": "Run",
            "Battle Chat": "A Wild Linoone appeared!\nGo! Latios!\nTurn 1",
        }
        for region in found.regions:
            if region.label in expected:
                with self.subTest(role=region.label):
                    reading = self.read(image, region, UI_SCALE_GEOMETRY)
                    self.assertEqual(reading.text, expected[region.label])
                    self.assertGreaterEqual(reading.mean_confidence, 70 if region.feature == "turn" else 85)
                    if region.label == "Wild Pokemon":
                        self.assertTrue(is_nature_matched_wild_pokemon_name(reading.text))
                        self.assertFalse(is_shiny_wild_pokemon_name(reading.text))

    def test_game_rendered_ui_scale_maps_back_to_nonzero_logical_screen_coordinates(self):
        image = fixture("ui_scale.png")
        logical = QRect(-410, 120, 1092, 614)
        found = self.detector.detect(image, logical)
        self.assertEqual(found.panel, QRect(-51, 246, 678, 422))
        expected = {
            "Wild Pokemon": "[Nature Match] Linoone", "Your Pokemon": "Latios",
            "Enemy Health": "100%", "Ally Health": "26%", "Turn Button": "Turn 1",
        }
        for region in found.regions:
            self.assertTrue(logical.contains(QRect(region.x, region.y, region.width, region.height)))
            if region.label in expected:
                with self.subTest(role=region.label):
                    reading = self.read(image, region, logical)
                    self.assertEqual(reading.text, expected[region.label])
                    self.assertGreaterEqual(reading.mean_confidence, 70)

    def test_game_rendered_ui_scale_also_survives_bitmap_dpi_enlargement(self):
        original = fixture("ui_scale.png")
        logical = QRect(-410, 120, 1365, 767)
        expected_panel = QRect(39, 278, 847, 527)
        expected = {
            "Wild Pokemon": "[Nature Match] Linoone", "Your Pokemon": "Latios",
            "Enemy Health": "100%", "Ally Health": "26%", "Turn Button": "Turn 1",
        }
        for scale in (1.25, 1.5, 2.0):
            with self.subTest(scale=scale):
                image = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                found = self.detector.detect(image, logical)
                self.assertEqual(len(found.regions), 24)
                for actual, target in zip(found.panel.getRect(), expected_panel.getRect()):
                    self.assertLessEqual(abs(actual - target), 1)
                for region in found.regions:
                    self.assertTrue(logical.contains(QRect(region.x, region.y, region.width, region.height)))
                    if region.label in expected:
                        with self.subTest(role=region.label):
                            reading = self.read(image, region, logical)
                            self.assertEqual(reading.text, expected[region.label])
                            self.assertGreaterEqual(reading.mean_confidence, 70 if region.feature == "turn" else 85)

    def test_game_rendered_ui_scale_still_rejects_hidden_anchors_menu_and_frame_side(self):
        image = fixture("ui_scale.png")
        pixel_regions = {role: rect for _feature, role, rect in self.detector._pixel_regions(UI_SCALE_PANEL)}
        for role in ("Turn Button", "Ally Health", "Enemy Health"):
            with self.subTest(hidden=role), self.assertRaises(BattlePanelNotFound):
                hidden = image.copy()
                rect = pixel_regions[role]
                hidden[rect.y():rect.y() + rect.height(), rect.x():rect.x() + rect.width()] = 0
                self.detector.detect(hidden, UI_SCALE_GEOMETRY)
        for name, coordinates in (
            ("menu", (468, 546, 338, 119)), ("left frame side", (449, 158, 9, 527)),
        ):
            with self.subTest(hidden=name), self.assertRaises(BattlePanelNotFound):
                hidden = image.copy()
                x, y, width, height = coordinates
                hidden[y:y + height, x:x + width] = 0
                self.detector.detect(hidden, UI_SCALE_GEOMETRY)

    def test_game_rendered_ui_scale_produces_reliable_live_state_with_nature_match_not_shiny(self):
        image = fixture("ui_scale.png")
        found = self.detector.detect(image, UI_SCALE_GEOMETRY)
        capture = Mock(capture_window=Mock(return_value=image))
        monitor = RealtimeRegionMonitor(
            found.regions, 42, UI_SCALE_GEOMETRY, image_capture=capture,
            text_extractor=self.reader.text_extractor,
        )
        state, _ = monitor._capture_state(timings())
        capture.capture_window.assert_called_once_with(42)
        self.assertTrue(state["onBattleMode"])
        self.assertTrue(state["clickStateReliable"])
        self.assertFalse(state["clickedFightButton"])
        self.assertFalse(state["showingBagItemMenu"])
        self.assertEqual((state["yourPokemonName"], state["wildPokemonName"]), ("Latios", "Linoone"))
        self.assertEqual((state["yourPokemonHealth"], state["enemyPokemonHealth"], state["turn"]), (26, 100, 1))
        self.assertFalse(state["wildPokemonIsShiny"])
        self.assertTrue(state["wildPokemonNatureMatch"])
        self.assertFalse(state["wildPokemonIsElite"])
        self.assertEqual(state["chatBoxMessage"], "A Wild Linoone appeared!\nGo! Latios!\nTurn 1")

    def test_placement_scales_from_the_frame_and_maps_physical_pixels_to_logical_coordinates(self):
        image = fixture("moves.png")
        logical = QRect(-320, 160, 1382, 784)
        expected = placement()
        for scale in (1.25, 1.5, 2.0):
            with self.subTest(scale=scale):
                resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                found = self.detector.detect(resized, logical)
                for actual, original in zip(found.regions, expected.regions):
                    self.assertEqual((actual.feature, actual.label), (original.feature, original.label))
                    self.assertLessEqual(abs(actual.x - (original.x - 320)), 1)
                    self.assertLessEqual(abs(actual.y - (original.y + 160)), 1)
                    self.assertLessEqual(abs(actual.width - original.width), 1)
                    self.assertLessEqual(abs(actual.height - original.height), 1)
                for region in found.regions:
                    if region.feature == "moves":
                        reading = self.read(resized, region, logical)
                        self.assertRegex(reading.text, r"(?:16/16|24/24)$")
                        self.assertGreaterEqual(reading.mean_confidence, 85)

    def test_translated_panel_is_not_tied_to_a_window_center_or_screen_size(self):
        scene = fixture()[153:617, 167:1169]
        image = np.zeros((1000, 1700, 3), np.uint8)
        image[110:110 + scene.shape[0], 42:42 + scene.shape[1]] = scene
        found = self.detector.detect(image, QRect(-800, 90, 1700, 1000))
        self.assertEqual(found.panel, QRect(-435, 238, 678, 422))
        fight = next(r for r in found.regions if r.feature == "fight")
        self.assertEqual((fight.x, fight.y, fight.width, fight.height), (-420, 549, 133, 45))

    def test_overworld_empty_frames_missing_anchors_and_clipped_chat_are_rejected(self):
        image = fixture()
        no_battle = image.copy()
        no_battle[153:617, 167:1169] = (35, 85, 50)
        no_turn = image.copy()
        no_turn[206:231, 1077:1152] = 0
        no_hp = image.copy()
        no_hp[445:462, 995:1030] = 0
        no_menu = image.copy()
        no_menu[502:598, 505:915] = 0
        empty_frame = np.zeros_like(image)
        cv2.rectangle(empty_frame, (490, 191), (1167, 612), (240, 240, 240), 6)
        for name, screenshot in (
            ("overworld", no_battle), ("blank", np.zeros_like(image)), ("white rectangle", empty_frame),
            ("hidden Turn", no_turn), ("unreadable ally HP", no_hp), ("hidden menu", no_menu),
            ("chat clipped off capture", image[:, 480:]),
        ):
            with self.subTest(case=name), self.assertRaises(BattlePanelNotFound):
                self.detector.detect(screenshot, QRect(0, 0, screenshot.shape[1], screenshot.shape[0]))

    def test_two_complete_battle_panels_are_rejected_as_ambiguous(self):
        scene = fixture()[153:617, 167:1169]
        image = np.zeros((1000, 2600, 3), np.uint8)
        for x in (20, 1400):
            image[100:100 + scene.shape[0], x:x + scene.shape[1]] = scene
        with self.assertRaisesRegex(BattlePanelNotFound, "More than one"):
            self.detector.detect(image, QRect(0, 0, image.shape[1], image.shape[0]))

    def test_all_placed_regions_produce_live_battle_state_and_recover_an_open_moves_menu(self):
        image = fixture("moves.png")
        found = self.detector.detect(image, GEOMETRY)
        monitor = RealtimeRegionMonitor(
            found.regions, 42, GEOMETRY, image_capture=Mock(capture_window=Mock(return_value=image)),
            text_extractor=self.reader.text_extractor,
        )
        monitor.update_regions(found.regions, GEOMETRY)
        state, _ = monitor._capture_state(timings())
        self.assertTrue(state["onBattleMode"])
        self.assertTrue(state["clickStateReliable"])
        self.assertTrue(state["clickedFightButton"])
        self.assertFalse(state["showingBagItemMenu"])
        self.assertEqual((state["yourPokemonName"], state["wildPokemonName"]), ("Zapdos", "Gyarados"))
        self.assertEqual((state["yourPokemonHealth"], state["enemyPokemonHealth"], state["turn"]), (69, 100, 3))
        self.assertIn("Go! Latios!\n", state["chatBoxMessage"])
        self.assertIn("Heat Wave (0.5x | 16/16)", state["pokemonMoves"])
        self.assertIn("Discharge (4x | 24/24)", state["pokemonMoves"])
        self.assertIn("Hidden Power (1x | 24/24)", state["pokemonMoves"])
        self.assertIn("Roost (- | 16/16)", state["pokemonMoves"])
        self.assertTrue(has_complete_move_set(state["pokemonMoves"]))
        self.assertEqual(choose_move_slot(state["pokemonMoves"]), 2)


class BattleRegionConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_placement_service_captures_only_the_requested_window(self):
        image = fixture()
        capture, detector = Mock(), Mock()
        capture.capture_window.return_value = image
        detector.detect.return_value = placement()
        service = BattleRegionPlacementService(capture, detector)
        self.assertEqual(service.run(42, GEOMETRY), placement())
        capture.capture_window.assert_called_once_with(42)
        detector.detect.assert_called_once_with(image, GEOMETRY)

    def test_invalid_images_or_game_bounds_do_not_invoke_ocr(self):
        reader = Mock()
        detector = BattleRegionDetector(reader)
        for image, bounds in (
            (None, GEOMETRY), (np.zeros((0, 0, 3), np.uint8), GEOMETRY),
            (np.zeros((80, 100), np.uint8), GEOMETRY), (fixture(), QRect()),
        ):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                detector.detect(image, bounds)
        reader.extract.assert_not_called()

    def test_detection_still_requires_a_complete_turn_word_and_70_percent_anchor_confidence(self):
        image = fixture()
        for text, confidence, accepted in (
            ("Turn 1", 70, True), ("Turn 1", 69, False), ("1", 99, False),
            ("Turn 1 HP 100%", 99, False), ("Return 1", 99, False),
        ):
            with self.subTest(text=text, confidence=confidence):
                reader = Mock()
                reader.extract.side_effect = lambda _prepared, region: Mock(result=OCRResult(
                    text if region.feature == "turn" else "100%", region.width, region.height,
                    confidence if region.feature == "turn" else 95,
                ))
                detector = BattleRegionDetector(reader)
                if accepted:
                    self.assertEqual(len(detector.detect(image, GEOMETRY).regions), 24)
                else:
                    with self.assertRaises(BattlePanelNotFound):
                        detector.detect(image, GEOMETRY)

    def test_missing_role_merge_keeps_adjustments_legacy_labels_and_nonbattle_regions(self):
        existing = (
            SavedRegion("moves", "Move 1", 410, 420, 123, 47),
            SavedRegion("pokemon-name", "Pokemon Name 2", 400, 200, 180, 24),
            SavedRegion("health", "Player HP", 880, 430, 45, 20),
            SavedRegion("pokemon-team", "Team Slot 3", 710, 420, 140, 48),
            SavedRegion("bag", "Next", 650, 500, 30, 95),
            SavedRegion("fishing", "Fishing Bar", 100, 150, 300, 40),
            SavedRegion("special-pokemon-message", "Message Box", 420, 300, 408, 200),
        )
        merged = merge_missing_battle_regions(existing, placement().regions)
        self.assertEqual(merged[:len(existing)], existing)
        self.assertTrue(all(merged[index] is region for index, region in enumerate(existing)))
        self.assertEqual(len(merged), 26)
        self.assertEqual(merge_missing_battle_regions(merged, placement().regions), merged)

    def test_live_layout_update_is_applied_between_frames_without_resetting_encounters(self):
        old = SavedRegion("fight", "Fight Button", 100, 100, 50, 30)
        new = SavedRegion("fight", "Fight Button", 200, 200, 60, 35)
        monitor = RealtimeRegionMonitor((old,), 42, GEOMETRY, image_capture=Mock(), text_extractor=Mock())
        monitor._ocr_cache[old] = (b"old", OCRResult("Fight", 50, 30, 95), 1)
        monitor._clicked_button_states["clickedFightButton"] = True
        monitor._encounter_active = True
        monitor._encounter_battle_active = True
        monitor._encounter_pokemon_name = "Eevee"
        monitor._encounter_is_shiny = True
        monitor._encounter_is_elite = True
        monitor._last_chat_message = "A Wild [S] Eevee appeared!"
        listener = monitor._click_listener
        listener._target_process_id = 7
        listener._record_click(110, 110, 7, 7)
        monitor.update_regions((new,), QRect(-20, 30, 1382, 784))
        self.assertEqual(monitor.regions, (old,))
        monitor._apply_region_configuration()
        self.assertEqual(monitor.regions, (new,))
        self.assertEqual(monitor.game_geometry, QRect(-20, 30, 1382, 784))
        self.assertEqual(monitor._ocr_cache, {})
        self.assertTrue(monitor._recovering_battle_menu)
        self.assertFalse(monitor._clicked_button_states["clickedFightButton"])
        self.assertTrue(monitor._encounter_active)
        self.assertTrue(monitor._encounter_is_shiny)
        self.assertTrue(monitor._encounter_is_elite)
        self.assertEqual(monitor._encounter_pokemon_name, "Eevee")
        self.assertEqual(monitor._last_chat_message, "A Wild [S] Eevee appeared!")
        self.assertEqual(listener.consume_clicks(), set())
        listener._record_click(110, 110, 7, 7)
        self.assertEqual(listener.consume_clicks(), set())
        listener._record_click(210, 210, 7, 7)
        self.assertEqual(listener.consume_clicks(), {"fight"})

    def test_capture_freshness_starts_before_layout_application(self):
        monitor = RealtimeRegionMonitor(
            (), 42, GEOMETRY, image_capture=Mock(capture_window=Mock(return_value=fixture())),
            text_extractor=Mock(),
        )
        configured_at = []
        with patch("capture.realtime_region_monitor.time.monotonic", side_effect=(100.0, 101.0)), patch.object(
            monitor, "_apply_region_configuration", side_effect=lambda: configured_at.append(time.monotonic()),
        ):
            state, _ = monitor._capture_state(timings())
        self.assertEqual(state["captureStartedAt"], 100.0)
        self.assertEqual(configured_at, [101.0])

    def test_running_capture_discards_an_old_layout_frame_and_preserves_encounter_traits(self):
        old = (
            SavedRegion("fight", "Fight Button", 100, 100, 50, 30),
            SavedRegion("health", "Enemy Health", 100, 60, 50, 20),
            SavedRegion("pokemon-name", "Wild Pokemon", 100, 30, 100, 20),
        )
        new = tuple(replace(region, x=region.x + 100) for region in old)
        entered, release, published = threading.Event(), threading.Event(), threading.Event()
        captures, states, encounters = [], [], []
        image = np.zeros((784, 1382, 3), np.uint8)
        monitor = RealtimeRegionMonitor(old, 42, GEOMETRY, image_capture=Mock(), text_extractor=Mock())
        monitor._POLL_INTERVAL_SECONDS = monitor._BATTLE_POLL_INTERVAL_SECONDS = 0.005
        monitor._get_memory_usage = Mock(return_value=(0, 0, 0, 1))
        monitor._encounter_active = monitor._encounter_battle_active = True
        monitor._encounter_pokemon_name = "Eevee"
        monitor._encounter_is_shiny = monitor._encounter_is_elite = True
        monitor._ocr_cache[old[0]] = (b"old", OCRResult("Fight", 50, 30, 95), 1)

        def capture(_window_handle):
            captures.append(monitor.regions)
            if len(captures) == 1:
                entered.set()
                release.wait(3)
            return image

        def read(_image, regions, _timings):
            text = {"fight": "Fight", "health": "100%", "pokemon-name": "Eevee"}
            return [(region, OCRResult(text[region.feature], region.width, region.height, 95)) for region in regions]

        def receive(state):
            states.append(state)
            monitor.requestInterruption()
            published.set()

        def stop():
            release.set()
            monitor.requestInterruption()
            monitor.wait(4000)

        self.addCleanup(stop)
        monitor.image_capture.capture_window.side_effect = capture
        monitor._read_regions = Mock(side_effect=read)
        monitor.state_updated.connect(receive, Qt.ConnectionType.DirectConnection)
        monitor.pokemon_encountered.connect(lambda *args: encounters.append(args), Qt.ConnectionType.DirectConnection)
        # Exercise a real capture thread, but never install a game input hook.
        with patch.object(monitor._click_listener, "start"), patch.object(monitor._click_listener, "stop"), patch("builtins.print"):
            monitor.start()
            self.assertTrue(entered.wait(2), "Initial capture did not start")
            monitor.update_regions(new, GEOMETRY)
            release.set()
            self.assertTrue(published.wait(3), "Fresh layout was not published")
            self.assertTrue(monitor.wait(1500), "Capture worker did not stop")
        self.assertEqual(captures, [old, new])
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["wildPokemonName"], "Eevee")
        self.assertTrue(states[0]["wildPokemonIsShiny"])
        self.assertTrue(states[0]["wildPokemonIsElite"])
        self.assertEqual(encounters, [])
        self.assertEqual(monitor._ocr_cache, {})


class BattleRegionPlacementUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        game_input = Mock()
        with patch("overlay.overlay_window.BattleHistory"), patch(
            "overlay.overlay_window.GameInput", return_value=game_input,
        ):
            self.overlay = OverlayWindow()
        self.overlay._game_window_visible = True
        self.overlay._target_window_id = 42
        self.overlay._target_geometry = QRect(GEOMETRY)
        self.panel = self.overlay.region_window
        self._releases = []
        self.addCleanup(self.dispose)

    def dispose(self):
        for event in self._releases:
            event.set()
        self.overlay.cancel_battle_region_placement(wait=True)
        self.overlay.stop_tracking()
        for widget in (
            self.panel.hover_label, self.overlay.region_selector, self.overlay.fishing_preview_window,
            self.overlay.region_test_log_window, self.overlay,
        ):
            widget.hide()
            widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def finish_placement(self):
        if self.overlay._region_placement_runs:
            # Exercise queued worker signals and deletion through the same
            # event loop as the application, with a bounded wait.
            loop = QEventLoop()
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            for thread, _task in self.overlay._region_placement_runs:
                thread.finished.connect(loop.quit)
            QTimer.singleShot(0, lambda: loop.quit() if not self.overlay._region_placement_runs else None)
            timeout.start(5000)
            loop.exec()
            timeout.stop()
        self.application.processEvents()
        self.assertFalse(self.overlay._region_placement_runs, "Placement worker did not finish")

    def begin_manual_region(self, feature):
        if not self.overlay._region_window_requested:
            self.overlay.buttons["select"].click()
        self.panel.buttons[feature].click()
        self.panel.add_region_button.click()
        self.assertTrue(self.overlay._region_selection_active)
        self.assertEqual(self.overlay._pending_region_feature, feature)
        self.assertTrue(self.overlay.region_selector.isVisible())
        self.assertFalse(self.panel.isVisible())
        self.assertFalse(self.overlay.isVisible())

    def selector_mouse(self, event_type, point):
        selector = self.overlay.region_selector
        button = Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
        event = QMouseEvent(
            event_type, QPointF(selector.mapFromGlobal(point)), QPointF(point),
            button, buttons, Qt.KeyboardModifier.NoModifier,
        )
        self.application.sendEvent(selector, event)

    def draw_manual_region(self, geometry, choose_role):
        self.selector_mouse(QEvent.Type.MouseButtonPress, geometry.topLeft())
        self.selector_mouse(QEvent.Type.MouseMove, geometry.bottomRight())
        QTimer.singleShot(0, lambda: choose_role(self.application.activeModalWidget()))
        self.selector_mouse(QEvent.Type.MouseButtonRelease, geometry.bottomRight())

    def test_every_feature_keeps_manual_selection_separate_from_global_auto_place(self):
        self.panel.auto_place_requested.disconnect(self.overlay._start_battle_region_placement)
        self.panel.add_region_requested.disconnect(self.overlay._begin_region_selection)
        auto, manual = [], []
        self.panel.auto_place_requested.connect(lambda: auto.append(True))
        self.panel.add_region_requested.connect(manual.append)
        for feature, _label in REGION_FEATURES:
            self.panel.buttons[feature].click()
            self.assertEqual(self.panel.add_region_button.text(), "+  Add Region")
            self.assertIn("manually", self.panel.add_region_button.toolTip())
            self.panel.add_region_button.click()
        self.assertEqual(manual, [feature for feature, _label in REGION_FEATURES])
        self.assertEqual(auto, [])
        self.panel.back_button.click()
        self.assertIs(self.panel.auto_place_button.parentWidget(), self.panel.toolbar_page)
        self.panel.auto_place_button.click()
        self.assertEqual(auto, [True])
        self.assertEqual(manual, [feature for feature, _label in REGION_FEATURES])

    def test_busy_placement_disables_manual_selection_and_both_request_routes(self):
        self.panel.add_region_requested.disconnect(self.overlay._begin_region_selection)
        self.panel.auto_place_requested.disconnect(self.overlay._start_battle_region_placement)
        manual, auto = [], []
        self.panel.add_region_requested.connect(manual.append)
        self.panel.auto_place_requested.connect(lambda: auto.append(True))
        self.panel.buttons["moves"].click()
        self.panel.set_placement_busy(True)
        self.assertFalse(self.panel.add_region_button.isEnabled())
        self.panel.add_region_button.click()
        self.panel._request_add_region()
        self.panel._request_auto_place()
        self.assertEqual((manual, auto), ([], []))
        self.panel.set_placement_busy(False)
        self.panel.add_region_button.click()
        self.assertEqual(manual, ["moves"])
        self.assertEqual(auto, [])

    def test_all_24_battle_roles_can_be_drawn_manually_and_auto_place_keeps_them(self):
        service = Mock(run=Mock(return_value=placement()))
        self.overlay._region_placement_service = service
        changed, expected = [], []
        self.panel.regions_changed.connect(changed.append)
        for proposed in placement().regions:
            with self.subTest(feature=proposed.feature, role=proposed.label):
                selected = replace(proposed, x=proposed.x + 2, y=proposed.y + 1)
                self.begin_manual_region(selected.feature)

                def choose_role(dialog, role=selected.label):
                    dialog.role_input.setCurrentText(role)
                    dialog.ok_button.click()

                self.draw_manual_region(QRect(selected.x, selected.y, selected.width, selected.height), choose_role)
                expected.append(selected)
                self.assertEqual(tuple(self.panel.regions), tuple(expected))
                self.assertFalse(self.overlay._region_selection_active)
                self.assertFalse(self.overlay._region_dialog_active)
        self.assertEqual(len(changed), 24)
        for feature in AUTO_BATTLE_FEATURES:
            self.panel.buttons[feature].click()
            self.assertFalse(self.panel.add_region_button.isEnabled())
            self.assertEqual(self.panel.add_region_button.text(), "All roles configured")
        service.run.assert_not_called()
        self.panel.back_button.click()
        self.panel.auto_place_button.click()
        self.finish_placement()
        service.run.assert_called_once_with(42, GEOMETRY)
        self.assertEqual(tuple(self.panel.regions), tuple(expected))
        self.assertEqual(len(changed), 24)
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_manual_drawing_and_role_assignment_pause_input_until_the_new_layout_is_fresh(self):
        bounds = QRect(-410, 120, 1092, 614)
        selected = QRect(-35, 470, 155, 54)
        existing = SavedRegion("moves", "Move Slot 1", -300, 400, 150, 54)
        manual = SavedRegion("moves", "Move Slot 3", selected.x(), selected.y(), selected.width(), selected.height())
        monitor = Mock(window_handle=42)
        monitor.isInterruptionRequested.return_value = False
        self.overlay._target_geometry = bounds
        self.overlay._realtime_region_monitor = monitor
        self.addCleanup(setattr, self.overlay, "_realtime_region_monitor", None)
        self.overlay._realtime_capture_requested = True
        self.overlay.settings_window.auto_battle_toggle.setChecked(True)
        self.overlay._last_input_completed_at = 50.0
        self.overlay._current_encounter_name = "Eevee"
        self.overlay._current_encounter_category = "shiny"
        self.overlay._pokemon_encounter_count = 415
        self.overlay.pokemon_encounter_summary_window._elapsed_session_ms = 600_000
        self.overlay.settings_window.overlay_background_transparency_spin.setValue(65)
        self.panel.set_regions((existing,))
        state = {
            "onBattleMode": True, "turn": 3, "captureStartedAt": 100.0,
            "clickStateReliable": True, "showingFightButton": True,
            "showingBagButton": True, "showingPokemonButton": True,
            "showingRunButton": True, "wildPokemonName": "Eevee",
        }
        self.overlay._latest_realtime_state = state
        dialogs, closing, layouts = [], [], []
        self.panel.naming_dialog_changed.connect(
            lambda window_id: closing.append((
                self.overlay._region_selection_active,
                self.overlay._last_input_completed_at, tuple(self.panel.regions),
            )) if not window_id else None,
        )
        monitor.update_regions.side_effect = lambda *_args: layouts.append(self.overlay._region_selection_active)

        def choose_role(dialog):
            dialogs.append((
                dialog.objectName(), self.overlay._region_selection_active,
                self.overlay._region_dialog_active, self.overlay.region_selector.isVisible(),
                self.overlay.buttons["play"].isEnabled(),
                tuple(dialog.role_input.itemText(index) for index in range(dialog.role_input.count())),
            ))
            self.overlay._on_realtime_state_updated(state)
            dialog.role_input.setCurrentText("Move Slot 3")
            dialog.ok_button.click()

        with patch("overlay.overlay_window.time.monotonic", return_value=200.0):
            self.begin_manual_region("moves")
            self.assertEqual(self.overlay.region_selector.geometry(), bounds)
            self.draw_manual_region(selected, choose_role)
        self.assertEqual(dialogs, [(
            "region_name_dialog", True, True, False, False,
            ("Move Slot 2", "Move Slot 3", "Move Slot 4"),
        )])
        self.assertEqual(closing, [(True, 50.0, (existing,))])
        self.assertEqual(layouts, [True])
        monitor.update_regions.assert_called_once_with((existing, manual), bounds)
        self.assertEqual(tuple(self.panel.regions), (existing, manual))
        self.assertFalse(self.overlay._region_selection_active)
        self.assertFalse(self.overlay._region_dialog_active)
        self.assertIsNone(self.overlay._pending_region_feature)
        self.assertIsNone(self.overlay._window_tracker._region_selection_window_id)
        self.assertIsNone(QApplication.overrideCursor())
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(self.overlay._latest_realtime_state, {})
        self.assertEqual(self.overlay._last_input_completed_at, 200.0)
        self.assertEqual(self.overlay._current_encounter_name, "Eevee")
        self.assertEqual(self.overlay._current_encounter_category, "shiny")
        self.assertEqual(self.overlay._pokemon_encounter_count, 415)
        self.assertEqual(self.overlay.pokemon_encounter_summary_window._elapsed_session_ms, 600_000)
        self.assertEqual(self.overlay.settings_window.overlay_background_transparency_spin.value(), 65)
        for captured_at in (50.0, 100.0, 200.0):
            self.overlay._on_realtime_state_updated({**state, "captureStartedAt": captured_at})
            self.assertEqual(self.overlay._latest_realtime_state, {})
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()
        with patch("overlay.overlay_window.time.monotonic", return_value=202.0):
            self.overlay._on_realtime_state_updated({**state, "captureStartedAt": 201.0})
        self.overlay._auto_battle_input.tap_key.assert_called_once_with(
            42, ord("1"), duration_seconds=0.05, extended=False,
        )
        self.overlay._auto_battle_input.click_region.assert_not_called()
        self.overlay._battle_history.record_observation.assert_not_called()

    def test_escape_cancels_manual_selection_without_changing_saved_regions(self):
        saved = (SavedRegion("fight", "Fight Button", 400, 500, 135, 46),)
        self.panel.set_regions(saved)
        changed = []
        self.panel.regions_changed.connect(changed.append)
        self.overlay._latest_realtime_state = {"onBattleMode": True}
        self.begin_manual_region("health")
        self.assertIsNotNone(QApplication.overrideCursor())
        QTest.keyClick(self.overlay.region_selector, Qt.Key.Key_Escape)
        self.assertEqual(tuple(self.panel.regions), saved)
        self.assertEqual(changed, [])
        self.assertFalse(self.overlay._region_selection_active)
        self.assertIsNone(self.overlay._pending_region_feature)
        self.assertIsNone(self.overlay._window_tracker._region_selection_window_id)
        self.assertIsNone(QApplication.overrideCursor())
        self.assertFalse(self.overlay.region_selector.isVisible())
        self.assertTrue(self.panel.isVisible())
        self.assertTrue(self.overlay.buttons["play"].isEnabled())
        self.assertEqual(self.overlay._latest_realtime_state, {})
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_canceling_role_assignment_discards_the_drawn_region_and_keeps_input_paused_until_close(self):
        saved = (SavedRegion("moves", "Move Slot 1", 510, 503, 130, 43),)
        self.panel.set_regions(saved)
        changed, closing = [], []
        self.panel.regions_changed.connect(changed.append)
        self.panel.naming_dialog_changed.connect(
            lambda window_id: closing.append(self.overlay._region_selection_active) if not window_id else None,
        )
        self.begin_manual_region("moves")
        self.draw_manual_region(QRect(650, 503, 130, 43), lambda dialog: QTest.keyClick(dialog, Qt.Key.Key_Escape))
        self.assertEqual(tuple(self.panel.regions), saved)
        self.assertEqual(changed, [])
        self.assertEqual(closing, [True])
        self.assertFalse(self.overlay._region_selection_active)
        self.assertFalse(self.overlay._region_dialog_active)
        self.assertIsNone(self.overlay._pending_region_feature)
        self.assertIsNone(QApplication.overrideCursor())
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(self.overlay._latest_realtime_state, {})
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_manual_drags_are_clamped_to_the_game_and_tiny_selections_are_canceled(self):
        start = QPoint(GEOMETRY.right() - 40, GEOMETRY.bottom() - 30)
        outside = QPoint(GEOMETRY.right() + 80, GEOMETRY.bottom() + 80)
        expected = QRect(start, GEOMETRY.bottomRight())
        self.begin_manual_region("health")
        self.draw_manual_region(QRect(start, outside), lambda dialog: dialog.ok_button.click())
        self.assertEqual(self.panel.regions, [SavedRegion(
            "health", "Ally Health", expected.x(), expected.y(), expected.width(), expected.height(),
        )])
        self.begin_manual_region("health")
        with patch.object(self.panel, "prompt_region_name") as prompt:
            self.selector_mouse(QEvent.Type.MouseButtonPress, QPoint(700, 400))
            self.selector_mouse(QEvent.Type.MouseButtonRelease, QPoint(704, 404))
        prompt.assert_not_called()
        self.assertEqual(len(self.panel.regions), 1)
        self.assertFalse(self.overlay._region_selection_active)
        self.assertIsNone(QApplication.overrideCursor())

    def test_manual_selection_blocks_reentry_auto_place_and_automation_start(self):
        service = Mock()
        self.overlay._region_placement_service = service
        starts = []
        self.overlay.start_requested.connect(lambda: starts.append(True))
        with patch.object(self.overlay.region_selector, "begin_selection") as begin:
            for flag in ("_region_dialog_active", "_region_placement_active"):
                setattr(self.overlay, flag, True)
                self.overlay._begin_region_selection("fight")
                setattr(self.overlay, flag, False)
            begin.assert_not_called()
            self.overlay._begin_region_selection("health")
            self.overlay._begin_region_selection("moves")
            self.overlay._start_battle_region_placement()
            self.overlay._on_play()
            begin.assert_called_once_with(GEOMETRY)
            self.assertEqual(self.overlay._pending_region_feature, "health")
            self.assertFalse(self.overlay._region_placement_active)
            self.assertFalse(self.overlay._realtime_capture_requested)
            self.assertEqual(starts, [])
            self.assertFalse(self.overlay.buttons["play"].isEnabled())
            self.overlay._realtime_capture_requested = True
            with patch("overlay.overlay_window.RealtimeRegionMonitor") as capture:
                self.overlay._start_realtime_region_capture()
                capture.assert_not_called()
            self.overlay._realtime_capture_requested = False
            self.overlay._on_region_selection_cancelled()
        service.run.assert_not_called()
        self.assertFalse(self.overlay._region_selection_active)

    def test_hiding_the_overlay_cancels_manual_selection_without_saving_or_leaving_a_pause(self):
        changed = []
        self.panel.regions_changed.connect(changed.append)
        self.begin_manual_region("fight")
        self.overlay._latest_realtime_state = {"onBattleMode": True}
        self.overlay.toggle_overlay_visibility()
        self.assertFalse(self.overlay._region_selection_active)
        self.assertIsNone(self.overlay._pending_region_feature)
        self.assertFalse(self.overlay.region_selector.isVisible())
        self.assertIsNone(self.overlay._window_tracker._region_selection_window_id)
        self.assertIsNone(QApplication.overrideCursor())
        self.assertEqual(self.overlay._latest_realtime_state, {})
        self.assertTrue(self.overlay.buttons["play"].isEnabled())
        self.assertEqual(changed, [])
        self.overlay._begin_region_selection("fight")
        self.assertFalse(self.overlay._region_selection_active)
        self.assertFalse(self.overlay.region_selector.isVisible())
        self.overlay.toggle_overlay_visibility()
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(self.panel.current_feature, "fight")

    def test_auto_placement_is_off_gui_thread_and_queued_results_survive_worker_deletion(self):
        release, entered = threading.Event(), threading.Event()
        self._releases.append(release)
        worker_threads, result_threads = [], []

        def inspect_game(window_handle, bounds):
            worker_threads.append(QThread.currentThread() == self.application.thread())
            entered.set()
            release.wait(3)
            return placement()

        self.overlay._region_placement_service = Mock(run=Mock(side_effect=inspect_game))
        self.overlay.regions_changed.connect(lambda _: result_threads.append(QThread.currentThread() == self.application.thread()))
        self.panel.auto_place_button.click()
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.overlay._region_placement_active)
        self.assertFalse(self.panel.auto_place_button.isEnabled())
        self.assertFalse(self.panel.buttons["moves"].isEnabled())
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.application.processEvents()
        self.assertEqual(ticks, [True])
        release.set()
        self.finish_placement()
        self.assertEqual(worker_threads, [False])
        self.assertEqual(result_threads, [True])
        self.assertEqual(tuple(self.panel.regions), placement().regions)
        self.assertFalse(self.overlay._region_placement_active)
        self.assertTrue(self.panel.auto_place_button.isEnabled())
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_canceled_worker_can_finish_without_the_gui_delivering_queued_slots(self):
        release, entered = threading.Event(), threading.Event()
        self._releases.append(release)

        def inspect_game(*_args):
            entered.set()
            release.wait(3)
            return placement()

        self.overlay._region_placement_service = Mock(run=Mock(side_effect=inspect_game))
        self.panel.auto_place_button.click()
        thread = self.overlay._region_placement_runs[0][0]
        self.assertTrue(entered.wait(1))
        self.overlay.cancel_battle_region_placement()
        release.set()
        # No processEvents()/qWait(): quit must not be queued to this thread.
        self.assertTrue(thread.wait(1500), "Worker shutdown depends on the GUI event queue")
        self.finish_placement()
        self.assertEqual(self.panel.regions, [])
        self.assertFalse(self.overlay._region_placement_active)

    def test_fast_failure_keeps_saved_regions_and_reenables_retry(self):
        saved = (SavedRegion("fight", "Fight Button", 400, 500, 135, 46),)
        self.panel.set_regions(saved)
        self.overlay._region_placement_service = Mock(run=Mock(side_effect=BattlePanelNotFound("Enter a battle first.")))
        changed = []
        self.panel.regions_changed.connect(changed.append)
        self.panel.auto_place_button.click()
        self.finish_placement()
        self.assertEqual(tuple(self.panel.regions), saved)
        self.assertEqual(changed, [])
        self.assertIn("Enter a battle first", self.panel.placement_status_label.text())
        self.assertTrue(self.panel.placement_status_label.property("error"))
        self.assertTrue(self.panel.auto_place_button.isEnabled())

    def test_unavailable_window_does_not_start_capture(self):
        service = Mock()
        self.overlay._region_placement_service = service
        self.overlay._game_window_visible = False
        self.panel.auto_place_button.click()
        service.run.assert_not_called()
        self.assertFalse(self.overlay._region_placement_active)
        self.assertIn("visible", self.panel.placement_status_label.text())
        with patch.object(self.overlay.region_selector, "begin_selection") as begin:
            self.overlay._begin_region_selection("fight")
        begin.assert_not_called()
        self.assertFalse(self.overlay._region_selection_active)

    def test_result_is_rejected_if_window_moves_or_resizes_during_detection(self):
        original = (SavedRegion("fight", "Fight Button", 505, 502, 133, 45),)
        self.panel.set_regions(original)
        self.overlay._target_geometry = GEOMETRY.translated(20, 30)
        self.overlay._apply_battle_region_placement(placement(), 42, GEOMETRY)
        self.assertEqual(tuple(self.panel.regions), original)
        self.assertTrue(self.panel.placement_status_label.property("error"))
        self.assertIn("moved", self.panel.placement_status_label.text())

    def test_canceled_request_cannot_apply_a_late_success_or_clear_a_new_request(self):
        old = BattleRegionPlacementTask(Mock(), 42, GEOMETRY)
        new = BattleRegionPlacementTask(Mock(), 42, GEOMETRY)
        self.overlay._active_region_placement_task = new
        self.overlay._region_placement_active = True
        self.overlay._on_battle_region_placement_succeeded(old.request_id, placement())
        self.overlay._on_battle_region_placement_failed(old.request_id, "stale failure")
        self.assertEqual(self.panel.regions, [])
        self.assertTrue(self.overlay._region_placement_active)
        self.assertIs(self.overlay._active_region_placement_task, new)
        self.overlay._on_battle_region_placement_succeeded(new.request_id, placement())
        self.assertEqual(tuple(self.panel.regions), placement().regions)

    def test_a_canceled_worker_finishing_does_not_clear_a_running_retry(self):
        releases = (threading.Event(), threading.Event())
        entered = (threading.Event(), threading.Event())
        self._releases.extend(releases)
        calls = []

        def inspect_game(*_args):
            index = len(calls)
            calls.append(index)
            entered[index].set()
            releases[index].wait(3)
            return placement()

        self.overlay._region_placement_service = Mock(run=Mock(side_effect=inspect_game))
        self.panel.auto_place_button.click()
        self.assertTrue(entered[0].wait(1))
        old = self.overlay._active_region_placement_task
        self.overlay.cancel_battle_region_placement()
        self.panel.auto_place_button.click()
        self.assertTrue(entered[1].wait(1))
        retry = self.overlay._active_region_placement_task
        loop, timeout = QEventLoop(), QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        old.finished.connect(loop.quit)
        timeout.start(1500)
        releases[0].set()
        loop.exec()
        timeout.stop()
        self.application.processEvents()
        self.assertEqual(self.overlay._region_placement_runs, [(retry, retry)])
        self.assertIs(self.overlay._active_region_placement_task, retry)
        self.assertTrue(self.overlay._region_placement_active)
        self.assertFalse(self.panel.auto_place_button.isEnabled())
        self.assertEqual(self.panel.regions, [])
        releases[1].set()
        self.finish_placement()
        self.assertEqual(tuple(self.panel.regions), placement().regions)

    def test_repeat_placement_keeps_adjusted_regions_and_emits_only_real_changes(self):
        adjusted = SavedRegion("moves", "Move Slot 1", 510, 503, 130, 43)
        self.panel.set_regions((adjusted,))
        changed = []
        self.panel.regions_changed.connect(changed.append)
        self.panel.apply_automatic_regions(placement().regions)
        first = tuple(self.panel.regions)
        self.panel.apply_automatic_regions(placement().regions)
        self.assertIs(self.panel.regions[0], adjusted)
        self.assertEqual(len(first), 24)
        self.assertEqual(changed, [first])
        self.assertIn("adjustments", self.panel.placement_status_label.text())

    def test_compact_placement_controls_stay_inside_the_game_with_long_status_messages(self):
        self.panel.set_regions(placement().regions)
        message = "Automatic battle-region placement failed: " + "A long Windows capture diagnostic. " * 12
        self.panel.set_placement_status(message, error=True)
        self.assertFalse(self.panel.hasHeightForWidth())
        for width, height in ((800, 600), (350, 320), (320, 240), (240, 220)):
            with self.subTest(size=(width, height)):
                game = QRect(100, 100, width, height)
                self.panel.show_for_game(game)
                self.application.processEvents()
                self.assertTrue(game.contains(self.panel.geometry()))
                status = self.panel.placement_status_label
                status_rect = QRect(status.mapTo(self.panel, status.rect().topLeft()), status.size())
                self.assertLessEqual(status.height(), status.fontMetrics().height() * 3)
                self.assertEqual(status.toolTip(), message)
                self.assertEqual(status.accessibleName(), message)
                self.assertGreaterEqual(self.panel.toolbar_scroll_area.viewport().height(), 40)
                for button in (self.panel.auto_place_button, self.panel.reset_regions_button, self.panel.close_button):
                    rect = QRect(button.mapTo(self.panel, button.rect().topLeft()), button.size())
                    self.assertTrue(self.panel.rect().contains(rect))
                    self.assertFalse(rect.intersects(status_rect))
                    self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_reset_warning_defaults_to_cancel_and_return_does_not_delete_anything(self):
        saved = (
            SavedRegion("fight", "Fight Button", 505, 502, 133, 45),
            SavedRegion("fishing", "Fishing Bar", 300, 200, 200, 40),
        )
        self.panel.set_regions(saved)
        changed, resets, snapshots = [], [], []
        self.panel.regions_changed.connect(changed.append)
        self.panel.battle_regions_reset.connect(lambda: resets.append(True))

        def cancel_with_return():
            dialog = self.application.activeModalWidget()
            snapshots.append((dialog.objectName(), dialog.cancel_button.isDefault(), dialog.reset_button.autoDefault()))
            QTest.keyClick(dialog, Qt.Key.Key_Return)
            snapshots.append(dialog.isVisible())
            if dialog.isVisible():
                dialog.reject()

        QTimer.singleShot(0, cancel_with_return)
        self.panel.reset_regions_button.click()
        self.assertEqual(snapshots, [("region_reset_dialog", True, False), False])
        self.assertEqual(tuple(self.panel.regions), saved)
        self.assertEqual(changed, [])
        self.assertEqual(resets, [])
        self.assertFalse(self.overlay._region_dialog_active)

    def test_confirmed_reset_preserves_optional_regions_preferences_counts_and_elapsed_time(self):
        optional = (
            SavedRegion("synchronize", "Nature Match", 460, 174, 20, 28),
            SavedRegion("fishing", "Fishing Bar", 300, 200, 200, 40),
            SavedRegion("special-pokemon-message", "Message Box", 479, 303, 408, 200),
            SavedRegion("special-pokemon-message", "Close Button", 851, 303, 36, 35),
        )
        self.panel.set_regions((*placement().regions, *optional))
        self.overlay.settings_window.desired_pokemon_input.setText("[Nature Match] Alakazam")
        self.overlay.settings_window.elite_pokemon_input.setText("[Nature Match] Dragonite, Eevee")
        self.overlay._pokemon_encounter_count = 415
        self.overlay.pokemon_encounter_summary_window._elapsed_session_ms = 600_000
        self.overlay.settings_window.overlay_background_transparency_spin.setValue(65)
        changed, warnings = [], []
        self.panel.regions_changed.connect(changed.append)

        def confirm():
            dialog = self.application.activeModalWidget()
            warnings.append(dialog.findChild(QLabel, "region_reset_warning").text())
            dialog.reset_button.click()

        QTimer.singleShot(0, confirm)
        self.panel.reset_regions_button.click()
        self.assertEqual(tuple(self.panel.regions), optional)
        self.assertEqual(changed, [optional])
        self.assertIn("24 placed battle regions", warnings[0])
        self.assertIn("adjustments", warnings[0])
        self.assertIn("Synchronize", warnings[0])
        self.assertEqual(self.overlay.settings_window.desired_pokemon_input.text(), "[Nature Match] Alakazam")
        self.assertEqual(self.overlay.settings_window.elite_pokemon_input.text(), "Eevee, [Nature Match] Dragonite")
        self.assertEqual(self.overlay._pokemon_encounter_count, 415)
        self.assertEqual(self.overlay.pokemon_encounter_summary_window._elapsed_session_ms, 600_000)
        self.assertEqual(self.overlay.settings_window.overlay_background_transparency_spin.value(), 65)
        self.assertFalse(self.overlay._realtime_capture_requested)
        self.assertFalse(self.panel.reset_regions_button.isEnabled())
        self.overlay._battle_history.record_observation.assert_not_called()

    def test_reset_warning_inherits_and_live_updates_overlay_transparency(self):
        self.panel.set_regions(placement().regions)
        self.overlay.settings_window.overlay_background_transparency_spin.setValue(65)
        appearances = []

        def cancel():
            dialog = self.application.activeModalWidget()
            appearances.append((
                dialog.property("_overlay_background_transparency"),
                "background-color: rgba(17, 24, 39, 83)" in dialog.styleSheet(),
                dialog.reset_button.palette().buttonText().color().name(),
            ))
            self.overlay.settings_window.overlay_background_transparency_spin.setValue(75)
            appearances.append((
                dialog.property("_overlay_background_transparency"),
                "background-color: rgba(17, 24, 39, 60)" in dialog.styleSheet(),
                dialog.reset_button.palette().buttonText().color().name(),
            ))
            dialog.cancel_button.click()

        QTimer.singleShot(0, cancel)
        self.panel.reset_regions_button.click()
        self.assertEqual(appearances, [(65, True, "#fda4af"), (75, True, "#fda4af")])
        self.assertEqual(tuple(self.panel.regions), placement().regions)

    def test_dialog_detection_and_selection_pause_all_automated_input_until_a_fresh_frame(self):
        state = {"onBattleMode": True, "turn": 3, "captureStartedAt": time.monotonic() + 10}
        self.overlay.settings_window.auto_walk_toggle.setChecked(True)
        self.overlay._realtime_capture_requested = True
        self.overlay._realtime_monitor_start_attempted = True
        for flag in ("_region_dialog_active", "_region_placement_active", "_region_selection_active"):
            setattr(self.overlay, flag, True)
            self.overlay._on_realtime_state_updated(state)
            self.assertFalse(self.overlay._send_battle_action("fight", 3, "Fight Button", automation_name="Auto Battle", settle_delay=None))
            self.assertTrue(self.overlay._handle_special_pokemon_message({**state, "showingSpecialPokemonMessage": True}))
            self.overlay._latest_realtime_state = {"onBattleMode": False}
            self.overlay._sync_auto_walk()
            setattr(self.overlay, flag, False)
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()
        self.overlay._auto_battle_input.press_key.assert_not_called()
        self.overlay._latest_realtime_state = state
        self.overlay._resume_region_configuration()
        cutoff = self.overlay._last_input_completed_at
        self.overlay._on_realtime_state_updated({**state, "captureStartedAt": cutoff})
        self.assertEqual(self.overlay._latest_realtime_state, {})

    def test_region_edits_reconfigure_live_capture_and_do_not_restart_unrelated_fishing(self):
        monitor, fishing = Mock(), Mock()
        monitor.isInterruptionRequested.return_value = False
        self.overlay._realtime_region_monitor = monitor
        self.overlay._auto_fishing_monitor = fishing
        self.overlay._current_encounter_name = "Eevee"
        self.overlay._current_encounter_category = "shiny"
        self.overlay._latest_realtime_state = {"onBattleMode": True}
        with patch.object(self.overlay, "_sync_auto_fishing_monitor") as sync_fishing:
            self.overlay._on_regions_updated(placement().regions)
        monitor.update_regions.assert_called_once_with(placement().regions, GEOMETRY)
        fishing.requestInterruption.assert_not_called()
        fishing.set_configuration_paused.assert_called_once_with(True)
        fishing.set_battle_active.assert_not_called()
        sync_fishing.assert_called_once()
        self.assertEqual(self.overlay._latest_realtime_state, {})
        self.assertEqual(self.overlay._current_encounter_name, "Eevee")
        self.assertEqual(self.overlay._current_encounter_category, "shiny")
        self.overlay._realtime_region_monitor = None
        self.overlay._auto_fishing_monitor = None

    def test_a_frame_started_while_a_layout_update_is_queued_cannot_resume_automation(self):
        monitor, in_flight = Mock(), []
        monitor.isInterruptionRequested.return_value = False
        monitor.update_regions.side_effect = lambda *_args: in_flight.append({
            "onBattleMode": False, "captureStartedAt": time.monotonic(),
        })
        self.overlay._realtime_region_monitor = monitor
        self.addCleanup(setattr, self.overlay, "_realtime_region_monitor", None)
        with patch.object(self.overlay, "_sync_auto_fishing_monitor"):
            self.overlay._on_regions_updated(placement().regions)
        self.overlay._on_realtime_state_updated(in_flight[0])
        self.assertEqual(self.overlay._latest_realtime_state, {})
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_region_dialog_pauses_the_existing_fishing_cycle_until_fresh_battle_evidence(self):
        region = SavedRegion("fishing", "Fishing Bar", 300, 200, 200, 40)
        fishing = FishingMonitor(
            region, 42, GEOMETRY, auto_fishing=True, press_f_on_missing_zone=True,
            image_capture=Mock(), game_input=Mock(),
        )
        self.overlay.region_window.set_regions((region,))
        self.overlay.settings_window.auto_fishing_press_f_on_missing_zone_toggle.setChecked(True)
        self.overlay.settings_window.auto_fishing_toggle.setChecked(True)
        self.overlay._realtime_capture_requested = True
        self.overlay._realtime_monitor_start_attempted = True
        self.overlay._auto_fishing_monitor = fishing
        self.overlay._fishing_battle_active = False
        fishing._missing_zone_fallback_pressed = True
        fishing._overlap_latched = True
        self.overlay._region_dialog_active = True
        self.overlay._pause_region_configuration()
        self.assertIs(self.overlay._auto_fishing_monitor, fishing)
        self.assertTrue(fishing._configuration_paused)
        fishing._handle_overlap(FishingDetection(None, None, None), 100)
        fishing.game_input.tap_key.assert_not_called()
        self.assertTrue(fishing._missing_zone_fallback_pressed)
        self.assertTrue(fishing._overlap_latched)
        self.overlay._region_dialog_active = False
        with patch.object(fishing, "isRunning", return_value=True):
            self.overlay._resume_region_configuration()
        self.assertTrue(fishing._configuration_paused)
        self.assertIs(self.overlay._auto_fishing_monitor, fishing)
        self.overlay._update_fishing_battle_state({"onBattleMode": False})
        self.assertFalse(fishing._configuration_paused)
        self.assertTrue(fishing._missing_zone_fallback_pressed)
        self.assertTrue(fishing._overlap_latched)
        fishing._handle_overlap(FishingDetection(None, None, None), 101)
        fishing.game_input.tap_key.assert_not_called()
        self.overlay._auto_fishing_monitor = None
        self.overlay._realtime_capture_requested = False

    def test_manual_selection_preserves_fishing_latches_and_rejects_pre_resume_frames(self):
        region = SavedRegion("fishing", "Fishing Bar", 300, 200, 200, 40)
        fishing = FishingMonitor(
            region, 42, GEOMETRY, auto_fishing=True, press_f_on_missing_zone=True,
            image_capture=Mock(), game_input=Mock(),
        )
        self.addCleanup(fishing.deleteLater)
        self.addCleanup(setattr, self.overlay, "_auto_fishing_monitor", None)
        self.panel.set_regions((region,))
        self.overlay.settings_window.auto_fishing_press_f_on_missing_zone_toggle.setChecked(True)
        self.overlay.settings_window.auto_fishing_toggle.setChecked(True)
        self.overlay._realtime_capture_requested = True
        self.overlay._realtime_monitor_start_attempted = True
        self.overlay._auto_fishing_monitor = fishing
        self.overlay._fishing_battle_active = False
        fishing._missing_zone_fallback_pressed = fishing._overlap_latched = True
        missing = FishingDetection(None, None, None)
        with patch.object(fishing, "isRunning", return_value=True):
            self.begin_manual_region("health")
            self.assertIs(self.overlay._auto_fishing_monitor, fishing)
            self.assertTrue(fishing._configuration_paused)
            fishing._handle_overlap(missing, 100)
            self.assertTrue(fishing._missing_zone_fallback_pressed)
            self.assertTrue(fishing._overlap_latched)
            QTest.keyClick(self.overlay.region_selector, Qt.Key.Key_Escape)
            self.assertIs(self.overlay._auto_fishing_monitor, fishing)
            self.assertTrue(fishing._configuration_paused)
            cutoff = self.overlay._last_input_completed_at
            self.overlay._on_realtime_state_updated({"onBattleMode": False, "captureStartedAt": cutoff})
            self.assertTrue(fishing._configuration_paused)
            self.overlay._on_realtime_state_updated({"onBattleMode": False, "captureStartedAt": cutoff + 1})
        self.assertFalse(fishing._configuration_paused)
        self.assertTrue(fishing._missing_zone_fallback_pressed)
        self.assertTrue(fishing._overlap_latched)
        fishing._handle_overlap(missing, 101, capture_started_at=fishing._configuration_resumed_at)
        fishing._handle_overlap(missing, 102, capture_started_at=fishing._configuration_resumed_at + 1)
        fishing.game_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()
        self.assertEqual(tuple(self.panel.regions), (region,))

    def test_manual_auto_placement_and_confirmed_reset_persist_without_changing_other_settings(self):
        optional = (SavedRegion("fishing", "Fishing Bar", 300, 200, 200, 40),)
        adjusted = SavedRegion("moves", "Move Slot 1", 510, 503, 130, 43)
        saved = AppSettings(
            regions=(*optional, adjusted), pokemon_encounter_count=415,
            pokemon_encounter_rarity_counts={"c": 289, "uc": 94, "r": 31, "vr": 1, "er": 0, "l": 0},
            overlay_background_transparency=65, use_mouse_clicking=False,
            auto_close_special_pokemon_message=True, auto_switch_fainted_pokemon=True,
        )
        temp_root = Path(tempfile.gettempdir()) / "opencode"
        with tempfile.TemporaryDirectory(dir=temp_root if temp_root.is_dir() else None) as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(saved)
            self.panel.set_regions(saved.regions)
            self.overlay.regions_changed.connect(lambda regions: store.save(replace(saved, regions=regions)))
            manual_rect = QRect(420, 500, 135, 46)
            manual = SavedRegion("fight", "Fight Button", 420, 500, 135, 46)
            self.begin_manual_region("fight")
            self.draw_manual_region(manual_rect, lambda dialog: dialog.ok_button.click())
            manually_placed = (*saved.regions, manual)
            self.assertEqual(store.load(), replace(saved, regions=manually_placed))
            before_cancel = store.path.read_bytes()
            self.begin_manual_region("health")
            QTest.keyClick(self.overlay.region_selector, Qt.Key.Key_Escape)
            self.begin_manual_region("health")
            self.draw_manual_region(QRect(900, 380, 50, 24), lambda dialog: dialog.reject())
            self.assertEqual(store.path.read_bytes(), before_cancel)
            self.overlay._region_placement_service = Mock(run=Mock(return_value=placement()))
            self.panel.back_button.click()
            self.panel.auto_place_button.click()
            self.finish_placement()
            placed = store.load()
            self.assertEqual(len(placed.regions), 25)
            self.assertEqual(placed.regions[:3], manually_placed)
            self.assertEqual(replace(placed, regions=saved.regions), saved)
            self.panel._set_region_geometry(manual, manual_rect.translated(3, 2))
            self.assertEqual(store.load().regions[2], replace(manual, x=423, y=502))
            before_repeat = store.path.read_bytes()
            self.panel.auto_place_button.click()
            self.finish_placement()
            self.assertEqual(store.path.read_bytes(), before_repeat)
            before_cancel = store.path.read_bytes()
            QTimer.singleShot(0, lambda: self.application.activeModalWidget().reject())
            self.panel.reset_regions_button.click()
            self.assertEqual(store.path.read_bytes(), before_cancel)
            QTimer.singleShot(0, lambda: self.application.activeModalWidget().reset_button.click())
            self.panel.reset_regions_button.click()
            self.assertEqual(store.load(), replace(saved, regions=optional))


if __name__ == "__main__":
    unittest.main()
