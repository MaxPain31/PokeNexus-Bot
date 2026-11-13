import re
import unicodedata


def parse_health_text(text: str) -> int | None:
    """Read HP evidence, rejecting unrelated digits and impossible values."""
    normalized = unicodedata.normalize("NFKC", text).strip(" \t\r\n[]()|")
    hp_prefix = re.match(r"HP\b[\s:|]*", normalized, flags=re.IGNORECASE)
    if hp_prefix:
        normalized = normalized[hp_prefix.end():].strip()
    normalized = re.sub(
        r"(?<=\d)[Oo](?=\d|\s*[%/|])|^[Oo](?=\s*[%/|])", "0", normalized,
    )
    percent = re.fullmatch(r"(\d{1,3})\s*%", normalized)
    if percent:
        value = int(percent.group(1))
        return value if value <= 100 else None

    fraction = re.fullmatch(r"(\d{1,4})\s*[/|]\s*(\d{1,4})", normalized)
    if fraction:
        current, maximum = map(int, fraction.groups())
        if maximum > 0 and current <= maximum:
            return round(current / maximum * 100)
        return None

    # A map sprite can confidently resemble a lone '1' or '2'. A number needs
    # a percent sign, a maximum, or an explicit HP label to establish health.
    if hp_prefix and re.fullmatch(r"\d{1,3}", normalized):
        value = int(normalized)
        return value if value <= 100 else None
    return None
