from dataclasses import dataclass


REGION_FEATURES = (
    ("chat-box", "Chat Box"),
    ("pokemon-name", "Pokemon Name"),
    ("pokemon-team", "Pokemon Team"),
    ("fight", "Fight"),
    ("bag", "Bag"),
    ("swap", "Swap"),
    ("run", "Run"),
    ("turn", "Turn"),
    ("moves", "Moves"),
    ("health", "Health"),
    ("fishing", "Fishing"),
)

REGION_FEATURE_LABELS = dict(REGION_FEATURES)

REGION_ROLE_LABELS = {
    "chat-box": ("Battle Chat",),
    "pokemon-name": ("Your Pokemon", "Wild Pokemon"),
    "pokemon-team": tuple(f"Pokemon Team Slot {slot}" for slot in range(1, 7)),
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
}


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
