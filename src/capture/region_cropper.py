import numpy as np
from PySide6.QtCore import QRect

from models.region_config import SavedRegion


class RegionCropper:
    """Crop a saved screen-space region from a captured game-window image."""

    def crop(
        self,
        screenshot: np.ndarray,
        region: SavedRegion,
        game_geometry: QRect,
    ) -> np.ndarray:
        if screenshot is None or screenshot.size == 0:
            raise ValueError("The captured screenshot is empty.")
        if screenshot.ndim < 2:
            raise ValueError("The captured screenshot has invalid dimensions.")
        if not game_geometry.isValid():
            raise ValueError("The game window bounds are invalid.")

        region_rect = QRect(region.x, region.y, region.width, region.height)
        if not game_geometry.contains(region_rect):
            raise ValueError(
                f"Region '{region.label}' is outside the current game window."
            )

        scale_x = screenshot.shape[1] / game_geometry.width()
        scale_y = screenshot.shape[0] / game_geometry.height()
        left = round((region_rect.left() - game_geometry.left()) * scale_x)
        top = round((region_rect.top() - game_geometry.top()) * scale_y)
        right = round((region_rect.right() - game_geometry.left() + 1) * scale_x)
        bottom = round((region_rect.bottom() - game_geometry.top() + 1) * scale_y)
        left = max(0, min(left, screenshot.shape[1]))
        right = max(0, min(right, screenshot.shape[1]))
        top = max(0, min(top, screenshot.shape[0]))
        bottom = max(0, min(bottom, screenshot.shape[0]))

        if right <= left or bottom <= top:
            raise ValueError(f"Region '{region.label}' has no pixels in the screenshot.")

        cropped = screenshot[top:bottom, left:right]
        if cropped.size == 0:
            raise ValueError(f"Region '{region.label}' produced an empty crop.")
        return cropped.copy()
