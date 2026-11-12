import json
import re
from pathlib import Path

from utils.runtime_paths import user_data_path


class BattleHistory:
    """Persist observed move damage by active Pokemon and opponent."""

    _VERSION = 1
    _MINIMUM_OBSERVATIONS = 2

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_path("battle_history.json")
        self._observations: dict[tuple[str, str, str], tuple[str, int, float]] = {}
        self._load()

    def average_damage_by_move(
        self,
        player_pokemon: str,
        opponent_pokemon: str,
    ) -> dict[str, float]:
        player_key = _normalize_name(player_pokemon)
        opponent_key = _normalize_name(opponent_pokemon)
        return {
            move_key: total_damage / count
            for (stored_player, stored_opponent, move_key), (
                _,
                count,
                total_damage,
            ) in self._observations.items()
            if stored_player == player_key
            and stored_opponent == opponent_key
            and count >= self._MINIMUM_OBSERVATIONS
        }

    def record_damage(
        self,
        player_pokemon: str,
        opponent_pokemon: str,
        move_name: str,
        damage_percent: int,
    ) -> tuple[int, float]:
        player_key = _normalize_name(player_pokemon)
        opponent_key = _normalize_name(opponent_pokemon)
        move_key = _normalize_name(move_name)
        if not player_key or not opponent_key or not move_key:
            raise ValueError("Battle history requires both Pokemon and a move name.")
        if not 0 < damage_percent <= 100:
            raise ValueError("Observed move damage must be between 1 and 100 percent.")

        key = (player_key, opponent_key, move_key)
        display_name, count, total_damage = self._observations.get(
            key,
            (move_name.strip(), 0, 0.0),
        )
        updated_count = count + 1
        updated_total_damage = total_damage + damage_percent
        self._observations[key] = (
            display_name,
            updated_count,
            updated_total_damage,
        )
        try:
            self._save()
        except OSError:
            if count:
                self._observations[key] = (display_name, count, total_damage)
            else:
                del self._observations[key]
            raise
        return updated_count, updated_total_damage / updated_count

    def _load(self) -> None:
        if not self.path.exists():
            return

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Could not read battle history at {self.path}: {error}"
            ) from error
        if not isinstance(data, dict) or data.get("version") != self._VERSION:
            raise ValueError(f"Unsupported battle history format in {self.path}.")

        observations = data.get("observations")
        if not isinstance(observations, list):
            raise ValueError(f"Invalid battle history observations in {self.path}.")
        for observation in observations:
            if not isinstance(observation, dict):
                raise ValueError(f"Invalid battle history entry in {self.path}.")
            player = observation.get("player")
            opponent = observation.get("opponent")
            move = observation.get("move")
            count = observation.get("count")
            total_damage = observation.get("total_damage")
            if (
                not isinstance(player, str)
                or not isinstance(opponent, str)
                or not isinstance(move, str)
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 1
                or not isinstance(total_damage, (int, float))
                or isinstance(total_damage, bool)
                or not 0 < total_damage <= count * 100
            ):
                raise ValueError(f"Invalid battle history entry in {self.path}.")
            key = (
                _normalize_name(player),
                _normalize_name(opponent),
                _normalize_name(move),
            )
            if not all(key):
                raise ValueError(f"Invalid battle history entry in {self.path}.")
            self._observations[key] = (move, count, float(total_damage))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self._VERSION,
            "observations": [
                {
                    "player": player,
                    "opponent": opponent,
                    "move": display_name,
                    "count": count,
                    "total_damage": total_damage,
                }
                for (player, opponent, _), (
                    display_name,
                    count,
                    total_damage,
                ) in sorted(self._observations.items())
            ],
        }
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )


def normalize_move_name(move_name: str) -> str:
    return _normalize_name(move_name)


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()
