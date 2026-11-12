import unittest

from overlay.overlay_window import OverlayWindow


class _HistoryRecorder:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, int]] = []

    def record_damage(
        self,
        player: str,
        opponent: str,
        move: str,
        damage: int,
    ) -> tuple[int, float]:
        self.records.append((player, opponent, move, damage))
        return len(self.records), float(damage)


class _Log:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def append_entry(self, level: str, message: str) -> None:
        self.entries.append((level, message))


class _OverlayHarness:
    _wild_pokemon_identity = staticmethod(OverlayWindow._wild_pokemon_identity)
    _update_battle_move_history = OverlayWindow._update_battle_move_history

    def __init__(self) -> None:
        self._pending_battle_move = (
            "latios",
            "magcargo",
            "Surf",
            1,
            100,
        )
        self._battle_history = _HistoryRecorder()
        self.region_test_log_window = _Log()


class BattleHistoryRecordingTests(unittest.TestCase):
    def test_records_hp_drop_without_waiting_for_turn_change(self) -> None:
        overlay = _OverlayHarness()

        overlay._update_battle_move_history(
            {
                "onBattleMode": True,
                "turn": 1,
                "wildPokemonName": "Magcargo",
                "enemyPokemonHealth": 100,
            }
        )
        self.assertEqual(overlay._battle_history.records, [])
        self.assertIsNotNone(overlay._pending_battle_move)

        overlay._update_battle_move_history(
            {
                "onBattleMode": True,
                "turn": 1,
                "wildPokemonName": "Magcargo",
                "enemyPokemonHealth": 0,
            }
        )

        self.assertEqual(
            overlay._battle_history.records,
            [("latios", "magcargo", "Surf", 100)],
        )
        self.assertIsNone(overlay._pending_battle_move)
        self.assertTrue(
            any("Surf vs magcargo: 100% damage" in message
                for _, message in overlay.region_test_log_window.entries)
        )

    def test_does_not_record_an_unchanged_hp_sample(self) -> None:
        overlay = _OverlayHarness()

        overlay._update_battle_move_history(
            {
                "onBattleMode": True,
                "turn": 1,
                "wildPokemonName": "Magcargo",
                "enemyPokemonHealth": 100,
            }
        )

        self.assertEqual(overlay._battle_history.records, [])
        self.assertIsNotNone(overlay._pending_battle_move)


if __name__ == "__main__":
    unittest.main()
