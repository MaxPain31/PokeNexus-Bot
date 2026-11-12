from dataclasses import dataclass


@dataclass
class OCRResult:
    """Store OCR text, image dimensions, and confidence metrics."""

    text: str
    image_width: int
    image_height: int
    mean_confidence: float = 0.0
    word_count: int = 0
