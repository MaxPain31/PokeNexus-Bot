import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect

from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.battle_action import ACTION_BUTTON_STATES
from core.special_pokemon_message import (
    SpecialPokemonMessageController,
    special_pokemon_message_kind,
)
from models.ocr_result import OCRResult
from models.region_config import SavedRegion


SPECIAL_ENCOUNTER_TEXT = (
    "Special Encounter!\n"
    "You have encountered a special pokemon!\n"
    "Click the Close button to continue\n"
    "Window will Auto-Close in: 02:54"
)


class SpecialPokemonMessageControllerTests(unittest.TestCase):
    def test_recognizes_the_screenshot_wording_without_a_rarity_name(self) -> None:
        for text in (
            SPECIAL_ENCOUNTER_TEXT,
            SPECIAL_ENCOUNTER_TEXT.replace("02:54", "00:01"),
            "Special Encounter!",
            "You have encountered a special pokemon!",
            "YOU HAVE\nENCOUNTERED A SPECIAL POKÉMON!",
            "Click the Close button to continue",
        ):
            with self.subTest(text=text):
                self.assertEqual(special_pokemon_message_kind(text), "Special Encounter")
        for text in (
            "Window will Auto-Close in: 02:54",
            "You have encountered a pokemon!",
            "Special attack increased!",
        ):
            with self.subTest(background=text):
                self.assertIsNone(special_pokemon_message_kind(text))

    def test_recognizes_all_three_rarities_without_matching_word_fragments(self) -> None:
        for text, expected in (
            ("You encountered a SHINY Pokemon!", "Shiny"),
            ("This Pokemon is extremely\nrare.", "Extremely Rare"),
            ("An Extremely-Rare Pokemon appeared!", "Extremely Rare"),
            ("Legendary encounter!", "Legendary"),
            ("A wild Eevee appeared!", None),
            ("Rare Pokemon", None),
            ("Very Rare Pokemon", None),
            ("Unshiny / legendaries / extremely rarely", None),
            ("", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(special_pokemon_message_kind(text), expected)

    def test_close_retries_and_confirmation_require_fresh_post_input_frames(self) -> None:
        controller = SpecialPokemonMessageController()

        def update(visible, at):
            return controller.update(enabled=True, visible=visible, frame_at=at)

        first = update(True, 100.0)
        self.assertTrue(first.detected)
        self.assertTrue(first.click_close)
        controller.record_attempt(100.5)
        for at in (100.1, 100.5):
            self.assertTrue(update(False, at).handled)
        self.assertFalse(update(True, 102.49).click_close)
        retry = update(True, 102.5)
        self.assertTrue(retry.click_close)
        self.assertFalse(retry.detected)
        controller.record_attempt(102.6)

        self.assertTrue(update(False, 102.7).handled)
        self.assertTrue(update(False, 102.7).handled)
        self.assertTrue(update(False, 102.65).handled)
        self.assertTrue(update(None, 102.8).handled)
        self.assertTrue(update(False, 102.9).handled)
        dismissed = update(False, 103.0)
        self.assertTrue(dismissed.dismissed)
        self.assertFalse(dismissed.handled)
        self.assertFalse(controller.blocked)
        self.assertFalse(update(False, 103.1).dismissed)

        next_encounter = update(True, 103.2)
        self.assertTrue(next_encounter.detected)
        self.assertTrue(next_encounter.click_close)

    def test_uncertain_detection_pauses_without_clicking_until_two_clear_observations(self) -> None:
        controller = SpecialPokemonMessageController()
        for at in (100.0, 103.0):
            decision = controller.update(enabled=True, visible=None, frame_at=at)
            self.assertTrue(decision.handled)
            self.assertFalse(decision.click_close)
            self.assertFalse(decision.detected)
        self.assertTrue(controller.update(enabled=True, visible=False, frame_at=103.1).handled)
        clear = controller.update(enabled=True, visible=False, frame_at=103.2)
        self.assertFalse(clear.handled)
        self.assertFalse(clear.dismissed)

    def test_disabling_discards_the_popup_and_retry_cooldown(self) -> None:
        controller = SpecialPokemonMessageController()
        controller.update(enabled=True, visible=True, frame_at=100.0)
        controller.record_attempt(100.1)
        stopped = controller.update(enabled=False, visible=True, frame_at=100.2)
        self.assertFalse(stopped.handled)
        self.assertFalse(stopped.click_close)
        self.assertFalse(controller.blocked)
        restarted = controller.update(enabled=True, visible=True, frame_at=100.3)
        self.assertTrue(restarted.detected)
        self.assertTrue(restarted.click_close)


class SpecialPokemonMessageMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.message_box = SavedRegion(
            "special-pokemon-message", "Message Box", 220, 160, 300, 120,
        )
        self.close_button = SavedRegion(
            "special-pokemon-message", "Close Button", 460, 250, 50, 25,
        )
        self.battle_regions = (
            SavedRegion("chat-box", "Battle Chat", 50, 200, 300, 100),
            SavedRegion("pokemon-name", "Wild Pokemon", 400, 150, 100, 30),
            SavedRegion("turn", "Turn Button", 100, 100, 50, 20),
            *(SavedRegion(feature, role, 100 + index * 70, 400, 60, 30)
              for index, (feature, role) in enumerate((
                  ("fight", "Fight Button"), ("bag", "Bag Button"),
                  ("swap", "Pokemon/Swap Button"), ("run", "Run Button"),
              ))),
            SavedRegion("moves", "Move Slot 1", 100, 300, 100, 30),
        )
        self.results = {}
        self.failed_roles = set()
        self.monitor = self._monitor((self.message_box, self.close_button, *self.battle_regions))
        self.now = 100.0
        clock = patch("capture.realtime_region_monitor.time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def _monitor(self, regions):
        monitor = RealtimeRegionMonitor(
            regions, 42, QRect(0, 0, 800, 600), image_capture=Mock(), text_extractor=Mock(),
        )
        monitor._read_regions = Mock(side_effect=lambda screenshot, requested, timings: [
            (region, self.results.get(region.label, OCRResult("", 100, 30, 0.0)))
            for region in requested if region.label not in self.failed_roles
        ])
        return monitor

    def _text(self, role, text, confidence=95.0):
        self.results[role] = OCRResult(text, 100, 30, confidence)

    def _capture(self):
        self.now += 0.1
        return self.monitor._capture_state({
            "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
        })[0]

    def _battle_menu(self, visible):
        self._text("Battle Chat", "A wild Eevee appeared!")
        self._text("Turn Button", "Turn 1")
        for role in ("Fight Button", "Bag Button", "Pokemon/Swap Button", "Run Button"):
            self._text(role, role if visible else "")
        self._text("Move Slot 1", "Tackle 1.0x 20/35" if not visible else "Fight")

    def test_detects_each_popup_when_normal_battle_ocr_is_hidden_and_never_reads_close(self) -> None:
        for kind in ("Shiny", "Extremely Rare", "Legendary"):
            with self.subTest(kind=kind):
                self._text("Message Box", f"You encountered a {kind} Pokemon!")
                state = self._capture()
                self.assertTrue(state["showingSpecialPokemonMessage"])
                self.assertTrue(state["onBattleMode"])
                self.assertIn(kind, state["specialPokemonMessage"])
                self.assertFalse(state["clickStateReliable"])
                self.assertIsNone(state["turn"])
                self.assertIsNone(state["pokemonMoves"])
                for clicked, visible in ACTION_BUTTON_STATES.values():
                    self.assertFalse(state[clicked])
                    self.assertIsNone(state[visible])
        requested = {
            region for call in self.monitor._read_regions.call_args_list for region in call.args[1]
        }
        self.assertIn(self.message_box, requested)
        self.assertNotIn(self.close_button, requested)

    def test_rarity_text_in_other_regions_does_not_trigger_popup_dismissal(self) -> None:
        self._battle_menu(True)
        self._text("Battle Chat", "A wild Shiny Eevee appeared!")
        self._text("Wild Pokemon", "[S] Eevee")
        self._text("Close Button", "Legendary")
        state = self._capture()
        self.assertFalse(state["showingSpecialPokemonMessage"])
        self.assertIsNone(state["specialPokemonMessage"])
        self.assertTrue(state["wildPokemonIsShiny"])
        self.assertTrue(state["showingFightButton"])

    def test_generic_popup_preempts_the_readable_battle_menu_behind_it(self) -> None:
        self._battle_menu(True)
        self._text("Message Box", SPECIAL_ENCOUNTER_TEXT)
        state = self._capture()
        self.assertTrue(state["showingSpecialPokemonMessage"])
        self.assertTrue(state["onBattleMode"])
        self.assertFalse(state["clickStateReliable"])
        self.assertFalse(state["wildPokemonIsShiny"])
        self.assertFalse(state["wildPokemonIsElite"])
        self.assertIn("You have encountered a special pokemon!", state["specialPokemonMessage"])
        for clicked, visible in ACTION_BUTTON_STATES.values():
            self.assertFalse(state[clicked])
            self.assertIsNone(state[visible])

    def test_failed_or_popup_like_ocr_is_unknown_but_background_noise_is_absent(self) -> None:
        for text, confidence, expected in (
            (None, 0.0, None),
            ("Shiny Pokemon!", 49.0, None),
            (SPECIAL_ENCOUNTER_TEXT, 49.0, None),
            ("Extrernely Rore Pokernon", 15.0, None),
            ("SpeciaI Encounler!", 25.0, None),
            ("Ciose butlon to continue", 30.0, None),
            ("Window will Auto-Close in: 02:54", 35.0, None),
            ("", 0.0, False),
            ("Ultra Ball", 95.0, False),
            ("Ultra Ball", 20.0, False),
            ("Special attack increased!", 20.0, False),
            ("Actlernyx fh . a sew BH OU furs |__| | PokeAreMM Hooodge VetryJuny", 32.41, False),
            (". J =) - =) - =) - oe oe ) > a phucngousa0806_ INIT2WINIT", 22.55, False),
        ):
            with self.subTest(text=text):
                self.failed_roles = {"Message Box"} if text is None else set()
                self._text("Message Box", text or "", confidence)
                state = self._capture()
                self.assertIs(state["showingSpecialPokemonMessage"], expected)
                self.assertEqual(state["clickStateReliable"], expected is False)

    def test_background_noise_does_not_hide_a_readable_battle_menu(self) -> None:
        self._battle_menu(True)
        self._text("Message Box", "fh . a sew BH OU furs |__|", 20.0)
        state = self._capture()
        self.assertTrue(state["onBattleMode"])
        self.assertFalse(state["showingSpecialPokemonMessage"])
        self.assertTrue(state["clickStateReliable"])
        self.assertTrue(state["showingFightButton"])

    def test_no_message_box_region_leaves_ordinary_battle_detection_available(self) -> None:
        self.monitor = self._monitor((self.close_button, *self.battle_regions))
        self._text("Close Button", "Shiny Pokemon!")
        self._battle_menu(True)
        state = self._capture()
        self.assertFalse(state["showingSpecialPokemonMessage"])
        self.assertTrue(state["onBattleMode"])
        self.assertTrue(state["showingFightButton"])
        self.assertTrue(state["clickStateReliable"])

    def test_popup_interrupts_click_tracking_then_recovers_an_already_open_move_menu(self) -> None:
        self._battle_menu(True)
        self.monitor.record_automated_action("fight", "Fight Button")
        self._capture()
        self._text("Message Box", "Legendary Pokemon!")
        blocked = self._capture()
        self.assertFalse(blocked["clickedFightButton"])
        self.assertIsNone(self.monitor._pending_action_feature)
        self.assertIsNone(blocked["pokemonMoves"])

        self._text("Message Box", "")
        self._battle_menu(False)
        recovered = self._capture()
        self.assertTrue(recovered["clickedFightButton"])
        self.assertIn("Tackle", recovered["pokemonMoves"])

    def test_unknown_popup_reading_cannot_confirm_or_discard_a_pending_fight(self) -> None:
        self._battle_menu(True)
        self.monitor.record_automated_action("fight", "Fight Button")
        self._capture()
        self._battle_menu(False)
        self.failed_roles.add("Message Box")
        uncertain = self._capture()
        self.assertIsNone(uncertain["showingSpecialPokemonMessage"])
        self.assertFalse(uncertain["clickedFightButton"])
        self.assertIsNone(uncertain["pokemonMoves"])
        self.failed_roles.clear()
        self.assertTrue(self._capture()["clickedFightButton"])


if __name__ == "__main__":
    unittest.main()
