import cv2
import numpy as np


class ImageProcessor:
    """Isolate small game text without closing up digits or thinning strokes."""

    _TEXT_METHODS = {
        "white_150": ("white", 150),
        "white_160": ("white", 160),
        "white_180": ("white", 180),
        "white_200": ("white", 200),
        "white_otsu": ("white", None),
        "color_150": ("color", 150),
        "color_180": ("color", 180),
        "color_otsu": ("color", None),
        "light_otsu": ("gray", None),
        "dark_otsu": ("dark", None),
    }

    def process(
        self,
        image: np.ndarray,
        threshold_method: str = "otsu_inverted",
        *,
        single_glyph: bool = False,
    ) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("Input image is empty.")

        if image.dtype != np.uint8 or image.ndim not in (2, 3):
            raise ValueError("Input image must be an 8-bit grayscale or color image.")
        if image.ndim == 3 and image.shape[2] not in (3, 4):
            raise ValueError("Color images must have three or four channels.")

        dark_on_white = True
        if threshold_method in self._TEXT_METHODS:
            channel, threshold = self._TEXT_METHODS[threshold_method]
        elif threshold_method in ("binary_230", "binary_200", "binary_167"):
            channel, threshold = "gray", int(threshold_method.rsplit("_", 1)[1])
            dark_on_white = False
        elif threshold_method in ("otsu", "otsu_inverted"):
            channel, threshold = "gray", None
            dark_on_white = threshold_method == "otsu_inverted"
        else:
            raise ValueError(
                f"Unsupported threshold method: {threshold_method}"
            )

        if image.ndim == 2:
            signal = image
        elif channel == "white":
            # White strokes are bright in every channel. Yellow buttons, blue
            # water, and red HP bars are not, even when their luminance is high.
            signal = image[:, :, :3].min(axis=2)
        elif channel == "color":
            # Keep colored [S]/[E] markers and colored battle-chat words too.
            signal = image[:, :, :3].max(axis=2)
        else:
            signal = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
        if channel == "dark":
            signal = 255 - signal

        scale = self._text_scale(signal, threshold, single_glyph=single_glyph)
        enlarged = cv2.resize(
            signal, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC,
        )
        threshold_type = cv2.THRESH_BINARY
        if threshold is None:
            threshold_type |= cv2.THRESH_OTSU
        if int(signal.max()) - int(signal.min()) < 3:
            mask = np.zeros(enlarged.shape, dtype=np.uint8)
        else:
            mask = cv2.threshold(enlarged, threshold or 0, 255, threshold_type)[1]
        mask = self._remove_panel_edges(mask)

        # A quiet border lets Tesseract read PP and percentages right against
        # the saved crop's edge. Do not erode/close these small glyphs.
        background = 255 if dark_on_white else 0
        processed = 255 - mask if dark_on_white else mask
        return cv2.copyMakeBorder(
            processed, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=background,
        )

    @staticmethod
    def team_button_text(image: np.ndarray) -> np.ndarray | None:
        """Exclude the sprite gutter only on a full gold team-menu button.

        Name-only crops and the gray party nameplates must keep their full
        width, including any colored encounter markers. The battle team menu
        instead reserves the left of each gold button for a Pokemon sprite.
        """
        if image is None or image.ndim != 3 or image.shape[2] not in (3, 4):
            return None
        height, width = image.shape[:2]
        if height < 8 or width / height < 1.2:
            return None
        blue, green, red = np.moveaxis(image[:, :, :3].astype(np.int16), 2, 0)
        gold = (red > blue + 55) & (green > blue + 30) & (red > green + 10)
        if float(gold.mean()) < 0.45:
            return None
        # A text-only adjustment on gold also needs the white-text profile,
        # but must not lose its first letters. The full button has a long,
        # neutral highlight along its top edge; a name-only crop does not.
        colors = image[:, :, :3].astype(np.int16)
        neutral = (colors.max(axis=2) - colors.min(axis=2) < 35) & (colors.min(axis=2) >= 120)
        top_band = max(1, round(height * 0.08))
        full_button = (
            2.1 <= width / height <= 4.5
            and float(neutral[:top_band].mean(axis=1).max()) >= 0.70
        )
        if not full_button:
            return image
        # The icon gutter scales with the button, not with desktop DPI. Keep
        # the space before centered names; never guess or substitute a species.
        left = round(min(width * 0.26, height * 0.8))
        return image[:, left:]

    @staticmethod
    def wild_name_text(image: np.ndarray) -> tuple[np.ndarray, tuple[str, ...]]:
        """Keep white species text and small colored leading encounter glyphs.

        A max-color mask also selects bright trees and water behind the name.
        Use white strokes elsewhere, retaining colored components only in the
        name's leading glyph gutter. Keep the color identity in left-to-right
        order: a green S is a Nature match, not the Shiny [S] marker. Recognition
        must still confirm the glyph; color alone must never create a trait.
        """
        if (
            image is None or image.size == 0 or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] not in (3, 4)
        ):
            return image, ()
        white = image[:, :, :3].min(axis=2)
        color = image[:, :, :3].max(axis=2)
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            (white >= 180).astype(np.uint8), 8,
        )
        glyphs = [
            (x, y, w, h) for x, y, w, h, area in stats[1:]
            if 3 <= h <= min(64, image.shape[0]) and 1 <= w <= h * 2
            and area >= 3 and area < w * h * 0.95
        ]
        if len(glyphs) < 2:
            return image, ()
        glyph_height = float(np.median([h for _x, _y, _w, h in glyphs]))
        glyphs = [(x, y, w, h) for x, y, w, h in glyphs if h >= glyph_height * 0.65]
        first_x = min(x for x, _y, _w, _h in glyphs)
        top = min(y for _x, y, _w, _h in glyphs)
        bottom = max(y + h for _x, y, _w, h in glyphs)
        colored = (
            (color >= 150) & (white < 150)
            & (color.astype(np.int16) - white >= 50)
        ).astype(np.uint8)
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(colored, 8)
        keep = np.zeros(count, dtype=np.uint8)
        green = ImageProcessor._green_text_mask(image)
        hues = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)[:, :, 0]
        prefixes: list[tuple[int, int, str, float, int]] = []
        for label, (x, y, w, h, _area) in enumerate(stats[1:], 1):
            if (
                # Three badges, including their brackets/spaces, can span
                # almost ten lowercase glyph heights before the species.
                x + w <= first_x and x >= first_x - glyph_height * 10
                and 3 <= h <= glyph_height * 2 and w <= glyph_height * 1.5
                and y >= top - glyph_height * 0.5
                and y + h <= bottom + glyph_height * 0.5
            ):
                component = labels[y:y + h, x:x + w] == label
                is_green = float(green[y:y + h, x:x + w][component].mean()) >= 0.70
                hue = float(np.median(hues[y:y + h, x:x + w][component]))
                prefixes.append((int(x), int(x + w), "green" if is_green else "other", hue, label))
        # Keep only the adjacent leading run, not detached colored scenery
        # merely inside the wider multi-badge gutter.
        adjacent_prefixes = []
        left_edge = first_x
        for prefix in sorted(prefixes, key=lambda prefix: prefix[1], reverse=True):
            left, end, _marker_color, _hue, label = prefix
            if end < left_edge - glyph_height * 1.5:
                break
            keep[label] = 1
            adjacent_prefixes.append(prefix)
            left_edge = min(left_edge, left)
        selected = keep[labels].astype(bool)
        signal = white.copy()
        signal[selected] = color[selected]
        # Brackets and the letter inside them are separate components of the
        # same badge. Group their adjacent colors without merging a green S
        # with a differently colored Shiny or Elite marker beside it.
        colors: list[str] = []
        right = -1
        previous_hue = 0.0
        for left, end, marker_color, hue, _label in sorted(adjacent_prefixes):
            hue_difference = abs(hue - previous_hue)
            hue_difference = min(hue_difference, 180 - hue_difference)
            if (
                not colors or marker_color != colors[-1]
                or left > right + glyph_height * 0.5 or hue_difference > 10
            ):
                colors.append(marker_color)
            right = max(right, end)
            previous_hue = hue
        return signal, tuple(colors)

    @staticmethod
    def _green_text_mask(image: np.ndarray, minimum: int = 150) -> np.ndarray:
        blue, green, red = np.moveaxis(image[:, :, :3].astype(np.int16), 2, 0)
        return (green >= minimum) & (green >= red + 30) & (green >= blue + 30)

    @staticmethod
    def nature_match_text(image: np.ndarray) -> tuple[np.ndarray, bool]:
        """Isolate a green glyph in the optional, marker-only Nature Match crop."""
        if image is None or image.size == 0:
            return image, False
        if image.ndim != 3 or image.shape[2] not in (3, 4):
            # Monochrome evidence cannot distinguish a green S from Shiny.
            return np.zeros(image.shape[:2], dtype=np.uint8), False
        bright_green = ImageProcessor._green_text_mask(image)
        # Retain antialiased stroke edges only when connected to the bright
        # green evidence. Hard-cutting those edges turns a small S into a 5.
        green = ImageProcessor._green_text_mask(image, minimum=80).astype(np.uint8)
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(green, 8)
        keep = np.zeros(count, dtype=np.uint8)
        height, width = image.shape[:2]
        for label, (x, y, w, h, area) in enumerate(stats[1:], 1):
            if (
                3 <= h <= 64 and 1 <= w <= h * 1.5 and 3 <= area < w * h * 0.95
                and x > 0 and y > 0 and x + w < width and y + h < height
                and np.count_nonzero(bright_green[y:y + h, x:x + w] & (labels[y:y + h, x:x + w] == label)) >= 3
            ):
                # A clipped glyph or green scenery connected to the crop edge
                # cannot positively identify the complete S badge.
                keep[label] = 1
        selected = keep[labels].astype(bool)
        signal = np.zeros(image.shape[:2], dtype=np.uint8)
        signal[selected] = image[:, :, :3].max(axis=2)[selected]
        if selected.any():
            ys, xs = np.nonzero(selected)
            # process() adds the quiet OCR border after adaptive enlargement.
            signal = signal[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        return signal, bool(selected.any())

    @staticmethod
    def _text_scale(
        signal: np.ndarray, threshold: int | None, *, single_glyph: bool = False,
    ) -> float:
        mode = cv2.THRESH_BINARY | (cv2.THRESH_OTSU if threshold is None else 0)
        mask = cv2.threshold(signal, threshold or 0, 255, mode)[1]
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        heights = [
            height for _x, _y, width, height, area in stats[1:]
            if 3 <= height <= 64 and 1 <= width <= height * 2
            and area >= 3 and area < width * height * 0.95
        ]
        glyph_height = float(np.median(heights)) if heights else 8.0
        scale = (6.0 if single_glyph else 4.0) if glyph_height <= 12 else 3.0 if glyph_height <= 18 else 2.0
        # Bound enlargement for unusually large saved regions.
        height, width = signal.shape
        return max(1.0, min(
            scale, 3600 / max(width, height), (8_000_000 / signal.size) ** 0.5,
        ))

    @staticmethod
    def _remove_panel_edges(mask: np.ndarray) -> np.ndarray:
        height, width = mask.shape
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        keep = np.full(count, 255, dtype=np.uint8)
        keep[0] = 0
        for label, (x, y, w, h, _area) in enumerate(stats[1:], 1):
            touches_edge = (
                x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1
            )
            # Remove long borders and bright nameplate backgrounds, leaving
            # the disconnected letter interiors inside their dark outlines.
            if w >= width * 0.65 and w >= h * 4 and (touches_edge or w >= h * 8):
                keep[label] = 0
        return keep[labels]

    def imageMorphology(self, image: np.ndarray, operation: str = "open") -> np.ndarray:

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))

        if operation == "open":
            return cv2.morphologyEx(image, cv2.MORPH_OPEN, kernel)

        elif operation == "close":
            return cv2.morphologyEx(image, cv2.MORPH_CLOSE, kernel)

        elif operation == "erode":
            return cv2.erode(image, kernel, iterations=1)

        elif operation == "dilate":
            return cv2.dilate(image, kernel, iterations=1)

        else:
            raise ValueError(f"Unsupported morphology operation: {operation}")
