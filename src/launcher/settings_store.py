import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from core.pokemon_rarity import RARITIES
from models.region_config import REGION_FEATURE_LABELS, SavedRegion


@dataclass(frozen=True)
class AppSettings:
    executable_path: str = ""
    auto_battle: bool = True
    auto_switch_fainted_pokemon: bool = False
    stop_auto_battle_for_desired_pokemon: bool = False
    desired_pokemon: str = ""
    stop_auto_battle_for_shiny: bool = False
    stop_auto_battle_for_elite: bool = False
    elite_pokemon_names: str = ""
    desired_pokemon_sound_enabled: bool = False
    desired_pokemon_sound_path: str = ""
    shiny_pokemon_sound_enabled: bool = False
    shiny_pokemon_sound_path: str = ""
    elite_pokemon_sound_enabled: bool = False
    elite_pokemon_sound_path: str = ""
    auto_walk: bool = False
    auto_walk_direction: str = "Horizontal"
    auto_walk_duration_mode: str = "Default"
    auto_walk_duration_seconds: float = 0.5
    auto_walk_min_duration_seconds: float = 0.5
    auto_walk_max_duration_seconds: float = 3.0
    show_chat_battle_logs: bool = False
    show_pokemon_encounter_summary: bool = False
    pokemon_encounter_count: int = 0
    pokemon_encounter_rarity_counts: dict[str, int] = field(default_factory=dict)
    pokemon_encounter_date: str = field(
        default_factory=lambda: date.today().isoformat()
    )
    show_test_region_logs: bool = True
    save_test_region_screenshots: bool = True
    regions: tuple[SavedRegion, ...] = ()
    auto_run: bool = False
    auto_catching: bool = False
    auto_fishing: bool = False
    auto_fishing_reaction_lead_ms: int = 50
    auto_fishing_space_hold_ms: int = 10
    auto_fishing_press_f_on_missing_zone: bool = False
    auto_catching_wild_hp: int = 100
    auto_catching_item: str = "Ultra Ball"
    auto_catching_desired_filter: bool = False
    auto_catching_shiny_filter: bool = False
    auto_catching_elite_filter: bool = False
    overlay_background_transparency: int = 0
    use_mouse_clicking: bool = False


