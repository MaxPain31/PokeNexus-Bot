import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QRect

from capture.image_processor import ImageProcessor
from capture.realtime_region_monitor import RealtimeRegionMonitor
from capture.region_test_service import RegionTestService
from core.pokemon_name import (
    is_elite_wild_pokemon_name,
    is_nature_matched_wild_pokemon_name,
    is_shiny_wild_pokemon_name,
    pokemon_species_name,
)
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from ocr.region_ocr import PreparedOCR, RegionOCR
from ocr.text_extractor import TextExtractor


FIXTURES = Path(__file__).parent / "fixtures"


def result(text: str, confidence: float = 95.0) -> OCRResult:
    return OCRResult(text, 200, 40, confidence, len(text.split()))


def timings() -> dict[str, float]:
    return {"capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0}


class NatureMatchNameTests(unittest.TestCase):
    def test_nature_match_is_distinct_from_shiny_and_elite_and_removed_from_species(self) -> None:
        for name in (
            "[Nature Match] Eevee", "[ nature  match ] Eevee", "[Nature\nMatch] Eevee",
            "［Ｎａｔｕｒｅ Ｍａｔｃｈ］ Eevee", "Eevee [Nature Match]", "Wild [Nature Match] Eevee Lv. 20",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_nature_matched_wild_pokemon_name(name))
                self.assertFalse(is_shiny_wild_pokemon_name(name))
                self.assertFalse(is_elite_wild_pokemon_name(name))
                self.assertEqual(pokemon_species_name(name), "Eevee")

    def test_mixed_explicit_traits_and_forms_keep_all_three_independent_meanings(self) -> None:
        for name in (
            "[Nature Match] [S] [E] Raichu (Alolan) Lv. 20",
            "[S] [Nature Match] [E] Raichu (Alolan)",
            "Shiny [Nature Match] Elite Raichu (Alolan)",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_nature_matched_wild_pokemon_name(name))
                self.assertTrue(is_shiny_wild_pokemon_name(name))
                self.assertTrue(is_elite_wild_pokemon_name(name))
                self.assertEqual(pokemon_species_name(name), "Raichu (Alolan)")

    def test_bare_letters_species_names_and_other_markers_do_not_imply_nature_match(self) -> None:
        for name in (None, "", "S Eevee", "[S] Eevee", "Shiny Eevee", "[E] Eevee", "Shedinja", "Shinx", "Nature Match Eevee"):
            with self.subTest(name=name):
                self.assertFalse(is_nature_matched_wild_pokemon_name(name))


class NatureMatchOCRTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Mock()
        self.reader = RegionOCR(ImageProcessor(), self.extractor)
        self.wild = SavedRegion("pokemon-name", "Wild Pokemon", 0, 0, 200, 40)
        self.marker = SavedRegion("synchronize", "Nature Match", 0, 0, 30, 30)
        self.prepared = tuple(
            PreparedOCR(f"variant_{index}", 7, np.full((20, 40), index, np.uint8), bytes([index]), True, ("green",))
            for index in range(3)
        )

    def read(self, text, *, colors=("green",), feature="pokemon-name", label="Wild Pokemon") -> OCRResult:
        self.extractor.extract.return_value = result(text, 93)
        candidate = replace(self.prepared[0], colored_name_prefix=bool(colors), name_prefix_colors=colors)
        region = SavedRegion(feature, label, 0, 0, 200, 40)
        return self.reader.extract((candidate,), region).result

    def test_green_s_requires_both_color_evidence_and_an_s_glyph(self) -> None:
        for text, colors, expected in (
            ("S Eevee", ("green",), "[Nature Match] Eevee"),
            ("[S] Eevee", ("green",), "[Nature Match] Eevee"),
            ("s Eevee", ("green",), "[Nature Match] Eevee"),
            ("S Eevee", ("other",), "[S] Eevee"),
            ("[S] Eevee", ("other",), "[S] Eevee"),
            ("S Eevee", (), "S Eevee"),
            ("Eevee", ("green",), "Eevee"),
            ("5 Eevee", ("green",), "5 Eevee"),
            ("Shedinja", ("green",), "Shedinja"),
            ("Shinx", ("green",), "Shinx"),
            ("[E] Eevee", ("green",), "[E] Eevee"),
        ):
            with self.subTest(text=text, colors=colors):
                reading = self.read(text, colors=colors)
                self.assertEqual(reading.text, expected)
                self.assertEqual(reading.mean_confidence, 93)

    def test_ordered_colors_keep_mixed_nature_shiny_and_elite_prefixes(self) -> None:
        for text, colors, expected in (
            ("S [S] [E] Eevee", ("green", "other", "other"), "[Nature Match] [S] [E] Eevee"),
            ("[S] S [E] Eevee", ("other", "green", "other"), "[S] [Nature Match] [E] Eevee"),
            ("[E] S [S] Eevee", ("other", "green", "other"), "[E] [Nature Match] [S] Eevee"),
            ("Shiny S [E] Eevee", ("green", "other"), "Shiny [Nature Match] [E] Eevee"),
        ):
            with self.subTest(text=text):
                reading = self.read(text, colors=colors)
                self.assertEqual(reading.text, expected)
                self.assertTrue(is_nature_matched_wild_pokemon_name(reading.text))
                self.assertTrue(is_shiny_wild_pokemon_name(reading.text))
                self.assertTrue(is_elite_wild_pokemon_name(reading.text))
                self.assertEqual(pokemon_species_name(reading.text), "Eevee")

    def test_green_name_normalization_does_not_apply_to_ally_team_or_chat_text(self) -> None:
        for feature, label in (
            ("pokemon-name", "Your Pokemon"), ("pokemon-team", "Pokemon Team Slot 2"), ("chat-box", "Battle Chat"),
        ):
            with self.subTest(feature=feature):
                self.assertEqual(self.read("S Eevee", feature=feature, label=label).text, "S Eevee")

    def test_marker_only_positive_requires_green_and_exact_letter_ocr_not_numbers_or_words(self) -> None:
        for text, colors, positive in (
            ("S", ("green",), True), ("[ S ]", ("green",), True), ("s", ("green",), True),
            ("S", (), False), ("[S]", ("other",), False), ("5", ("green",), False),
            ("E", ("green",), False), ("SS", ("green",), False), ("S Eevee", ("green",), False),
            ("[Nature Match]", (), False), ("[Nature Match]", ("green",), False),
            ("[ nature  match ]", ("green",), False), ("[Nature\nMatch]", (), False),
            ("［Ｎａｔｕｒｅ Ｍａｔｃｈ］", ("green",), False), ("Eevee [Nature Match]", ("green",), False),
        ):
            with self.subTest(text=text, colors=colors):
                reading = self.read(text, colors=colors, feature="synchronize", label="Nature Match")
                self.assertEqual(is_nature_matched_wild_pokemon_name(reading.text), positive)
                self.assertFalse(is_shiny_wild_pokemon_name(reading.text) and positive)
                self.assertEqual(reading.mean_confidence, 93)

    def test_high_confidence_non_s_does_not_skip_marker_recovery_or_beat_a_readable_s(self) -> None:
        self.extractor.extract.side_effect = (result("5", 99), result("S", 82), result("s", 20))
        reading = self.reader.extract(self.prepared, self.marker)
        self.assertEqual((reading.result.text, reading.result.mean_confidence), ("[Nature Match]", 82))
        self.assertEqual(self.extractor.extract.call_count, 3)

    def test_partial_mixed_traits_do_not_skip_a_complete_readable_fallback(self) -> None:
        prepared = tuple(
            replace(candidate, name_prefix_colors=("green", "other", "other"))
            for candidate in self.prepared
        )
        self.extractor.extract.side_effect = (result("S Eevee", 99), result("S [S] [E] Eevee", 91))
        reading = self.reader.extract(prepared, self.wild).result
        self.assertEqual(reading.text, "[Nature Match] [S] [E] Eevee")
        self.assertEqual(reading.mean_confidence, 91)
        self.assertEqual(self.extractor.extract.call_count, 2)

    def test_changed_green_identity_invalidates_identical_masks_and_unchanged_color_is_cached(self) -> None:
        other = (replace(self.prepared[0], name_prefix_colors=("other",)),)
        green = self.prepared[:1]
        self.assertEqual(other[0].fingerprint, green[0].fingerprint)
        self.assertNotEqual(self.reader.fingerprint(other), self.reader.fingerprint(green))
        self.reader.prepare = Mock(side_effect=(other, green, green, other))
        self.extractor.extract.return_value = result("S Eevee")
        monitor = RealtimeRegionMonitor(
            (self.wild,), 42, QRect(0, 0, 200, 40), image_capture=Mock(), text_extractor=self.extractor,
        )
        monitor.region_ocr = self.reader
        image = np.zeros((40, 200, 3), np.uint8)
        texts, evidence = [], []
        for _ in range(4):
            monitor._nature_match_color_evidence.clear()
            observations = monitor._read_regions(image, (self.wild,), timings())
            texts.append(observations[0][1].text)
            evidence.append(monitor._read_nature_match(observations))
        self.assertEqual(texts, ["[S] Eevee", "[Nature Match] Eevee", "[Nature Match] Eevee", "[S] Eevee"])
        self.assertEqual(evidence, [False, True, True, False])
        self.assertEqual(self.extractor.extract.call_count, 3)

    def test_deduplication_does_not_merge_same_masks_with_different_marker_colors(self) -> None:
        other = replace(self.prepared[0], name_prefix_colors=("other",))
        green = self.prepared[0]
        self.extractor.extract.side_effect = (result("Eevee", 99), result("S Eevee", 93))
        reading = self.reader.extract((other, green), self.wild)
        self.assertEqual(reading.result.text, "[Nature Match] Eevee")
        self.assertEqual(self.extractor.extract.call_count, 2)

    def test_green_scenery_clipped_glyphs_and_dim_green_alone_are_not_complete_marker_evidence(self) -> None:
        pattern = np.array([
            [0, 1, 1, 1, 1, 1, 0], [1, 1, 0, 0, 0, 0, 0], [1, 1, 0, 0, 0, 0, 0],
            [0, 1, 1, 1, 1, 0, 0], [0, 0, 0, 1, 1, 1, 0], [0, 0, 0, 0, 0, 1, 1],
            [0, 0, 0, 0, 0, 1, 1], [0, 1, 1, 1, 1, 1, 0],
        ], np.uint8).astype(bool)
        enclosed = np.zeros((24, 24, 3), np.uint8)
        enclosed[7:15, 8:15][pattern] = (30, 220, 70)
        signal, evidence = ImageProcessor.nature_match_text(enclosed)
        self.assertTrue(evidence)
        self.assertTrue(signal.any())
        dim = enclosed.copy()
        dim[dim[:, :, 1] > 0] = (20, 110, 40)
        for image in (
            enclosed[7:15, 8:15], dim, np.full((24, 24, 3), (30, 220, 70), np.uint8),
            cv2.cvtColor(enclosed, cv2.COLOR_BGR2GRAY),
        ):
            with self.subTest(shape=image.shape):
                signal, evidence = ImageProcessor.nature_match_text(image)
                self.assertFalse(evidence)
                self.assertFalse(signal.any())


class NatureMatchRealtimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.regions = (
            SavedRegion("chat-box", "Battle Chat", 0, 0, 200, 80),
            SavedRegion("pokemon-name", "Wild Pokemon", 200, 0, 200, 40),
            SavedRegion("pokemon-name", "Your Pokemon", 200, 200, 200, 40),
            SavedRegion("synchronize", "Nature Match", 180, 0, 20, 40),
        )
        self.monitor = RealtimeRegionMonitor(
            self.regions, 42, QRect(0, 0, 800, 600),
            image_capture=Mock(capture_window=Mock(return_value=np.zeros((600, 800, 3), np.uint8))),
            text_extractor=Mock(),
        )
        self.readings = {"Battle Chat": result("A wild Eevee appeared!"), "Your Pokemon": result("Alakazam")}
        self.color_evidence = {"Wild Pokemon": False, "Nature Match": False}
        self.failed_labels: set[str] = set()

        def read_regions(_image, requested, _timings):
            for region in requested:
                if region.label in self.color_evidence:
                    self.monitor._nature_match_color_evidence[region] = self.color_evidence[region.label]
            return [
                (region, self.readings.get(region.label, result(""))) for region in requested
                if region.label not in self.failed_labels
            ]

        self.monitor._read_regions = Mock(side_effect=read_regions)

    def capture(self):
        return self.monitor._capture_state(timings())[0]

    def test_full_name_and_optional_marker_supply_live_nature_evidence_not_shiny(self) -> None:
        for source in ("Wild Pokemon", "Nature Match"):
            with self.subTest(source=source):
                self.readings["Wild Pokemon"] = result("Eevee")
                self.readings["Nature Match"] = result("")
                self.readings[source] = result("[Nature Match] Eevee" if source == "Wild Pokemon" else "[Nature Match]", 82)
                state = self.capture()
                self.assertTrue(state["onBattleMode"])
                self.assertTrue(state["wildPokemonNatureMatch"])
                self.assertFalse(state["wildPokemonIsShiny"])
                self.assertFalse(state["wildPokemonIsElite"])
                self.assertEqual(pokemon_species_name(state["wildPokemonName"]), "Eevee")

    def test_low_confidence_or_ally_only_nature_markers_are_not_wild_evidence(self) -> None:
        self.readings["Your Pokemon"] = result("[Nature Match] Alakazam")
        self.readings["Wild Pokemon"] = result("[Nature Match] Eevee", 49)
        self.readings["Nature Match"] = result("[Nature Match]", 49)
        self.color_evidence.update({"Wild Pokemon": True, "Nature Match": True})
        self.assertIsNone(self.capture()["wildPokemonNatureMatch"])

    def test_nature_match_does_not_activate_battle_mode_without_real_battle_evidence(self) -> None:
        self.readings["Battle Chat"] = result("")
        self.readings["Wild Pokemon"] = result("[Nature Match] Eevee")
        self.readings["Nature Match"] = result("[Nature Match]")
        self.assertFalse(self.capture()["onBattleMode"])

    def test_nature_match_changes_are_not_latched_or_counted_as_new_encounters(self) -> None:
        states, encounters, updates = [], [], []
        self.monitor.state_updated.connect(states.append)
        self.monitor.pokemon_encountered.connect(lambda name, state: encounters.append((name, state)))
        self.monitor.pokemon_encounter_updated.connect(lambda name, state: updates.append((name, state)))
        for nature, shiny, elite in ((True, True, True), (False, False, False), (None, False, False), (True, False, False)):
            self.monitor._publish_state({
                "onBattleMode": True, "wildPokemonName": "Eevee", "wildPokemonNatureMatch": nature,
                "wildPokemonIsShiny": shiny, "wildPokemonIsElite": elite,
            }, "12:00:00 PM")
        self.assertEqual([state["wildPokemonNatureMatch"] for state in states], [True, False, None, True])
        self.assertTrue(all(state["wildPokemonIsShiny"] and state["wildPokemonIsElite"] for state in states))
        self.assertEqual(len(encounters), 1)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][0], "Eevee")
        self.assertIs(updates[0][1]["wildPokemonNatureMatch"], True)

    def test_unreadable_green_glyph_is_unknown_not_absent_even_with_readable_species(self) -> None:
        self.readings["Wild Pokemon"] = result("Eevee")
        for source in ("Wild Pokemon", "Nature Match"):
            with self.subTest(source=source):
                self.color_evidence = {"Wild Pokemon": False, "Nature Match": False, source: True}
                self.readings["Nature Match"] = result("5", 95)
                self.assertIsNone(self.capture()["wildPokemonNatureMatch"])

    def test_reliable_absent_color_evidence_returns_false_and_is_fresh_each_capture(self) -> None:
        self.readings["Wild Pokemon"] = result("Eevee")
        self.color_evidence["Nature Match"] = True
        self.assertIsNone(self.capture()["wildPokemonNatureMatch"])
        self.color_evidence["Nature Match"] = False
        self.assertIs(self.capture()["wildPokemonNatureMatch"], False)
        self.readings["Nature Match"] = result("[Nature Match]", 82)
        self.assertIs(self.capture()["wildPokemonNatureMatch"], True)
        self.readings["Nature Match"] = result("")
        self.assertIs(self.capture()["wildPokemonNatureMatch"], False)

    def test_failed_optional_marker_is_unknown_unless_another_region_proves_green_s(self) -> None:
        self.readings["Wild Pokemon"] = result("Eevee")
        self.color_evidence.pop("Nature Match")
        self.failed_labels.add("Nature Match")
        self.assertIsNone(self.capture()["wildPokemonNatureMatch"])
        self.readings["Wild Pokemon"] = result("[Nature Match] Eevee", 82)
        self.assertIs(self.capture()["wildPokemonNatureMatch"], True)

    def test_failed_green_name_ocr_cannot_be_overridden_by_absent_optional_marker(self) -> None:
        self.color_evidence["Wild Pokemon"] = True
        self.failed_labels.add("Wild Pokemon")
        self.assertIsNone(self.capture()["wildPokemonNatureMatch"])

    def test_without_optional_region_readable_name_and_color_evidence_can_prove_absence(self) -> None:
        self.monitor.update_regions(self.regions[:3], QRect(0, 0, 800, 600))
        self.readings["Wild Pokemon"] = result("Eevee")
        self.assertIs(self.capture()["wildPokemonNatureMatch"], False)
        self.color_evidence["Wild Pokemon"] = True
        self.assertIsNone(self.capture()["wildPokemonNatureMatch"])
        self.color_evidence["Wild Pokemon"] = False
        self.readings["Wild Pokemon"] = result("Eevee", 49)
        self.assertIsNone(self.capture()["wildPokemonNatureMatch"])

    def test_chat_and_ally_markers_do_not_supply_green_s_evidence(self) -> None:
        self.readings["Battle Chat"] = result("A wild [Nature Match] Eevee appeared!")
        self.readings["Your Pokemon"] = result("[Nature Match] Alakazam")
        self.readings["Wild Pokemon"] = result("Eevee")
        self.assertIs(self.capture()["wildPokemonNatureMatch"], False)

    def test_capture_does_not_add_synchronize_ocr_when_the_optional_region_is_not_saved(self) -> None:
        self.monitor.update_regions(self.regions[:3], QRect(0, 0, 800, 600))
        self.capture()
        self.assertTrue(all(
            region.feature != "synchronize"
            for call in self.monitor._read_regions.call_args_list for region in call.args[1]
        ))


class NatureMatchRenderedOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.extractor = TextExtractor()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))
        cls.reader = RegionOCR(ImageProcessor(), cls.extractor)

    def test_actual_green_s_crops_agree_in_region_test_and_realtime_without_shiny(self) -> None:
        for filename, crop_rect in (
            (FIXTURES / "battle_placement/ui_scale.png", (458, 174, 225, 28)),
            (FIXTURES / "ocr/nature_match_name.png", None),
        ):
            image = cv2.imread(str(filename))
            self.assertIsNotNone(image, f"Missing Nature Match fixture: {filename}")
            if crop_rect is not None:
                x, y, width, height = crop_rect
                image = image[y:y + height, x:x + width]
            geometry = QRect(0, 0, image.shape[1], image.shape[0])
            for feature, label, expected in (
                ("pokemon-name", "Wild Pokemon", "[Nature Match] Linoone"),
                ("synchronize", "Nature Match", "[Nature Match]"),
            ):
                with self.subTest(filename=filename.name, feature=feature):
                    region = SavedRegion(feature, label, 0, 0, image.shape[1], image.shape[0])
                    capture = Mock(capture_window=Mock(return_value=image))
                    monitor = RealtimeRegionMonitor((region,), 42, geometry, image_capture=capture, text_extractor=self.extractor)
                    live = monitor._read_regions(image, (region,), timings())[0][1]
                    tested = RegionTestService(image_capture=capture, text_extractor=self.extractor).run(
                        region, 42, geometry, Mock(), save_images=False,
                    )
                    self.assertEqual(live, tested)
                    self.assertEqual(live.text, expected)
                    self.assertGreaterEqual(live.mean_confidence, 70)
                    self.assertTrue(is_nature_matched_wild_pokemon_name(live.text))
                    self.assertFalse(is_shiny_wild_pokemon_name(live.text))

    def test_rendered_mixed_badges_retain_nature_shiny_and_elite_at_small_and_large_sizes(self) -> None:
        font_path = Path("C:/Windows/Fonts/arialbd.ttf")
        if not font_path.is_file():
            self.skipTest("Windows game-like font is unavailable.")
        for badges in (
            (("S", "#20ce60"), ("[S]", "#facc15"), ("[E]", "#ff3030")),
            (("[S]", "#facc15"), ("S", "#20ce60"), ("[E]", "#ff3030")),
            (("S", "#20ce60"), ("[S]", "#facc15"), ("[E]", "#facc15")),
            (("[E]", "#ff3030"), ("S", "#20ce60"), ("[S]", "#facc15")),
        ):
            for size in (13, 22):
                with self.subTest(badges=badges, size=size):
                    font = ImageFont.truetype(str(font_path), size)
                    canvas = Image.new("RGB", (260, 40), "#18212e")
                    draw = ImageDraw.Draw(canvas)
                    x = 4.0
                    for badge, color in badges:
                        draw.text((x, 4), badge, fill=color, font=font, stroke_width=1, stroke_fill="black")
                        x += draw.textlength(badge + " ", font=font)
                    draw.text((x, 4), "Eevee", fill="white", font=font, stroke_width=1, stroke_fill="black")
                    image = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
                    _signal, colors = ImageProcessor.wild_name_text(image)
                    self.assertEqual(colors, tuple("green" if badge == "S" else "other" for badge, _color in badges))
                    region = SavedRegion("pokemon-name", "Wild Pokemon", 0, 0, canvas.width, canvas.height)
                    reading = self.reader.extract(self.reader.prepare(image, region), region).result
                    self.assertTrue(is_nature_matched_wild_pokemon_name(reading.text), reading)
                    self.assertTrue(is_shiny_wild_pokemon_name(reading.text), reading)
                    self.assertTrue(is_elite_wild_pokemon_name(reading.text), reading)
                    self.assertEqual(pokemon_species_name(reading.text), "Eevee")

    def test_wider_prefix_gutter_does_not_keep_detached_green_scenery(self) -> None:
        font_path = Path("C:/Windows/Fonts/arialbd.ttf")
        if not font_path.is_file():
            self.skipTest("Windows game-like font is unavailable.")
        font = ImageFont.truetype(str(font_path), 13)
        canvas = Image.new("RGB", (200, 40), "#18212e")
        draw = ImageDraw.Draw(canvas)
        for x, text, color in ((4, "S", "#20ce60"), (68, "[S]", "#facc15"), (93, "Eevee", "white")):
            draw.text((x, 4), text, fill=color, font=font, stroke_width=1, stroke_fill="black")
        image = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
        signal, colors = ImageProcessor.wild_name_text(image)
        self.assertEqual(colors, ("other",))
        self.assertLess(signal[6:16, 5:12].max(), 150)


if __name__ == "__main__":
    unittest.main()
