import cv2
import numpy as np

class ImageProcessor:
    """Preprocess game screenshots for OCR testing."""

    def process(
        self,
        image: np.ndarray,
        threshold_method: str = "otsu_inverted",
    ) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("Input image is empty.")

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Enlarge small game text.
        enlarged = cv2.resize(
            gray,
            None,
            fx=2,
            fy=2,
            interpolation=cv2.INTER_CUBIC,
        )

        # Reduce noise while preserving edges.
        denoised = cv2.bilateralFilter(
            enlarged,
            d=5,
            sigmaColor=40,
            sigmaSpace=40,
        )

        # Sharpen text edges.
        blurred = cv2.GaussianBlur(
            denoised,
            (0, 0),
            sigmaX=1.2,
        )

        sharpened = cv2.addWeighted(
            denoised,
            1.5,
            blurred,
            -0.5,
            0,
        )

        # Apply the selected thresholding method.
        if threshold_method == "binary_230":
            processed = cv2.threshold(
                sharpened, 230, 255, cv2.THRESH_BINARY
            )[1]

        elif threshold_method == "binary_200":
            processed = cv2.threshold(
                sharpened, 200, 255, cv2.THRESH_BINARY
            )[1]

        elif threshold_method == "binary_167":
            processed = cv2.threshold(
                sharpened, 167, 255, cv2.THRESH_BINARY
            )[1]

        elif threshold_method == "otsu":
            processed = cv2.threshold(
                sharpened, 0, 255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )[1]

        elif threshold_method == "otsu_inverted":
            processed = cv2.threshold(
                sharpened, 0, 255,
                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
            )[1]

        else:
            raise ValueError(
                f"Unsupported threshold method: {threshold_method}"
            )

        return processed


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
