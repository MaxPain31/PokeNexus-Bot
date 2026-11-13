import re
from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QRect

from capture.image_processor import ImageProcessor
from core.health_text import parse_health_text
from models.region_config import AUTO_BATTLE_FEATURES, REGION_ROLE_LABELS, SavedRegion
from ocr.region_ocr import RegionOCR
from ocr.text_extractor import TextExtractor


class BattlePanelNotFound(ValueError):
    """A complete, unambiguous battle panel could not be confirmed."""


@dataclass(frozen=True)
class BattleRegionPlacement:
    regions: tuple[SavedRegion, ...]
    panel: QRect


class BattleRegionDetector:
    """Locate the battle frame and validate it before proposing capture regions.

    Poke Nexus uses the same viewport and menu grid for Fight, Bag and Pokemon.
    Measurements are relative to its visible 678 x 422 frame at 1x, not to the
    desktop or game-window size. Hidden menus can therefore be placed without
    opening them or sending any input to the game.
    """

    _FRAME_WIDTH = 678
    _FRAME_HEIGHT = 422
    _MINIMUM_ANCHOR_CONFIDENCE = 70.0
    _BASE_REGIONS = (
        ("chat-box", "Battle Chat", (-322, -1, 309, 425)),
        ("pokemon-name", "Your Pokemon", (401, 231, 170, 22)),
        # Include the encounter-marker gutter without extending into Lv text.
        ("pokemon-name", "Wild Pokemon", (7, 13, 180, 22)),
        ("fight", "Fight Button", (15, 311, 133, 45)),
        ("bag", "Bag Button", (153, 311, 133, 45)),
        ("bag", "Previous Button", (15, 311, 30, 95)),
        ("bag", "Next Button", (153, 311, 30, 95)),
        ("bag", "Item Name", (52, 380, 90, 25)),
        ("bag", "Use Item Button", (185, 311, 100, 95)),
        ("swap", "Pokemon/Swap Button", (15, 361, 133, 45)),
        ("run", "Run Button", (153, 361, 133, 45)),
        ("turn", "Turn Button", (587, 15, 75, 25)),
        ("health", "Ally Health", (505, 254, 35, 17)),
        ("health", "Enemy Health", (122, 35, 35, 17)),
    )

    def __init__(self, region_ocr: RegionOCR | None = None) -> None:
        # Resolve the engine only when placement is requested, not on the GUI
        # thread while constructing the overlay.
        self._region_ocr = region_ocr

    def detect(self, screenshot: np.ndarray, game_geometry: QRect) -> BattleRegionPlacement:
        if (
            screenshot is None or screenshot.size == 0 or screenshot.dtype != np.uint8
            or screenshot.ndim != 3 or screenshot.shape[2] not in (3, 4)
        ):
            raise ValueError("Battle placement requires a nonempty color game screenshot.")
        if not game_geometry.isValid():
            raise ValueError("The game window bounds are invalid.")

        bright = (screenshot[:, :, :3].min(axis=2) >= 200).astype(np.uint8)
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(bright, 8)
        candidates = []
        for x, y, width, height, area in stats[1:]:
            if width < 250 or height < 150:
                continue
            aspect = width / height
            if abs(aspect / (self._FRAME_WIDTH / self._FRAME_HEIGHT) - 1) > 0.08:
                continue
            if not 0.015 <= area / (width * height) <= 0.20:
                continue
            panel = QRect(int(x), int(y), int(width), int(height))
            if self._has_complete_frame(bright, panel):
                candidates.append(panel)

        placements = []
        pixel_bounds = QRect(0, 0, screenshot.shape[1], screenshot.shape[0])
        for panel in candidates:
            pixel_regions = self._pixel_regions(panel)
            if not all(pixel_bounds.contains(rect) for _feature, _role, rect in pixel_regions):
                continue
            if not self._battle_anchors_visible(screenshot, pixel_regions):
                continue
            regions = tuple(
                self._screen_region(feature, role, rect, screenshot, game_geometry)
                for feature, role, rect in pixel_regions
            )
            placements.append(BattleRegionPlacement(
                regions, self._screen_rect(panel, screenshot, game_geometry),
            ))

        if len(placements) > 1:
            raise BattlePanelNotFound(
                "More than one battle panel was detected. Show only one complete "
                "battle panel and try Auto Place again. Saved regions were not changed."
            )
        if not placements:
            raise BattlePanelNotFound(
                "No complete battle panel could be confirmed. Enter a battle and leave "
                "the battle chat, Turn, both HP values and menu visible, then choose "
                "Auto Place again. Saved regions were not changed."
            )
        return placements[0]

    @classmethod
    def _has_complete_frame(cls, bright: np.ndarray, panel: QRect) -> bool:
        mask = bright[panel.y():panel.y() + panel.height(), panel.x():panel.x() + panel.width()]
        band_x = max(2, round(8 * panel.width() / cls._FRAME_WIDTH))
        band_y = max(2, round(8 * panel.height() / cls._FRAME_HEIGHT))
        # Every side must be visible. Large bright sprites, title bars, HP
        # nameplates and partially obscured windows are not battle frames.
        coverage = (
            mask[:band_y].max(axis=0).mean(), mask[-band_y:].max(axis=0).mean(),
            mask[:, :band_x].max(axis=1).mean(), mask[:, -band_x:].max(axis=1).mean(),
        )
        return all(value >= 0.93 for value in coverage)

    @classmethod
    def _relative_rect(cls, panel: QRect, coordinates: tuple[int, int, int, int]) -> QRect:
        x, y, width, height = coordinates
        scale_x, scale_y = panel.width() / cls._FRAME_WIDTH, panel.height() / cls._FRAME_HEIGHT
        left, top = round(panel.x() + x * scale_x), round(panel.y() + y * scale_y)
        right = round(panel.x() + (x + width) * scale_x)
        bottom = round(panel.y() + (y + height) * scale_y)
        return QRect(left, top, max(1, right - left), max(1, bottom - top))

    @classmethod
    def _pixel_regions(cls, panel: QRect) -> tuple[tuple[str, str, QRect], ...]:
        regions = [(feature, role, cls._relative_rect(panel, rect)) for feature, role, rect in cls._BASE_REGIONS]
        for feature, slots, columns in (("pokemon-team", 6, 3), ("moves", 4, 2)):
            for slot in range(slots):
                rect = (15 + (slot % columns) * 138, 311 + (slot // columns) * 50, 133, 45)
                regions.append((feature, REGION_ROLE_LABELS[feature][slot], cls._relative_rect(panel, rect)))
        # Keep the same feature/role ordering as the region editor.
        return tuple(sorted(regions, key=lambda item: (
            AUTO_BATTLE_FEATURES.index(item[0]), REGION_ROLE_LABELS[item[0]].index(item[1]),
        )))

    def _battle_anchors_visible(
        self, screenshot: np.ndarray, regions: tuple[tuple[str, str, QRect], ...],
    ) -> bool:
        if self._region_ocr is None:
            self._region_ocr = RegionOCR(ImageProcessor(), TextExtractor())
        menu_buttons = 0
        for feature, role, rect in regions:
            if feature not in ("health", "turn", "fight", "bag", "swap", "run"):
                continue
            image = screenshot[rect.y():rect.y() + rect.height(), rect.x():rect.x() + rect.width()]
            if feature in ("fight", "swap", "run") or (feature == "bag" and role == "Bag Button"):
                blue, green, red = np.moveaxis(image[:, :, :3].astype(np.int16), 2, 0)
                gold = (red > blue + 55) & (green > blue + 30) & (red > green + 10)
                menu_buttons += int(float(gold.mean()) >= 0.20)
            elif feature in ("health", "turn"):
                region = SavedRegion(feature, role, 0, 0, rect.width(), rect.height())
                reading = self._region_ocr.extract(self._region_ocr.prepare(image, region), region).result
                if reading.mean_confidence < self._MINIMUM_ANCHOR_CONFIDENCE:
                    return False
                if feature == "health" and parse_health_text(reading.text) is None:
                    return False
                if feature == "turn" and not re.fullmatch(r"turn\s*\d+", reading.text.strip(), re.IGNORECASE):
                    return False
        return menu_buttons >= 2

    @staticmethod
    def _screen_rect(rect: QRect, screenshot: np.ndarray, game_geometry: QRect) -> QRect:
        scale_x = game_geometry.width() / screenshot.shape[1]
        scale_y = game_geometry.height() / screenshot.shape[0]
        left = game_geometry.x() + round(rect.x() * scale_x)
        top = game_geometry.y() + round(rect.y() * scale_y)
        right = game_geometry.x() + round((rect.x() + rect.width()) * scale_x)
        bottom = game_geometry.y() + round((rect.y() + rect.height()) * scale_y)
        return QRect(left, top, max(1, right - left), max(1, bottom - top))

    @classmethod
    def _screen_region(
        cls, feature: str, role: str, rect: QRect, screenshot: np.ndarray, game_geometry: QRect,
    ) -> SavedRegion:
        rect = cls._screen_rect(rect, screenshot, game_geometry)
        if not game_geometry.contains(rect):
            raise BattlePanelNotFound("A detected battle region is outside the game window. Try Auto Place again.")
        return SavedRegion(feature, role, rect.x(), rect.y(), rect.width(), rect.height())
