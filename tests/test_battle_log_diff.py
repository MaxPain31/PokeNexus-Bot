import unittest

from capture.realtime_region_monitor import RealtimeRegionMonitor


class BattleLogDiffTests(unittest.TestCase):
    def test_extracts_only_new_text_after_previous_partial_log(self) -> None:
        previous = "Turn 3"
        current = (
            "Go! Latios! Turn 1 Latios used Draco Meteor! "
            "Latios fainted! Go! Golurk! Turn 2 Golurk used Stomping Tantrum! "
            "Turn 3 Golurk used Stomping Tantrum! The opposing Turtonator "
            "lost 7% of its health!"
        )

        self.assertEqual(
            RealtimeRegionMonitor._new_chat_message(previous, current),
            "Golurk used Stomping Tantrum! The opposing Turtonator "
            "lost 7% of its health!",
        )

    def test_extracts_suffix_from_incremental_chat(self) -> None:
        self.assertEqual(
            RealtimeRegionMonitor._new_chat_message(
                "Turn 3",
                "Turn 3 | Golurk used Stomping Tantrum!",
            ),
            "Golurk used Stomping Tantrum!",
        )

    def test_does_not_repeat_an_unchanged_chat_snapshot(self) -> None:
        snapshot = "Golurk used Stomping Tantrum!"
        self.assertEqual(
            RealtimeRegionMonitor._new_chat_message(snapshot, snapshot),
            "",
        )

    def test_extracts_only_new_turn_after_chat_view_scrolls(self) -> None:
        previous = (
            "Turn 2\nGolurk used Night Shade!\n"
            "The opposing Drifloon lost 43% of its health!\n"
            "Turn 3\nGolurk used Night Shade!\n"
            "The opposing Drifloon lost 44% of its health!\n"
            "Turn 4\nGolurk used Night Shade!"
        )
        current = (
            "Turn 3\nGolurk used Night Shade!\n"
            "The opposing Drifloon lost 44% of its health!\n"
            "Turn 4\nGolurk used Night Shade!\n"
            "Turn 5\nGolurk used Night Shade!"
        )

        self.assertEqual(
            RealtimeRegionMonitor._new_chat_message(previous, current),
            "Turn 5\nGolurk used Night Shade!",
        )

    def test_ignores_shorter_stale_chat_snapshot(self) -> None:
        previous = (
            "Turn 3\nGolurk used Night Shade!\n"
            "Turn 4\nGolurk used Night Shade!"
        )

        self.assertEqual(
            RealtimeRegionMonitor._new_chat_message(
                previous,
                "Turn 4\nGolurk used Night Shade!",
            ),
            "",
        )

    def test_normalizes_chat_whitespace_without_collapsing_ocr_rows(self) -> None:
        self.assertEqual(
            RealtimeRegionMonitor._normalize_chat_snapshot(
                " Turn 2 \n Golurk   used Night Shade! \n\n"
            ),
            "Turn 2\nGolurk used Night Shade!",
        )


if __name__ == "__main__":
    unittest.main()
