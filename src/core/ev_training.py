import csv
import re
from dataclasses import dataclass, field

from core.pokemon_name import pokemon_species_name
from utils.runtime_paths import application_path


EV_STATS = (
    ("hp", "HP", "HP"),
    ("attack", "Attack", "Attack"),
    ("defense", "Defense", "Defense"),
    ("sp_attack", "Sp. Atk", "Special Attack"),
    ("sp_defense", "Sp. Def", "Special Defense"),
    ("speed", "Speed", "Speed"),
)
EV_STAT_LIMIT = 252
EV_TOTAL_LIMIT = 510


def default_ev_values() -> dict[str, int]:
    return {key: 0 for key, _label, _category in EV_STATS}


def validate_ev_values(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(default_ev_values()):
        raise ValueError("EV values must contain all six stats.")
    for key, number in value.items():
        if type(number) is not int or not 0 <= number <= EV_STAT_LIMIT:
            raise ValueError(f"Invalid EV value for {key}: expected an integer from 0 to 252.")
    if sum(value.values()) > EV_TOTAL_LIMIT:
        raise ValueError("Total EVs cannot exceed 510.")
    return {key: value[key] for key, _label, _category in EV_STATS}


@dataclass(frozen=True)
class EVProgress:
    """The accumulated EVs and desired final allocation for one trained Pokemon."""

    current: dict[str, int] = field(default_factory=default_ev_values)
    targets: dict[str, int] = field(default_factory=default_ev_values)

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", validate_ev_values(self.current))
        object.__setattr__(self, "targets", validate_ev_values(self.targets))

    @property
    def remaining(self) -> dict[str, int]:
        return {key: max(0, self.targets[key] - value) for key, value in self.current.items()}

    @property
    def total(self) -> int:
        return sum(self.current.values())

    @property
    def complete(self) -> bool:
        return self.total >= EV_TOTAL_LIMIT or not any(self.remaining.values())

    def fits(self, yields: tuple[int, ...]) -> bool:
        """Accept the entire yield, including secondary stats, or run instead."""
        if not _valid_yield(yields) or self.complete or self.total + sum(yields) > EV_TOTAL_LIMIT:
            return False
        remaining = self.remaining
        return all(
            value <= remaining[key] and self.current[key] + value <= EV_STAT_LIMIT
            for (key, _label, _category), value in zip(EV_STATS, yields)
        )

    def after_defeat(self, yields: tuple[int, ...]) -> "EVProgress":
        """Record an actual defeat, even if targets were edited after attacking.

        Routing uses fits() before attacking. Accounting must not discard a real
        gain because a target was subsequently lowered or a manual battle occurred.
        The game's per-stat and total caps still apply.
        """
        if not _valid_yield(yields):
            raise ValueError("Invalid defeated Pokemon EV yield.")
        current = self.current.copy()
        budget = EV_TOTAL_LIMIT - self.total
        for (key, _label, _category), value in zip(EV_STATS, yields):
            gained = min(value, EV_STAT_LIMIT - current[key], budget)
            current[key] += gained
            budget -= gained
        return EVProgress(current, self.targets)


def _valid_yield(yields: tuple[int, ...]) -> bool:
    return (
        len(yields) == len(EV_STATS)
        and all(type(value) is int and 0 <= value <= EV_STAT_LIMIT for value in yields)
        and 0 < sum(yields) <= EV_TOTAL_LIMIT
    )


@dataclass(frozen=True)
class EVTrainingEntry:
    """One location/category record and its six per-defeat EV yields."""

    pokemon: str
    training_category: str
    region: str
    location: str
    ev_yield: str
    yields: tuple[int, ...]


def ev_yield_for_pokemon(
    pokemon: object, entries: tuple[EVTrainingEntry, ...],
) -> tuple[int, ...] | None:
    """Location/category duplicates describe one yield, never multiple awards."""
    identity = pokemon_species_name(pokemon).casefold()
    yields = {entry.yields for entry in entries if entry.pokemon.casefold() == identity}
    # Conflicting catalog records are not enough evidence to intentionally battle.
    return next(iter(yields)) if len(yields) == 1 else None


def load_ev_training_data() -> tuple[EVTrainingEntry, ...]:
    """Preserve location records; a species can belong to several categories."""
    path = application_path("assets", "data", "ev_training_data.csv")
    entries: list[EVTrainingEntry] = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            pokemon = row["pokemon"].strip()
            category = row["training_category"].strip()
            text = row["ev_yield"].strip()
            yields = {key: 0 for key, _label, _category in EV_STATS}
            for part in text.split("+"):
                match = re.fullmatch(r"\s*(\d+)\s+(.+?)\s*", part)
                key = next((
                    key for key, _label, stat in EV_STATS
                    if match and stat.casefold() == match.group(2).casefold()
                ), None)
                if key is None or not 1 <= int(match.group(1)) <= EV_STAT_LIMIT:
                    raise ValueError(f"Invalid EV yield in {path}: {text}.")
                yields[key] += int(match.group(1))
                if yields[key] > EV_STAT_LIMIT:
                    raise ValueError(f"Invalid EV yield in {path}: {text}.")
            if not _valid_yield(tuple(yields.values())):
                raise ValueError(f"Invalid EV yield in {path}: {text}.")
            if not pokemon or not category:
                raise ValueError(f"Missing Pokemon or training category in {path}.")
            entries.append(EVTrainingEntry(
                pokemon, category, row["region"].strip(), row["location"].strip(),
                text, tuple(yields.values()),
            ))
    if not entries:
        raise ValueError(f"No EV training Pokemon found in {path}.")
    return tuple(entries)


_WILD_ANNOUNCEMENT = re.compile(r"\ba\s+wild\s+(.+?)\s+appeared\b", re.IGNORECASE)
_OPPONENT_FAINTED = re.compile(
    r"\b(?:(?:the\s+)?(?:opposing|enemy|wild)|(?:the\s+)?opponent(?:['’]s)?)\s+"
    r"(.+?)\s+(?:has\s+)?fainted\b", re.IGNORECASE,
)
_CAUGHT_OR_ESCAPED = re.compile(
    r"\b(?:congratulations,\s*you\s+caught\s+the\s+opposing|"
    r"you\s+(?:successfully\s+)?(?:caught|captured)\b|"
    r"got\s+away\s+safely|(?:you|wild\s+.+?)\s+(?:fled|escaped|ran\s+away)\b)",
    re.IGNORECASE,
)


def latest_encounter_chat(text: object) -> str:
    if not isinstance(text, str):
        return ""
    announcements = list(_WILD_ANNOUNCEMENT.finditer(text))
    return text[announcements[-1].end():] if announcements else text


def chat_confirms_opponent_defeat(text: object) -> bool:
    return _OPPONENT_FAINTED.search(latest_encounter_chat(text)) is not None


class EVDefeatTracker:
    """Award once per observed encounter, never for a disappearing battle alone."""

    _BATTLE_END_POLLS = 3

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last_frame_at = 0.0
        self._clear_encounter()

    def _clear_encounter(self) -> None:
        self._pokemon = ""
        self._player = ""
        self._positive_hp_seen = False
        self._player_alive_seen = False
        self._player_fainted = False
        self._exit_attempted = False
        self._finished = False
        self._zero_seen = False
        self._zero_polls = 0
        self._non_battle_polls = 0
        self._faint_count = 0
        self._terminal_chat = ""

    @property
    def awaiting_next_encounter(self) -> bool:
        return self._finished or self._zero_seen

    def record_action(self, feature: str, role: str) -> None:
        if feature == "run" or (feature == "bag" and role.casefold() == "use item button"):
            self._exit_attempted = True
        elif feature == "moves":
            self._exit_attempted = False

    def pause(self) -> None:
        self._zero_polls = self._non_battle_polls = 0

    @staticmethod
    def _faints_for(chat: str, pokemon: str) -> int:
        return sum(
            pokemon_species_name(match.group(1)).casefold() == pokemon.casefold()
            for match in _OPPONENT_FAINTED.finditer(chat)
        )

    @staticmethod
    def _new_announcement(previous: str, current: str, pokemon: str) -> bool:
        previous = "\n".join(line.strip() for line in previous.splitlines() if line.strip())
        current = "\n".join(line.strip() for line in current.splitlines() if line.strip())
        if current == previous or current in previous:
            return False
        if previous and previous in current:
            added = current[current.rfind(previous) + len(previous):]
        else:
            old_lines, new_lines = previous.splitlines(), current.splitlines()
            added = current
            for size in range(min(len(old_lines), len(new_lines)), 0, -1):
                if old_lines[-size:] == new_lines[:size]:
                    added = "\n".join(new_lines[size:])
                    break
        announcements = list(_WILD_ANNOUNCEMENT.finditer(added))
        return bool(announcements) and pokemon_species_name(announcements[-1].group(1)).casefold() == pokemon.casefold()

    def observe(
        self, state: dict[str, object], *, enabled: bool, frame_at: float,
    ) -> str | None:
        if not enabled:
            self.reset()
            return None
        if frame_at <= self._last_frame_at:
            return None
        self._last_frame_at = frame_at
        if (
            state.get("clickStateReliable") is False
            or state.get("showingSpecialPokemonMessage", False) is not False
        ):
            self.pause()
            return None

        battle = state.get("onBattleMode")
        pokemon = pokemon_species_name(state.get("wildPokemonName"))
        if not any(character.isalpha() for character in pokemon):
            pokemon = ""
        snapshot = state.get("chatBoxMessage")
        snapshot = snapshot if isinstance(snapshot, str) else ""
        chat = latest_encounter_chat(snapshot)
        enemy_hp = state.get("enemyPokemonHealth")
        positive_hp = type(enemy_hp) is int and enemy_hp > 0
        zero_hp = type(enemy_hp) is int and enemy_hp == 0
        exited = _CAUGHT_OR_ESCAPED.search(chat) is not None
        faints = self._faints_for(chat, pokemon or self._pokemon)

        # A new announcement plus a living opponent can follow a completed
        # battle without three empty frames (the chat may stay on screen).
        if self._finished and battle is True and positive_hp and pokemon and not faints and not exited:
            if self._new_announcement(self._terminal_chat, snapshot, pokemon):
                self._clear_encounter()

        if not self._pokemon:
            if battle is not True or not pokemon:
                return None
            self._pokemon = pokemon
            self._player = pokemon_species_name(state.get("yourPokemonName"))
            # Already-visible faint text is a baseline, not a new defeat. This
            # also prevents recounting after capture or the application restarts.
            self._faint_count = faints
            self._positive_hp_seen = positive_hp
            self._player_alive_seen = type(state.get("yourPokemonHealth")) is int and state["yourPokemonHealth"] > 0
            self._finished = exited or (bool(faints) and not positive_hp)
            self._terminal_chat = snapshot if self._finished else ""
            self._zero_seen = zero_hp
            return None

        self._non_battle_polls = self._non_battle_polls + 1 if battle is False else 0
        if pokemon and pokemon.casefold() != self._pokemon.casefold():
            # A different/unreliable identity cannot award the previous species.
            self._zero_polls = 0
            self._zero_seen = False
            if self._non_battle_polls >= self._BATTLE_END_POLLS:
                self._clear_encounter()
            return None
        player_hp = state.get("yourPokemonHealth")
        player = pokemon_species_name(state.get("yourPokemonName"))
        if type(player_hp) is int and player_hp > 0:
            self._player_alive_seen = True
        if (type(player_hp) is int and player_hp == 0) or (
            self._player and player and player.casefold() != self._player.casefold()
        ):
            self._player_fainted = True
        own_chat = _OPPONENT_FAINTED.sub("", chat)
        if re.search(r"\byour\s+pok[eé]mon\b.{0,30}\bfainted\b", own_chat, re.IGNORECASE) or (
            self._player and re.search(
                rf"(?<!\w){re.escape(self._player)}\s+(?:has\s+)?fainted\b", own_chat, re.IGNORECASE,
            )
        ):
            self._player_fainted = True
        if exited and not self._finished:
            self._finished = True
            self._terminal_chat = snapshot
        if state.get("clickedRunButton") is True or state.get("clickedBagButton") is True:
            self._exit_attempted = True

        faints = self._faints_for(chat, self._pokemon)
        new_faint = faints > self._faint_count
        self._faint_count = max(self._faint_count, faints)
        if positive_hp:
            self._positive_hp_seen = True
            self._zero_seen = False
        elif zero_hp:
            self._zero_seen = True
        self._zero_polls = self._zero_polls + 1 if zero_hp else 0
        hp_confirms_defeat = (
            self._positive_hp_seen and not self._exit_attempted
            and (
                self._zero_polls >= 2
                or (self._zero_seen and self._non_battle_polls >= self._BATTLE_END_POLLS)
            )
        )
        defeated = None
        if (
            not self._finished and not self._player_fainted
            and (new_faint or (hp_confirms_defeat and self._player_alive_seen))
        ):
            self._finished = True
            self._terminal_chat = snapshot
            defeated = self._pokemon
        if self._non_battle_polls >= self._BATTLE_END_POLLS:
            self._clear_encounter()
        return defeated
