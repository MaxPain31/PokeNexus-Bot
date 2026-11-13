import re
import unicodedata


def normalize_move_text(text: str) -> str:
    """Normalize unambiguous OCR typography without guessing PP digits."""
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    normalized = re.sub(r"(?<=\d)\s*[xX×](?!\w)", "x", normalized)
    normalized = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", normalized)
    if re.search(r"\b\d+/\d+\b", normalized):
        # A standalone Ix/lx beside PP is the small 1x effectiveness label.
        # Do not replace letters within move names, or invent a missing slash.
        normalized = re.sub(r"(?<!\w)[Il|]\s*[xX×](?!\w)", "1x", normalized)
    return normalized


def parse_move_text(text: str) -> tuple[str, str, str | None] | None:
    normalized = normalize_move_text(text)
    if not normalized:
        return None

    effectiveness_match = re.search(
        r"(?<![\w.])(\d+(?:\.\d+)?)\s*x(?!\w)", normalized,
    )
    pp_match = re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", normalized)
    pp = None
    if pp_match:
        current, maximum = (int(value) for value in pp_match.groups())
        if 0 <= current <= maximum and 1 <= maximum <= 99:
            pp = f"{current}/{maximum}"

    move_name = normalized
    for start, end in sorted(
        (match.span() for match in (effectiveness_match, pp_match) if match is not None),
        reverse=True,
    ):
        move_name = move_name[:start] + " " + move_name[end:]
    move_name = re.sub(r"\s+", " ", move_name).strip(" ,;:-|")
    if not move_name or not any(character.isalpha() for character in move_name):
        return None

    effectiveness = f"{effectiveness_match.group(1)}x" if effectiveness_match else "-"
    return move_name, effectiveness, pp
