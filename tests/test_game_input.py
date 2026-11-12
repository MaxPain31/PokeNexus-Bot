import ctypes
import unittest
from ctypes import wintypes
from unittest.mock import Mock, call, patch

from PySide6.QtCore import QPoint, QRect

from capture.game_input import GameInput, _Input, _Point
from models.region_config import SavedRegion


@unittest.skipUnless(hasattr(ctypes, "WinDLL"), "Windows mouse input")
class GameInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.input = GameInput()
        self.region = SavedRegion("fight", "Fight Button", 150, 160, 40, 20)
        self.geometry = QRect(100, 100, 800, 600)
        self.origins = {42: (108, 132), 43: (148, 162), 44: (160, 164)}
        self.sizes = {42: (800, 560), 43: (40, 30), 44: (20, 12)}
        self.context = -4
        self.cursor = (400, 300)
        self.events = []
        self.send_results = []
        self.user32 = Mock()
        self.user32.IsWindow.return_value = True
        self.user32.IsWindowVisible.return_value = True
        self.user32.IsWindowEnabled.return_value = True
        self.user32.IsIconic.return_value = False
        self.user32.IsChild.side_effect = lambda root, child: root == 42 and child in (43, 44)
        self.user32.ScreenToClient.side_effect = self._screen_to_client
        self.user32.GetClientRect.side_effect = self._client_rect
        self.user32.GetForegroundWindow.return_value = 42
        self.user32.GetWindowThreadProcessId.side_effect = self._process_id
        self.user32.WindowFromPhysicalPoint.return_value = 42
        self.user32.GetPhysicalCursorPos.side_effect = self._get_cursor
        self.user32.SetPhysicalCursorPos.side_effect = self._set_cursor
        self.user32.GetAsyncKeyState.return_value = 0
        self.user32.GetWindowDpiAwarenessContext.return_value = -4
        self.user32.SetThreadDpiAwarenessContext.side_effect = self._set_context
        self.user32.PhysicalToLogicalPointForPerMonitorDPI.return_value = True
        self.user32.SendInput.side_effect = self._send_input
        self.sleep = Mock()
        for target, replacement in (
            ("capture.game_input.ctypes.WinDLL", Mock(return_value=self.user32)),
            ("capture.game_input.ctypes.get_last_error", Mock(return_value=0)),
            ("capture.game_input.logical_to_physical_point", Mock(side_effect=QPoint)),
            ("capture.game_input.time.sleep", self.sleep),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._assert_context_restored)

    def _assert_context_restored(self) -> None:
        for name in (
            "GetCursorPos", "SetCursorPos", "PostMessageW", "WindowFromPoint",
            "SetForegroundWindow", "SetFocus", "SetCapture",
        ):
            getattr(self.user32, name).assert_not_called()
        self.assertEqual(self.context, -4)

    def _process_id(self, handle, pointer):
        process_id = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents
        process_id.value = 100 if handle in (42, 43, 44) else 200
        return 1

    def _get_cursor(self, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(_Point)).contents
        point.x, point.y = self.cursor
        return True

    def _set_cursor(self, x, y):
        self.cursor = (x, y)
        return True

    def _send_input(self, count, pointer, size):
        event = ctypes.cast(pointer, ctypes.POINTER(_Input)).contents
        self.assertEqual(count, 1)
        self.assertEqual(size, ctypes.sizeof(_Input))
        self.assertEqual(event.type, GameInput._INPUT_MOUSE)
        self.events.append((event.value.mi.dwFlags, self.cursor))
        return self.send_results.pop(0) if self.send_results else 1

    def _set_context(self, context: int) -> int:
        previous, self.context = self.context, context
        return previous

    def _screen_to_client(self, handle: int, pointer) -> bool:
        point = ctypes.cast(pointer, ctypes.POINTER(_Point)).contents
        x, y = self.origins[handle]
        point.x -= x
        point.y -= y
        return True

    def _client_rect(self, handle: int, pointer) -> bool:
        rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        rect.left = rect.top = 0
        rect.right, rect.bottom = self.sizes[handle]
        return True

    def test_system_click_is_held_across_frames_and_restores_the_pointer(self) -> None:
        self.input.click_region(42, self.region, self.geometry)

        self.assertEqual(self.events, [(0x0002, (169, 169)), (0x0004, (169, 169))])
        self.assertEqual(self.sleep.call_args_list, [call(0.05), call(0.05)])
        self.assertEqual(
            self.user32.SetPhysicalCursorPos.call_args_list,
            [call(169, 169), call(400, 300)],
        )
        self.assertEqual(self.cursor, (400, 300))

    def test_visible_game_child_can_receive_the_system_click(self) -> None:
        self.user32.WindowFromPhysicalPoint.return_value = 44
        self.input.click_region(42, self.region, self.geometry)
        self.assertEqual(len(self.events), 2)
        self.user32.IsChild.assert_called_with(42, 44)

    def test_zero_settle_delay_still_holds_the_mouse_button(self) -> None:
        self.input.click_region(42, self.region, self.geometry, settle_delay=0)
        self.sleep.assert_called_once_with(GameInput._CLICK_HOLD_SECONDS)

    def test_scaled_negative_screen_coordinates_use_the_targets_dpi_space(self) -> None:
        self.origins[42] = (-1192, 132)
        self.user32.GetWindowDpiAwarenessContext.return_value = -1

        def target_logical_point(handle, pointer):
            self.assertEqual(self.context, -1)
            point = ctypes.cast(pointer, ctypes.POINTER(_Point)).contents
            point.x = round(point.x / 1.5)
            point.y = round(point.y / 1.5)
            return True

        self.user32.PhysicalToLogicalPointForPerMonitorDPI.side_effect = target_logical_point
        region = SavedRegion("fight", "Fight Button", -1100, 200, 41, 21)
        with patch(
            "capture.game_input.logical_to_physical_point", return_value=QPoint(-1620, 315)
        ) as convert:
            self.input.click_region(42, region, QRect(-1200, 100, 800, 600))

        convert.assert_called_once_with(QPoint(-1080, 210))
        self.assertEqual(self.events, [(0x0002, (-1620, 315)), (0x0004, (-1620, 315))])
        self.assertEqual(self.cursor, (400, 300))

    def test_invalid_and_outside_regions_are_rejected_before_moving_or_clicking(self) -> None:
        for region in (
            SavedRegion("fight", "Fight Button", 99, 160, 40, 20),
            SavedRegion("fight", "Fight Button", 150, 100, 40, 20),  # Title bar.
        ):
            with self.subTest(region=region), self.assertRaises(ValueError):
                self.input.click_region(42, region, self.geometry)
        self.user32.SendInput.assert_not_called()
        self.user32.SetPhysicalCursorPos.assert_not_called()

    def test_hidden_minimized_disabled_or_destroyed_game_is_not_clicked(self) -> None:
        for name, value in (
            ("IsWindow", False), ("IsWindowVisible", False),
            ("IsIconic", True), ("IsWindowEnabled", False),
        ):
            function = getattr(self.user32, name)
            previous = function.return_value
            function.return_value = value
            with self.subTest(api=name), self.assertRaises(RuntimeError):
                self.input.click_region(42, self.region, self.geometry)
            function.return_value = previous
        self.user32.SendInput.assert_not_called()
        self.user32.SetPhysicalCursorPos.assert_not_called()

    def test_background_game_is_not_clicked_or_activated(self) -> None:
        self.user32.GetForegroundWindow.return_value = 1000
        with self.assertRaises(RuntimeError):
            self.input.click_region(42, self.region, self.geometry)
        self.user32.SendInput.assert_not_called()
        self.user32.SetPhysicalCursorPos.assert_not_called()

    def test_covered_button_is_not_clicked(self) -> None:
        for target in (0, 1000):
            self.user32.WindowFromPhysicalPoint.return_value = target
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                self.input.click_region(42, self.region, self.geometry)
        self.user32.SendInput.assert_not_called()
        self.user32.SetPhysicalCursorPos.assert_not_called()

    def test_already_held_mouse_button_is_not_interrupted(self) -> None:
        self.user32.GetAsyncKeyState.return_value = 0x8000
        with self.assertRaises(RuntimeError):
            self.input.click_region(42, self.region, self.geometry)
        self.user32.SendInput.assert_not_called()
        self.user32.SetPhysicalCursorPos.assert_not_called()

    def test_focus_loss_or_cover_after_moving_aborts_and_restores_the_pointer(self) -> None:
        for name in ("GetForegroundWindow", "WindowFromPhysicalPoint"):
            function = getattr(self.user32, name)
            function.side_effect = [42, 1000]
            with self.subTest(api=name), self.assertRaises(RuntimeError):
                self.input.click_region(42, self.region, self.geometry)
            self.assertEqual(self.cursor, (400, 300))
            function.side_effect = None
        self.user32.SendInput.assert_not_called()

    def test_user_movement_during_click_is_preserved(self) -> None:
        def move_during_hold(_duration):
            self.cursor = (600, 450)

        self.sleep.side_effect = move_during_hold
        self.input.click_region(42, self.region, self.geometry)
        self.assertEqual(self.cursor, (600, 450))
        self.user32.SetPhysicalCursorPos.assert_called_once_with(169, 169)
        self.assertEqual([event[0] for event in self.events], [0x0002, 0x0004])

    def test_user_movement_before_press_aborts_without_overwriting_it(self) -> None:
        def move_before_press(x, y):
            self.cursor = (600, 450)
            return True

        self.user32.SetPhysicalCursorPos.side_effect = move_before_press
        with self.assertRaisesRegex(RuntimeError, "Mouse moved"):
            self.input.click_region(42, self.region, self.geometry)
        self.user32.SendInput.assert_not_called()
        self.assertEqual(self.cursor, (600, 450))
        self.user32.SetPhysicalCursorPos.assert_called_once()

    def test_coordinate_failure_restores_the_callers_dpi_context(self) -> None:
        self.user32.GetWindowDpiAwarenessContext.return_value = -1
        self.user32.ScreenToClient.side_effect = None
        self.user32.ScreenToClient.return_value = False
        with self.assertRaises(OSError):
            self.input.click_region(42, self.region, self.geometry)
        self.user32.SendInput.assert_not_called()
        self.user32.SetPhysicalCursorPos.assert_not_called()

    def test_rejected_mouse_down_is_reported_and_pointer_is_restored(self) -> None:
        self.send_results = [0]
        with self.assertRaises(OSError):
            self.input.click_region(42, self.region, self.geometry)
        self.assertEqual(self.events, [(0x0002, (169, 169))])
        self.assertEqual(self.cursor, (400, 300))

    def test_mouse_release_is_retried_and_a_permanent_failure_is_reported(self) -> None:
        for released in (True, False):
            self.events.clear()
            self.send_results = [1, 0, int(released)]
            with self.subTest(released=released):
                if released:
                    self.input.click_region(42, self.region, self.geometry)
                else:
                    with self.assertRaises(OSError):
                        self.input.click_region(42, self.region, self.geometry)
                self.assertEqual(
                    [event[0] for event in self.events], [0x0002, 0x0004, 0x0004],
                )
                self.assertEqual(self.cursor, (400, 300))

    def test_interrupted_hold_still_releases_and_restores_the_pointer(self) -> None:
        self.sleep.side_effect = RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.input.click_region(42, self.region, self.geometry)
        self.assertEqual([event[0] for event in self.events], [0x0002, 0x0004])
        self.assertEqual(self.cursor, (400, 300))

    def test_invalid_settle_delay_is_rejected_before_input(self) -> None:
        for delay in (-1, float("nan"), float("inf")):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                self.input.click_region(42, self.region, self.geometry, settle_delay=delay)
        self.user32.SendInput.assert_not_called()
        self.user32.SetPhysicalCursorPos.assert_not_called()


