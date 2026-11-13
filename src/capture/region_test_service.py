from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import re

import cv2
import numpy as np
from PySide6.QtCore import QRect

from capture.image_capture import ImageCapture
from capture.image_processor import ImageProcessor
from capture.region_cropper import RegionCropper
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from ocr.region_ocr import RegionOCR
from ocr.text_extractor import TextExtractor
from utils.runtime_paths import user_data_path
from utils.utils import text_split

LogCallback = Callable[[str, str], None]


class RegionTestService:
    """Capture a game window, crop one saved region, and run image OCR."""

    def __init__(
        self,
        image_capture: ImageCapture | None = None,
        region_cropper: RegionCropper | None = None,
        image_processor: ImageProcessor | None = None,
        text_extractor: TextExtractor | None = None,
        output_directory: Path | None = None,
    ) -> None:
        self.image_capture = image_capture or ImageCapture()
        self.region_cropper = region_cropper or RegionCropper()
        self.image_processor = image_processor or ImageProcessor()
        self.text_extractor = text_extractor or TextExtractor()
        self.region_ocr = RegionOCR(self.image_processor, self.text_extractor)
        self.output_directory = output_directory or user_data_path("screenshot")

    def run(
        self,
        region: SavedRegion,
        window_handle: int,
        game_geometry: QRect,
        log: LogCallback,
        save_images: bool = True,
    ) -> OCRResult:
        log(
            "INFO",
            f"Testing {region.label} — "
            f"x={region.x}, y={region.y}, "
            f"width={region.width}, height={region.height}.",
        )

        try:
            screenshot = self.image_capture.capture_window(window_handle)
        except Exception as error:
            log("ERROR", f"Screenshot capture failed: {error}")
            raise
        artifact_stem = self._artifact_stem(region)
        log(
            "SUCCESS",
            f"Screenshot captured: {screenshot.shape[1]} × {screenshot.shape[0]} px.",
        )
        if save_images:
            screenshot_path = self._save_image(
                screenshot, f"{artifact_stem}_screenshot.png"
            )
            log("SUCCESS", f"Screenshot saved: {screenshot_path}")

        try:
            cropped = self.region_cropper.crop(screenshot, region, game_geometry)
        except (ValueError, TypeError, IndexError) as error:
            log("ERROR", f"Region crop failed: {error}")
            raise
        log(
            "SUCCESS",
            f"Cropped region: {cropped.shape[1]} × {cropped.shape[0]} px.",
        )
        if save_images:
            cropped_path = self._save_image(cropped, f"{artifact_stem}_cropped.png")
            log("SUCCESS", f"Cropped region saved: {cropped_path}")

        prepared = self.region_ocr.prepare(cropped, region, log)
        reading = self.region_ocr.extract(prepared, region, log)
        method = reading.prepared.label
        processed, result = reading.prepared.image, reading.result
        log(
            "SUCCESS",
            f"Best OCR variant: '{method}' "
            f"({result.mean_confidence:.2f}% confidence, "
            f"{result.word_count} words).",
        )
        log(
            "SUCCESS",
            f"Best processed image: {processed.shape[1]} × "
            f"{processed.shape[0]} px.",
        )
        if save_images:
            processed_path = self._save_image(
                processed, f"{artifact_stem}_processed.png"
            )
            log("SUCCESS", f"Best processed image saved: {processed_path}")
        else:
            log("INFO", "Saving test images is disabled in Settings.")

        if result.text:
            log(
                "SUCCESS",
                f"Extracted OCR text ({result.mean_confidence:.2f}% confidence):\n"
                f"{text_split(result.text)}",
            )
        else:
            log(
                "WARNING",
                "OCR completed with no detected text. "
                f"Best confidence: {result.mean_confidence:.2f}%.",
            )
        return result

    def _save_image(self, image: np.ndarray, filename: str) -> Path:
        path = self.output_directory / filename
        try:
            self.output_directory.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), image):
                raise OSError(f"Could not encode image file: {path}")
        except (OSError, cv2.error) as error:
            raise OSError(f"Could not save test image to '{path}': {error}") from error
        return path

    @staticmethod
    def _artifact_stem(region: SavedRegion) -> str:
        label = re.sub(r"[^A-Za-z0-9_-]+", "_", region.label).strip("_")
        feature = re.sub(r"[^A-Za-z0-9_-]+", "_", region.feature).strip("_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"{timestamp}_{feature}_{label or 'region'}"
