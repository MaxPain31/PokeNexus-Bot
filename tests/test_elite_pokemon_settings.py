import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QLabel

from launcher.settings_store import AppSettings, SettingsStore
from overlay.overlay_window import OverlayWindow
from overlay.settings_window import SettingsWindow


class ElitePokemonSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_elite_species_selection_defaults_to_all_and_filters_when_set(self) -> None:
        stop_toggle = Mock()
        stop_toggle.isChecked.return_value = True
        pokemon_input = Mock()
        settings = SimpleNamespace(
            stop_for_elite_toggle=stop_toggle,
            elite_pokemon_input=pokemon_input,
        )
        overlay = SimpleNamespace(
            settings_window=settings,
            _normalise_wild_pokemon_name=OverlayWindow._normalise_wild_pokemon_name,
            _wild_pokemon_identity=OverlayWindow._wild_pokemon_identity,
            _nature_matched_encounter_identity=None,
        )
        overlay._encounter_nature_match = MethodType(OverlayWindow._encounter_nature_match, overlay)

        pokemon_input.text.return_value = ""
        self.assertTrue(
            OverlayWindow._should_stop_for_elite_pokemon(
                overlay,
                {
                    "wildPokemonIsElite": True,
                    "wildPokemonName": "[E] Eevee appeared",
                },
            )
        )

        pokemon_input.text.return_value = "Bulbasaur, Ivysaur"
        self.assertTrue(
            OverlayWindow._should_stop_for_elite_pokemon(
                overlay,
                {
                    "wildPokemonIsElite": True,
                    "wildPokemonName": "[E] Bulbasaur appeared",
                },
            )
        )
        self.assertFalse(
            OverlayWindow._should_stop_for_elite_pokemon(
                overlay,
                {
                    "wildPokemonIsElite": True,
                    "wildPokemonName": "Elite Eevee",
                },
            )
        )
        self.assertFalse(
            OverlayWindow._should_stop_for_elite_pokemon(
                overlay,
                {
                    "wildPokemonIsElite": False,
                    "wildPokemonName": "Bulbasaur",
                },
            )
        )

    def test_elite_species_selection_is_saved_and_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.txt")
            store.save(AppSettings(elite_pokemon_names="Bulbasaur, Ivysaur"))

            self.assertEqual(
                store.load().elite_pokemon_names,
                "Bulbasaur, Ivysaur",
            )

    def test_elite_species_picker_is_available_in_encounter_settings(self) -> None:
        settings = SettingsWindow()
        label = settings.findChild(QLabel, "elite_pokemon_setting_label")

        self.assertIsNotNone(label)
        self.assertIn("empty = all", label.text())
        self.assertFalse(settings.elite_pokemon_advanced_toggle.isChecked())
        self.assertTrue(settings._elite_pokemon_advanced_settings.isHidden())
        self.assertEqual(settings.elite_pokemon_input.text(), "")
        self.assertEqual(
            settings.elite_pokemon_input.popup.findChild(QLabel).text(),
            "Choose elite Pokemon",
        )
        settings.elite_pokemon_advanced_toggle.setChecked(True)
        self.assertFalse(settings._elite_pokemon_advanced_settings.isHidden())
        settings.elite_pokemon_advanced_toggle.setChecked(False)
        self.assertTrue(settings._elite_pokemon_advanced_settings.isHidden())
        settings.close()


if __name__ == "__main__":
    unittest.main()
