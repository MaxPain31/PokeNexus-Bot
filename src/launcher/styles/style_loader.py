import re
from pathlib import Path

from utils.runtime_paths import application_path

STYLES_DIR = Path(__file__).resolve().parent


def background_alpha(alpha: int, transparency: int) -> int:
    """Fade a theme background without changing foreground colors."""
    if not 0 <= transparency <= 100:
        raise ValueError("Overlay background transparency must be between 0 and 100 percent.")
    return round(alpha * (100 - transparency) / 100)


def load_stylesheet(filename: str, *, background_transparency: int = 0) -> str:
    """Load a QSS stylesheet from the styles directory."""
    stylesheet_path = STYLES_DIR / filename

    if not stylesheet_path.is_file():
        raise FileNotFoundError(f"Stylesheet not found: {stylesheet_path}")

    stylesheet = stylesheet_path.read_text(encoding="utf-8").replace(
        "{{ASSETS_DIR}}", application_path("assets").as_posix()
    )
    return re.sub(
        r"\{\{BACKGROUND_ALPHA:(\d+)\}\}",
        lambda match: str(background_alpha(int(match[1]), background_transparency)),
        stylesheet,
    )
