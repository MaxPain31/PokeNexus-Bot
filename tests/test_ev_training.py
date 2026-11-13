import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.auto_battle import matches_battle_target
from core.ev_training import (
    EV_STATS, EVDefeatTracker, EVProgress, chat_confirms_opponent_defeat,
    default_ev_values, ev_yield_for_pokemon, load_ev_training_data, validate_ev_values,
)


class EVTrainingDataTests(unittest.TestCase):
    def test_catalog_keeps_location_records_and_combined_training_categories(self) -> None:
        entries = load_ev_training_data()
        gyarados = [entry for entry in entries if entry.pokemon == "Gyarados"]
        self.assertEqual({entry.region for entry in gyarados}, {"Johto", "Hoenn", "Sinnoh"})
        self.assertTrue(all(entry.yields == (0, 2, 0, 0, 0, 0) for entry in gyarados))
        self.assertEqual(
            {entry.training_category for entry in entries if entry.pokemon == "Pidgey"},
            {"Speed", "Attack + Speed"},
        )
        self.assertTrue(all(len(entry.yields) == len(EV_STATS) for entry in entries))
        self.assertGreater(len(entries), len({entry.pokemon for entry in entries}))

    def test_duplicate_locations_and_categories_resolve_to_one_exact_species_yield(self) -> None:
        entries = load_ev_training_data()
        self.assertEqual(ev_yield_for_pokemon("[E] Gyarados Lv. 25", entries), (0, 2, 0, 0, 0, 0))
        self.assertEqual(ev_yield_for_pokemon("Pidgey", entries), (0, 0, 0, 0, 0, 1))
        self.assertEqual(ev_yield_for_pokemon("Exploud", entries), (3, 0, 0, 0, 0, 0))
        for unknown in ("Unknown", "Rattata (Alolan)", "Pidge", None):
            self.assertIsNone(ev_yield_for_pokemon(unknown, entries))

    def test_multiple_yields_are_mapped_to_the_correct_stats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ev.csv"
            path.write_text(
                "training_category,region,location,pokemon,ev_yield\n"
                "HP + Special Defense,Kanto,Test,Eevee,1 HP + 2 Special Defense\n"
                "Special Attack,Kanto,Test,Gengar,3 Special Attack\n",
                encoding="utf-8-sig",
            )
            with patch("core.ev_training.application_path", return_value=path):
                entries = load_ev_training_data()
        self.assertEqual(entries[0].yields, (1, 0, 0, 0, 2, 0))
        self.assertEqual(entries[1].yields, (0, 0, 0, 3, 0, 0))

    def test_malformed_yields_are_not_silently_loaded_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ev.csv"
            for invalid in ("", "Attack", "2 Unknown", "-1 HP", "0 Speed", "253 HP", "200 HP + 100 HP"):
                with self.subTest(yield_text=invalid):
                    path.write_text(
                        "training_category,region,location,pokemon,ev_yield\n"
                        f"HP,Kanto,Test,Eevee,{invalid}\n",
                        encoding="utf-8",
                    )
                    with patch("core.ev_training.application_path", return_value=path):
                        with self.assertRaisesRegex(ValueError, "Invalid EV yield"):
                            load_ev_training_data()

    def test_ev_values_require_six_integers_and_enforce_both_caps(self) -> None:
        original = {**default_ev_values(), "hp": 6, "attack": 252, "speed": 252}
        converted = validate_ev_values(original)
        self.assertEqual(converted, original)
        converted["hp"] = 0
        self.assertEqual(original["hp"], 6)
        for invalid in (
            None, {}, {**original, "unknown": 0}, {**original, "defense": 1},
            *({**original, "hp": value} for value in (-1, 253, False, 1.5, "6", [0, 6])),
        ):
            with self.subTest(values=invalid), self.assertRaises(ValueError):
                validate_ev_values(invalid)


