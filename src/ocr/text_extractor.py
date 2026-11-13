from typing import Any
import os

import numpy as np
import pytesseract
from pytesseract import Output

from models.ocr_result import OCRResult
from utils.runtime_paths import tesseract_installation


class TextExtractor:
    """Extract text and confidence metrics from processed images."""

    def __init__(
        self,
        language: str = "eng",
        page_segmentation_mode: int = 11,
        minimum_confidence: float = 0.0,
    ) -> None:
        tesseract, tessdata = tesseract_installation()
        pytesseract.pytesseract.tesseract_cmd = str(tesseract)
        os.environ["TESSDATA_PREFIX"] = str(tessdata)

        self.language = language
        self.page_segmentation_mode = page_segmentation_mode
        self.minimum_confidence = minimum_confidence

    def extract(
        self,
        image: np.ndarray,
        *,
        preserve_lines: bool = False,
        minimum_line_confidence: float = 0.0,
        minimum_line_characters: int = 1,
        page_segmentation_mode: int | None = None,
    ) -> OCRResult:
        """Extract recognized text and calculate word confidence."""

        if image is None or image.size == 0:
            raise ValueError("Input image is empty.")

        if image.ndim not in (2, 3):
            raise ValueError("Input image must be grayscale or color.")

        psm = (
            self.page_segmentation_mode
            if page_segmentation_mode is None else page_segmentation_mode
        )
        config = f"--psm {psm} --dpi 300"

        # Get recognized words and their confidence scores.
        data: dict[str, Any] = pytesseract.image_to_data(
            image,
            lang=self.language,
            config=config,
            output_type=Output.DICT,
        )

        recognized_words: list[str] = []
        confidences: list[float] = []
        recognized_lines: dict[
            tuple[int, int, int],
            tuple[list[str], list[float]],
        ] = {}

        for index, (word, raw_confidence) in enumerate(
            zip(data["text"], data["conf"])
        ):
            word = word.strip()

            try:
                confidence = float(raw_confidence)
            except (TypeError, ValueError):
                continue

            # Tesseract uses negative values for non-word entries.
            if not word or confidence < 0:
                continue

            recognized_words.append(word)
            confidences.append(confidence)
            if confidence >= self.minimum_confidence:
                line_key = (
                    int(data["block_num"][index]),
                    int(data["par_num"][index]),
                    int(data["line_num"][index]),
                )
                line_words, line_confidences = recognized_lines.setdefault(
                    line_key, ([], [])
                )
                line_words.append(word)
                line_confidences.append(confidence)

        # Keep words whose confidence meets the configured threshold.
        accepted_words = [
            word
            for word, confidence in zip(
                recognized_words,
                confidences,
            )
            if confidence >= self.minimum_confidence
        ]

        accepted_confidences = [
            confidence
            for confidence in confidences
            if confidence >= self.minimum_confidence
        ]

        if preserve_lines:
            accepted_lines = [
                (words, line_confidences)
                for words, line_confidences in recognized_lines.values()
                if words
                and sum(line_confidences) / len(line_confidences)
                >= minimum_line_confidence
                and sum(character.isalnum() for word in words for character in word)
                >= minimum_line_characters
            ]
            text = "\n".join(" ".join(words) for words, _ in accepted_lines).strip()
            # Discarded background lines must not lower the confidence of the
            # battle text we actually return (or inflate its word count).
            accepted_words = [word for words, _ in accepted_lines for word in words]
            accepted_confidences = [
                confidence for _, line_confidences in accepted_lines
                for confidence in line_confidences
            ]
        else:
            text = " ".join(accepted_words).strip()

        mean_confidence = (
            sum(accepted_confidences) / len(accepted_confidences)
            if accepted_confidences
            else 0.0
        )

        height, width = image.shape[:2]

        return OCRResult(
            text=text,
            image_width=width,
            image_height=height,
            mean_confidence=round(mean_confidence, 2),
            word_count=len(accepted_words),
        )