@unittest.skipUnless(hasattr(ctypes, "WinDLL"), "Windows keyboard input")
class GameKeyboardInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.input = GameInput()
        self.events = []
        self.send_results = []
        self.user32 = Mock()
        self.user32.SendInput.side_effect = self._send_input
        self.foreground = Mock(return_value=True)
        self.sleep = Mock()
        for target, replacement in (
            ("capture.game_input.ctypes.WinDLL", Mock(return_value=self.user32)),
            ("capture.game_input.ctypes.get_last_error", Mock(return_value=0)),
            ("capture.game_input.GameInput.is_game_foreground", self.foreground),
            ("capture.game_input.time.sleep", self.sleep),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _send_input(self, count, pointer, size):
        event = ctypes.cast(pointer, ctypes.POINTER(_Input)).contents
        self.assertEqual(count, 1)
        self.assertEqual(size, ctypes.sizeof(_Input))
        self.assertEqual(event.type, GameInput._INPUT_KEYBOARD)
        self.events.append((event.value.ki.wVk, event.value.ki.dwFlags))
        return self.send_results.pop(0) if self.send_results else 1

    def test_repeated_number_one_taps_have_separate_non_extended_down_and_up_events(self) -> None:
        for _ in range(2):
            self.input.tap_key(42, ord("1"), duration_seconds=0.05, extended=False)
        self.assertEqual(self.events, [(0x31, 0), (0x31, 2), (0x31, 0), (0x31, 2)])
        self.assertEqual(self.sleep.call_args_list, [call(0.05), call(0.05)])
        self.assertEqual(self.foreground.call_args_list, [call(42), call(42)])
        self.user32.SetPhysicalCursorPos.assert_not_called()

    def test_background_game_is_not_sent_a_key_or_activated(self) -> None:
        self.foreground.return_value = False
        with self.assertRaisesRegex(RuntimeError, "foreground"):
            self.input.tap_key(42, ord("2"), extended=False)
        self.assertEqual(self.events, [])
        self.sleep.assert_not_called()
        self.user32.SetForegroundWindow.assert_not_called()

    def test_focus_loss_during_hold_still_releases_the_number_key(self) -> None:
        def lose_focus(_duration):
            self.foreground.return_value = False

        self.sleep.side_effect = lose_focus
        self.input.tap_key(42, ord("3"), extended=False)
        self.assertEqual(self.events, [(0x33, 0), (0x33, 2)])
        self.foreground.assert_called_once_with(42)

    def test_interrupted_hold_still_releases_the_number_key(self) -> None:
        self.sleep.side_effect = RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.input.tap_key(42, ord("4"), extended=False)
        self.assertEqual(self.events, [(0x34, 0), (0x34, 2)])

    def test_rejected_key_down_does_not_release_a_key_that_was_never_pressed(self) -> None:
        self.send_results = [0]
        with self.assertRaises(OSError):
            self.input.tap_key(42, ord("5"), extended=False)
        self.assertEqual(self.events, [(0x35, 0)])
        self.sleep.assert_not_called()

    def test_key_release_is_retried_and_a_permanent_failure_is_reported(self) -> None:
        for released in (True, False):
            with self.subTest(released=released):
                self.events.clear()
                self.send_results = [1, 0, int(released)]
                if released:
                    self.input.tap_key(42, ord("6"), extended=False)
                else:
                    with self.assertRaises(OSError):
                        self.input.tap_key(42, ord("6"), extended=False)
                self.assertEqual(self.events, [(0x36, 0), (0x36, 2), (0x36, 2)])


if __name__ == "__main__":
    unittest.main()