class EVProgressTests(unittest.TestCase):
    def test_two_exploud_defeats_reach_six_hp_and_stop_training_hp(self) -> None:
        progress = EVProgress(targets={**default_ev_values(), "hp": 6, "attack": 252, "speed": 252})
        exploud = (3, 0, 0, 0, 0, 0)
        self.assertTrue(progress.fits(exploud))
        first = progress.after_defeat(exploud)
        self.assertEqual((first.current["hp"], first.remaining["hp"]), (3, 3))
        self.assertTrue(first.fits(exploud))
        second = first.after_defeat(exploud)
        self.assertEqual((second.current["hp"], second.remaining["hp"]), (6, 0))
        self.assertFalse(second.fits(exploud))
        self.assertTrue(second.fits((0, 2, 0, 0, 0, 0)))
        self.assertTrue(second.fits((0, 0, 0, 0, 0, 1)))
        self.assertFalse(second.complete)
        self.assertEqual(progress.current, default_ev_values())

    def test_all_six_remaining_values_and_whole_yield_must_fit(self) -> None:
        current = dict(zip(default_ev_values(), (4, 40, 20, 30, 10, 24)))
        targets = dict(zip(default_ev_values(), (6, 100, 40, 100, 12, 252)))
        progress = EVProgress(current, targets)
        self.assertEqual(tuple(progress.remaining.values()), (2, 60, 20, 70, 2, 228))
        self.assertFalse(progress.fits((3, 0, 0, 0, 0, 0)))
        self.assertTrue(progress.fits((2, 0, 0, 0, 0, 0)))
        self.assertTrue(progress.fits((1, 0, 0, 0, 2, 0)))
        self.assertFalse(progress.fits((1, 0, 0, 0, 3, 0)))
        for index in range(6):
            yields = tuple(1 if stat == index else 0 for stat in range(6))
            with self.subTest(stat=index):
                self.assertTrue(progress.fits(yields))
                result = progress.after_defeat(yields)
                self.assertEqual(tuple(result.current.values())[index], tuple(current.values())[index] + 1)

    def test_existing_skipped_stats_count_toward_510_and_no_partial_yield_is_battled(self) -> None:
        progress = EVProgress(
            {**default_ev_values(), "attack": 252, "speed": 252, "defense": 4},
            {**default_ev_values(), "hp": 6, "attack": 252, "speed": 252},
        )
        self.assertEqual(progress.total, 508)
        self.assertEqual(progress.remaining["defense"], 0)
        self.assertFalse(progress.fits((3, 0, 0, 0, 0, 0)))
        self.assertFalse(progress.fits((1, 0, 1, 0, 0, 0)))
        self.assertTrue(progress.fits((2, 0, 0, 0, 0, 0)))
        final = progress.after_defeat((2, 0, 0, 0, 0, 0))
        self.assertEqual(final.total, 510)
        self.assertEqual(final.remaining["hp"], 4)
        self.assertTrue(final.complete)
        self.assertFalse(final.fits((1, 0, 0, 0, 0, 0)))

    def test_reached_lowered_and_unset_targets_never_accept_more_evs(self) -> None:
        for current, targets in (
            (default_ev_values(), default_ev_values()),
            ({**default_ev_values(), "hp": 6}, {**default_ev_values(), "hp": 6}),
            ({**default_ev_values(), "hp": 6}, {**default_ev_values(), "hp": 4}),
            ({**default_ev_values(), "hp": 252}, {**default_ev_values(), "hp": 252}),
        ):
            with self.subTest(current=current, targets=targets):
                progress = EVProgress(current, targets)
                self.assertTrue(progress.complete)
                self.assertTrue(all(value == 0 for value in progress.remaining.values()))
                self.assertFalse(progress.fits((1, 0, 0, 0, 0, 0)))

    def test_actual_defeats_are_accounted_even_after_targets_change_with_game_caps(self) -> None:
        progress = EVProgress({**default_ev_values(), "hp": 4}, {**default_ev_values(), "hp": 4})
        self.assertEqual(progress.after_defeat((3, 0, 0, 0, 0, 0)).current["hp"], 7)
        capped = EVProgress({**default_ev_values(), "hp": 251, "attack": 252, "speed": 6})
        result = capped.after_defeat((3, 0, 0, 0, 0, 0))
        self.assertEqual((result.current["hp"], result.total), (252, 510))


class EVDefeatTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = EVDefeatTracker()
        self.at = 0.0
        self.alive = {
            "onBattleMode": True, "wildPokemonName": "Exploud", "enemyPokemonHealth": 100,
            "yourPokemonName": "Pikachu", "yourPokemonHealth": 100,
            "chatBoxMessage": "A wild Exploud appeared!",
        }
        self.fainted = {
            **self.alive, "enemyPokemonHealth": 0,
            "chatBoxMessage": "A wild Exploud appeared!\nThe opposing Exploud fainted!",
        }
        self.ended = {
            **self.alive, "onBattleMode": False, "wildPokemonName": None,
            "enemyPokemonHealth": None, "chatBoxMessage": "",
        }

    def observe(self, state, *, enabled=True):
        self.at += 1
        return self.tracker.observe(state, enabled=enabled, frame_at=self.at)

    def test_explicit_opponent_faint_awards_once_and_identical_next_encounter_can_award(self) -> None:
        for _battle in range(2):
            self.assertIsNone(self.observe(self.alive))
            self.assertEqual(self.observe(self.fainted), "Exploud")
            for _poll in range(4):
                self.assertIsNone(self.observe(self.fainted))
            for _poll in range(3):
                self.assertIsNone(self.observe(self.ended))

    def test_new_announcement_after_defeat_starts_next_same_species_without_blank_chat(self) -> None:
        self.observe(self.alive)
        self.assertEqual(self.observe(self.fainted), "Exploud")
        next_battle = {**self.alive, "chatBoxMessage": self.fainted["chatBoxMessage"] + "\nA wild Exploud appeared!"}
        self.assertIsNone(self.observe(next_battle))
        self.assertEqual(self.observe({
            **next_battle, "chatBoxMessage": next_battle["chatBoxMessage"] + "\nThe opposing Exploud fainted!",
        }), "Exploud")

    def test_already_visible_old_faint_and_player_faint_are_not_opponent_defeats(self) -> None:
        for chat in (
            "The opposing Exploud fainted!\nA wild Exploud appeared!",
            "A wild Exploud appeared!\nPikachu fainted!",
            "A wild Exploud appeared!\nYour Pokemon fainted!",
            "A wild Exploud appeared!\nThe opposing Loudred fainted!",
        ):
            with self.subTest(chat=chat):
                self.tracker.reset()
                self.observe(self.alive)
                self.assertIsNone(self.observe({**self.alive, "chatBoxMessage": chat}))
        self.tracker.reset()
        self.assertIsNone(self.observe(self.fainted))
        self.assertIsNone(self.observe(self.fainted))

    def test_running_catching_fainting_and_battle_disappearance_do_not_award_evs(self) -> None:
        for change in (
            {}, {"clickedRunButton": True}, {"yourPokemonHealth": 0},
            {"chatBoxMessage": "A wild Exploud appeared!\nGot away safely!"},
            {"chatBoxMessage": "Congratulations, you caught the opposing Exploud!"},
        ):
            with self.subTest(change=change):
                self.tracker.reset()
                self.observe(self.alive)
                for _poll in range(4):
                    self.assertIsNone(self.observe({**self.ended, **change}))
        for change in (
            {"yourPokemonHealth": 0}, {"yourPokemonName": "Raichu"},
            {"chatBoxMessage": "A wild Exploud appeared!\nCongratulations, you caught the opposing Exploud!"},
        ):
            with self.subTest(terminal_change=change):
                self.tracker.reset()
                self.observe(self.alive)
                for _poll in range(3):
                    self.assertIsNone(self.observe({**self.fainted, **change}))

    def test_hp_fallback_requires_a_living_opponent_then_two_distinct_zero_frames(self) -> None:
        self.observe(self.alive)
        zero = {**self.alive, "enemyPokemonHealth": 0}
        self.assertIsNone(self.observe(zero))
        self.assertIsNone(self.tracker.observe(zero, enabled=True, frame_at=self.at))
        self.assertIsNone(self.tracker.observe(zero, enabled=True, frame_at=self.at - 1))
        self.assertEqual(self.observe(zero), "Exploud")
        self.assertIsNone(self.observe(zero))

    def test_one_zero_plus_three_confirmed_end_frames_can_confirm_defeat(self) -> None:
        self.observe(self.alive)
        self.assertIsNone(self.observe({**self.alive, "enemyPokemonHealth": 0}))
        self.assertIsNone(self.observe(self.ended))
        self.assertIsNone(self.observe(self.ended))
        self.assertEqual(self.observe(self.ended), "Exploud")

    def test_attempted_run_or_ball_suppresses_ambiguous_zero_hp_but_not_real_faint_chat(self) -> None:
        for feature, role in (("run", "Run Button"), ("bag", "Use Item Button")):
            with self.subTest(feature=feature):
                self.tracker.reset()
                self.observe(self.alive)
                self.tracker.record_action(feature, role)
                for _poll in range(3):
                    self.assertIsNone(self.observe({**self.alive, "enemyPokemonHealth": 0}))
                self.assertEqual(self.observe(self.fainted), "Exploud")

    def test_unknown_evidence_cannot_confirm_or_end_an_encounter(self) -> None:
        for change in ({"clickStateReliable": False}, {"showingSpecialPokemonMessage": None}):
            with self.subTest(change=change):
                self.tracker.reset()
                self.observe(self.alive)
                self.assertIsNone(self.observe({**self.fainted, **change}))
                self.assertEqual(self.observe(self.fainted), "Exploud")
        self.tracker.reset()
        self.observe(self.alive, enabled=False)
        self.assertIsNone(self.observe(self.fainted))

    def test_explicit_defeat_chat_can_account_without_an_ally_hp_region(self) -> None:
        self.observe({**self.alive, "yourPokemonHealth": None})
        self.assertEqual(self.observe({**self.fainted, "yourPokemonHealth": None}), "Exploud")

    def test_region_or_popup_pause_breaks_consecutive_defeat_confirmation_polls(self) -> None:
        self.observe(self.alive)
        zero = {**self.alive, "enemyPokemonHealth": 0}
        self.assertIsNone(self.observe(zero))
        self.tracker.pause()
        self.assertIsNone(self.observe(zero))
        self.assertEqual(self.observe(zero), "Exploud")

    def test_mismatched_terminal_species_cannot_credit_the_previous_opponent(self) -> None:
        self.observe(self.alive)
        for _poll in range(3):
            self.assertIsNone(self.observe({
                **self.fainted, "onBattleMode": False, "wildPokemonName": "Loudred",
                "chatBoxMessage": "The opposing Loudred fainted!",
            }))

    def test_defeat_detection_uses_only_opponent_messages_after_latest_announcement(self) -> None:
        self.assertTrue(chat_confirms_opponent_defeat(self.fainted["chatBoxMessage"]))
        for text in (
            "Pikachu fainted!", "Your Pokemon fainted!",
            self.fainted["chatBoxMessage"] + "\nA wild Exploud appeared!",
        ):
            self.assertFalse(chat_confirms_opponent_defeat(text))


