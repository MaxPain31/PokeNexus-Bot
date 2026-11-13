from PySide6.QtCore import QObject, QRect, QThread, Signal

from capture.battle_region_detector import BattleRegionDetector, BattleRegionPlacement
from capture.image_capture import ImageCapture


class BattleRegionPlacementService:
    """Capture and inspect the game without clicking menus or changing settings."""

    def __init__(
        self, image_capture: ImageCapture | None = None,
        detector: BattleRegionDetector | None = None,
    ) -> None:
        self.image_capture = image_capture or ImageCapture()
        self.detector = detector or BattleRegionDetector()

    def run(self, window_handle: int, game_geometry: QRect) -> BattleRegionPlacement:
        screenshot = self.image_capture.capture_window(window_handle)
        return self.detector.detect(screenshot, game_geometry)


class BattleRegionPlacementTask(QThread):
    """Run capture, frame detection and anchor OCR off the GUI thread."""

    succeeded = Signal(object, object)
    failed = Signal(object, str)

    def __init__(
        self, service: BattleRegionPlacementService, window_handle: int, game_geometry: QRect,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.request_id = object()
        self.window_handle = window_handle
        self.game_geometry = QRect(game_geometry)

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            placement = self.service.run(self.window_handle, self.game_geometry)
            if not self.isInterruptionRequested():
                self.succeeded.emit(self.request_id, placement)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(self.request_id, f"Automatic battle-region placement failed: {error}")
