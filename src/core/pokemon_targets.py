import re
import unicodedata
from dataclasses import dataclass

from core.pokemon_name import pokemon_species_name


@dataclass(frozen=True)
class PokemonTarget:
    name: str
    nature_match_only: bool = False

    @property
    def text(self) -> str:
        return f"[Nature Match] {self.name}" if self.nature_match_only else self.name


def parse_pokemon_targets(value: str) -> tuple[PokemonTarget, ...]:
    """Keep each species' optional green-S requirement in its saved selection."""
    targets: dict[str, PokemonTarget] = {}
    for entry in value.split(","):
        text = unicodedata.normalize("NFKC", entry).strip()
        if not text:
            continue
        marker = re.match(r"^\[\s*Nature\s+Match\s*\]\s*", text, re.IGNORECASE)
        name = text[marker.end():].strip() if marker else text
        if not name:
            continue
        target = PokemonTarget(name, nature_match_only=marker is not None)
        previous = targets.get(name.casefold())
        # An ordinary target already includes synchronized encounters. A
        # redundant S-only entry must not narrow an existing ordinary choice.
        if previous is None or not target.nature_match_only:
            targets[name.casefold()] = target
    return tuple(targets.values())


def match_pokemon_target(
    wild_name: object,
    selected_names: str,
    *,
    nature_match: object = False,
    exact_species: bool = False,
) -> PokemonTarget | None:
    species = pokemon_species_name(wild_name)
    if not species:
        return None
    for target in parse_pokemon_targets(selected_names):
        if target.nature_match_only and nature_match is not True:
            continue
        if exact_species or target.nature_match_only:
            matched = species.casefold() == target.name.casefold()
        else:
            # Preserve ordinary Desired Pokemon's existing word-boundary
            # matching, but never accept a different form for an S-only target.
            matched = re.search(
                rf"(?<!\w){re.escape(target.name)}(?!\w)", species, re.IGNORECASE,
            ) is not None
        if matched:
            return target
    return None