class BattleTargetMatchingTests(unittest.TestCase):
    def test_targets_match_whole_species_and_forms_with_normalized_traits_and_levels(self) -> None:
        for wild, selected, expected in (
            ("[S] [E] [Nature Match] Pidgey Lv. 20", "pIdGeY, Rattata", True),
            ("A wild Raichu (Alolan) appeared!", "Raichu", False),
            ("Raichu (Alolan)", "Raichu (Alolan)", True),
            ("Raichu", "Raichu (Alolan)", False),
            ("Pidgeotto", "Pidgey", False),
            ("Type: Null", "Type: Null", True),
            ("Pidgey", "", False),
            (None, "", False),
        ):
            with self.subTest(wild=wild, selected=selected):
                self.assertIs(matches_battle_target(wild, selected), expected)

    def test_missing_name_or_elite_evidence_is_unknown_not_a_nonmatch(self) -> None:
        for wild in (None, "", "   ", "[E]", "???", 123):
            with self.subTest(wild=wild):
                self.assertIsNone(matches_battle_target(wild, "Pidgey"))
        for elite in (None, "true", 1, 0):
            with self.subTest(elite=elite):
                self.assertIsNone(matches_battle_target("Pidgey", "Pidgey", elite_only=True, is_elite=elite))
        self.assertIs(matches_battle_target("Pidgey", "Pidgey", elite_only=True, is_elite=True), True)
        self.assertIs(matches_battle_target("Pidgey", "Pidgey", elite_only=True, is_elite=False), False)
        self.assertIs(matches_battle_target("Rattata", "Pidgey", elite_only=True), False)
        self.assertIs(matches_battle_target("Pidgey", "Pidgey", is_elite=None), True)


if __name__ == "__main__":
    unittest.main()
