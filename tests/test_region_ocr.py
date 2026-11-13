import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QRect

from capture.image_processor import ImageProcessor
from capture.realtime_region_monitor import RealtimeRegionMonitor
from capture.region_test_service import RegionTestService
from core.auto_battle import choose_move_slot, has_complete_move_set
from core.health_text import parse_health_text
from core.move_text import normalize_move_text, parse_move_text
from core.pokemon_name import (
    is_elite_wild_pokemon_name,
    is_nature_matched_wild_pokemon_name,
    is_shiny_wild_pokemon_name,
    pokemon_species_name,
)
from core.special_pokemon_message import special_pokemon_message_kind
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from ocr.region_ocr import PreparedOCR, RegionOCR
from ocr.text_extractor import TextExtractor


FIXTURES = Path(__file__).parent / "fixtures" / "ocr"


def result(text: str, confidence: float = 95.0) -> OCRResult:
    return OCRResult(text, 200, 80, confidence, len(text.split()))


def crop(filename: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURES / filename))
    if image is None:
        raise AssertionError(f"Missing OCR fixture: {filename}")
    return image


def saved_region(image: np.ndarray, feature: str, label: str) -> SavedRegion:
    return SavedRegion(feature, label, 0, 0, image.shape[1], image.shape[0])


def timings() -> dict[str, float]:
    return {"capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0}


class OCRConfidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Exercise the TSV interpretation independently of the local executable.
        self.extractor = TextExtractor.__new__(TextExtractor)
        self.extractor.language = "eng"
        self.extractor.page_segmentation_mode = 11
        self.extractor.minimum_confidence = 0.0
        self.image = np.zeros((20, 80), dtype=np.uint8)
        self.data = {
            "text": ["A", "Wild", "Gyarados", "appeared!", "scenery", "noise", "i"],
            "conf": [90, 92, 94, 96, 15, 5, 80],
            "block_num": [1] * 7,
            "par_num": [1] * 7,
            "line_num": [1, 1, 1, 1, 2, 2, 3],
        }

    def test_discarded_background_lines_do_not_dilute_returned_chat_confidence(self) -> None:
        with patch("ocr.text_extractor.pytesseract.image_to_data", return_value=self.data):
            reading = self.extractor.extract(
                self.image, preserve_lines=True,
                minimum_line_confidence=60, minimum_line_characters=2,
            )
        self.assertEqual(reading.text, "A Wild Gyarados appeared!")
        self.assertEqual(reading.mean_confidence, 93.0)
        self.assertEqual(reading.word_count, 4)

    def test_rejected_chat_has_zero_confidence_and_no_words(self) -> None:
        with patch("ocr.text_extractor.pytesseract.image_to_data", return_value=self.data):
            reading = self.extractor.extract(
                self.image, preserve_lines=True, minimum_line_confidence=99,
            )
        self.assertEqual((reading.text, reading.mean_confidence, reading.word_count), ("", 0.0, 0))

    def test_single_line_mode_does_not_leak_into_the_next_region(self) -> None:
        with patch(
            "ocr.text_extractor.pytesseract.image_to_data", return_value=self.data,
        ) as engine:
            self.extractor.extract(self.image, page_segmentation_mode=7)
            self.extractor.extract(self.image)
        self.assertIn("--psm 7", engine.call_args_list[0].kwargs["config"])
        self.assertIn("--psm 11", engine.call_args_list[1].kwargs["config"])


class OCRFieldParsingTests(unittest.TestCase):
    def test_effectiveness_typography_is_normalized_without_changing_move_names(self) -> None:
        for text, expected in (
            ("Psychic Ix 16 / 16", ("Psychic", "1x", "16/16")),
            ("Draco Meteor lx 8/8", ("Draco Meteor", "1x", "8/8")),
            ("Surf ０．５ × 24/24", ("Surf", "0.5x", "24/24")),
            ("X-Scissor 2X 0/15", ("X-Scissor", "2x", "0/15")),
            ("Will-O-Wisp 10/15", ("Will-O-Wisp", "-", "10/15")),
            ("Hidden Power (Ice) 1x 15/15", ("Hidden Power (Ice)", "1x", "15/15")),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_move_text(text), expected)
        self.assertEqual(normalize_move_text("X-Scissor"), "X-Scissor")

    def test_invalid_or_missing_pp_cannot_form_a_complete_move_set(self) -> None:
        for pp in ("16/10", "8/0", "0/0", "16:16", "1616", "1000/1000"):
            with self.subTest(pp=pp):
                readings = [
                    (SavedRegion("moves", f"Move Slot {slot}", 0, 0, 133, 45),
                     result(f"Psychic 1x {pp if slot == 1 else '16/16'}"))
                    for slot in range(1, 5)
                ]
                text = RealtimeRegionMonitor._join_move_ocr_text(readings)
                self.assertFalse(has_complete_move_set(text or ""))

    def test_health_accepts_zero_percent_fractions_and_explicit_hp(self) -> None:
        for text, expected in (
            ("0%", 0), ("O%", 0), ("1O0%", 100), ("100%", 100),
            ("[HP | 26%]", 26), ("87/300", 29), ("O / 300", 0),
            ("0|300", 0), ("HP 0", 0), ("HP 50", 50),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_health_text(text), expected)

    def test_scenery_numbers_and_impossible_health_are_not_battle_evidence(self) -> None:
        for text in ("1", "2", "0", "100", "| ~2", "Turn 1", "Lv 75", "101%", "999%", "-1%", "30/20", "30/0"):
            with self.subTest(text=text):
                self.assertIsNone(parse_health_text(text))


class OCRSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Mock()
        self.reader = RegionOCR(ImageProcessor(), self.extractor)
        self.prepared = tuple(
            PreparedOCR(f"variant_{index}", 11, np.full((20, 40), index, np.uint8), bytes([index]))
            for index in range(3)
        )

    def select(self, feature, label, *readings):
        self.extractor.extract.side_effect = readings
        region = SavedRegion(feature, label, 0, 0, 133, 45)
        return self.reader.extract(self.prepared[:len(readings)], region).result

    def test_complete_move_beats_more_confident_partial_and_invalid_pp(self) -> None:
        reading = self.select(
            "moves", "Move Slot 1", result("Psychic", 99),
            result("Psychic 1x 16/10", 99), result("Psychic 1x 16/16", 93),
        )
        self.assertEqual(reading.text, "Psychic 1x 16/16")

    def test_percentage_beats_a_more_confident_bare_number(self) -> None:
        reading = self.select("health", "Ally Health", result("26", 99), result("26%", 92))
        self.assertEqual(reading.text, "26%")

    def test_turn_border_pipes_are_normalized_without_increasing_confidence(self) -> None:
        region = SavedRegion("turn", "Turn Button", 0, 0, 94, 31)
        for text, expected in (
            ("| Turn 1 |", "Turn 1"), ("Turn 12 |", "Turn 12"),
            ("|turn2|", "turn2"), ("|| TURN 34 ||", "TURN 34"),
        ):
            with self.subTest(text=text):
                reading = self.select("turn", "Turn Button", result(text, 83))
                self.assertEqual(reading.text, expected)
                self.assertEqual(reading.mean_confidence, 83)
                self.assertEqual(reading.word_count, len(expected.split()))
                self.assertEqual(self.reader._quality(reading, region), 3)

    def test_turn_cleanup_cannot_create_anchors_from_missing_digits_or_extra_content(self) -> None:
        region = SavedRegion("turn", "Turn Button", 0, 0, 94, 31)
        for text in (
            "1", "| 1 |", "| Turn |", "Turn I", "Return 1", "Turn 1 2",
            "Turn 1 HP 100%", "chat Turn 1", "Turn -1", "Turn 1%", "Turn 1,", "Turn 1 | Turn 2 |",
        ):
            with self.subTest(text=text):
                reading = self.select("turn", "Turn Button", result(text))
                self.assertEqual(reading.text, text)
                self.assertEqual(self.reader._quality(reading, region), 0)

    def test_complete_bordered_turn_beats_more_confident_unrelated_content(self) -> None:
        reading = self.select(
            "turn", "Turn Button", result("Turn 1 HP 100%", 99), result("| Turn 1 |", 83),
        )
        self.assertEqual((reading.text, reading.mean_confidence), ("Turn 1", 83))

    def test_turn_border_cleanup_does_not_change_chat_or_pokemon_names(self) -> None:
        for feature, label in (("chat-box", "Battle Chat"), ("pokemon-name", "Wild Pokemon")):
            with self.subTest(feature=feature):
                reading = self.select(feature, label, result("| Turn 1 |"))
                self.assertEqual(reading.text, "| Turn 1 |")

    def test_bare_encounter_badges_require_colored_prefix_pixels_and_letter_ocr(self) -> None:
        ordinary = self.select("pokemon-name", "Wild Pokemon", result("S Eevee"))
        self.assertEqual(ordinary.text, "S Eevee")
        self.assertFalse(is_shiny_wild_pokemon_name(ordinary.text))
        self.prepared = tuple(replace(candidate, colored_name_prefix=True) for candidate in self.prepared)
        for label, text, expected in (
            ("Wild Pokemon", "S Eevee", "[S] Eevee"),
            ("Wild Pokemon", "E Eevee", "[E] Eevee"),
            ("Pokemon Name 2", "S Eevee", "[S] Eevee"),
            ("Wild Pokemon", "Eevee", "Eevee"),
            ("Wild Pokemon", "Shedinja", "Shedinja"),
            ("Your Pokemon", "S Eevee", "S Eevee"),
        ):
            with self.subTest(label=label, text=text):
                reading = self.select("pokemon-name", label, result(text, 93))
                self.assertEqual(reading.text, expected)
                self.assertEqual(reading.mean_confidence, 93)

    def test_a_confident_species_only_reading_does_not_skip_an_observed_colored_badge(self) -> None:
        self.prepared = tuple(replace(candidate, colored_name_prefix=True) for candidate in self.prepared)
        reading = self.select(
            "pokemon-name", "Wild Pokemon", result("Eevee", 99), result("S Eevee", 93),
        )
        self.assertEqual((reading.text, reading.mean_confidence), ("[S] Eevee", 93))
        self.assertEqual(self.extractor.extract.call_count, 2)

    def test_status_move_does_not_require_an_invented_multiplier(self) -> None:
        reading = self.select("moves", "Move Slot 1", result("Protect 10/10"), result("Protect", 99))
        self.assertEqual(parse_move_text(reading.text), ("Protect", "-", "10/10"))

    def test_full_chat_beats_a_high_confidence_turn_only_reading(self) -> None:
        full = "A Wild Gyarados appeared!\nGo! Latios!\nTurn 1"
        reading = self.select("chat-box", "Battle Chat", result("Turn 1", 99), result(full, 92))
        self.assertEqual(reading.text, full)

    def test_uncertain_popup_evidence_survives_a_background_fallback(self) -> None:
        reading = self.select(
            "special-pokemon-message", "Message Box",
            result("SpeciaI Encounler!", 25), result("Ultra Ball", 96),
        )
        self.assertEqual(reading.text, "SpeciaI Encounler!")
        self.assertEqual(reading.mean_confidence, 25)

    def test_background_does_not_trigger_popup_keyword_hunting(self) -> None:
        reading = self.select(
            "special-pokemon-message", "Message Box",
            result("fh . a sew BH OU furs |__|", 32.41), result("Shiny", 96),
        )
        self.assertNotIn("Shiny", reading.text)
        self.extractor.extract.assert_called_once()

    def test_background_fallback_does_not_stop_recovery_of_known_popup_wording(self) -> None:
        reading = self.select(
            "special-pokemon-message", "Message Box",
            result("SpeciaI Encounler!", 25), result("Ultra Ball", 96),
            result("Special Encounter! You have encountered a special pokemon!", 95),
        )
        self.assertEqual(special_pokemon_message_kind(reading.text), "Special Encounter")
        self.assertEqual(reading.mean_confidence, 95)

    def test_all_engine_failures_remain_unknown_to_popup_detection(self) -> None:
        image = crop("psychic.png")
        region = saved_region(image, "special-pokemon-message", "Message Box")
        self.extractor.extract.side_effect = RuntimeError("engine unavailable")
        monitor = RealtimeRegionMonitor(
            (region,), 42, QRect(0, 0, region.width, region.height),
            image_capture=Mock(), text_extractor=self.extractor,
        )
        self.assertEqual(monitor._read_special_pokemon_message(image, timings()), (None, None))

    def test_unchanged_primary_mask_cannot_cache_a_changed_fallback_pp_reading(self) -> None:
        image = np.full((10, 20, 3), 2, dtype=np.uint8)
        region = saved_region(image, "moves", "Move Slot 1")
        processor = Mock()
        processor.process.side_effect = lambda image, threshold_method: np.full(
            (20, 40), 1 if threshold_method == "white_180" else image[0, 0, 0],
            dtype=np.uint8,
        )
        self.extractor.extract.side_effect = lambda image, **kwargs: {
            1: result("Psychic 1x", 99),
            2: result("Psychic 1x 16/16", 92),
            3: result("Psychic 1x 15/16", 92),
        }[int(image[0, 0])]
        monitor = RealtimeRegionMonitor(
            (region,), 42, QRect(0, 0, region.width, region.height),
            image_capture=Mock(), image_processor=processor, text_extractor=self.extractor,
        )
        first = monitor._read_regions(image, (region,), timings())[0][1]
        second = monitor._read_regions(image, (region,), timings())[0][1]
        self.assertEqual(first.text, "Psychic 1x 16/16")
        self.assertEqual(second, first)
        self.assertEqual(self.extractor.extract.call_count, 2)
        image[:] = 3
        third = monitor._read_regions(image, (region,), timings())[0][1]
        self.assertEqual(third.text, "Psychic 1x 15/16")
        self.assertEqual(self.extractor.extract.call_count, 4)

    def test_unused_background_fallbacks_do_not_repeat_confident_primary_ocr(self) -> None:
        image = np.full((10, 20, 3), 2, dtype=np.uint8)
        region = saved_region(image, "moves", "Move Slot 1")
        processor = Mock()
        processor.process.side_effect = lambda image, threshold_method: np.full(
            (20, 40), 1 if threshold_method == "white_180" else image[0, 0, 0],
            dtype=np.uint8,
        )
        self.extractor.extract.return_value = result("Psychic 1x 16/16", 95)
        monitor = RealtimeRegionMonitor(
            (region,), 42, QRect(0, 0, region.width, region.height),
            image_capture=Mock(), image_processor=processor, text_extractor=self.extractor,
        )
        monitor._read_regions(image, (region,), timings())
        image[:] = 3
        reading = monitor._read_regions(image, (region,), timings())[0][1]
        self.assertEqual(reading.text, "Psychic 1x 16/16")
        self.extractor.extract.assert_called_once()

    def test_team_icon_animation_does_not_change_the_name_ocr_cache(self) -> None:
        image = crop("team_button_1.png")
        region = saved_region(image, "pokemon-team", "Pokemon Team Slot 1")
        self.extractor.extract.return_value = result("Latios")
        monitor = RealtimeRegionMonitor(
            (region,), 42, QRect(0, 0, region.width, region.height),
            image_capture=Mock(), text_extractor=self.extractor,
        )
        first = monitor._read_regions(image, (region,), timings())[0][1]
        image[5:-5, 2:34] = (220, 40, 220)
        second = monitor._read_regions(image, (region,), timings())[0][1]
        self.assertEqual(first, second)
        self.extractor.extract.assert_called_once()

    def test_color_evidence_changes_invalidate_even_an_identical_monochrome_name_mask(self) -> None:
        image = np.zeros((20, 40, 3), dtype=np.uint8)
        region = saved_region(image, "pokemon-name", "Wild Pokemon")
        ordinary = self.prepared[:1]
        colored = (replace(ordinary[0], colored_name_prefix=True),)
        self.assertNotEqual(self.reader.fingerprint(ordinary), self.reader.fingerprint(colored))
        self.reader.prepare = Mock(side_effect=(ordinary, ordinary, colored))
        self.extractor.extract.return_value = result("S Eevee")
        monitor = RealtimeRegionMonitor(
            (region,), 42, QRect(0, 0, region.width, region.height),
            image_capture=Mock(), text_extractor=self.extractor,
        )
        monitor.region_ocr = self.reader
        first = monitor._read_regions(image, (region,), timings())[0][1]
        cached = monitor._read_regions(image, (region,), timings())[0][1]
        changed = monitor._read_regions(image, (region,), timings())[0][1]
        self.assertEqual(first.text, "S Eevee")
        self.assertEqual(cached, first)
        self.assertEqual(changed.text, "[S] Eevee")
        self.extractor.extract.assert_called_with(
            ordinary[0].image, page_segmentation_mode=11, preserve_lines=False,
            minimum_line_confidence=0.0, minimum_line_characters=1,
        )
        self.assertEqual(self.extractor.extract.call_count, 2)


class SavedGameOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.extractor = TextExtractor()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))
        cls.reader = RegionOCR(ImageProcessor(), cls.extractor)
        cls.cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))

    def read_image(self, image, feature, label):
        region = saved_region(image, feature, label)
        return self.reader.extract(self.reader.prepare(image, region), region).result

    def test_original_game_crops_read_exact_names_pp_health_turn_buttons_and_chat(self) -> None:
        for case in self.cases:
            with self.subTest(image=case["image"]):
                reading = self.read_image(crop(case["image"]), case["feature"], case["label"])
                self.assertEqual(reading.text, case["text"])
                self.assertGreaterEqual(reading.mean_confidence, 85)

    def test_larger_move_and_health_scales_keep_digits_and_fraction_separators(self) -> None:
        for case in self.cases:
            if case["feature"] not in ("moves", "health"):
                continue
            original = crop(case["image"])
            for scale in (1.25, 1.5, 2.0):
                with self.subTest(image=case["image"], scale=scale):
                    image = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                    reading = self.read_image(image, case["feature"], case["label"])
                    self.assertEqual(reading.text, case["text"])
                    self.assertGreaterEqual(reading.mean_confidence, 85)

    def test_icon_bearing_team_buttons_read_at_all_supported_larger_scales(self) -> None:
        names = ("Latios", "Golurk", "Zapdos", "Slaking", "Reuniclus", "Alakazam")
        for slot, name in enumerate(names, 1):
            original = crop(f"team_button_{slot}.png")
            for scale in (1.25, 1.5, 2.0):
                with self.subTest(slot=slot, scale=scale):
                    image = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                    reading = self.read_image(image, "pokemon-team", f"Pokemon Team Slot {slot}")
                    self.assertEqual(reading.text, name)
                    self.assertGreaterEqual(reading.mean_confidence, 85)

    def test_name_only_gold_adjustments_do_not_lose_the_first_letters(self) -> None:
        for slot, name in enumerate(("Latios", "Golurk", "Zapdos", "Slaking", "Reuniclus", "Alakazam"), 1):
            with self.subTest(slot=slot):
                image = crop(f"team_button_{slot}.png")[10:32, 40:128]
                self.assertEqual(ImageProcessor.team_button_text(image).shape, image.shape)
                reading = self.read_image(image, "pokemon-team", f"Pokemon Team Slot {slot}")
                self.assertEqual(reading.text, name)

    def test_map_crops_do_not_create_health_or_popup_state(self) -> None:
        for filename in ("scenery_health_1.png", "scenery_health_2.png"):
            with self.subTest(filename=filename):
                reading = self.read_image(crop(filename), "health", "Enemy Health")
                self.assertIsNone(parse_health_text(reading.text))
        for filename in ("scenery_popup_1.png", "scenery_popup_2.png"):
            with self.subTest(filename=filename):
                image = crop(filename)
                region = saved_region(image, "special-pokemon-message", "Message Box")
                monitor = RealtimeRegionMonitor(
                    (region,), 42, QRect(0, 0, region.width, region.height),
                    image_capture=Mock(), text_extractor=self.extractor,
                )
                _message, showing = monitor._read_special_pokemon_message(image, timings())
                self.assertIs(showing, False)

    def test_region_test_and_realtime_return_the_same_reading(self) -> None:
        for filename, feature, label in (
            ("psychic.png", "moves", "Move Slot 1"),
            ("ally_health_bar.png", "health", "Ally Health"),
            ("chat.png", "chat-box", "Battle Chat"),
            ("team_button_5.png", "pokemon-team", "Pokemon Team Slot 5"),
        ):
            with self.subTest(feature=feature):
                image = crop(filename)
                region = saved_region(image, feature, label)
                geometry = QRect(0, 0, region.width, region.height)
                capture = Mock(capture_window=Mock(return_value=image))
                monitor = RealtimeRegionMonitor(
                    (region,), 42, geometry, image_capture=capture, text_extractor=self.extractor,
                )
                live = monitor._read_regions(image, (region,), timings())[0][1]
                service = RegionTestService(image_capture=capture, text_extractor=self.extractor)
                tested = service.run(region, 42, geometry, Mock(), save_images=False)
                self.assertEqual(tested, live)

    def test_actual_game_ui_scale_turn_and_nature_match_name_agree_in_test_and_realtime(self) -> None:
        image = cv2.imread(str(FIXTURES.parent / "battle_placement" / "ui_scale.png"))
        self.assertIsNotNone(image, "Missing actual game UI-scale fixture")
        geometry = QRect(0, 0, 1365, 767)
        for feature, label, coordinates, expected in (
            ("turn", "Turn Button", (1182, 177, 94, 31), "Turn 1"),
            ("pokemon-name", "Wild Pokemon", (458, 174, 225, 28), "[Nature Match] Linoone"),
        ):
            with self.subTest(feature=feature):
                region = SavedRegion(feature, label, *coordinates)
                capture = Mock(capture_window=Mock(return_value=image))
                monitor = RealtimeRegionMonitor(
                    (region,), 42, geometry, image_capture=capture, text_extractor=self.extractor,
                )
                live = monitor._read_regions(image, (region,), timings())[0][1]
                service = RegionTestService(image_capture=capture, text_extractor=self.extractor)
                tested = service.run(region, 42, geometry, Mock(), save_images=False)
                self.assertEqual(tested, live)
                self.assertEqual(live.text, expected)
                self.assertGreaterEqual(live.mean_confidence, 70 if feature == "turn" else 85)
                if feature == "pokemon-name":
                    self.assertTrue(is_nature_matched_wild_pokemon_name(live.text))
                    self.assertFalse(is_shiny_wild_pokemon_name(live.text))
                    self.assertEqual(pokemon_species_name(live.text), "Linoone")

    def test_real_ocr_produces_a_complete_battle_state_and_selects_thunderbolt(self) -> None:
        canvas = np.zeros((800, 1000, 3), dtype=np.uint8)
        regions = []
        placements = (
            ("chat.png", "chat-box", "Battle Chat", 0, 0),
            ("wild_name.png", "pokemon-name", "Wild Pokemon", 350, 0),
            ("ally_name.png", "pokemon-name", "Your Pokemon", 600, 0),
            ("enemy_health_bar.png", "health", "Enemy Health", 350, 50),
            ("ally_health_bar.png", "health", "Ally Health", 600, 50),
            ("turn.png", "turn", "Turn Button", 850, 0),
            ("psychic.png", "moves", "Move Slot 1", 350, 100),
            ("draco_meteor.png", "moves", "Move Slot 2", 550, 100),
            ("surf.png", "moves", "Move Slot 3", 350, 170),
            ("thunderbolt.png", "moves", "Move Slot 4", 550, 170),
        )
        for filename, feature, label, x, y in placements:
            image = crop(filename)
            height, width = image.shape[:2]
            canvas[y:y+height, x:x+width] = image
            regions.append(SavedRegion(feature, label, x, y, width, height))
        for index, (feature, label) in enumerate((
            ("fight", "Fight Button"), ("bag", "Bag Button"),
            ("swap", "Pokemon/Swap Button"), ("run", "Run Button"),
        )):
            regions.append(SavedRegion(feature, label, 350 + index * 150, 250, 100, 30))
        monitor = RealtimeRegionMonitor(
            tuple(regions), 42, QRect(0, 0, 1000, 800),
            image_capture=Mock(capture_window=Mock(return_value=canvas)),
            text_extractor=self.extractor,
        )
        monitor._recovering_battle_menu = True
        state, _captured_at = monitor._capture_state(timings())
        self.assertTrue(state["onBattleMode"])
        self.assertTrue(state["clickStateReliable"])
        self.assertTrue(state["clickedFightButton"])
        self.assertEqual(state["wildPokemonName"], "Gyarados")
        self.assertEqual(state["yourPokemonName"], "Latios")
        self.assertEqual(state["enemyPokemonHealth"], 100)
        self.assertEqual(state["yourPokemonHealth"], 26)
        self.assertEqual(state["turn"], 1)
        self.assertTrue(has_complete_move_set(state["pokemonMoves"]))
        self.assertEqual(choose_move_slot(state["pokemonMoves"]), 4)


class SmallRenderedOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        font_path = Path("C:/Windows/Fonts/arialbd.ttf")
        if not font_path.is_file():
            raise unittest.SkipTest("Windows game-like font is unavailable.")
        try:
            cls.reader = RegionOCR(ImageProcessor(), TextExtractor())
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))
        cls.font_path = font_path

    def read_image(self, image, feature, label):
        image = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        region = saved_region(image, feature, label)
        return self.reader.extract(self.reader.prepare(image, region), region).result

    def test_small_colored_nature_shiny_and_elite_markers_keep_their_distinct_meanings(self) -> None:
        for marker, color in (
            ("[S]", "#facc15"), ("[E]", "#ff3030"), ("[S]", "#22ccff"),
            ("S", "#20ce60"), ("E", "#ff3030"),
        ):
            for size in (13, 22):
                with self.subTest(marker=marker, color=color, size=size):
                    font = ImageFont.truetype(str(self.font_path), size)
                    image = Image.new("RGB", (200, 36), "#18212e")
                    draw = ImageDraw.Draw(image)
                    draw.text((4, 4), marker, fill=color, font=font, stroke_width=1, stroke_fill="black")
                    x = 4 + draw.textlength(marker + " ", font=font)
                    draw.text((x, 4), "Eevee", fill="white", font=font, stroke_width=1, stroke_fill="black")
                    reading = self.read_image(image, "pokemon-name", "Wild Pokemon")
                    expected_marker = (
                        "[Nature Match]" if marker == "S" and color == "#20ce60"
                        else marker if marker.startswith("[") else f"[{marker}]"
                    )
                    self.assertEqual(reading.text, f"{expected_marker} Eevee")
                    self.assertEqual(is_shiny_wild_pokemon_name(reading.text), expected_marker == "[S]")
                    self.assertEqual(is_elite_wild_pokemon_name(reading.text), expected_marker == "[E]")
                    self.assertEqual(is_nature_matched_wild_pokemon_name(reading.text), expected_marker == "[Nature Match]")

    def test_white_initials_are_not_treated_as_colored_encounter_badges(self) -> None:
        for marker in ("S", "E"):
            for size in (13, 22):
                with self.subTest(marker=marker, size=size):
                    font = ImageFont.truetype(str(self.font_path), size)
                    image = Image.new("RGB", (200, 36), "#18212e")
                    draw = ImageDraw.Draw(image)
                    draw.text((4, 4), f"{marker} Eevee", fill="white", font=font, stroke_width=1, stroke_fill="black")
                    reading = self.read_image(image, "pokemon-name", "Wild Pokemon")
                    self.assertEqual(reading.text, f"{marker} Eevee")
                    self.assertFalse(is_shiny_wild_pokemon_name(reading.text))
                    self.assertFalse(is_elite_wild_pokemon_name(reading.text))

    def test_small_bag_controls_run_and_close_labels(self) -> None:
        for text, feature, role in (
            ("Run", "run", "Run Button"), ("Previous", "bag", "Previous Button"),
            ("Next", "bag", "Next Button"), ("Use Item", "bag", "Use Item Button"),
            ("Ultra Ball", "bag", "Item Name"), ("X", "special-pokemon-message", "Close Button"),
        ):
            with self.subTest(text=text):
                font = ImageFont.truetype(str(self.font_path), 13)
                image = Image.new("RGB", (32 if text == "X" else 133, 40), "#f8b822")
                draw = ImageDraw.Draw(image)
                draw.text((image.width / 2, 20), text, anchor="mm", font=font, fill="white", stroke_width=1, stroke_fill="black")
                reading = self.read_image(image, feature, role)
                self.assertEqual(reading.text, text)
                self.assertGreaterEqual(reading.mean_confidence, 85)

    def test_small_fainted_and_low_health_remain_readable(self) -> None:
        for text, expected in (("0%", 0), ("1%", 1), ("8%", 8), ("0/300", 0), ("87/300", 29)):
            with self.subTest(text=text):
                font = ImageFont.truetype(str(self.font_path), 11)
                image = Image.new("RGB", (55, 15), "#52745f")
                draw = ImageDraw.Draw(image)
                draw.text((27, 7), text, anchor="mm", font=font, fill="white", stroke_width=1, stroke_fill="black")
                reading = self.read_image(image, "health", "Ally Health")
                self.assertEqual(reading.text, text)
                self.assertEqual(parse_health_text(reading.text), expected)

    def test_small_dialog_wording_is_readable_on_dark_and_light_panels(self) -> None:
        text = "Special Encounter!\nYou have encountered a special pokemon!\nClick the close button to continue."
        for background, foreground in (("#222c3c", "white"), ("#dddddd", "black")):
            with self.subTest(background=background):
                image = Image.new("RGB", (350, 110), background)
                draw = ImageDraw.Draw(image)
                font = ImageFont.truetype(str(self.font_path.with_name("arial.ttf")), 13)
                draw.multiline_text((10, 10), text, font=font, fill=foreground, spacing=5)
                reading = self.read_image(image, "special-pokemon-message", "Message Box")
                self.assertEqual(reading.text, text.replace("\n", " "))
                self.assertEqual(special_pokemon_message_kind(reading.text), "Special Encounter")
                self.assertGreaterEqual(reading.mean_confidence, 85)


if __name__ == "__main__":
    unittest.main()
