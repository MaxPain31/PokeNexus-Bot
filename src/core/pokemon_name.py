import re
import unicodedata


_TRAIT_MARKER = r"\[\s*(S|E|Nature\s+Match)\s*\]"


def _name_details(name: object) -> tuple[str, bool, bool, bool]:
    """Read explicit traits from a wild name, keeping its species separate."""
    if not isinstance(name, str):
        return "", False, False, False
    text = unicodedata.normalize("NFKC", name)
    text = re.sub(
        r"^\s*(?:(?:a\s+)?wild(?:\s+pokemon)?\s*[:\-]?\s*)?",
        "", text, flags=re.IGNORECASE,
    )
    markers = re.findall(_TRAIT_MARKER, text, re.IGNORECASE)
    shiny = any(marker.upper() == "S" for marker in markers)
    elite = any(marker.upper() == "E" for marker in markers)
    nature_match = any(re.fullmatch(r"nature\s+match", marker, re.IGNORECASE) for marker in markers)
    text = re.sub(_TRAIT_MARKER, "", text, flags=re.IGNORECASE).strip()
    while match := re.match(r"^(shiny|elite)\b\s*", text, re.IGNORECASE):
        shiny = shiny or match.group(1).casefold() == "shiny"
        elite = elite or match.group(1).casefold() == "elite"
        text = text[match.end():].lstrip()
    return text, shiny, elite, nature_match


def is_shiny_wild_pokemon_name(name: str | None) -> bool:
    return _name_details(name)[1]


def is_elite_wild_pokemon_name(name: str | None) -> bool:
    return _name_details(name)[2]


def is_nature_matched_wild_pokemon_name(name: str | None) -> bool:
    return _name_details(name)[3]


def normalise_wild_pokemon_name(name: object) -> str:
    return _name_details(name)[0]


def pokemon_species_name(name: object) -> str:
    text = normalise_wild_pokemon_name(name)
    text = re.split(r"\s*[|,;\n]\s*", text, maxsplit=1)[0]
    text = re.sub(
        r"\s+(?:appeared|level\s+\d+|lv\.?\s*\d+)\b.*$",
        "", text, flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip()
