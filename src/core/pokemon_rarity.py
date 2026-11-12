import csv

from utils.runtime_paths import application_path

RARITIES = (
    ("c", "Common", "#22C55E"),
    ("uc", "Uncommon", "#A3E635"),
    ("r", "Rare", "#3B82F6"),
    ("vr", "Very Rare", "#D946EF"),
    ("er", "Extremely Rare", "#F97316"),
    ("l", "Legendary", "#EF4444"),
)

DEFAULT_RARITY_CODE = "c"


def load_pokemon_rarity_data() -> tuple[list[str], dict[str, str]]:
    data_path = application_path("assets", "data", "pokemon_data.csv")
    with data_path.open(encoding="utf-8-sig", newline="") as pokemon_file:
        rows = [
            (row["name"].strip(), row.get("rarity", "").strip().casefold())
            for row in csv.DictReader(pokemon_file)
            if row.get("name", "").strip()
        ]
    names = [name for name, _ in rows]
    if not names:
        raise ValueError(f"No Pokemon names found in {data_path}.")
    return names, {name.casefold(): rarity for name, rarity in rows}