class SettingsStore:
    """Read and write launcher preferences in a human-readable text file."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            config_directory = Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppConfigLocation
                )
            )
            path = config_directory / "settings.txt"
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()

        values: dict[str, str] = {}
        for line_number, raw_line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(
                    f"Invalid settings entry on line {line_number}: expected key=value."
                )
            key, value = line.split("=", maxsplit=1)
            values[key.strip()] = value.strip()

        settings = AppSettings(
            executable_path=values.get("executable_path", ""),
            auto_battle=self._parse_bool(values, "auto_battle", default=True),
            auto_switch_fainted_pokemon=self._parse_bool(
                values, "auto_switch_fainted_pokemon", default=False
            ),
            auto_run=self._parse_bool(values, "auto_run", default=False),
            auto_catching=self._parse_bool(
                values, "auto_catching", default=False
            ),
            auto_fishing=self._parse_bool(
                values, "auto_fishing", default=False
            ),
            auto_fishing_reaction_lead_ms=self._parse_integer(
                values,
                "auto_fishing_reaction_lead_ms",
                default=50,
                minimum=0,
                maximum=500,
            ),
            auto_fishing_space_hold_ms=self._parse_integer(
                values,
                "auto_fishing_space_hold_ms",
                default=10,
                minimum=0,
                maximum=100,
            ),
            auto_fishing_press_f_on_missing_zone=self._parse_bool(
                values,
                "auto_fishing_press_f_on_missing_zone",
                default=False,
            ),
            auto_catching_wild_hp=self._parse_integer(
                values,
                "auto_catching_wild_hp",
                default=100,
                minimum=1,
                maximum=100,
            ),
            auto_catching_item=values.get(
                "auto_catching_item", "Ultra Ball"
            ),
            auto_catching_desired_filter=self._parse_bool(
                values, "auto_catching_desired_filter", default=False
            ),
            auto_catching_shiny_filter=self._parse_bool(
                values, "auto_catching_shiny_filter", default=False
            ),
            auto_catching_elite_filter=self._parse_bool(
                values, "auto_catching_elite_filter", default=False
            ),
            stop_auto_battle_for_desired_pokemon=self._parse_bool(
                values,
                "stop_auto_battle_for_desired_pokemon",
                default=False,
            ),
            desired_pokemon=values.get(
                "desired_pokemon",
                values.get("auto_catching_desired_pokemon", ""),
            ),
            stop_auto_battle_for_shiny=self._parse_bool(
                values,
                "stop_auto_battle_for_shiny",
                default=False,
            ),
            stop_auto_battle_for_elite=self._parse_bool(
                values,
                "stop_auto_battle_for_elite",
                default=False,
            ),
            elite_pokemon_names=values.get(
                "elite_pokemon_names",
                values.get("auto_catching_elite_pokemon", ""),
            ),
            desired_pokemon_sound_enabled=self._parse_bool(
                values, "desired_pokemon_sound_enabled", default=False
            ),
            desired_pokemon_sound_path=values.get(
                "desired_pokemon_sound_path", ""
            ),
            shiny_pokemon_sound_enabled=self._parse_bool(
                values, "shiny_pokemon_sound_enabled", default=False
            ),
            shiny_pokemon_sound_path=values.get("shiny_pokemon_sound_path", ""),
            elite_pokemon_sound_enabled=self._parse_bool(
                values, "elite_pokemon_sound_enabled", default=False
            ),
            elite_pokemon_sound_path=values.get("elite_pokemon_sound_path", ""),
            auto_walk=self._parse_bool(values, "auto_walk", default=False),
            auto_walk_direction=self._parse_choice(
                values,
                "auto_walk_direction",
                ("Horizontal", "Vertical", "Random"),
                "Horizontal",
            ),
            auto_walk_duration_mode=self._parse_choice(
                values,
                "auto_walk_duration_mode",
                ("Default", "Random"),
                "Default",
            ),
            auto_walk_duration_seconds=self._parse_duration(
                values, "auto_walk_duration_seconds", 0.5
            ),
            auto_walk_min_duration_seconds=self._parse_duration(
                values, "auto_walk_min_duration_seconds", 0.5
            ),
            auto_walk_max_duration_seconds=self._parse_duration(
                values, "auto_walk_max_duration_seconds", 3.0
            ),
            show_chat_battle_logs=self._parse_bool(
                values, "show_chat_battle_logs", default=False
            ),
            show_pokemon_encounter_summary=self._parse_bool(
                values, "show_pokemon_encounter_summary", default=False
            ),
            pokemon_encounter_count=self._parse_daily_encounter_count(values),
            pokemon_encounter_rarity_counts=self._parse_daily_rarity_counts(values),
            pokemon_encounter_date=self._current_encounter_date(values),
            show_test_region_logs=self._parse_bool(
                values, "show_test_region_logs", default=True
            ),
            save_test_region_screenshots=self._parse_bool(
                values, "save_test_region_screenshots", default=True
            ),
            overlay_background_transparency=self._parse_integer(
                values,
                "overlay_background_transparency",
                default=0,
                minimum=0,
                maximum=100,
            ),
            use_mouse_clicking=self._parse_bool(
                values, "use_mouse_clicking", default=False
            ),
            regions=self._parse_regions(values.get("regions", "[]")),
        )
        if settings.auto_walk_max_duration_seconds < settings.auto_walk_min_duration_seconds:
            raise ValueError(
                "Invalid auto-walk duration range: maximum must be greater "
                "than or equal to minimum."
            )
        return settings

    def save(self, settings: AppSettings) -> None:
        if settings.auto_walk_direction not in ("Horizontal", "Vertical", "Random"):
            raise ValueError(
                "Invalid auto-walk direction: expected Horizontal, Vertical, or Random."
            )
        if settings.pokemon_encounter_count < 0:
            raise ValueError("Pokemon encounter count cannot be negative.")
        valid_rarity_codes = {code for code, _label, _color in RARITIES}
        if any(
            not isinstance(code, str)
            or code not in valid_rarity_codes
            or type(count) is not int
            or count < 0
            for code, count in settings.pokemon_encounter_rarity_counts.items()
        ):
            raise ValueError(
                "Pokemon encounter rarity counts must have non-negative integer values."
            )
        if not 1 <= settings.auto_catching_wild_hp <= 100:
            raise ValueError(
                "Auto catching wild HP must be between 1 and 100 percent."
            )
        if not 0 <= settings.auto_fishing_reaction_lead_ms <= 500:
            raise ValueError(
                "Auto Fishing reaction lead must be between 0 and 500 ms."
            )
        if not 0 <= settings.auto_fishing_space_hold_ms <= 100:
            raise ValueError(
                "Auto Fishing Space hold duration must be between 0 and 100 ms."
            )
        if (
            type(settings.overlay_background_transparency) is not int
            or not 0 <= settings.overlay_background_transparency <= 100
        ):
            raise ValueError(
                "Overlay background transparency must be between 0 and 100 percent."
            )
        try:
            date.fromisoformat(settings.pokemon_encounter_date)
        except ValueError as error:
            raise ValueError(
                "Invalid value for pokemon_encounter_date: expected YYYY-MM-DD."
            ) from error
        if settings.auto_walk_duration_mode not in ("Default", "Random"):
            raise ValueError(
                "Invalid auto-walk duration mode: expected Default or Random."
            )
        durations = (
            settings.auto_walk_duration_seconds,
            settings.auto_walk_min_duration_seconds,
            settings.auto_walk_max_duration_seconds,
        )
        if any(
            not math.isfinite(duration)
            or duration < 0.5
            or duration > 3600.0
            for duration in durations
        ):
            raise ValueError(
                "Auto-walk durations must be between 0.5 and 3600 seconds."
            )
        if settings.auto_walk_max_duration_seconds < settings.auto_walk_min_duration_seconds:
            raise ValueError(
                "Invalid auto-walk duration range: maximum must be greater "
                "than or equal to minimum."
            )
        regions_json = json.dumps(
            [self._region_to_dict(region) for region in settings.regions],
            separators=(",", ":"),
        )
        contents = (
            "# Game Bot settings\n"
            f"executable_path={settings.executable_path}\n"
            f"auto_battle={str(settings.auto_battle).lower()}\n"
            "auto_switch_fainted_pokemon="
            f"{str(settings.auto_switch_fainted_pokemon).lower()}\n"
            f"auto_run={str(settings.auto_run).lower()}\n"
            f"auto_catching={str(settings.auto_catching).lower()}\n"
            f"auto_fishing={str(settings.auto_fishing).lower()}\n"
            "auto_fishing_reaction_lead_ms="
            f"{settings.auto_fishing_reaction_lead_ms}\n"
            "auto_fishing_space_hold_ms="
            f"{settings.auto_fishing_space_hold_ms}\n"
            "auto_fishing_press_f_on_missing_zone="
            f"{str(settings.auto_fishing_press_f_on_missing_zone).lower()}\n"
            f"auto_catching_wild_hp={settings.auto_catching_wild_hp}\n"
            f"auto_catching_item={settings.auto_catching_item}\n"
            "auto_catching_desired_filter="
            f"{str(settings.auto_catching_desired_filter).lower()}\n"
            "auto_catching_shiny_filter="
            f"{str(settings.auto_catching_shiny_filter).lower()}\n"
            "auto_catching_elite_filter="
            f"{str(settings.auto_catching_elite_filter).lower()}\n"
            "stop_auto_battle_for_desired_pokemon="
            f"{str(settings.stop_auto_battle_for_desired_pokemon).lower()}\n"
            f"desired_pokemon={settings.desired_pokemon}\n"
            "stop_auto_battle_for_shiny="
            f"{str(settings.stop_auto_battle_for_shiny).lower()}\n"
            "stop_auto_battle_for_elite="
            f"{str(settings.stop_auto_battle_for_elite).lower()}\n"
            f"elite_pokemon_names={settings.elite_pokemon_names}\n"
            "desired_pokemon_sound_enabled="
            f"{str(settings.desired_pokemon_sound_enabled).lower()}\n"
            f"desired_pokemon_sound_path={settings.desired_pokemon_sound_path}\n"
            "shiny_pokemon_sound_enabled="
            f"{str(settings.shiny_pokemon_sound_enabled).lower()}\n"
            f"shiny_pokemon_sound_path={settings.shiny_pokemon_sound_path}\n"
            "elite_pokemon_sound_enabled="
            f"{str(settings.elite_pokemon_sound_enabled).lower()}\n"
            f"elite_pokemon_sound_path={settings.elite_pokemon_sound_path}\n"
            f"auto_walk={str(settings.auto_walk).lower()}\n"
            f"auto_walk_direction={settings.auto_walk_direction}\n"
            f"auto_walk_duration_mode={settings.auto_walk_duration_mode}\n"
            f"auto_walk_duration_seconds={settings.auto_walk_duration_seconds}\n"
            "auto_walk_min_duration_seconds="
            f"{settings.auto_walk_min_duration_seconds}\n"
            "auto_walk_max_duration_seconds="
            f"{settings.auto_walk_max_duration_seconds}\n"
            f"show_chat_battle_logs={str(settings.show_chat_battle_logs).lower()}\n"
            "show_pokemon_encounter_summary="
            f"{str(settings.show_pokemon_encounter_summary).lower()}\n"
            f"pokemon_encounter_count={settings.pokemon_encounter_count}\n"
            "pokemon_encounter_rarity_counts="
            f"{json.dumps(settings.pokemon_encounter_rarity_counts, separators=(',', ':'))}\n"
            f"pokemon_encounter_date={settings.pokemon_encounter_date}\n"
            f"show_test_region_logs={str(settings.show_test_region_logs).lower()}\n"
            "save_test_region_screenshots="
            f"{str(settings.save_test_region_screenshots).lower()}\n"
            "overlay_background_transparency="
            f"{settings.overlay_background_transparency}\n"
            f"use_mouse_clicking={str(settings.use_mouse_clicking).lower()}\n"
            f"regions={regions_json}\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(contents, encoding="utf-8")

    @staticmethod
    def _parse_bool(
        values: dict[str, str], key: str, default: bool
    ) -> bool:
        value = values.get(key)
        if value is None:
            return default

        normalized = value.casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(f"Invalid value for {key}: expected true or false.")

    @staticmethod
    def _current_encounter_date(values: dict[str, str]) -> str:
        raw_date = values.get("pokemon_encounter_date")
        if raw_date is None:
            return date.today().isoformat()
        try:
            saved_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError(
                "Invalid value for pokemon_encounter_date: expected YYYY-MM-DD."
            ) from error
        return date.today().isoformat() if saved_date != date.today() else raw_date

    @classmethod
    def _parse_daily_encounter_count(cls, values: dict[str, str]) -> int:
        current_date = cls._current_encounter_date(values)
        if current_date != values.get("pokemon_encounter_date"):
            return 0
        raw_count = values.get("pokemon_encounter_count", "0")
        try:
            count = int(raw_count)
        except ValueError as error:
            raise ValueError(
                "Invalid value for pokemon_encounter_count: expected an integer."
            ) from error
        if count < 0:
            raise ValueError("Pokemon encounter count cannot be negative.")
        return count

    @classmethod
    def _parse_daily_rarity_counts(cls, values: dict[str, str]) -> dict[str, int]:
        empty_counts = {code: 0 for code, _label, _color in RARITIES}
        current_date = cls._current_encounter_date(values)
        if current_date != values.get("pokemon_encounter_date"):
            return empty_counts
        raw_counts = values.get("pokemon_encounter_rarity_counts")
        if raw_counts is None:
            return empty_counts
        try:
            parsed_counts = json.loads(raw_counts)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Invalid value for pokemon_encounter_rarity_counts: "
                "expected a JSON object."
            ) from error
        if not isinstance(parsed_counts, dict):
            raise ValueError(
                "Invalid value for pokemon_encounter_rarity_counts: "
                "expected a JSON object."
            )
        valid_codes = set(empty_counts)
        if any(
            not isinstance(code, str)
            or code not in valid_codes
            or type(count) is not int
            or count < 0
            for code, count in parsed_counts.items()
        ):
            raise ValueError(
                "Invalid value for pokemon_encounter_rarity_counts: "
                "expected known rarity codes and non-negative integer counts."
            )
        return {code: parsed_counts.get(code, 0) for code in empty_counts}

    @staticmethod
    def _parse_choice(
        values: dict[str, str],
        key: str,
        choices: tuple[str, ...],
        default: str,
    ) -> str:
        value = values.get(key, default)
        choice = next(
            (candidate for candidate in choices if candidate.casefold() == value.casefold()),
            None,
        )
        if choice is None:
            expected = ", ".join(choices)
            raise ValueError(f"Invalid value for {key}: expected one of {expected}.")
        return choice

    @staticmethod
    def _parse_duration(
        values: dict[str, str],
        key: str,
        default: float,
    ) -> float:
        raw_value = values.get(key)
        if raw_value is None:
            return default
        try:
            duration = float(raw_value)
        except ValueError as error:
            raise ValueError(f"Invalid value for {key}: expected seconds.") from error
        if not math.isfinite(duration) or duration < 0.5 or duration > 3600.0:
            raise ValueError(
                f"Invalid value for {key}: duration must be between 0.5 and 3600 seconds."
            )
        return duration

    @staticmethod
    def _parse_integer(
        values: dict[str, str],
        key: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        raw_value = values.get(key)
        if raw_value is None:
            return default
        try:
            value = int(raw_value)
        except ValueError as error:
            raise ValueError(f"Invalid value for {key}: expected an integer.") from error
        if not minimum <= value <= maximum:
            raise ValueError(
                f"Invalid value for {key}: expected {minimum} to {maximum}."
            )
        return value

    @staticmethod
    def _region_to_dict(region: SavedRegion) -> dict[str, str | int]:
        return {
            "feature": region.feature,
            "label": region.label,
            "x": region.x,
            "y": region.y,
            "width": region.width,
            "height": region.height,
        }

    @staticmethod
    def _parse_regions(raw_value: str) -> tuple[SavedRegion, ...]:
        try:
            values = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid saved regions: {error.msg}.") from error
        if not isinstance(values, list):
            raise ValueError("Invalid saved regions: expected a JSON list.")

        regions: list[SavedRegion] = []
        expected_keys = {"feature", "label", "x", "y", "width", "height"}
        for index, value in enumerate(values, start=1):
            if not isinstance(value, dict) or set(value) != expected_keys:
                raise ValueError(
                    f"Invalid saved region {index}: expected feature, label, "
                    "x, y, width, and height."
                )
            feature = value["feature"]
            label = value["label"]
            coordinates = (value["x"], value["y"], value["width"], value["height"])
            if (
                not isinstance(feature, str)
                or feature not in REGION_FEATURE_LABELS
                or not isinstance(label, str)
                or any(type(coordinate) is not int for coordinate in coordinates)
            ):
                raise ValueError(
                    f"Invalid saved region {index}: invalid field type or feature."
                )
            regions.append(
                SavedRegion(
                    feature=feature,
                    label=label,
                    x=value["x"],
                    y=value["y"],
                    width=value["width"],
                    height=value["height"],
                )
            )
        return tuple(regions)
