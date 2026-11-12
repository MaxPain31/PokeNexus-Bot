import unittest

from core.auto_switch import AutoSwitchController


class AutoSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = AutoSwitchController()

    def _update(self, now, *, health=0, turn=1, enabled=True, in_battle=True):
        return self.controller.update(
            enabled=enabled, on_battle_mode=in_battle, health=health, turn=turn, now=now,
        )

    def test_replacement_is_not_ready_until_exactly_two_seconds_after_faint(self) -> None:
        for now in (100.0, 101.5, 101.99):
            decision = self._update(now)
            self.assertTrue(decision.handled)
            self.assertIsNone(decision.slot)
        self.assertEqual(self._update(102.0).slot, 2)

    def test_each_recovered_pokemon_gets_its_own_two_second_faint_delay(self) -> None:
        for slot in range(2, 7):
            with self.subTest(slot=slot):
                now = 100.0 + slot * 10
                self.assertIsNone(self._update(now, turn=slot).slot)
                self.assertIsNone(self._update(now + 1.99, turn=slot).slot)
                self.assertEqual(self._update(now + 2.0, turn=slot).slot, slot)
                self.controller.record_click(slot, slot, succeeded=True)
                self.assertIsNone(self._update(now + 2.1, turn=slot + 1).slot)
                self.assertFalse(self._update(now + 2.2, health=80, turn=slot + 1).handled)
        self._update(170.0, turn=7)
        self.assertTrue(self._update(172.0, turn=7).exhausted)
        self.assertFalse(self._update(172.1, turn=7).exhausted)

    def test_missing_health_and_turn_keep_the_original_faint_delay_and_slot(self) -> None:
        self._update(100.0, turn=4)
        self.assertIsNone(self._update(101.99, health=None, turn=None).slot)
        decision = self._update(102.0, health=None, turn=None)
        self.assertEqual((decision.slot, decision.turn), (2, 4))

    def test_health_recovery_before_delay_cancels_the_replacement(self) -> None:
        self._update(100.0)
        self.assertFalse(self._update(101.0, health=80).handled)
        self.assertFalse(self._update(102.0, health=80).handled)
        self.assertEqual(self.controller._next_slot, 2)
        self.assertIsNone(self._update(103.0, turn=2).slot)
        self.assertIsNone(self._update(104.99, turn=2).slot)
        self.assertEqual(self._update(105.0, turn=2).slot, 2)

    def test_disabled_or_ended_battle_cancels_the_delayed_selection(self) -> None:
        for changes in ({"enabled": False}, {"in_battle": False}):
            with self.subTest(changes=changes):
                self._update(100.0)
                self.assertFalse(self._update(101.0, **changes).handled)
                self.assertIsNone(self._update(102.0).slot)
                self.assertIsNone(self._update(103.99).slot)
                self.assertEqual(self._update(104.0).slot, 2)
                self.controller.reset()

    def test_failed_submission_restarts_the_two_second_delay_without_skipping_a_slot(self) -> None:
        self._update(100.0)
        self.assertEqual(self._update(102.0).slot, 2)
        self.controller.record_click(2, 1, succeeded=False)
        self.assertIsNone(self._update(102.1).slot)
        self.assertIsNone(self._update(104.09).slot)
        self.assertEqual(self._update(104.1).slot, 2)


if __name__ == "__main__":
    unittest.main()
