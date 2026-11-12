from PySide6.QtCore import QObject, QRect, Signal, Slot

from capture.region_test_service import RegionTestService
from models.region_config import SavedRegion


class RegionTestTask(QObject):
    """Run one region OCR test off the GUI thread and report progress."""

    log_message = Signal(str, str)
    finished = Signal()

    def __init__(
        self,
        service: RegionTestService,
        regions: tuple[SavedRegion, ...],
        window_handle: int,
        game_geometry: QRect,
        save_images: bool = True,
    ) -> None:
        super().__init__()
        self.service = service
        self.regions = regions
        self.window_handle = window_handle
        self.game_geometry = QRect(game_geometry)
        self.save_images = save_images

    @Slot()
    def run(self) -> None:
        succeeded = 0
        try:
            for region in self.regions:
                try:
                    self.service.run(
                        region,
                        self.window_handle,
                        self.game_geometry,
                        self.log_message.emit,
                        save_images=self.save_images,
                    )
                    succeeded += 1
                    self.log_message.emit(
                        "SUCCESS", f"Region test completed: {region.label}."
                    )
                except Exception as error:
                    self.log_message.emit(
                        "ERROR", f"Region test failed for {region.label}: {error}"
                    )
            self.log_message.emit(
                "INFO",
                f"Tests finished: {succeeded}/{len(self.regions)} succeeded.",
            )
        finally:
            self.finished.emit()
