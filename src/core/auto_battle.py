import re
from collections.abc import Mapping

from core.battle_history import normalize_move_name
from core.pokemon_name import pokemon_species_name
from core.pokemon_targets import parse_pokemon_targets

AUTO_BATTLE_MODES = ("Default", "Specific Only", "EV Training Only")


def matches_battle_target(
    wild_name: object,
    selected_names: str,
    *,
    elite_only: bool = False,
    is_elite: object = None,
) -> bool | None:
    """Exact species/form targeting; unreadable evidence is not a non-match."""
    selected = {target.name.casefold() for target in parse_pokemon_targets(selected_names)}
    if not selected:
        return False
    species = pokemon_species_name(wild_name).casefold()
    if not species or not any(character.isalpha() for character in species):
        return None
    if species not in selected:
        return False
    if elite_only:
        return is_elite if type(is_elite) is bool else None
    return True


_MOVE_ENTRY = re.compile(
    r"Move Slot ([1-4]):\s*(.*?)\s+\((.*?)\)",
    flags=re.IGNORECASE,
)
_EFFECTIVENESS = re.compile(r"(\d+(?:\.\d+)?)\s*x", flags=re.IGNORECASE)
_PP = re.compile(r"(\d+)\s*/\s*(\d+)")


def has_complete_move_set(moves_text: str) -> bool:
    """Check that OCR produced move and PP data for every configured slot."""
    matches = list(_MOVE_ENTRY.finditer(moves_text))
    slots = {int(match.group(1)) for match in matches}
    return slots == {1, 2, 3, 4} and all(
        _PP.search(match.group(3)) is not None for match in matches
    )


def choose_move_slot(
    moves_text: str,
    historical_damage: Mapping[str, float] | None = None,
) -> int | None:
    """Choose by effectiveness, using battle history to break ties."""
    damaging_moves: list[tuple[float, float, int]] = []
    status_moves: list[int] = []
    historical_damage = historical_damage or {}

    for match in _MOVE_ENTRY.finditer(moves_text):
        slot = int(match.group(1))
        move_name = re.sub(r"\s+", " ", match.group(2)).strip().casefold()
        details = match.group(3)
        pp_match = _PP.search(details)
        if pp_match is None or int(pp_match.group(1)) <= 0:
            continue

        effectiveness_match = _EFFECTIVENESS.search(details)
        if effectiveness_match is None:
            status_moves.append(slot)
            continue

        effectiveness = float(effectiveness_match.group(1))
        if effectiveness > 0:
            damaging_moves.append(
                (
                    effectiveness,
                    historical_damage.get(normalize_move_name(move_name), 0.0),
                    slot,
                )
            )

    if damaging_moves:
        return max(
            damaging_moves,
            key=lambda move: (move[0], move[1], -move[2]),
        )[2]
    return status_moves[0] if status_moves else None


def move_name_for_slot(moves_text: str, slot: int) -> str | None:
    for match in _MOVE_ENTRY.finditer(moves_text):
        if int(match.group(1)) == slot:
            move_name = match.group(2).strip()
            return move_name or None
    return None
