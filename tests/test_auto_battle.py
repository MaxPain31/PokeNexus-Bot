import tempfile
import unittest
from pathlib import Path

from core.auto_battle import choose_move_slot, has_complete_move_set, move_name_for_slot
from core.battle_history import BattleHistory
from capture.game_input import GameInput


class AutoBattleTests(unittest.TestCase):
    def test_default_mouse_click_settle_delay_is_fifty_milliseconds(self) -> None:
        self.assertEqual(GameInput._CLICK_SETTLE_SECONDS, 0.05)

    def test_prefers_live_effectiveness_and_keeps_first_slot_on_tie(self) -> None:
        moves = (
            "Move Slot 1: Slash (1.0x | 10/20) | "
            "Move Slot 2: Thunderbolt (2.0x | 10/15) | "
            "Move Slot 3: Tackle (2.0x | 0/35) | "
            "Move Slot 4: Growl (0x | 10/40)"
        )

        self.assertTrue(has_complete_move_set(moves))
        self.assertEqual(choose_move_slot(moves), 2)

    def test_battle_history_breaks_effectiveness_ties(self) -> None:
        moves = (
            "Move Slot 1: Slash (1.0x | 10/20) | "
            "Move Slot 2: Thunderbolt (1.0x | 10/15)"
        )

        self.assertEqual(
            choose_move_slot(moves, {"thunderbolt": 28.0}),
            2,
        )

    def test_move_name_lookup_uses_selected_slot(self) -> None:
        moves = "Move Slot 1: Slash (1.0x | 10/20)"
        self.assertEqual(move_name_for_slot(moves, 1), "Slash")
        self.assertIsNone(move_name_for_slot(moves, 2))

    def test_history_persists_and_only_recommends_after_repeated_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "battle_history.json"
            history = BattleHistory(path)

            count, _ = history.record_damage(
                "Pikachu",
                "Pidgey",
                "Thunderbolt",
                40,
            )
            self.assertEqual(count, 1)
            self.assertEqual(
                history.average_damage_by_move("Pikachu", "Pidgey"),
                {},
            )

            history.record_damage(
                "Pikachu",
                "Pidgey",
                "Thunderbolt",
                20,
            )
            reloaded_history = BattleHistory(path)

            self.assertEqual(
                reloaded_history.average_damage_by_move("PIKACHU", "pidgey"),
                {"thunderbolt": 30.0},
            )


if __name__ == "__main__":
    unittest.main()
