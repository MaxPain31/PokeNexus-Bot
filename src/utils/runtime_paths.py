import os
import shutil
import sys
from pathlib import Path


def tesseract_installation(root: Path | None = None) -> tuple[Path, Path]:
    """Find a Tesseract executable and its English language data."""
    app_root = root or application_root()
    configured_root = os.environ.get("TESSERACT_ROOT")
    if configured_root:
        candidates = [Path(configured_root)]
    else:
        candidates = [
            app_root / "tesseract",
            app_root / "dist" / "Game Bot" / "_internal" / "tesseract",
        ]
        if not getattr(sys, "frozen", False):
            program_files = os.environ.get("ProgramFiles")
            program_files_x86 = os.environ.get("ProgramFiles(x86)")
            local_app_data = os.environ.get("LOCALAPPDATA")
            candidates.extend(
                Path(path) / "Tesseract-OCR"
                for path in (program_files, program_files_x86)
                if path
            )
            if local_app_data:
                candidates.append(
                    Path(local_app_data) / "Programs" / "Tesseract-OCR"
                )
            path_executable = shutil.which("tesseract.exe") or shutil.which(
                "tesseract"
            )
            if path_executable:
                candidates.append(Path(path_executable).parent)

    for candidate in candidates:
        executable = candidate / "tesseract.exe"
        tessdata = candidate / "tessdata"
        if executable.is_file() and (tessdata / "eng.traineddata").is_file():
            return executable, tessdata

    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        "Tesseract executable and English language data were not found. "
        f"Searched: {searched}. Install Tesseract or set TESSERACT_ROOT to "
        "a Tesseract installation directory."
    )


def application_root() -> Path:
    """Return the source project root or the bundled application data directory."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def application_path(*parts: str) -> Path:
    return application_root().joinpath(*parts)


def user_data_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA",
                Path.home() / "AppData" / "Local",
            )
        )
        root = local_app_data / "Poke Nexus"
    else:
        root = application_root()
    return root.joinpath(*parts)
