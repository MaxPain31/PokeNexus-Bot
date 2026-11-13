import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QRect

from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.auto_battle import has_complete_move_set
from models.ocr_result import OCRResult
from models.region_config import SavedRegion
from ocr.region_ocr import PreparedOCR, RegionOCRReading


def timings() -> dict[str, float]:
    return {"capture_window": 0.0, "image_process": 0.0, "text_extraction": 0.0}


class RealtimeOCRBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.regions = tuple(
            SavedRegion(feature, label, index * 40, 0, 30, 20)
            for index, (feature, label) in enumerate((
                *(("moves", f"Move Slot {slot}") for slot in range(1, 5)),
                ("health", "Ally Health"), ("health", "Enemy Health"),
                ("pokemon-name", "Wild Pokemon"), ("turn", "Turn Button"),
            ))
        )
        self.screenshot = np.zeros((600, 800, 3), dtype=np.uint8)
        for index, region in enumerate(self.regions):
            self.screenshot[region.y:region.y + region.height, region.x:region.x + region.width] = index + 10
        self.monitor = RealtimeRegionMonitor(
            self.regions, 42, QRect(0, 0, 800, 600),
            image_capture=Mock(capture_window=Mock(return_value=self.screenshot)),
            text_extractor=Mock(),
        )
        self.monitor.region_ocr.prepare = Mock(side_effect=self._prepare)
        self.monitor.region_ocr.extract = Mock(side_effect=self._extract)

    @staticmethod
    def _prepare(image, region):
        value = int(image[0, 0, 0])
        return (PreparedOCR(
            "white_180", 11, np.full((20, 30), value, dtype=np.uint8), bytes([value]),
        ),)

    @staticmethod
    def _extract(prepared, region):
        text = f"{region.label}: {int(prepared[0].image[0, 0])}"
        return RegionOCRReading(prepared[0], OCRResult(text, 30, 20, 95.0))

    def test_independent_regions_run_concurrently_with_a_four_worker_bound_and_stable_order(self) -> None:
        barrier = threading.Barrier(self.monitor._MAX_OCR_WORKERS)
        later_result_ready = threading.Event()
        lock = threading.Lock()
        owner = threading.get_ident()
        workers = set()
        prepare_threads = []
        finished = []
        active = 0
        peak = 0

        def prepare(image, region):
            prepare_threads.append(threading.get_ident())
            return self._prepare(image, region)

        def extract(prepared, region):
            nonlocal active, peak
            with lock:
                workers.add(threading.current_thread())
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=5)
                if region == self.regions[0]:
                    self.assertTrue(later_result_ready.wait(5))
                reading = self._extract(prepared, region)
                with lock:
                    finished.append(region)
                    self.assertEqual(self.monitor._ocr_cache, {})
                if region == self.regions[3]:
                    later_result_ready.set()
                return reading
            finally:
                with lock:
                    active -= 1

        self.monitor.region_ocr.prepare.side_effect = prepare
        self.monitor.region_ocr.extract.side_effect = extract
        results = self.monitor._read_regions(self.screenshot, self.regions, timings())

        self.assertEqual([region for region, _result in results], list(self.regions))
        self.assertNotEqual(finished[0], self.regions[0])
        self.assertEqual(peak, 4)
        self.assertEqual(len(workers), 4)
        self.assertTrue(all(thread.ident != owner and not thread.is_alive() for thread in workers))
        self.assertEqual(prepare_threads, [owner] * len(self.regions))
        self.assertEqual(set(self.monitor._ocr_cache), set(self.regions))

    def test_cached_regions_do_not_start_workers_or_repeat_recognition(self) -> None:
        first = self.monitor._read_regions(self.screenshot, self.regions, timings())
        self.monitor.region_ocr.extract.reset_mock()
        measured = timings()
        with patch(
            "capture.realtime_region_monitor.ThreadPoolExecutor",
            side_effect=AssertionError("Cached regions must not start workers"),
        ):
            second = self.monitor._read_regions(self.screenshot, self.regions, measured)

        self.assertEqual(second, first)
        self.monitor.region_ocr.extract.assert_not_called()
        self.assertEqual(measured["text_extraction"], 0.0)

    def test_mixed_batch_only_recognizes_changed_crops_and_keeps_cached_result_objects(self) -> None:
        first = dict(self.monitor._read_regions(self.screenshot, self.regions, timings()))
        self.monitor.region_ocr.extract.reset_mock()
        changed = self.regions[:2]
        for region in changed:
            self.screenshot[:region.height, region.x:region.x + region.width] += 1
        second = dict(self.monitor._read_regions(self.screenshot, self.regions, timings()))

        self.assertEqual(
            {call.args[1] for call in self.monitor.region_ocr.extract.call_args_list}, set(changed),
        )
        for region in self.regions:
            if region in changed:
                self.assertNotEqual(second[region].text, first[region].text)
            else:
                self.assertIs(second[region], first[region])

    def test_duplicate_regions_share_one_job_but_retain_requested_order(self) -> None:
        requested = (self.regions[1], self.regions[0], self.regions[1])
        results = self.monitor._read_regions(self.screenshot, requested, timings())

        self.assertEqual([region for region, _result in results], list(requested))
        self.assertIs(results[0][1], results[2][1])
        self.assertEqual(self.monitor.region_ocr.extract.call_count, 2)

    def test_ocr_timing_records_one_elapsed_batch_instead_of_summing_worker_times(self) -> None:
        measured = timings()
        clock = [0.0] * (2 * len(self.regions)) + [2.0, 2.5]
        with patch("capture.realtime_region_monitor.time.perf_counter", side_effect=clock):
            results = self.monitor._read_regions(self.screenshot, self.regions, measured)

        self.assertEqual(len(results), len(self.regions))
        self.assertEqual(measured["image_process"], 0.0)
        self.assertEqual(measured["text_extraction"], 0.5)

    def test_worker_errors_including_timeout_errors_block_actions_without_losing_other_regions(self) -> None:
        for exception in (RuntimeError("OCR unavailable"), TimeoutError("OCR timed out")):
            with self.subTest(exception=type(exception).__name__):
                self.monitor._ocr_cache.clear()
                self.monitor._action_ocr_failed = False
                logs = []
                callback = lambda level, text: logs.append((threading.get_ident(), level, text))
                self.monitor.log_message.connect(callback)

                def extract(prepared, region):
                    if region == self.regions[0]:
                        raise exception
                    return self._extract(prepared, region)

                self.monitor.region_ocr.extract.side_effect = extract
                owner = threading.get_ident()
                results = self.monitor._read_regions(self.screenshot, self.regions, timings())
                self.monitor.log_message.disconnect(callback)

                self.assertTrue(self.monitor._action_ocr_failed)
                self.assertEqual([region for region, _result in results], list(self.regions[1:]))
                self.assertNotIn(self.regions[0], self.monitor._ocr_cache)
                self.assertEqual(len(logs), 1)
                self.assertEqual(logs[0][:2], (owner, "ERROR"))
                self.assertIn("Realtime OCR failed for Move Slot 1", logs[0][2])

    def test_crop_or_preprocessing_failure_still_marks_action_evidence_unreliable(self) -> None:
        original_crop = self.monitor.region_cropper.crop
        for phase in ("crop", "prepare"):
            with self.subTest(phase=phase):
                self.monitor._ocr_cache.clear()
                self.monitor._action_ocr_failed = False

                def crop(screenshot, region, geometry):
                    if phase == "crop" and region == self.regions[0]:
                        raise ValueError("Invalid crop")
                    return original_crop(screenshot, region, geometry)

                def prepare(image, region):
                    if phase == "prepare" and region == self.regions[0]:
                        raise ValueError("Invalid preprocessing")
                    return self._prepare(image, region)

                self.monitor.region_cropper.crop = Mock(side_effect=crop)
                self.monitor.region_ocr.prepare.side_effect = prepare
                results = self.monitor._read_regions(self.screenshot, self.regions, timings())

                self.assertTrue(self.monitor._action_ocr_failed)
                self.assertEqual([region for region, _result in results], list(self.regions[1:]))

    def test_stop_cancels_queued_reads_and_drains_running_workers_without_updating_cache(self) -> None:
        stop = threading.Event()
        release = threading.Event()
        submitted = threading.Event()
        cancelled = threading.Event()
        first_wave = threading.Event()
        returned = threading.Event()
        lock = threading.Lock()
        started = []
        workers = set()
        submitted_count = 0
        cancelled_count = 0
        output = []
        errors = []

        class TrackedExecutor(ThreadPoolExecutor):
            def submit(executor, function, *args, **kwargs):
                nonlocal submitted_count
                future = super().submit(function, *args, **kwargs)

                def completed(completed_future):
                    nonlocal cancelled_count
                    if completed_future.cancelled():
                        with lock:
                            cancelled_count += 1
                            if cancelled_count == 4:
                                cancelled.set()

                future.add_done_callback(completed)
                with lock:
                    submitted_count += 1
                    if submitted_count == 8:
                        submitted.set()
                return future

        def extract(prepared, region):
            with lock:
                started.append(region)
                workers.add(threading.current_thread())
                if len(started) == 4:
                    first_wave.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release running OCR")
            return self._extract(prepared, region)

        def read():
            try:
                output.extend(self.monitor._read_regions(self.screenshot, self.regions, timings()))
            except Exception as error:
                errors.append(error)
            finally:
                returned.set()

        self.monitor.region_ocr.extract.side_effect = extract
        reader = threading.Thread(target=read)
        with patch.object(self.monitor, "isInterruptionRequested", side_effect=stop.is_set), patch(
            "capture.realtime_region_monitor.ThreadPoolExecutor", TrackedExecutor,
        ):
            reader.start()
            try:
                self.assertTrue(first_wave.wait(5))
                self.assertTrue(submitted.wait(5))
                stop.set()
                self.assertTrue(cancelled.wait(5))
                self.assertFalse(returned.is_set())
            finally:
                stop.set()
                release.set()
                reader.join(timeout=5)

        self.assertFalse(reader.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(output, [])
        self.assertEqual(set(started), set(self.regions[:4]))
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(self.monitor._ocr_cache, {})

    def test_interruption_during_a_capture_does_not_publish_a_partial_state(self) -> None:
        stop = threading.Event()
        self.monitor._click_listener._action_regions = frozenset()
        self.monitor._get_memory_usage = Mock(return_value=(0, 0, 0, 1))
        self.monitor._publish_state = Mock()

        def capture(measured):
            stop.set()
            return {"onBattleMode": True}, "12:00:00 PM"

        self.monitor._capture_state = Mock(side_effect=capture)
        with patch.object(self.monitor, "isInterruptionRequested", side_effect=stop.is_set), patch("builtins.print"):
            self.monitor.run()

        self.monitor._capture_state.assert_called_once()
        self.monitor._publish_state.assert_not_called()

    def test_input_posted_by_a_recognition_worker_requires_a_new_capture_before_reading_moves(self) -> None:
        regions = tuple(
            SavedRegion(feature, label, index * 40, 0, 30, 20)
            for index, (feature, label) in enumerate((
                ("chat-box", "Battle Chat"), ("pokemon-name", "Your Pokemon"),
                ("fight", "Fight Button"), ("bag", "Bag Button"),
                ("swap", "Pokemon/Swap Button"), ("run", "Run Button"),
                ("turn", "Turn Button"),
                *(("moves", f"Move Slot {slot}") for slot in range(1, 5)),
            ))
        )
        self.monitor.update_regions(regions, self.monitor.game_geometry)
        self.monitor._apply_region_configuration()
        self.monitor._recovering_battle_menu = False
        now = [100.0]
        posted = threading.Event()
        crop_inputs = []
        original_crop = self.monitor.region_cropper.crop

        def crop(screenshot, region, geometry):
            crop_inputs.append(screenshot)
            return original_crop(screenshot, region, geometry)

        def extract(prepared, region):
            if region.feature == "chat-box":
                text = "A wild Eevee appeared!"
                if not posted.is_set():
                    now[0] += 0.01
                    self.monitor.record_automated_action("fight", "Fight Button")
                    posted.set()
            elif region.feature == "pokemon-name":
                text = "Pikachu"
            elif region.feature == "turn":
                text = "Turn 1"
            elif region.feature == "moves":
                text = "Tackle 1x 20/35"
            else:
                text = ""
            return RegionOCRReading(prepared[0], OCRResult(text, 30, 20, 95.0))

        self.monitor.region_cropper.crop = Mock(side_effect=crop)
        self.monitor.region_ocr.extract.side_effect = extract
        with patch("capture.realtime_region_monitor.time.monotonic", side_effect=lambda: now[0]):
            first, _captured_at = self.monitor._capture_state(timings())
            now[0] = 100.1
            second, _captured_at = self.monitor._capture_state(timings())

        self.assertTrue(posted.is_set())
        self.assertFalse(first["clickedFightButton"])
        self.assertIsNone(first["pokemonMoves"])
        self.assertEqual(first["captureStartedAt"], 100.0)
        self.assertTrue(second["clickedFightButton"])
        self.assertTrue(has_complete_move_set(second["pokemonMoves"]))
        self.assertEqual(second["captureStartedAt"], 100.1)
        self.assertEqual(self.monitor.image_capture.capture_window.call_count, 2)
        self.assertTrue(all(image is self.screenshot for image in crop_inputs))


if __name__ == "__main__":
    unittest.main()
