from dataclasses import dataclass
import re


REGION_FEATURES = (
    ("chat-box", "Chat Box"),
    ("pokemon-name", "Pokemon Name"),
    ("pokemon-team", "Pokemon Team"),
    ("synchronize", "Synchronize"),
    ("fight", "Fight"),
    ("bag", "Bag"),
    ("swap", "Swap"),
    ("run", "Run"),
    ("turn", "Turn"),
    ("moves", "Moves"),
    ("health", "Health"),
    ("fishing", "Fishing"),
    ("special-pokemon-message", "Special Pokemon Message"),
)

REGION_FEATURE_LABELS = dict(REGION_FEATURES)

REGION_ROLE_LABELS = {
    "chat-box": ("Battle Chat",),
    "pokemon-name": ("Your Pokemon", "Wild Pokemon"),
    "pokemon-team": tuple(f"Pokemon Team Slot {slot}" for slot in range(1, 7)),
    "synchronize": ("Nature Match",),
    "fight": ("Fight Button",),
    "bag": (
        "Bag Button",
        "Previous Button",
        "Next Button",
        "Item Name",
        "Use Item Button",
    ),
    "swap": ("Pokemon/Swap Button",),
    "run": ("Run Button",),
    "turn": ("Turn Button",),
    "moves": tuple(f"Move Slot {slot}" for slot in range(1, 5)),
    "health": ("Ally Health", "Enemy Health"),
    "fishing": ("Fishing Bar",),
    "special-pokemon-message": ("Message Box", "Close Button"),
}

AUTO_BATTLE_FEATURES = tuple(
    feature for feature, _label in REGION_FEATURES
    if feature not in ("synchronize", "fishing", "special-pokemon-message")
)


@dataclass(frozen=True)
class SavedRegion:
    feature: str
    label: str
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.feature not in REGION_FEATURE_LABELS:
            raise ValueError(f"Unknown region feature: {self.feature}")
        if not self.label.strip():
            raise ValueError("Region label cannot be empty.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Region dimensions must be positive.")


def canonical_region_role(region: SavedRegion) -> str | None:
    """Recognize current and legacy role labels without renaming saved regions."""
    label = region.label.casefold()
    roles = REGION_ROLE_LABELS[region.feature]
    exact_match = next((role for role in roles if role.casefold() == label), None)
    if exact_match is not None:
        return exact_match
    if len(roles) == 1:
        return roles[0]
    if region.feature == "health":
        if "enemy" in label or "opponent" in label:
            return "Enemy Health"
        if any(token in label for token in ("ally", "player", "your")):
            return "Ally Health"
        if re.search(r"(?:pokemon|health)\s*1\b", label):
            return "Ally Health"
        if re.search(r"(?:pokemon|health)\s*2\b", label):
            return "Enemy Health"
    elif region.feature == "pokemon-name":
        if "wild" in label:
            return "Wild Pokemon"
        if any(token in label for token in ("your", "current", "player", "ally")):
            return "Your Pokemon"
        if re.search(r"pokemon name\s*1\b", label):
            return "Your Pokemon"
        if re.search(r"pokemon name\s*2\b", label):
            return "Wild Pokemon"
    elif region.feature in ("pokemon-team", "moves"):
        pattern = (
            r"(?:pokemon team|team)(?: slot)?\s*(\d+)\b"
            if region.feature == "pokemon-team" else r"moves?(?: slot)?\s*(\d+)\b"
        )
        match = re.search(pattern, label)
        if match:
            prefix = "Pokemon Team Slot" if region.feature == "pokemon-team" else "Move Slot"
            role = f"{prefix} {int(match.group(1))}"
            return role if role in roles else None
    elif region.feature == "bag":
        aliases = {
            "bag": "Bag Button", "previous": "Previous Button", "next": "Next Button",
            "item": "Item Name", "use item": "Use Item Button",
        }
        return aliases.get(label)
    return None


def merge_missing_battle_regions(
    existing: tuple[SavedRegion, ...], detected: tuple[SavedRegion, ...],
) -> tuple[SavedRegion, ...]:
    """Fill missing battle roles, preserving all saved coordinates and extras."""
    merged = list(existing)
    used = {(region.feature, canonical_region_role(region)) for region in existing}
    for region in detected:
        identity = (region.feature, canonical_region_role(region))
        if region.feature in AUTO_BATTLE_FEATURES and identity[1] is not None and identity not in used:
            merged.append(region)
            used.add(identity)
    return tuple(merged)
