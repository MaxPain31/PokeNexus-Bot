import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from capture.realtime_region_monitor import (
    RealtimeRegionMonitor,
    is_shiny_wild_pokemon_name,
)
from core.pokemon_name import pokemon_species_name
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from overlay.overlay_window import OverlayWindow


class ShinyDetectionTests(unittest.TestCase):
    def test_wild_name_ocr_preserves_a_colored_marker_alongside_white_species_text(self) -> None:
        region = SavedRegion("pokemon-name", "Wild Pokemon", 0, 0, 100, 40)
        crop = np.full((40, 100, 3), (46, 33, 24), dtype=np.uint8)
        crop[10:30, 10:20] = (21, 204, 250)  # Gold marker, below the old threshold.
        crop[10:30, 40:80] = (255, 255, 255)
        extractor = Mock()
        extractor.extract.return_value = OCRResult("", 200, 80)
        monitor = RealtimeRegionMonitor(
            (region,), 42, QRect(0, 0, 800, 600), image_capture=Mock(),
            region_cropper=Mock(crop=Mock(return_value=crop)), text_extractor=extractor,
        )
        monitor._read_regions(crop, (region,), {
            "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
        })
        processed = extractor.extract.call_args.args[0]
        height, width = processed.shape
        marker = processed[height // 2, width // 6]
        species = processed[height // 2, width * 3 // 5]
        self.assertNotEqual(marker, processed[0, 0])
        self.assertEqual(marker, species)

    def test_shiny_name_markers_allow_ocr_spacing_and_explicit_prefixes(self) -> None:
        for name in (
            "[S] Eevee", "[ s ] Eevee", "[\nS\n] Eevee", "［Ｓ］ Eevee",
            "Shiny Eevee", "SHINY Eevee", "[E] Shiny Eevee", "Eevee [S]",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_shiny_wild_pokemon_name(name))
        for name in (None, "", "Eevee", "[E] Eevee", "Shinx", "Unshiny Eevee"):
            with self.subTest(ordinary=name):
                self.assertFalse(is_shiny_wild_pokemon_name(name))

    def test_battle_announcement_can_supply_shiny_status_when_name_ocr_is_missing(self) -> None:
        regions = (
            SavedRegion("chat-box", "Battle Chat", 20, 20, 300, 200),
            SavedRegion("pokemon-name", "Wild Pokemon", 400, 20, 200, 40),
            SavedRegion("pokemon-name", "Your Pokemon", 400, 300, 200, 40),
            SavedRegion("special-pokemon-message", "Message Box", 200, 150, 300, 160),
        )
        monitor = RealtimeRegionMonitor(
            regions, 42, QRect(0, 0, 800, 600), image_capture=Mock(), text_extractor=Mock(),
        )
        texts = {"Battle Chat": "A wild [S] Eevee appeared!"}
        monitor._read_regions = Mock(side_effect=lambda screenshot, requested, timings: [
            (region, OCRResult(texts.get(region.label, ""), 200, 40, 95.0))
            for region in requested
        ])

        def capture():
            return monitor._capture_state({
                "capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0,
            })[0]

        self.assertTrue(capture()["wildPokemonIsShiny"])
        texts["Battle Chat"] = "A wild Shiny Eevee appeared!"
        self.assertTrue(capture()["wildPokemonIsShiny"])
        texts.update({
            "Battle Chat": "A wild Eevee appeared!\nYour Shiny Pikachu used Tackle!",
            "Your Pokemon": "[S] Pikachu",
            "Message Box": "Special Encounter! You have encountered a special pokemon!",
        })
        self.assertFalse(capture()["wildPokemonIsShiny"])

    def test_latest_encounter_announcement_wins_over_older_shiny_chat(self) -> None:
        self.assertEqual(
            RealtimeRegionMonitor._extract_wild_pokemon_name(
                "A wild [S] Eevee appeared!\nA wild Pidgey appeared!"
            ),
            "Pidgey",
        )

    def test_species_identity_excludes_traits_and_level_but_keeps_form_names(self) -> None:
        for name, expected in (
            ("[ S ] [ E ] Eevee Lv. 20", "Eevee"),
            ("Shiny Mr. Mime", "Mr. Mime"),
            ("Elite Shiny Raichu (Alolan)", "Raichu (Alolan)"),
            ("[S] Farfetch'd", "Farfetch'd"),
            ("Shiny Type: Null", "Type: Null"),
        ):
            with self.subTest(name=name):
                self.assertEqual(pokemon_species_name(name), expected)


class ShinyEncounterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio_path = Path(self.directory.name) / "shiny.wav"
        with wave.open(str(self.audio_path), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(8000)
            sound.writeframes(b"\x00\x00" * 80)
        player_patch = patch("overlay.overlay_window.QMediaPlayer")
        audio_patch = patch("overlay.overlay_window.QAudioOutput")
        self.player_factory = player_patch.start()
        self.audio_factory = audio_patch.start()
        self.addCleanup(player_patch.stop)
        self.addCleanup(audio_patch.stop)
        with patch("overlay.overlay_window.BattleHistory", return_value=Mock()):
            self.overlay = OverlayWindow()
        self.addCleanup(self._dispose)
        self.overlay._target_window_id = 42
        self.overlay._target_geometry = QRect(0, 0, 800, 600)
        self.overlay._auto_battle_input = Mock()
        self.overlay.region_test_log_window.append_entry = Mock()
        settings = self.overlay.settings_window
        settings.auto_battle_toggle.setChecked(False)
        settings.shiny_pokemon_sound_toggle.setChecked(True)
        settings.shiny_pokemon_sound_path.setText(str(self.audio_path))
        self.overlay._pokemon_rarity_by_name = {"eevee": "r", "pidgey": "c"}
        self.regions = (
            SavedRegion("chat-box", "Battle Chat", 20, 20, 300, 200),
            SavedRegion("pokemon-name", "Wild Pokemon", 400, 20, 200, 40),
        )
        self.overlay.region_window.set_regions(self.regions)
        self.monitor = RealtimeRegionMonitor(
            self.regions, 42, self.overlay._target_geometry,
            image_capture=Mock(), text_extractor=Mock(), parent=self.overlay,
        )
        self.overlay._realtime_capture_requested = True
        with patch("overlay.overlay_window.RealtimeRegionMonitor.start"), patch(
            "overlay.overlay_window.RealtimeRegionMonitor", return_value=self.monitor,
        ):
            self.overlay._start_realtime_region_capture()

    def _dispose(self) -> None:
        self.overlay.stop_tracking()
        self.overlay.fishing_preview_window.close()
        self.overlay.region_test_log_window.close()
        self.overlay.close()
        self.overlay.deleteLater()
        self.application.processEvents()

    @staticmethod
    def _state(at, *, shiny=False, name="Eevee", battle=True, **changes):
        return {
            "captureStartedAt": at,
            "onBattleMode": battle,
            "wildPokemonName": name if battle else None,
            "wildPokemonIsShiny": shiny,
            "wildPokemonIsElite": False,
            "chatBoxMessage": f"A wild {name} appeared!" if battle else "",
            "showingSpecialPokemonMessage": False,
            **changes,
        }

    def _run_frames(self, *states) -> None:
        position = [0]
        self.monitor._capture_state = Mock(side_effect=lambda timings: (
            states[position[0]], "12:00:00 PM",
        ))
        self.monitor.isInterruptionRequested = Mock(
            side_effect=lambda: position[0] >= len(states),
        )
        self.monitor.msleep = Mock(side_effect=lambda milliseconds: position.__setitem__(
            0, position[0] + 1,
        ))
        self.monitor._get_memory_usage = Mock(return_value=(0, 0, 0, 0))
        with patch("builtins.print"), patch(
            "overlay.overlay_window.time.monotonic",
            side_effect=lambda: states[min(position[0], len(states) - 1)]["captureStartedAt"],
        ):
            self.monitor.run()

    def test_late_shiny_detection_updates_summary_and_alerts_once_without_recounting(self) -> None:
        summary = self.overlay.pokemon_encounter_summary_window
        summary._elapsed_session_ms = 123_000
        self._run_frames(
            self._state(100.0),
            self._state(100.1, shiny=True),
            self._state(100.2, shiny=True),
            self._state(100.3),
            self._state(100.4, shiny=True),
        )

        self.player_factory.return_value.play.assert_called_once()
        self.assertIn("Shiny Eevee", summary.current_pokemon_label.text())
        self.assertIn("#FACC15", summary.current_pokemon_label.text())
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.assertEqual(self.overlay.pokemon_encounter_rarity_counts["r"], 1)
        self.assertEqual(summary._elapsed_session_ms, 123_000)
        self.assertEqual(summary.timer_label.text(), "00:02:03")

    def test_shiny_survives_short_ocr_gaps_and_resets_for_the_next_same_species_encounter(self) -> None:
        observed = []
        self.monitor.state_updated.connect(observed.append)
        self._run_frames(
            self._state(100.0, shiny=True),
            self._state(100.1, battle=False),
            self._state(100.2, battle=False),
            self._state(100.3),
        )
        self.assertTrue(observed[-1]["wildPokemonIsShiny"])
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.player_factory.return_value.play.assert_called_once()

        self._run_frames(
            self._state(101.0, battle=False),
            self._state(101.1, battle=False),
            self._state(101.2, battle=False),
            self._state(101.3),
        )
        self.assertFalse(observed[-1]["wildPokemonIsShiny"])
        self.assertNotIn(
            "Shiny", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text(),
        )
        self.assertEqual(self.overlay.pokemon_encounter_count, 2)
        self.player_factory.return_value.play.assert_called_once()

        self._run_frames(
            self._state(102.0, battle=False),
            self._state(102.1, battle=False),
            self._state(102.2, battle=False),
            self._state(102.3, shiny=True),
        )
        self.assertEqual(self.overlay.pokemon_encounter_count, 3)
        self.assertEqual(self.overlay.pokemon_encounter_rarity_counts["r"], 3)
        self.assertEqual(self.player_factory.return_value.play.call_count, 2)

    def test_popup_uncertainty_cannot_discard_a_confirmed_shiny_or_repeat_its_alert(self) -> None:
        observed = []
        self.monitor.state_updated.connect(observed.append)
        self._run_frames(
            self._state(100.0, shiny=True),
            *(self._state(at, battle=False, showingSpecialPokemonMessage=None)
              for at in (100.1, 100.2, 100.3, 100.4)),
            self._state(100.5),
        )
        self.assertTrue(observed[-1]["wildPokemonIsShiny"])
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.player_factory.return_value.play.assert_called_once()

    def test_a_shiny_marker_seen_before_the_name_is_readable_is_kept_for_the_announcement(self) -> None:
        self._run_frames(
            self._state(100.0, shiny=True, name="[S]", chatBoxMessage=""),
            self._state(100.1),
        )
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.player_factory.return_value.play.assert_called_once()
        self.assertIn(
            "Shiny Eevee", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text(),
        )

    def test_popup_closure_allows_a_late_shiny_alert_while_remaining_mouse_only(self) -> None:
        close = SavedRegion("special-pokemon-message", "Close Button", 500, 100, 25, 25)
        self.overlay.region_window.set_regions((*self.regions, close))
        self._run_frames(
            self._state(
                100.0, showingSpecialPokemonMessage=True,
                specialPokemonMessage="Special Encounter! You have encountered a special pokemon!",
            ),
            self._state(100.1, shiny=True),
            self._state(100.2),
        )
        self.player_factory.return_value.play.assert_called_once()
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.overlay._auto_battle_input.click_region.assert_called_once_with(
            42, close, self.overlay._target_geometry,
        )
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.assertIn(
            "Shiny Eevee", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text(),
        )

    def test_queued_encounter_events_from_a_stopped_monitor_are_ignored(self) -> None:
        self._run_frames(self._state(100.0))
        self.overlay.stop_realtime_region_capture()
        self.monitor.isInterruptionRequested = Mock(return_value=False)
        self.monitor.pokemon_encounter_updated.emit("Eevee", self._state(101.0, shiny=True))
        self.monitor.pokemon_encountered.emit("Eevee", self._state(101.1, shiny=True))
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.player_factory.assert_not_called()
        self.assertNotIn(
            "Shiny", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text(),
        )

    def test_encounter_alert_uses_its_capture_even_when_battle_input_rejects_that_frame(self) -> None:
        self.overlay._last_input_completed_at = 101.0
        self._run_frames(self._state(100.0, shiny=True))

        self.player_factory.return_value.play.assert_called_once()
        self.assertIn(
            "Shiny Eevee", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text(),
        )
        self.overlay._auto_battle_input.tap_key.assert_not_called()
        self.overlay._auto_battle_input.click_region.assert_not_called()

    def test_disabling_the_sound_still_displays_shiny_and_preserves_species_rarity(self) -> None:
        self.overlay.settings_window.shiny_pokemon_sound_toggle.setChecked(False)
        self._run_frames(self._state(100.0, shiny=True, name="[ S ] Eevee"))

        self.player_factory.assert_not_called()
        self.assertIn(
            "Shiny Eevee", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text(),
        )
        self.assertEqual(self.overlay.pokemon_encounter_rarity_counts["r"], 1)
        self.assertEqual(self.overlay.pokemon_encounter_rarity_counts["c"], 0)

    def test_shiny_alert_is_available_without_a_readable_chat_announcement(self) -> None:
        self._run_frames(self._state(
            100.0, shiny=True, name="[S] Eevee", chatBoxMessage="", enemyPokemonHealth=100,
        ))

        self.player_factory.return_value.play.assert_called_once()
        self.assertEqual(self.overlay.pokemon_encounter_count, 1)
        self.assertIn(
            "Shiny Eevee", self.overlay.pokemon_encounter_summary_window.current_pokemon_label.text(),
        )


if __name__ == "__main__":
    unittest.main()
