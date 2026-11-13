import unittest
from unittest.mock import Mock

from PySide6.QtCore import QRect

from capture.realtime_region_monitor import RealtimeRegionMonitor
from models.ocr_result import OCRResult
from models.region_config import SavedRegion


class EliteTargetEvidenceTests(unittest.TestCase):
    def _monitor(self, *, wild_region=True):
        regions = (
            SavedRegion("chat-box", "Battle Chat", 20, 20, 300, 200),
            SavedRegion("pokemon-name", "Your Pokemon", 400, 300, 200, 40),
            *((SavedRegion("pokemon-name", "Wild Pokemon", 400, 20, 200, 40),) if wild_region else ()),
        )
        monitor = RealtimeRegionMonitor(
            regions, 42, QRect(0, 0, 800, 600), image_capture=Mock(), text_extractor=Mock(),
        )
        texts = {"Battle Chat": ("A wild Pidgey appeared!", 95.0)}
        monitor._read_regions = Mock(side_effect=lambda screenshot, requested, timings: [
            (region, OCRResult(*self._ocr_args(texts.get(region.label, ("", 95.0)))))
            for region in requested
        ])
        published = []
        monitor.state_updated.connect(published.append)
        return monitor, texts, published

    @staticmethod
    def _ocr_args(reading):
        text, confidence = reading
        return text, 200, 40, confidence

    @staticmethod
    def _capture(monitor):
        return monitor._capture_state({
            "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
        })[0]

    def test_unreadable_required_wild_name_remains_unknown_through_capture_and_publication(self) -> None:
        for reading in (("", 95.0), ("[E] Pidgey", 10.0), ("???", 95.0)):
            with self.subTest(reading=reading):
                monitor, texts, published = self._monitor()
                texts["Wild Pokemon"] = reading
                state = self._capture(monitor)
                self.assertEqual(state["wildPokemonName"], "Pidgey")
                self.assertIsNone(state["wildPokemonIsElite"])
                monitor._publish_state(state, "12:00:00 PM")
                self.assertIsNone(published[-1]["wildPokemonIsElite"])
                wild_reads = [
                    region for call in monitor._read_regions.call_args_list
                    for region in call.args[1] if region.label == "Wild Pokemon"
                ]
                self.assertEqual(len(wild_reads), 1)

    def test_positive_elite_proof_can_come_from_the_name_or_latest_announcement(self) -> None:
        for wild, chat, expected in (
            ("Pidgey", "A wild Pidgey appeared!", False),
            ("[E] Pidgey", "A wild Pidgey appeared!", True),
            ("", "A wild [E] Pidgey appeared!", True),
            ("", "A wild Elite Pidgey appeared!", True),
            ("Pidgey", "A wild [E] Eevee appeared!\nA wild Pidgey appeared!", False),
        ):
            with self.subTest(wild=wild, chat=chat):
                monitor, texts, published = self._monitor()
                texts.update({
                    "Wild Pokemon": (wild, 95.0), "Battle Chat": (chat, 95.0),
                    "Your Pokemon": ("[E] Pikachu", 95.0),
                })
                state = self._capture(monitor)
                self.assertIs(state["wildPokemonIsElite"], expected)
                monitor._publish_state(state, "12:00:00 PM")
                self.assertIs(published[-1]["wildPokemonIsElite"], expected)

    def test_chat_supplies_negative_evidence_only_when_no_wild_name_region_is_configured(self) -> None:
        for chat, expected in (("A wild Pidgey appeared!", False), ("A wild [E] Pidgey appeared!", True), ("", None)):
            with self.subTest(chat=chat):
                monitor, texts, published = self._monitor(wild_region=False)
                texts["Battle Chat"] = (chat, 95.0)
                state = self._capture(monitor)
                monitor._publish_state(state, "12:00:00 PM")
                self.assertIs(state["wildPokemonIsElite"], expected)
                self.assertIs(published[-1]["wildPokemonIsElite"], expected)

    def test_confirmed_elite_trait_survives_unreadable_frames_until_confirmed_encounter_reset(self) -> None:
        monitor, texts, published = self._monitor()
        encountered = []
        monitor.pokemon_encountered.connect(lambda name, state: encountered.append((name, state)))
        for wild, raw, expected in (
            ("", None, None), ("[E] Pidgey", True, True),
            ("Pidgey", False, True), ("", None, True),
        ):
            texts["Wild Pokemon"] = (wild, 95.0)
            state = self._capture(monitor)
            monitor._publish_state(state, "12:00:00 PM")
            self.assertIs(state["wildPokemonIsElite"], raw)
            self.assertIs(published[-1]["wildPokemonIsElite"], expected)
        texts.clear()
        for _ in range(2):
            monitor._publish_state(self._capture(monitor), "12:00:01 PM")
            self.assertTrue(monitor._encounter_is_elite)
        texts["Battle Chat"] = ("A wild Pidgey appeared!", 95.0)
        monitor._publish_state(self._capture(monitor), "12:00:01 PM")
        self.assertTrue(published[-1]["wildPokemonIsElite"])
        self.assertEqual(len(encountered), 1)
        texts.clear()
        for _ in range(3):
            monitor._publish_state(self._capture(monitor), "12:00:02 PM")
        self.assertFalse(monitor._encounter_is_elite)
        texts.update({"Wild Pokemon": ("Pidgey", 95.0), "Battle Chat": ("A wild Pidgey appeared!", 95.0)})
        monitor._publish_state(self._capture(monitor), "12:00:03 PM")
        self.assertIs(published[-1]["wildPokemonIsElite"], False)
        self.assertEqual(len(encountered), 2)
