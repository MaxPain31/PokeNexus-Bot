import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np

from capture.image_processor import ImageProcessor
from core.health_text import parse_health_text
from core.move_text import normalize_move_text, parse_move_text
from core.pokemon_name import (
    is_elite_wild_pokemon_name,
    is_nature_matched_wild_pokemon_name,
    is_shiny_wild_pokemon_name,
)
from core.special_pokemon_message import (
    has_special_pokemon_message_hint,
    special_pokemon_message_kind,
)
from models.ocr_result import OCRResult
from models.region_config import SavedRegion, canonical_region_role
from ocr.text_extractor import TextExtractor

LogCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class PreparedOCR:
    method: str
    page_segmentation_mode: int
    image: np.ndarray
    fingerprint: bytes
    colored_name_prefix: bool = False
    name_prefix_colors: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.method}, PSM {self.page_segmentation_mode}"


@dataclass(frozen=True)
class RegionOCRReading:
    prepared: PreparedOCR
    result: OCRResult
    variant_count: int = 1


class RegionOCR:
    """Shared region-aware recognition for live capture and the region tester."""

    MINIMUM_CHAT_LINE_CONFIDENCE = 60.0
    _READABLE_CONFIDENCE = 50.0
    _GOOD_CONFIDENCE = 85.0

    def __init__(
        self, image_processor: ImageProcessor, text_extractor: TextExtractor,
    ) -> None:
        self.image_processor = image_processor
        self.text_extractor = text_extractor

    @staticmethod
    def _variants(
        region: SavedRegion, *, team_button: bool = False,
    ) -> tuple[tuple[str, int], ...]:
        feature = region.feature
        role = region.label.casefold()
        if feature == "moves":
            return (("white_180", 11), ("white_200", 6), ("white_150", 11))
        if feature == "health":
            return (("white_150", 7), ("white_160", 7), ("light_otsu", 11))
        if feature == "turn":
            # At larger UI/DPI scales, gray box borders can survive Otsu and
            # 180. A stricter white fallback retains the actual Turn/digits.
            return (("white_otsu", 7), ("white_180", 7), ("white_200", 7))
        if feature == "chat-box":
            return (("color_otsu", 11), ("color_otsu", 6))
        if feature == "pokemon-team" and team_button:
            # The max-color channel also selects the gold button background.
            # White strokes and single-line segmentation isolate the name.
            return (("white_180", 7), ("white_200", 7), ("white_160", 7))
        if feature == "pokemon-name" and any(token in role for token in ("your", "ally", "player", "current")):
            # Bright blue scenery can connect to the active name's white
            # strokes. Wild names still use color-aware masks for [S]/[E].
            return (("white_180", 7), ("color_180", 7), ("color_150", 7))
        if feature in ("pokemon-name", "pokemon-team"):
            psm = (
                11 if feature == "pokemon-team" and region.height > region.width / 3
                else 7
            )
            return (("color_180", psm), ("color_150", psm), ("color_otsu", psm))
        if feature == "synchronize":
            return (("color_150", 10), ("color_180", 10), ("color_otsu", 7))
        if feature == "special-pokemon-message":
            if role == "close button":
                return (("white_180", 10), ("white_150", 7))
            return (("light_otsu", 11), ("color_150", 11), ("dark_otsu", 11))
        if feature == "bag" and role == "item name":
            return (("white_180", 7), ("white_150", 7), ("light_otsu", 11))
        return (("white_180", 11), ("white_150", 11), ("light_otsu", 11))

    def prepare(
        self, image: np.ndarray, region: SavedRegion, log: LogCallback | None = None,
    ) -> tuple[PreparedOCR, ...]:
        team_text = (
            ImageProcessor.team_button_text(image)
            if region.feature == "pokemon-team" else None
        )
        if team_text is not None:
            image = team_text
        colored_name_prefix = False
        name_prefix_colors: tuple[str, ...] = ()
        if (
            region.feature == "pokemon-name"
            and canonical_region_role(region) == "Wild Pokemon"
        ):
            image, name_prefix_colors = ImageProcessor.wild_name_text(image)
            colored_name_prefix = bool(name_prefix_colors)
        elif region.feature == "synchronize":
            image, colored_name_prefix = ImageProcessor.nature_match_text(image)
            name_prefix_colors = ("green",) if colored_name_prefix else ()
        processed_methods: dict[str, tuple[np.ndarray, bytes]] = {}
        prepared: list[PreparedOCR] = []
        last_error: Exception | None = None
        for method, psm in self._variants(region, team_button=team_text is not None):
            try:
                if method not in processed_methods:
                    options = {"single_glyph": True} if region.feature == "synchronize" else {}
                    processed = self.image_processor.process(
                        image, threshold_method=method, **options,
                    )
                    fingerprint = hashlib.blake2b(digest_size=16)
                    fingerprint.update(str(processed.shape).encode("ascii"))
                    fingerprint.update(processed.tobytes())
                    processed_methods[method] = (processed, fingerprint.digest())
                processed, fingerprint = processed_methods[method]
                prepared.append(PreparedOCR(
                    method, psm, processed, fingerprint, colored_name_prefix, name_prefix_colors,
                ))
            except Exception as error:
                last_error = error
                if log is not None:
                    log("ERROR", f"OCR preprocessing '{method}' failed: {error}")
        if not prepared:
            raise RuntimeError(
                f"OCR failed for every image-processing variant: {last_error}"
            ) from last_error
        return tuple(prepared)

    @staticmethod
    def fingerprint(prepared: Sequence[PreparedOCR]) -> bytes:
        # Include fallbacks too: a PP change can be invisible to one threshold
        # but visible to another. Color evidence affects name normalization
        # even when the resulting monochrome mask has not changed.
        fingerprint = hashlib.blake2b(digest_size=16)
        for candidate in prepared:
            fingerprint.update(candidate.label.encode("ascii"))
            fingerprint.update(candidate.fingerprint)
            fingerprint.update(bytes([candidate.colored_name_prefix]))
            fingerprint.update(repr(candidate.name_prefix_colors).encode("ascii"))
        return fingerprint.digest()

    def extract(
        self, prepared: Sequence[PreparedOCR], region: SavedRegion,
        log: LogCallback | None = None,
    ) -> RegionOCRReading:
        readings: list[RegionOCRReading] = []
        seen: set[tuple[int, bytes, bool, tuple[str, ...]]] = set()
        is_chat = region.feature == "chat-box"
        last_error: Exception | None = None
        variant_count = 0
        for variant_count, candidate in enumerate(prepared, 1):
            identity = (
                candidate.page_segmentation_mode, candidate.fingerprint,
                candidate.colored_name_prefix,
                candidate.name_prefix_colors,
            )
            if identity in seen:
                continue
            seen.add(identity)
            try:
                if not np.any(candidate.image < 255):
                    height, width = candidate.image.shape[:2]
                    result = OCRResult("", width, height)
                else:
                    result = self.text_extractor.extract(
                        candidate.image,
                        page_segmentation_mode=candidate.page_segmentation_mode,
                        preserve_lines=is_chat,
                        minimum_line_confidence=(
                            self.MINIMUM_CHAT_LINE_CONFIDENCE if is_chat else 0.0
                        ),
                        minimum_line_characters=2 if is_chat else 1,
                    )
                if region.feature == "moves":
                    result = replace(result, text=normalize_move_text(result.text))
                elif region.feature == "turn":
                    # Trim only panel-border pipes around a complete Turn N.
                    # Extra words, digits or other noise remain invalid anchors.
                    match = re.fullmatch(
                        r"[\s|]*(turn\s*\d+)[\s|]*", result.text, re.IGNORECASE,
                    )
                    if match is not None:
                        text = match.group(1)
                        result = replace(result, text=text, word_count=len(text.split()))
                elif region.feature == "synchronize":
                    if candidate.name_prefix_colors == ("green",) and re.fullmatch(
                        r"\s*(?:\[\s*S\s*\]|S)\s*", result.text, re.IGNORECASE,
                    ):
                        result = replace(result, text="[Nature Match]", word_count=2)
                    elif is_nature_matched_wild_pokemon_name(result.text):
                        # This token is generated only from green-plus-S
                        # evidence above, never accepted in any raw OCR form.
                        result = replace(result, text="", word_count=0)
                elif (
                    region.feature == "pokemon-name"
                    and canonical_region_role(region) == "Wild Pokemon"
                    and candidate.colored_name_prefix
                ):
                    text = self._normalize_name_prefix(result.text, candidate)
                    if text != result.text:
                        result = replace(result, text=text, word_count=len(text.split()))
            except Exception as error:
                last_error = error
                if log is not None:
                    log("ERROR", f"OCR variant '{candidate.label}' failed: {error}")
                continue
            readings.append(RegionOCRReading(candidate, result))
            if log is not None:
                log(
                    "INFO", f"OCR variant '{candidate.label}': "
                    f"confidence={result.mean_confidence:.2f}%, words={result.word_count}.",
                )
            best = max(readings, key=lambda reading: self._rank(reading.result, region))
            # A confident species-only reading must not skip recovery of an
            # observed colored badge. Still require actual S/E glyph OCR.
            missing_badge = (
                candidate.colored_name_prefix
                and region.feature in ("pokemon-name", "pokemon-team")
                and self._quality(best.result, region) < 1 + max(1, len(candidate.name_prefix_colors))
            )
            if not missing_badge and self._sufficient(best.result, region):
                break
        if not readings:
            raise RuntimeError(
                f"OCR failed for every recognition variant: {last_error}"
            ) from last_error
        best = max(readings, key=lambda reading: self._rank(reading.result, region))
        return replace(best, variant_count=variant_count)

    @staticmethod
    def _normalize_name_prefix(text: str, candidate: PreparedOCR) -> str:
        """Keep green S evidence separate from explicit Shiny/Elite markers."""
        position = 0
        color_index = 0
        prefixes: list[str] = []
        changed = False
        while match := re.match(
            r"\s*(?:\[\s*(?P<bracket>[SE])\s*\]|(?P<bare>[SE])(?=\s|$)|"
            r"(?P<word>shiny|elite)\b)\s*", text[position:], re.IGNORECASE,
        ):
            position += match.end()
            if match.group("word"):
                prefixes.append(match.group("word"))
                continue
            marker = (match.group("bracket") or match.group("bare")).upper()
            color = (
                candidate.name_prefix_colors[color_index]
                if color_index < len(candidate.name_prefix_colors) else None
            )
            color_index += 1
            if marker == "S" and color == "green":
                prefixes.append("[Nature Match]")
                changed = True
            elif match.group("bare") and (
                color == "other" or (
                    candidate.colored_name_prefix and not candidate.name_prefix_colors
                )
            ) and text[position:].strip():
                # A bare non-green badge still needs its S/E glyph. White
                # initials and unrecognized colored shapes create no trait.
                prefixes.append(f"[{marker}]")
                changed = True
            else:
                prefixes.append(match.group().strip())
        return " ".join((*prefixes, text[position:].lstrip())).strip() if changed else text

    @staticmethod
    def _is_dialog(region: SavedRegion) -> bool:
        return (
            region.feature == "special-pokemon-message"
            and region.label.casefold() == "message box"
        )

    @staticmethod
    def _health_quality(text: str) -> int:
        if parse_health_text(text) is not None:
            return 3
        text = text.strip()
        return 1 if re.fullmatch(r"[0-9]{1,3}", text) and int(text) <= 100 else 0

    def _quality(self, result: OCRResult, region: SavedRegion) -> int:
        text = result.text
        if not text.strip():
            return 0
        if self._is_dialog(region):
            if special_pokemon_message_kind(text) is not None:
                return 3
            return 2 if has_special_pokemon_message_hint(text) else 0
        if region.feature == "moves":
            parsed = parse_move_text(text)
            if parsed is None:
                return 0
            _name, effectiveness, pp = parsed
            return 2 + int(effectiveness != "-") if pp is not None else 1
        if region.feature == "health":
            return self._health_quality(text)
        if region.feature == "turn":
            return 3 if re.fullmatch(r"turn\s*\d+", text.strip(), re.IGNORECASE) else 0
        if region.feature == "synchronize":
            return 3 if is_nature_matched_wild_pokemon_name(text) else 0
        if region.feature in ("pokemon-name", "pokemon-team"):
            return (
                1 + int(is_shiny_wild_pokemon_name(text))
                + int(is_elite_wild_pokemon_name(text))
                + int(is_nature_matched_wild_pokemon_name(text))
            )
        if region.feature == "chat-box":
            # A very confident 'Turn 1' alone must not beat a readable full log.
            return sum(
                any(character.isalnum() for character in word)
                for word in text.split()
            )
        return int(any(character.isalnum() for character in text))

    def _rank(self, result: OCRResult, region: SavedRegion) -> tuple[float, ...]:
        quality = self._quality(result, region)
        readable = float(result.mean_confidence >= self._READABLE_CONFIDENCE)
        if self._is_dialog(region):
            # Retain uncertain popup evidence instead of replacing it with a
            # more confident background word and incorrectly unblocking input.
            return quality, readable, result.mean_confidence, result.word_count
        return readable, quality, result.mean_confidence, result.word_count

    def _sufficient(self, result: OCRResult, region: SavedRegion) -> bool:
        if region.feature == "chat-box":
            return False
        if self._is_dialog(region):
            # Scenery in the saved message box is common. Only retry dialog-like
            # wording; do not search unrelated background for popup keywords.
            quality = self._quality(result, region)
            return quality == 0 or (
                quality == 3 and result.mean_confidence >= self._GOOD_CONFIDENCE
            )
        if result.mean_confidence < self._GOOD_CONFIDENCE:
            return False
        if region.feature in ("moves", "health", "turn", "synchronize"):
            return self._quality(result, region) >= 3
        return bool(result.text.strip())
