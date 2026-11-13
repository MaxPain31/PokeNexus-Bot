import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from launcher.settings_store import AppSettings, SettingsStore
from models.region_config import SavedRegion


class BattleInputSettingsTests(unittest.TestCase):
    def test_new_and_legacy_settings_keep_keyboard_and_popup_defaults(self) -> None:
        self.assertFalse(AppSettings().use_mouse_clicking)
        self.assertTrue(AppSettings().auto_close_special_pokemon_message)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            store = SettingsStore(path)
            self.assertFalse(store.load().use_mouse_clicking)
            self.assertTrue(store.load().auto_close_special_pokemon_message)
            path.write_text("auto_battle=true\noverlay_background_transparency=37\n", encoding="utf-8")
            loaded = store.load()
            self.assertFalse(loaded.use_mouse_clicking)
            self.assertTrue(loaded.auto_close_special_pokemon_message)
            self.assertTrue(loaded.auto_battle)
            self.assertEqual(loaded.overlay_background_transparency, 37)

    def test_input_preferences_round_trip_with_existing_settings_and_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(AppSettings(
                regions=(
                    SavedRegion("fight", "Fight Button", 100, 200, 40, 30),
                    SavedRegion("bag", "Previous Button", 140, 200, 40, 30),
                    SavedRegion("special-pokemon-message", "Message Box", 220, 160, 300, 120),
                    SavedRegion("special-pokemon-message", "Close Button", 460, 250, 50, 25),
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
            for mouse_mode, auto_close in ((True, False), (False, True), (False, False), (True, True)):
                with self.subTest(use_mouse_clicking=mouse_mode, auto_close=auto_close):
                    expected = replace(
                        original, use_mouse_clicking=mouse_mode,
                        auto_close_special_pokemon_message=auto_close,
                    )
                    store.save(expected)
                    self.assertEqual(store.load(), expected)

    def test_invalid_input_preferences_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.txt"
            for name in ("use_mouse_clicking", "auto_close_special_pokemon_message"):
                with self.subTest(setting=name):
                    path.write_text(f"{name}=maybe\n", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, name):
                        SettingsStore(path).load()


if __name__ == "__main__":
    unittest.main()
