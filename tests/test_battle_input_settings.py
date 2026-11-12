import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from launcher.settings_store import AppSettings, SettingsStore
from models.region_config import SavedRegion


class BattleInputSettingsTests(unittest.TestCase):
    def test_new_and_legacy_settings_default_to_number_keys(self) -> None:
        self.assertFalse(AppSettings().use_mouse_clicking)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            store = SettingsStore(path)
            self.assertFalse(store.load().use_mouse_clicking)
            path.write_text("auto_battle=true\noverlay_background_transparency=37\n", encoding="utf-8")
            loaded = store.load()
            self.assertFalse(loaded.use_mouse_clicking)
            self.assertTrue(loaded.auto_battle)
            self.assertEqual(loaded.overlay_background_transparency, 37)

    def test_both_input_modes_round_trip_with_existing_preferences_and_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(AppSettings(
                regions=(
                    SavedRegion("fight", "Fight Button", 100, 200, 40, 30),
                    SavedRegion("bag", "Previous Button", 140, 200, 40, 30),
                ),
                auto_catching=True,
                auto_fishing=True,
                auto_fishing_press_f_on_missing_zone=True,
                auto_catching_item="Ultra Ball",
                overlay_background_transparency=37,
                pokemon_encounter_count=128,
                pokemon_encounter_rarity_counts={"r": 12},
            ))
            original = store.load()
            for enabled in (True, False):
                with self.subTest(use_mouse_clicking=enabled):
                    expected = replace(original, use_mouse_clicking=enabled)
                    store.save(expected)
                    self.assertEqual(store.load(), expected)

    def test_invalid_input_mode_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            path.write_text("use_mouse_clicking=maybe\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "use_mouse_clicking"):
                SettingsStore(path).load()


if __name__ == "__main__":
    unittest.main()
