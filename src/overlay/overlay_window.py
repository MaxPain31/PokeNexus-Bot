import re
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path
import time

from PySide6.QtCore import QRect, QSize, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from capture.game_input import GameInput, region_for_role
from capture.battle_region_detector import BattleRegionPlacement
from capture.battle_region_placement_task import BattleRegionPlacementService, BattleRegionPlacementTask
from capture.fishing_monitor import FishingMonitor
from capture.region_test_service import RegionTestService
from capture.region_test_task import RegionTestTask
from capture.realtime_region_monitor import RealtimeRegionMonitor
from core.battle_action import (
    ACTION_BUTTON_STATES,
    ACTION_RETRY_SECONDS,
    BATTLE_ACTION_KEYS,
    PendingBattleAction,
    action_menu_visible,
    bag_menu_visible,
)
from core.battle_history import BattleHistory
from core.auto_switch import AutoSwitchController
from core.special_pokemon_message import (
    SpecialPokemonMessageController,
    special_pokemon_message_kind,
)
from core.auto_walk import AutoWalkController
from core.auto_battle import (
    choose_move_slot,
    has_complete_move_set,
    matches_battle_target,
    move_name_for_slot,
)
from core.ev_training import EV_STATS, EVDefeatTracker
from core.pokemon_rarity import (
    DEFAULT_RARITY_CODE,
    RARITIES,
    load_pokemon_rarity_data,
)
from core.pokemon_name import normalise_wild_pokemon_name, pokemon_species_name
from core.pokemon_targets import match_pokemon_target, parse_pokemon_targets
from models.region_config import SavedRegion
from overlay.appearance import apply_overlay_stylesheet, set_background_transparency
from overlay.battle_log_window import BattleLogWindow
from overlay.fishing_preview_window import FishingPreviewWindow
from overlay.panel_geometry import position_panel
from overlay.panel_widgets import OverlayIconButton
from overlay.pokemon_encounter_summary_window import (
    PokemonEncounterSummaryWindow,
)
from overlay.region_selector import RegionSelectionWindow
from overlay.region_test_log_window import RegionTestLogWindow
from overlay.region_window import RegionWindow
from overlay.settings_window import SettingsWindow
from overlay.window_tracker import GameWindowTracker
from overlay.window_drag import OverlayWindowDrag
from utils.runtime_paths import application_path, user_data_path
from utils.svg_loader import load_svg_icon

ASSETS_DIR = application_path("assets")


class OverlayWindow(QWidget):
    """Glass-style floating toolbar for the game."""

    tracking_error = Signal(str)
    start_requested = Signal()
    regions_changed = Signal(object)
    region_test_requested = Signal(object)
    encounter_statistics_changed = Signal(int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("game_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Game Bot Controls")
        self._preferred_size = QSize(296, 116)
        self.setFixedSize(self._preferred_size)
        apply_overlay_stylesheet(self)

        self.glass_container = QFrame(self)
        self.glass_container.setObjectName("overlay_container")
        panel_layout = QVBoxLayout(self.glass_container)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(8)

        self.header_widget = QWidget(self.glass_container)
        self.header_widget.setObjectName("overlay_header")
        self.header_widget.setCursor(Qt.CursorShape.SizeAllCursor)
        self.header_widget.setToolTip("Drag to reposition the controls")
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        self.toolbar_title = QLabel("BOT CONTROLS")
        self.toolbar_title.setObjectName("overlay_title")
        self.status_label = QLabel("● Idle")
        self.status_label.setObjectName("overlay_status")
        header_layout.addWidget(self.toolbar_title)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        panel_layout.addWidget(self.header_widget)

        container_layout = QHBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)
        container_layout.addStretch(1)
        panel_layout.addLayout(container_layout)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(3, 3, 3, 3)
        outer_layout.addWidget(self.glass_container)

        self._drag_controller = OverlayWindowDrag(
            self,
            (
                self.glass_container,
                self.header_widget,
                self.toolbar_title,
                self.status_label,
            ),
        )
        self._game_window_visible = False
        self._user_requested_visible = True
        self._bot_running = False
        self._bot_transitioning = False
        self._target_geometry = QRect()
        self._target_ui_geometry = QRect()
        self._target_window_id = 0
        self._region_selection_active = False
        self._pending_region_feature: str | None = None
        self._region_placement_active = False
        self._region_dialog_active = False
        self._active_region_placement_task: BattleRegionPlacementTask | None = None
        self._region_placement_runs: list[tuple[QThread, BattleRegionPlacementTask]] = []
        self._region_placement_service = BattleRegionPlacementService()
        self.settings_window = SettingsWindow(self)
        self.region_window = RegionWindow(self)
        self.battle_log_window = BattleLogWindow(self)
        self.fishing_preview_window = FishingPreviewWindow()
        self.pokemon_encounter_summary_window = PokemonEncounterSummaryWindow(self)
        self.region_test_log_window = RegionTestLogWindow()
        try:
            self._battle_history: BattleHistory | None = BattleHistory()
        except (OSError, ValueError) as error:
            self._battle_history = None
            self.region_test_log_window.append_entry(
                "ERROR",
                f"Could not load battle move history: {error}",
            )
        self._pending_battle_move: tuple[str, str, str, int, int] | None = None
        self._pokemon_encounter_count = 0
        self._pokemon_encounter_date = date.today().isoformat()
        _pokemon_names, self._pokemon_rarity_by_name = load_pokemon_rarity_data()
        self._pokemon_encounter_rarity_counts = {
            code: 0 for code, _label, _color in RARITIES
        }
        self._latest_realtime_state: dict[str, object] = {}
        self._ringtone_players: dict[
            str,
            tuple[str, QMediaPlayer, QAudioOutput],
        ] = {}
        self._daily_encounter_reset_timer = QTimer(self)
        self._daily_encounter_reset_timer.setInterval(60_000)
        self._daily_encounter_reset_timer.timeout.connect(
            self._reset_daily_encounter_count_if_needed
        )
        self._daily_encounter_reset_timer.start()
        self.region_selector = RegionSelectionWindow()
        self._region_test_service = RegionTestService()
        self._region_test_runs: list[tuple[QThread, RegionTestTask]] = []
        self._auto_fishing_monitor: FishingMonitor | None = None
        self._fishing_battle_active: bool | None = None
        self._fishing_non_battle_poll_count = 0
        self._fishing_preview_monitor: FishingMonitor | None = None
        self._realtime_region_monitor: RealtimeRegionMonitor | None = None
        self._realtime_monitor_start_attempted = False
        self._realtime_capture_requested = False
        self._auto_battle_input = GameInput()
        self._pending_battle_action: PendingBattleAction | None = None
        self._last_input_completed_at = 0.0
        self._ev_defeat_tracker = EVDefeatTracker()
        self._auto_walk_controller = AutoWalkController(
            self._auto_battle_input,
            self._log_auto_walk,
            self,
        )
        self._auto_battle_turn: int | None = None
        self._auto_battle_fight_clicked = False
        self._auto_battle_move_clicked = False
        self._auto_run_clicked = False
        self._auto_run_pending = False
        self._auto_run_timer = QTimer(self)
        self._auto_run_timer.setSingleShot(True)
        self._auto_run_timer.timeout.connect(self._click_auto_run)
        self._auto_catch_phase = "idle"
        self._auto_catch_identity: str | None = None
        self._auto_catch_attempt_turn: int | None = None
        self._auto_catch_last_item_name: str | None = None
        self._auto_catch_chat_before_throw: str | None = None
        self._auto_catch_seen_items: set[str] = set()
        self._last_active_pokemon_name: str | None = None
        self._auto_switch_controller = AutoSwitchController()
        self._special_pokemon_message_controller = SpecialPokemonMessageController()
        self._auto_battle_stopped_for_desired_pokemon: str | None = None
        self._nature_matched_encounter_identity: str | None = None
        self._current_encounter_name_color = "#4ADE80"
        self._current_encounter_name: str | None = None
        self._current_encounter_category: str | None = None
        self._auto_battle_reported: set[tuple[int, str]] = set()
        self._last_battle_wait_reason: str | None = None
        self._region_test_log_requested = False
        self._window_tracker = GameWindowTracker(self)
        self._window_tracker.window_updated.connect(self._on_target_window_updated)
        self._window_tracker.tracking_error.connect(self._on_tracking_error)
        self.settings_window.visibility_changed.connect(
            self._on_settings_visibility_changed
        )
        self.settings_window.auto_walk_toggle.toggled.connect(
            self._on_auto_walk_toggled
        )
        self.settings_window.auto_walk_configuration_changed.connect(self._sync_auto_walk)
        self.settings_window.auto_battle_toggle.toggled.connect(
            self._on_auto_battle_configuration_changed
        )
        self.settings_window.auto_battle_configuration_changed.connect(
            self._on_auto_battle_configuration_changed
        )
        self.settings_window.auto_run_toggle.toggled.connect(
            self._on_auto_run_toggled
        )
        self.settings_window.stop_for_desired_pokemon_toggle.toggled.connect(
            self._on_desired_pokemon_stop_toggled
        )
        self.settings_window.auto_catching_toggle.toggled.connect(
            self._on_auto_catching_toggled
        )
        self.settings_window.auto_fishing_toggle.toggled.connect(
            self._sync_auto_fishing_monitor
        )
        self.settings_window.auto_fishing_reaction_lead_spin.valueChanged.connect(
            lambda _value: self._sync_auto_fishing_monitor()
        )
        self.settings_window.auto_fishing_space_hold_spin.valueChanged.connect(
            lambda _value: self._sync_auto_fishing_monitor()
        )
        self.settings_window.auto_fishing_press_f_on_missing_zone_toggle.toggled.connect(
            lambda _enabled: self._sync_auto_fishing_monitor()
        )
        self.settings_window.show_pokemon_encounter_summary_toggle.toggled.connect(
            self._on_encounter_summary_toggled
        )
        self.pokemon_encounter_summary_window.preferred_size_changed.connect(
            self._sync_encounter_summary_window
        )
        self.region_window.visibility_changed.connect(
            self._on_region_visibility_changed
        )
        self.region_window.closed.connect(self._on_region_closed)
        self.region_window.marker_windows_changed.connect(
            self._window_tracker.set_region_marker_windows
        )
        self.region_window.add_region_requested.connect(
            self._begin_region_selection
        )
        self.region_window.auto_place_requested.connect(self._start_battle_region_placement)
        self.region_window.battle_regions_reset.connect(self._on_battle_regions_reset)
        self.region_window.naming_dialog_changed.connect(
            self._on_region_dialog_changed
        )
        self.region_window.regions_changed.connect(self._on_regions_updated)
        self.region_window.test_region_requested.connect(
            self._start_region_test
        )
        self.fishing_preview_window.closed.connect(
            self._stop_fishing_preview
        )
        self.settings_window.save_test_region_screenshots_toggle.toggled.connect(
            self._on_save_test_region_screenshots_toggled
        )
        self.region_selector.selection_completed.connect(
            self._on_region_selection_completed
        )
        self.region_selector.selection_cancelled.connect(
            self._on_region_selection_cancelled
        )
        self._region_window_requested = False
        self.buttons: dict[str, QToolButton] = {}
        self._add_button(
            container_layout,
            "select",
            "select_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            "Configure screen regions",
            self._on_select,
        )
        self._add_button(
            container_layout,
            "play",
            "play_circle_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            "Start automation",
            self._on_play,
        )
        self._add_button(
            container_layout,
            "stop",
            "stop_circle_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            "Stop automation (Alt+P)",
            self._on_stop,
        )
        self._add_button(
            container_layout,
            "settings",
            "settings_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            "Bot settings",
            self._on_settings,
        )
        self.set_bot_state(running=False)
        container_layout.addStretch(1)

        self.settings_window.overlay_background_transparency_spin.valueChanged.connect(
            self._on_background_transparency_changed
        )

    def _on_background_transparency_changed(self, transparency: int) -> None:
        for root in (self, self.fishing_preview_window, self.region_test_log_window):
            set_background_transparency(root, transparency)
        self.pokemon_encounter_summary_window.refresh_background_transparency()

    def _add_button(
        self,
        layout: QHBoxLayout,
        name: str,
        icon_filename: str,
        tooltip: str,
        callback: Callable[[], None],
    ) -> None:
        button = OverlayIconButton(self.glass_container)
        button.setObjectName(f"overlay_{name}_button")
        button.setIcon(
            load_svg_icon(
                ASSETS_DIR / icon_filename,
                QSize(24, 24),
                fill_color="#E6EDF7",
            )
        )
        button.setIconSize(QSize(24, 24))
        button.setText(
            {
                "select": "Regions",
                "play": "Start",
                "stop": "Stop",
                "settings": "Settings",
            }[name]
        )
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setFixedSize(60, 62)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        button.hovered.connect(
            lambda button=button, text=tooltip: self._show_tooltip(button, text)
        )
        button.unhovered.connect(QToolTip.hideText)
        layout.addWidget(button)
        self.buttons[name] = button

    @staticmethod
    def _show_tooltip(button: QToolButton, text: str) -> None:
        if not button.isVisible():
            return

        QToolTip.showText(button.mapToGlobal(button.rect().center()), text, button)

    def attach_to_process(self, process_id: int) -> None:
        self._user_requested_visible = True
        self._realtime_monitor_start_attempted = False
        self._window_tracker.attach(process_id)

    def stop_tracking(self) -> None:
        self.cancel_battle_region_placement()
        self.stop_realtime_region_capture(
            wait_timeout_ms=5000,
            clear_request=True,
        )
        self._stop_fishing_preview()
        self._region_selection_active = False
        self._pending_region_feature = None
        self._region_test_log_requested = False
        self.region_selector.hide()
        self.region_test_log_window.hide()
        self._window_tracker.set_region_test_log_window(None)
        self._window_tracker.set_region_selection_window(None)
        self._window_tracker.stop()
        self._region_window_requested = False
        self._realtime_monitor_start_attempted = False
        self._realtime_capture_requested = False
        self.region_window.hide()
        self.hide()

    def show_overlay(self) -> None:
        self._user_requested_visible = True
        self._apply_visibility()

    def toggle_overlay_visibility(self) -> None:
        self._user_requested_visible = not self._user_requested_visible
        self._apply_visibility()

    def set_bot_state(self, running: bool, transitioning: bool = False) -> None:
        self._bot_running = running
        self._bot_transitioning = transitioning
        if transitioning:
            status = "transitioning"
            label = "Closing" if running else "Opening"
            description = (
                "Waiting for the game to close"
                if running
                else "Waiting for the game to open"
            )
        elif running and self._realtime_capture_requested:
            status = "running"
            label = "Running"
            description = "Automation is active"
        elif running:
            status = "ready"
            label = "Ready"
            description = "Game is open. Press Start to begin automation."
        else:
            status = "idle"
            label = "Idle"
            description = "Game is not running"
        self.status_label.setText(f"● {label}")
        self.status_label.setProperty("status", status)
        self.status_label.setAccessibleName(description)
        self.status_label.setToolTip(description)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        start_enabled = (
            not transitioning and (not running or not self._realtime_capture_requested)
            and not self._region_placement_active and not self._region_dialog_active
            and not self._region_selection_active
        )
        stop_enabled = (
            running and self._realtime_capture_requested and not transitioning
        )
        self.buttons["play"].setProperty("actionEnabled", start_enabled)
        self.buttons["stop"].setProperty("actionEnabled", stop_enabled)
        self.buttons["play"].setEnabled(start_enabled)
        self.buttons["stop"].setEnabled(stop_enabled)
        for button in (self.buttons["play"], self.buttons["stop"]):
            button.style().unpolish(button)
            button.style().polish(button)
        self.buttons["play"].setCursor(
            Qt.CursorShape.PointingHandCursor
            if start_enabled
            else Qt.CursorShape.ArrowCursor
        )
        self.buttons["stop"].setCursor(
            Qt.CursorShape.PointingHandCursor
            if stop_enabled
            else Qt.CursorShape.ArrowCursor
        )

    def _on_target_window_updated(
        self, window_id: int, geometry: QRect, window_visible: bool
    ) -> None:
        self._target_window_id = window_id
        self._target_geometry = geometry
        self._target_ui_geometry = self._window_tracker.client_geometry(
            window_id, geometry
        )
        self._game_window_visible = window_visible and geometry.isValid()
        self.settings_window._drag_controller.set_bounds(
            geometry if geometry.isValid() else QRect()
        )
        if (
            self._realtime_capture_requested
            and window_id > 0
            and geometry.isValid()
        ):
            self._start_realtime_region_capture()
            self._sync_auto_fishing_monitor()
        elif window_id <= 0 or not geometry.isValid():
            self.stop_realtime_region_capture(clear_request=False)
            self._stop_fishing_preview()
        if self._game_window_visible:
            position_panel(
                self, self._target_ui_geometry, self._preferred_size,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            )
        self._apply_visibility()

    def _on_tracking_error(self, message: str) -> None:
        self.cancel_battle_region_placement()
        self.setToolTip(message)
        self._window_tracker.stop()
        self.hide()
        self.tracking_error.emit(message)

    def _on_pokemon_encountered(
        self, pokemon_name: str, state: object = None,
    ) -> None:
        monitor = self.sender()
        if isinstance(monitor, RealtimeRegionMonitor) and (
            monitor is not self._realtime_region_monitor
            or not self._realtime_capture_requested
            or monitor.isInterruptionRequested()
        ):
            return
        pokemon_name = pokemon_species_name(pokemon_name)
        if not pokemon_name:
            return
        self._reset_daily_encounter_count_if_needed()
        self._current_encounter_name = pokemon_name
        self._current_encounter_category = None
        self._pokemon_encounter_count += 1
        rarity_code = self._pokemon_rarity_by_name.get(
            pokemon_name.casefold(),
            DEFAULT_RARITY_CODE,
        )
        if rarity_code not in self._pokemon_encounter_rarity_counts:
            rarity_code = DEFAULT_RARITY_CODE
        self._pokemon_encounter_rarity_counts[rarity_code] += 1
        self.pokemon_encounter_summary_window.set_daily_count(
            self._pokemon_encounter_count
        )
        self.pokemon_encounter_summary_window.set_rarity_counts(
            self._pokemon_encounter_rarity_counts
        )
        self._refresh_encounter_presentation(pokemon_name, state, new_encounter=True)
        self.encounter_statistics_changed.emit(
            self._pokemon_encounter_count,
            self._pokemon_encounter_date,
        )

    def _on_pokemon_encounter_updated(self, pokemon_name: str, state: object) -> None:
        monitor = self.sender()
        if isinstance(monitor, RealtimeRegionMonitor) and (
            monitor is not self._realtime_region_monitor
            or not self._realtime_capture_requested
            or monitor.isInterruptionRequested()
        ):
            return
        if self._current_encounter_name is None:
            return
        if self._wild_pokemon_identity(pokemon_name) != self._wild_pokemon_identity(
            self._current_encounter_name,
        ):
            return
        self._refresh_encounter_presentation(self._current_encounter_name, state)

    def _refresh_encounter_presentation(
        self, pokemon_name: str, state: object, *, new_encounter: bool = False,
    ) -> None:
        category = self._classify_encounter(
            pokemon_name, state if isinstance(state, dict) else None,
        )
        if not new_encounter and category == self._current_encounter_category:
            return
        self._current_encounter_category = category
        self._current_encounter_name_color = {
            "desired": "#A855F7", "shiny": "#FACC15", "elite": "#C0C0C0",
        }.get(category, "#4ADE80")
        display_name = (
            f"{category.title()} {pokemon_name}" if category in ("shiny", "elite")
            else pokemon_name
        )
        self.pokemon_encounter_summary_window.start_encounter(
            display_name, self._current_encounter_name_color,
        )
        if category is not None:
            self._play_encounter_sound(category)

    def set_encounter_statistics(
        self,
        count: int,
        encounter_date: str,
        rarity_counts: dict[str, int] | None = None,
    ) -> None:
        today = date.today().isoformat()
        if encounter_date != today:
            count = 0
            encounter_date = today
            rarity_counts = None
        self._pokemon_encounter_count = count
        self._pokemon_encounter_date = encounter_date
        self._pokemon_encounter_rarity_counts = {
            code: (rarity_counts or {}).get(code, 0)
            for code, _label, _color in RARITIES
        }
        self.pokemon_encounter_summary_window.set_daily_count(count)
        self.pokemon_encounter_summary_window.set_rarity_counts(
            self._pokemon_encounter_rarity_counts
        )

    @property
    def pokemon_encounter_count(self) -> int:
        return self._pokemon_encounter_count

    @property
    def pokemon_encounter_date(self) -> str:
        return self._pokemon_encounter_date

    @property
    def pokemon_encounter_rarity_counts(self) -> dict[str, int]:
        return self._pokemon_encounter_rarity_counts.copy()

    def _reset_daily_encounter_count_if_needed(self) -> None:
        today = date.today().isoformat()
        if self._pokemon_encounter_date == today:
            return
        self._pokemon_encounter_date = today
        self._pokemon_encounter_count = 0
        self._pokemon_encounter_rarity_counts = {
            code: 0 for code, _label, _color in RARITIES
        }
        self.pokemon_encounter_summary_window.set_daily_count(0)
        self.pokemon_encounter_summary_window.set_rarity_counts(
            self._pokemon_encounter_rarity_counts
        )
        self.encounter_statistics_changed.emit(0, today)

    def _on_encounter_summary_toggled(self, enabled: bool) -> None:
        del enabled
        self._sync_encounter_summary_window()

    def _sync_encounter_summary_window(self) -> None:
        self._layout_information_panels()

    def _layout_information_panels(
        self, *, battle_visible: bool | None = None,
        test_visible: bool | None = None, force_test: bool = False,
    ) -> None:
        """Lay out the summary and logs together, leaving dragged panels in place."""
        bounds = (
            self._target_ui_geometry
            if self._target_ui_geometry.isValid()
            else self._target_geometry
        )
        allowed = (
            self._game_window_visible
            and self._user_requested_visible
            and not self._region_selection_active
            and bounds.isValid()
        )
        summary = self.pokemon_encounter_summary_window
        battle = self.battle_log_window
        diagnostics = self.region_test_log_window
        show_summary = (
            allowed
            and self.settings_window.show_pokemon_encounter_summary_toggle.isChecked()
        )
        show_battle = allowed and (
            self.settings_window.show_chat_battle_logs_toggle.isChecked()
            if battle_visible is None else battle_visible
        )
        show_test = allowed and self._region_test_log_requested and (
            self.settings_window.show_test_region_logs_toggle.isChecked()
            if test_visible is None else test_visible
        )
        panels = (
            (summary, show_summary, self._window_tracker.set_encounter_summary_window),
            (battle, show_battle, self._window_tracker.set_battle_log_window),
            (diagnostics, show_test, self._window_tracker.set_region_test_log_window),
        )
        for panel, should_show, track in panels:
            if not should_show:
                panel.hide()
                track(None)
        if not allowed:
            return

        available = bounds.adjusted(12, 12, -12, -12)
        if not available.isValid():
            available = QRect(bounds)
        sizes = {
            panel: panel._preferred_size.boundedTo(available.size())
            for panel, _should_show, _track in panels
        }
        # Two columns keep both logs readable on smaller game windows.
        if show_battle and (show_summary or show_test):
            left_width = min(sizes[battle].width(), max(1, (available.width() - 8) // 2))
            right_width = max(1, available.width() - left_width - 8)
            sizes[battle].setWidth(left_width)
            for panel in (summary, diagnostics):
                sizes[panel].setWidth(min(sizes[panel].width(), right_width))

        right_height = available.height()
        if show_summary and self.isVisible() and not self.property("_overlay_user_moved"):
            column_width = max(
                sizes[summary].width(), sizes[diagnostics].width() if show_test else 0
            )
            column = QRect(
                available.right() - column_width + 1, available.top(),
                column_width, available.height(),
            )
            if column.intersects(self.geometry().adjusted(-8, -8, 8, 8)):
                right_height = max(1, available.bottom() - self.geometry().bottom() - 8)
        if show_summary and show_test:
            total_height = sizes[summary].height() + sizes[diagnostics].height()
            stack_height = max(2, right_height - 8)
            if total_height > stack_height:
                summary_height = max(1, stack_height * sizes[summary].height() // total_height)
                sizes[summary].setHeight(summary_height)
                sizes[diagnostics].setHeight(max(1, stack_height - summary_height))
        elif show_summary:
            sizes[summary].setHeight(min(sizes[summary].height(), right_height))

        # Clamp and resize manually positioned panels before reserving their areas.
        for panel, should_show, _track in panels:
            if should_show and panel.property("_overlay_user_moved"):
                panel.show_for_game(bounds, size_limit=sizes[panel])
        occupied = [self.geometry()] if self.isVisible() else []
        occupied.extend(
            panel.geometry() for panel, should_show, _track in panels
            if should_show and panel.property("_overlay_user_moved")
        )
        for panel, should_show, track in panels:
            if should_show:
                if not panel.property("_overlay_user_moved"):
                    panel.show_for_game(bounds, tuple(occupied), size_limit=sizes[panel])
                    occupied.append(panel.geometry())
                track(int(panel.winId()))
        if force_test and show_test:
            diagnostics.raise_()

    def _apply_visibility(self) -> None:
        self._sync_region_window()
        self._sync_fishing_preview_window()
        if self._region_selection_active:
            if not self._game_window_visible or not self._user_requested_visible:
                self._region_selection_active = False
                self._pending_region_feature = None
                self.region_selector.hide()
                self._window_tracker.set_region_selection_window(None)
                self.battle_log_window.hide()
                self._window_tracker.set_battle_log_window(None)
                self._resume_region_configuration()
                self._apply_visibility()
                return
            QToolTip.hideText()
            self.hide()
            self.settings_window.hide()
            self.region_window.hide()
            self.battle_log_window.hide()
            self.pokemon_encounter_summary_window.hide()
            self.region_test_log_window.hide()
            self._window_tracker.set_encounter_summary_window(None)
            self._window_tracker.set_region_test_log_window(None)
            self._window_tracker.set_battle_log_window(None)
            return
        if self._game_window_visible and self._user_requested_visible:
            self.show()
        else:
            QToolTip.hideText()
            self.hide()
            self.settings_window.hide()
            self.region_window.hide()
        self._layout_information_panels()

    def set_chat_battle_logs_visible(self, visible: bool) -> None:
        self._layout_information_panels(battle_visible=visible)

    def append_chat_battle_log(self, message: str) -> None:
        self.battle_log_window.append_entry(message)

    def set_test_region_logs_visible(self, visible: bool) -> None:
        if visible and self._region_test_log_requested:
            self._sync_region_test_log_window(force_show=True)
        elif not visible:
            self._layout_information_panels(test_visible=False)

    def _sync_region_test_log_window(self, force_show: bool = False) -> None:
        self._layout_information_panels(force_test=force_show)

    def _start_region_test(self, region: SavedRegion) -> None:
        if region.feature == "fishing":
            self._start_fishing_preview(region)
            return
        self._start_region_tests((region,))

    def _on_regions_updated(self, regions: object) -> None:
        monitor = self._realtime_region_monitor
        if monitor is not None and not monitor.isInterruptionRequested() and self._target_geometry.isValid():
            # In-flight frames and scheduled inputs still refer to the old
            # hitboxes. Wait for a fresh frame from the queued layout instead.
            self._latest_realtime_state = {}
            self._auto_run_timer.stop()
            self._auto_run_pending = False
            self._auto_walk_controller.stop()
            if self._auto_fishing_monitor is not None:
                self._auto_fishing_monitor.set_configuration_paused(True)
            monitor.update_regions(tuple(regions), self._target_geometry)
            # Set the cutoff after queuing: even a capture begun while the
            # configuration request was being queued must not use old hitboxes.
            self._last_input_completed_at = time.monotonic()
        self.regions_changed.emit(regions)
        # The fishing monitor already compares its own configuration; changing
        # an unrelated battle outline must not restart its one-F-per-cycle state.
        self._sync_auto_fishing_monitor()

    def _on_battle_regions_reset(self) -> None:
        self.stop_realtime_region_capture(wait_timeout_ms=1000, clear_request=True)
        self.region_test_log_window.append_entry(
            "INFO", "Battle regions reset; automation paused. Select regions manually or Auto Place before starting again.",
        )

    def _on_region_dialog_changed(self, window_id: int) -> None:
        self._region_dialog_active = bool(window_id)
        self._window_tracker.set_region_dialog_window(window_id)
        if self._region_dialog_active:
            self._pause_region_configuration()
        else:
            self._resume_region_configuration()

    def _pause_region_configuration(self) -> None:
        self._auto_run_timer.stop()
        self._auto_run_pending = False
        self._auto_walk_controller.stop()
        if self._auto_fishing_monitor is not None:
            self._auto_fishing_monitor.set_configuration_paused(True)
        self._sync_auto_fishing_monitor()
        self.set_bot_state(self._bot_running, self._bot_transitioning)

    def _resume_region_configuration(self) -> None:
        if self._region_placement_active or self._region_dialog_active or self._region_selection_active:
            return
        # Never resume with a frame captured behind selection/dialog surfaces or
        # before a placement result. Encounter counters/timers are not reset.
        self._last_input_completed_at = time.monotonic()
        self._latest_realtime_state = {}
        if self._realtime_capture_requested:
            self._start_realtime_region_capture()
        self._sync_auto_fishing_monitor()
        self.set_bot_state(self._bot_running, self._bot_transitioning)

    def _start_battle_region_placement(self) -> None:
        if self._region_placement_active or self._region_dialog_active or self._region_selection_active:
            return
        if not self._game_window_visible or self._target_window_id <= 0 or not self._target_geometry.isValid():
            self.region_window.set_placement_status(
                "The game window must be visible. Enter a battle, then choose Auto Place.", error=True,
            )
            return
        if self._bot_transitioning:
            self.region_window.set_placement_status("Wait for the game to finish starting, then choose Auto Place.", error=True)
            return

        self._region_placement_active = True
        self.region_window.set_placement_busy(True)
        self.region_window.set_placement_status("Detecting the battle panel. Leave the game window in place.")
        self._pause_region_configuration()
        self._region_test_log_requested = True
        self._sync_region_test_log_window(force_show=True)
        self.region_test_log_window.append_entry("INFO", "Detecting battle regions; no game keys or clicks will be sent.")

        task = BattleRegionPlacementTask(
            self._region_placement_service, self._target_window_id, self._target_geometry, parent=self,
        )
        # As with realtime capture, this worker owns its run() lifecycle. It
        # does not need the GUI event queue to quit a second thread object.
        thread = task
        task.succeeded.connect(self._on_battle_region_placement_succeeded)
        task.failed.connect(self._on_battle_region_placement_failed)
        thread.finished.connect(self._on_battle_region_placement_finished)
        thread.finished.connect(thread.deleteLater)
        self._region_placement_runs.append((thread, task))
        self._active_region_placement_task = task
        thread.start()

    @Slot(object, object)
    def _on_battle_region_placement_succeeded(self, request_id: object, placement: object) -> None:
        # The worker may already have been deleteLater'd when this queued
        # signal arrives. A Python request token survives; QObject.sender()
        # does not, and would silently discard fast placement results.
        task = self._active_region_placement_task
        if task is None or request_id is not task.request_id or not self._region_placement_active:
            return
        self._apply_battle_region_placement(placement, task.window_handle, task.game_geometry)

    def _apply_battle_region_placement(
        self, placement: object, window_handle: int, game_geometry: QRect,
    ) -> None:
        if (
            not self._game_window_visible or window_handle != self._target_window_id
            or game_geometry != self._target_geometry
        ):
            message = "The game window moved, resized or became unavailable. Try Auto Place again; saved regions were not changed."
            self.region_window.set_placement_status(message, error=True)
            self.region_test_log_window.append_entry("WARNING", message)
            return
        if not isinstance(placement, BattleRegionPlacement):
            self.region_window.set_placement_status("Invalid battle-placement result; saved regions were not changed.", error=True)
            return
        before = len(self.region_window.regions)
        self.region_window.apply_automatic_regions(placement.regions)
        self.region_test_log_window.append_entry(
            "SUCCESS", f"Battle panel detected at X {placement.panel.x()}, Y {placement.panel.y()}, "
            f"{placement.panel.width()} x {placement.panel.height()} px. "
            f"Placed {len(self.region_window.regions) - before} missing region(s); saved adjustments were kept.",
        )

    @Slot(object, str)
    def _on_battle_region_placement_failed(self, request_id: object, message: str) -> None:
        task = self._active_region_placement_task
        if task is None or request_id is not task.request_id or not self._region_placement_active:
            return
        self.region_window.set_placement_status(message, error=True)
        self.region_test_log_window.append_entry("ERROR", message)

    @Slot()
    def _on_battle_region_placement_finished(self) -> None:
        thread = self.sender()
        task = next((task for run_thread, task in self._region_placement_runs if run_thread is thread), None)
        self._region_placement_runs = [run for run in self._region_placement_runs if run[0] is not thread]
        if task is None or task is not self._active_region_placement_task:
            return
        self._active_region_placement_task = None
        self._region_placement_active = False
        self.region_window.set_placement_busy(False)
        self._resume_region_configuration()

    def cancel_battle_region_placement(self, *, wait: bool = False) -> None:
        self._active_region_placement_task = None
        self._region_placement_active = False
        self.region_window.set_placement_busy(False)
        for thread, _task in self._region_placement_runs:
            thread.requestInterruption()
            if wait:
                thread.wait()

    def _start_fishing_preview(self, region: SavedRegion) -> None:
        if self._target_window_id <= 0 or not self._target_geometry.isValid():
            self.region_test_log_window.append_entry(
                "ERROR", "Cannot preview fishing region: the game window is unavailable."
            )
            return
        self._stop_fishing_preview()
        preview = FishingMonitor(
            region,
            self._target_window_id,
            self._target_geometry,
            publish_frames=True,
            screenshot_directory=user_data_path("screenshot", "fishing"),
            save_screenshots=(
                self.settings_window.save_test_region_screenshots_toggle.isChecked()
            ),
            parent=self,
        )
        preview.frame_updated.connect(self.fishing_preview_window.set_frame)
        preview.log_message.connect(self.fishing_preview_window.set_capture_error)
        preview.log_message.connect(self.region_test_log_window.append_entry)
        preview.finished.connect(
            lambda preview=preview: self._on_fishing_preview_finished(preview)
        )
        preview.finished.connect(preview.deleteLater)
        self._fishing_preview_monitor = preview
        self._sync_fishing_preview_window()
        if (
            preview.screenshot_directory is not None
            and self.settings_window.save_test_region_screenshots_toggle.isChecked()
        ):
            self.region_test_log_window.append_entry(
                "INFO",
                "Saving live fishing-region screenshots to "
                f"{preview.screenshot_directory}. Close the preview to stop.",
            )
        else:
            self.region_test_log_window.append_entry(
                "INFO",
                "Live fishing screenshots are not being saved. Enable "
                "'Save Test Region Images' to save them.",
            )
        preview.start()

    def _sync_fishing_preview_window(self) -> None:
        bounds = (
            self._target_ui_geometry
            if self._target_ui_geometry.isValid()
            else self._target_geometry
        )
        should_show = (
            self._fishing_preview_monitor is not None
            and self._game_window_visible
            and self._user_requested_visible
            and not self._region_selection_active
            and self._target_window_id > 0
            and bounds.isValid()
        )
        if should_show:
            self.fishing_preview_window.show_for_game(bounds)
            self._window_tracker.set_fishing_preview_window(
                int(self.fishing_preview_window.winId())
            )
        else:
            self.fishing_preview_window.hide()
            self._window_tracker.set_fishing_preview_window(None)

    def _on_fishing_preview_finished(self, monitor: FishingMonitor) -> None:
        if self._fishing_preview_monitor is monitor:
            self._fishing_preview_monitor = None
            self._sync_fishing_preview_window()

    def _on_save_test_region_screenshots_toggled(self, enabled: bool) -> None:
        monitor = self._fishing_preview_monitor
        if monitor is None:
            return
        monitor.set_screenshot_saving_enabled(enabled)
        message = (
            f"Live fishing screenshot saving {'enabled' if enabled else 'paused'}."
        )
        if enabled and monitor.screenshot_directory is not None:
            message += f" Saving to {monitor.screenshot_directory}."
        self.region_test_log_window.append_entry("INFO", message)

    def _stop_fishing_preview(self) -> None:
        monitor = self._fishing_preview_monitor
        self._fishing_preview_monitor = None
        self._sync_fishing_preview_window()
        if monitor is None:
            return
        if monitor.isRunning():
            monitor.requestInterruption()
            if not monitor.wait(1000):
                self.region_test_log_window.append_entry(
                    "WARNING",
                    "Fishing preview is still finishing its current screen capture.",
                )
                return
        if monitor.saved_screenshot_count:
            self.region_test_log_window.append_entry(
                "SUCCESS",
                f"Saved {monitor.saved_screenshot_count} fishing screenshots "
                f"to {monitor.screenshot_directory}.",
            )

    def _sync_auto_fishing_monitor(self, enabled: bool | None = None) -> None:
        if enabled is False:
            if not self._stop_auto_fishing_monitor(wait_timeout_ms=1000):
                self.region_test_log_window.append_entry(
                    "WARNING",
                    "Auto Fishing is still finishing its current screen capture.",
                )
            return
        if (
            not self.settings_window.auto_fishing_toggle.isChecked()
            or not self._realtime_capture_requested
            or self._target_window_id <= 0
            or not self._target_geometry.isValid()
        ):
            if not self._stop_auto_fishing_monitor(wait_timeout_ms=1000):
                self.region_test_log_window.append_entry(
                    "WARNING",
                    "Auto Fishing is still finishing its current screen capture; "
                    "the updated configuration will start when it stops.",
                )
                return
            return
        if (
            getattr(self, "_region_placement_active", False) is True
            or getattr(self, "_region_dialog_active", False) is True
            or getattr(self, "_region_selection_active", False) is True
        ):
            # Keep the same fishing cycle/latches while configuring battle
            # regions. Restart only if its own configuration later changes.
            if self._auto_fishing_monitor is not None:
                self._auto_fishing_monitor.set_configuration_paused(True)
            return
        region = next(
            (
                region
                for region in self.region_window.regions
                if region.feature == "fishing"
            ),
            None,
        )
        if region is None:
            self.region_test_log_window.append_entry(
                "ERROR",
                "Auto Fishing is enabled, but no Fishing Bar region is configured.",
            )
            return
        current = self._auto_fishing_monitor
        if (
            current is not None
            and current.isRunning()
            and current.region == region
            and current.window_handle == self._target_window_id
            and current.game_geometry == self._target_geometry
            and current.reaction_lead_ms
            == self.settings_window.auto_fishing_reaction_lead_spin.value()
            and current.space_hold_ms
            == self.settings_window.auto_fishing_space_hold_spin.value()
            and current.press_f_on_missing_zone
            == self.settings_window.auto_fishing_press_f_on_missing_zone_toggle.isChecked()
        ):
            return
        if not self._stop_auto_fishing_monitor(wait_timeout_ms=1000):
            self.region_test_log_window.append_entry(
                "WARNING",
                "Auto Fishing is still finishing its current screen capture; "
                "the updated configuration will start when it stops.",
            )
            return
        monitor = FishingMonitor(
            region,
            self._target_window_id,
            self._target_geometry,
            auto_fishing=True,
            reaction_lead_ms=(
                self.settings_window.auto_fishing_reaction_lead_spin.value()
            ),
            space_hold_ms=(
                self.settings_window.auto_fishing_space_hold_spin.value()
            ),
            press_f_on_missing_zone=(
                self.settings_window.auto_fishing_press_f_on_missing_zone_toggle.isChecked()
            ),
            publish_frames=False,
            parent=self,
        )
        monitor.log_message.connect(self.region_test_log_window.append_entry)
        monitor.finished.connect(
            lambda monitor=monitor: self._on_auto_fishing_finished(monitor)
        )
        monitor.finished.connect(monitor.deleteLater)
        self._auto_fishing_monitor = monitor
        monitor.set_battle_active(self._fishing_battle_active is not False)
        monitor.set_configuration_paused(
            not bool(self._latest_realtime_state) or self._ev_training_complete()
        )
        self.region_test_log_window.append_entry(
            "INFO", "Auto Fishing is tracking the Fishing Bar region."
        )
        monitor.start()

    def _stop_auto_fishing_monitor(self, wait_timeout_ms: int = 0) -> bool:
        monitor = self._auto_fishing_monitor
        if monitor is not None and monitor.isRunning():
            monitor.requestInterruption()
            if wait_timeout_ms > 0 and not monitor.wait(wait_timeout_ms):
                return False
        self._auto_fishing_monitor = None
        return True

    def _on_auto_fishing_finished(self, monitor: FishingMonitor) -> None:
        if self._auto_fishing_monitor is monitor:
            self._auto_fishing_monitor = None
            self._sync_auto_fishing_monitor()

    def _reset_fishing_battle_state(self) -> None:
        self._fishing_battle_active = None
        self._fishing_non_battle_poll_count = 0
        if self._auto_fishing_monitor is not None:
            self._auto_fishing_monitor.set_battle_active(True)

    def _update_fishing_battle_state(self, state: dict[str, object]) -> None:
        battle_mode = state.get("onBattleMode")
        if (
            state.get("showingSpecialPokemonMessage", False) is not False
            or self._special_pokemon_message_controller.blocked
        ):
            battle_mode = True
        if battle_mode is True:
            self._fishing_battle_active = True
            self._fishing_non_battle_poll_count = 0
        elif battle_mode is False:
            if self._fishing_battle_active is True:
                # Raw OCR state can briefly miss a battle. Use the same exit
                # confirmation as encounter counting before resuming fishing.
                self._fishing_non_battle_poll_count += 1
                if (
                    self._fishing_non_battle_poll_count
                    >= RealtimeRegionMonitor._BATTLE_END_CONFIRMATION_POLLS
                ):
                    self._fishing_battle_active = False
                    self._fishing_non_battle_poll_count = 0
            else:
                self._fishing_battle_active = False
        else:
            self._fishing_non_battle_poll_count = 0

        if self._auto_fishing_monitor is not None:
            self._auto_fishing_monitor.set_battle_active(
                self._fishing_battle_active is not False
            )
            # This is reached only for a fresh frame after region dialogs or
            # layout changes. Never resume fishing using evidence from before
            # the pause, or erase its missing-zone/overlap latches to pause it.
            self._auto_fishing_monitor.set_configuration_paused(self._ev_training_complete())

    def _start_region_tests(
        self,
        regions: tuple[SavedRegion, ...],
        feature: str | None = None,
    ) -> None:
        self.region_test_log_window.log_view.clear()
        self._region_test_log_requested = True
        self._sync_region_test_log_window(force_show=True)
        if not regions:
            self.region_test_log_window.append_entry(
                "ERROR",
                f"No saved region is configured for {feature or 'this test'}. "
                "Add a region before testing.",
            )
            return
        if self._target_window_id <= 0 or not self._target_geometry.isValid():
            self.region_test_log_window.append_entry(
                "ERROR", "Cannot test region: the game window is not available."
            )
            return

        self.region_test_log_window.append_entry(
            "INFO",
            f"Starting {len(regions)} test(s)"
            + (f" for {feature}" if feature else "")
            + ".",
        )
        thread = QThread(self)
        task = RegionTestTask(
            self._region_test_service,
            regions,
            self._target_window_id,
            self._target_geometry,
            self.settings_window.save_test_region_screenshots_toggle.isChecked(),
        )
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.log_message.connect(self.region_test_log_window.append_entry)
        task.finished.connect(thread.quit)
        task.finished.connect(task.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda thread=thread: self._forget_region_test_thread(thread)
        )
        self._region_test_runs.append((thread, task))
        thread.start()

    def _start_realtime_region_capture(self) -> None:
        if (
            not self._realtime_capture_requested
            or self._region_placement_active
            or self._region_dialog_active
            or self._region_selection_active
            or self._realtime_region_monitor is not None
            or self._realtime_monitor_start_attempted
            or self._target_window_id <= 0
            or not self._target_geometry.isValid()
        ):
            return

        self._realtime_monitor_start_attempted = True
        regions = tuple(self.region_window.regions)
        self.region_test_log_window.log_view.clear()
        self._region_test_log_requested = True
        self._sync_region_test_log_window(force_show=True)
        if not regions:
            self.region_test_log_window.append_entry(
                "ERROR",
                "Realtime capture was not started: configure at least one "
                "region in Region Configuration.",
            )
            self._realtime_capture_requested = False
            self._realtime_monitor_start_attempted = False
            self.set_bot_state(self._bot_running, self._bot_transitioning)
            return

        self.region_test_log_window.append_entry(
            "INFO",
            f"Starting realtime capture for {len(regions)} saved region(s).",
        )
        self._reset_fishing_battle_state()
        self._reset_battle_actions()
        monitor = RealtimeRegionMonitor(
            regions,
            self._target_window_id,
            self._target_geometry,
            parent=self,
        )
        # Auto Place may have been run while Moves was already open. Recover
        # that menu only from fresh move/PP evidence, never from old hitboxes.
        monitor.update_regions(regions, self._target_geometry)
        monitor.log_message.connect(self.region_test_log_window.append_entry)
        monitor.state_updated.connect(self._on_realtime_state_updated)
        monitor.chat_message.connect(self._append_battle_chat_message)
        monitor.pokemon_encountered.connect(self._on_pokemon_encountered)
        monitor.pokemon_encounter_updated.connect(self._on_pokemon_encounter_updated)
        monitor.finished.connect(
            lambda monitor=monitor: self._on_realtime_monitor_finished(monitor)
        )
        monitor.finished.connect(monitor.deleteLater)
        self._realtime_region_monitor = monitor
        monitor.start()
        self._sync_auto_fishing_monitor()

    def _append_battle_chat_message(self, timestamp: str, message: str) -> None:
        if self._latest_realtime_state.get("onBattleMode") is not True:
            return
        self.battle_log_window.append_chat_message(timestamp, message)

    def _on_realtime_state_updated(self, state: object) -> None:
        monitor = self.sender()
        if isinstance(monitor, RealtimeRegionMonitor) and (
            monitor is not self._realtime_region_monitor
            or not self._realtime_capture_requested
            or monitor.isInterruptionRequested()
        ):
            return
        if not isinstance(state, dict):
            self.region_test_log_window.append_entry(
                "ERROR", "Auto Battle received an invalid realtime state."
            )
            return
        frame_at = state.get("captureStartedAt")
        if isinstance(frame_at, (int, float)) and frame_at <= self._last_input_completed_at:
            return
        self._latest_realtime_state = state
        # A queued Run belongs to the previous frame. Only the full priority
        # checks below may schedule it again from this new observation.
        self._cancel_queued_auto_run()
        if (
            getattr(self, "_region_placement_active", False) is True
            or getattr(self, "_region_dialog_active", False) is True
            or getattr(self, "_region_selection_active", False) is True
        ):
            self._ev_defeat_tracker.pause()
            return
        self._update_fishing_battle_state(state)
        self.region_window.update_synchronize_state(state)
        if self._handle_special_pokemon_message(state):
            self._ev_defeat_tracker.pause()
            return
        self._update_battle_move_history(state)
        self._update_ev_training_progress(state)
        self._update_encounter_nature_match(state)
        # Movement must start/stop even when battle actions are awaiting a
        # response or their button OCR is unreadable.
        self._sync_auto_walk()

        if self._awaiting_target_nature_match(state):
            self._auto_run_timer.stop()
            self._auto_run_pending = False
            self._log_battle_wait(
                "readable Synchronize evidence for an S-only target. "
                "Keep the green S in the Wild Pokemon region or add a Nature Match region.",
            )
            return
        if self._update_pending_battle_action(state):
            if state.get("clickStateReliable") is False:
                self._log_battle_wait("readable region OCR before checking the last action.")
            return
        if state.get("clickStateReliable") is False:
            self._log_battle_wait("readable region OCR before sending an action.")
            return

        turn = state.get("turn")
        if isinstance(turn, int) and not isinstance(turn, bool) and turn != self._auto_battle_turn:
            self._auto_battle_turn = turn
            self._auto_battle_fight_clicked = False
            self._auto_battle_move_clicked = False
            self._auto_run_clicked = False
            self._auto_battle_reported.clear()
        if action_menu_visible(state):
            self._auto_battle_fight_clicked = False

        if self._handle_auto_switch(
            state,
            faint_confirmed=self._is_player_faint_confirmed(state),
        ):
            self._log_battle_wait("Auto Switch to finish selecting a replacement Pokemon.")
            return

        should_prioritize_catching = (
            self.settings_window.auto_catching_toggle.isChecked()
            and (
                state.get("onBattleMode") is not True
                or self._auto_catch_phase != "idle"
                or self._is_stop_battle_encounter(state)
            )
        )
        if should_prioritize_catching and self._handle_auto_catching(state):
            self._log_battle_wait("Auto Catching to finish handling this encounter.")
            return

        if state.get("onBattleMode") is not True:
            self._auto_run_timer.stop()
            self._auto_run_pending = False
            self._auto_run_clicked = False
            self._auto_battle_turn = None
            self._auto_battle_fight_clicked = False
            self._auto_battle_move_clicked = False
            self._reset_auto_switch_state()
            self._auto_battle_stopped_for_desired_pokemon = None
            self._auto_battle_reported.clear()
            self._log_battle_wait(
                self.settings_window.ev_training_status.text() if self._ev_training_complete()
                else "battle detection (onBattleMode is not true)."
            )
            return

        if self._ev_training_complete():
            self._log_battle_wait(self.settings_window.ev_training_status.text())
            return
        if self._ev_training_enabled() and self._ev_defeat_tracker.awaiting_next_encounter:
            self._log_battle_wait("EV training to observe the next living opponent after this battle.")
            return

        wild_pokemon_name = state.get("wildPokemonName")
        normalised_wild_name = self._normalise_wild_pokemon_name(wild_pokemon_name)
        wild_pokemon_identity = self._wild_pokemon_identity(normalised_wild_name)
        matched_desired_name = self._matched_desired_pokemon(state)
        stop_for_shiny = (
            self.settings_window.stop_for_shiny_toggle.isChecked()
            and state.get("wildPokemonIsShiny") is True
        )
        stop_for_elite = self._should_stop_for_elite_pokemon(state)
        if self._auto_battle_stopped_for_desired_pokemon is not None:
            if (
                not wild_pokemon_identity
                or wild_pokemon_identity
                == self._auto_battle_stopped_for_desired_pokemon
            ):
                return
            self._auto_battle_stopped_for_desired_pokemon = None
            self._auto_battle_turn = None
            self._auto_battle_fight_clicked = False
            self._auto_battle_move_clicked = False
            self._auto_battle_reported.clear()

        if matched_desired_name is not None:
            self._auto_battle_stopped_for_desired_pokemon = (
                matched_desired_name.casefold()
            )
            synchronized = self._selected_target_requires_nature_match(
                self.settings_window.desired_pokemon_input.text(), matched_desired_name,
            )
            self.region_test_log_window.append_entry(
                "INFO",
                f"Auto Battle stopped for this encounter: "
                f"wild {matched_desired_name}"
                + (" with green S (Synchronize)" if synchronized else "")
                + " detected. "
                "Auto Battle will resume for the next encounter.",
            )
            return
        if stop_for_shiny:
            self._auto_battle_stopped_for_desired_pokemon = wild_pokemon_identity
            self.region_test_log_window.append_entry(
                "INFO",
                "Auto Battle stopped for this encounter: shiny wild Pokemon "
                "detected in the wild-name region. Auto Battle will resume "
                "for the next encounter.",
            )
            return
        if stop_for_elite:
            self._auto_battle_stopped_for_desired_pokemon = wild_pokemon_identity
            synchronized = self._selected_target_requires_nature_match(
                self.settings_window.elite_pokemon_input.text(), wild_pokemon_identity,
            )
            self.region_test_log_window.append_entry(
                "INFO",
                "Auto Battle stopped for this encounter: Elite wild Pokemon "
                + ("with green S (Synchronize) " if synchronized else "")
                + "detected. Auto Battle will resume for the next encounter.",
            )
            return

        battle_target = self._auto_battle_target_matches(state)
        if battle_target is None:
            self._log_battle_wait(
                "a readable wild Pokemon name and Elite status for the Auto Battle target filters."
            )
            return
        if not battle_target:
            if self._auto_run_enabled():
                self._handle_auto_run(state)
            elif self.settings_window.auto_battle_toggle.isChecked():
                self._log_battle_wait(
                    "a Pokemon matching the Auto Battle configuration; Auto Run is off."
                )
            return

        turn = state.get("turn")
        if not isinstance(turn, int) or isinstance(turn, bool):
            self._log_battle_wait("a readable turn number in the Turn Button region.")
            return
        if self._auto_battle_move_clicked:
            self._log_battle_wait("the next turn after the last move.")
            return

        if state.get("clickedFightButton") is True and not action_menu_visible(state):
            moves_text = state.get("pokemonMoves")
            if not isinstance(moves_text, str):
                self._log_battle_wait("readable move names and PP in the move regions.")
                return
            if not has_complete_move_set(moves_text):
                self._auto_battle_log_once(
                    turn,
                    "incomplete-move-set",
                    "Auto Battle is waiting for readable move and PP details "
                    "for all four move slots.",
                )
                return
            player_pokemon = state.get("yourPokemonName")
            opponent_pokemon = state.get("wildPokemonName")
            historical_damage: dict[str, float] = {}
            if (
                self._battle_history is not None
                and isinstance(player_pokemon, str)
                and isinstance(opponent_pokemon, str)
            ):
                historical_damage = self._battle_history.average_damage_by_move(
                    self._wild_pokemon_identity(player_pokemon),
                    self._wild_pokemon_identity(opponent_pokemon),
                )
            move_slot = choose_move_slot(moves_text, historical_damage)
            if move_slot is None:
                self._auto_battle_log_once(
                    turn,
                    "no-usable-move",
                    "Auto Battle found no move with remaining PP.",
                )
                return
            if self._submit_battle_action(
                "moves",
                turn,
                f"Move Slot {move_slot}",
            ):
                move_name = move_name_for_slot(moves_text, move_slot)
                enemy_health = state.get("enemyPokemonHealth")
                if (
                    isinstance(player_pokemon, str)
                    and isinstance(opponent_pokemon, str)
                    and isinstance(enemy_health, int)
                    and not isinstance(enemy_health, bool)
                    and move_name
                ):
                    self._pending_battle_move = (
                        self._wild_pokemon_identity(player_pokemon),
                        self._wild_pokemon_identity(opponent_pokemon),
                        move_name,
                        turn,
                        enemy_health,
                    )
            return

        if any(
            state.get(clicked_flag)
            for clicked_flag in (
                "clickedBagButton",
                "clickedSwapButton",
                "clickedRunButton",
            )
        ):
            self._log_battle_wait("the main battle menu to return from the current action.")
            return

        if self._auto_battle_fight_clicked or not all(
            state.get(visible_flag)
            for visible_flag in (
                "showingFightButton",
                "showingPokemonButton",
                "showingRunButton",
                "showingBagButton",
            )
        ):
            buttons = ", ".join(
                f"{feature.title()}={state.get(visible)}"
                for feature, (_clicked, visible) in ACTION_BUTTON_STATES.items()
            )
            self._log_battle_wait(f"the main battle menu ({buttons}).")
            return

        self._submit_battle_action("fight", turn, "Fight Button")

    def _handle_special_pokemon_message(self, state: dict[str, object]) -> bool:
        if (
            getattr(self, "_region_placement_active", False) is True
            or getattr(self, "_region_dialog_active", False) is True
            or getattr(self, "_region_selection_active", False) is True
        ):
            return True
        frame_at = state.get("captureStartedAt")
        controller = self._special_pokemon_message_controller
        auto_close = self.settings_window.auto_close_special_pokemon_message_toggle.isChecked()
        decision = controller.update(
            enabled=self._realtime_capture_requested,
            visible=state.get("showingSpecialPokemonMessage", False),
            frame_at=(
                float(frame_at) if isinstance(frame_at, (int, float)) else time.monotonic()
            ),
        )
        if decision.detected:
            # The modal intercepts battle input. Re-evaluate the battle menu
            # after closing it instead of completing an interrupted action.
            attempt = self._pending_battle_action
            if attempt is not None:
                if attempt.automation_name == "Auto Catching":
                    self._auto_catch_phase = "ready"
                elif attempt.automation_name == "Auto Switch":
                    slot = int(attempt.action_name.rsplit(" ", 1)[1])
                    self._auto_switch_controller.record_click(
                        slot, attempt.turn, succeeded=False,
                    )
            self._pending_battle_action = None
            self._pending_battle_move = None
            self._auto_battle_fight_clicked = False
            self._auto_battle_move_clicked = False
            self._auto_run_clicked = False
            message = state.get("specialPokemonMessage")
            kind = special_pokemon_message_kind(message) if isinstance(message, str) else None
            self.region_test_log_window.append_entry(
                "INFO",
                "Special Pokemon Message detected"
                + (f" ({kind})" if kind else "")
                + (
                    "; closing it before battle actions."
                    if auto_close
                    else "; auto-close is off; waiting for the popup to close."
                ),
            )
        if decision.dismissed:
            self.region_test_log_window.append_entry(
                "INFO", "Special Pokemon Message dismissal confirmed from the game response.",
            )
        if not decision.handled:
            return False

        self._sync_auto_walk()
        self._auto_run_timer.stop()
        self._auto_run_pending = False
        if not auto_close or not decision.click_close:
            self._log_battle_wait(
                "Special Pokemon Message OCR to become readable."
                if state.get("showingSpecialPokemonMessage", False) is None
                else "Special Pokemon Message dismissal."
            )
            return True

        region = region_for_role(
            tuple(self.region_window.regions), "special-pokemon-message", "Close Button",
        )
        try:
            if region is None:
                self._auto_battle_log_once(
                    -1, "missing-special-pokemon-close-region",
                    "Special Pokemon Message requires its Close Button region. "
                    "Add it in Region Overlay to dismiss the popup automatically.",
                )
                return True
            if self._target_window_id <= 0 or not self._target_geometry.isValid():
                raise RuntimeError("The game window is unavailable.")
            # This modal has a dedicated close target, including when ordinary
            # battle actions use number keys. GameInput keeps its usual guards.
            self._auto_battle_input.click_region(
                self._target_window_id, region, self._target_geometry,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self._auto_battle_log_once(
                -1, "special-pokemon-close-failed",
                f"Special Pokemon Message could not click Close Button: {error}",
            )
            return True
        finally:
            completed_at = time.monotonic()
            controller.record_attempt(completed_at)

        self._last_input_completed_at = completed_at
        self.region_test_log_window.append_entry(
            "INFO", "Special Pokemon Message clicked Close Button; waiting for the popup to disappear.",
        )
        return True

    def _auto_battle_target_matches(self, state: dict[str, object]) -> bool | None:
        """Resolve target eligibility without treating missing OCR as a mismatch."""
        settings = self.settings_window
        if not settings.auto_battle_toggle.isChecked():
            return False
        mode = settings.auto_battle_mode_selection.currentText()
        if mode == "Default":
            return True
        if mode == "Specific Only":
            return matches_battle_target(
                state.get("wildPokemonName"), settings.specific_pokemon_input.text(),
                elite_only=settings.auto_battle_elite_only_toggle.isChecked(),
                is_elite=state.get("wildPokemonIsElite"),
            )
        if mode == "EV Training Only":
            pokemon = state.get("wildPokemonName")
            if not any(character.isalpha() for character in pokemon_species_name(pokemon)):
                return None
            selected = matches_battle_target(pokemon, settings.ev_training_pokemon_input.text())
            if selected is not True:
                return selected
            yields = settings.ev_training_yield(pokemon)
            return settings.ev_training_progress().fits(yields) if yields is not None else None
        return None

    def _ev_training_enabled(self) -> bool:
        return (
            self.settings_window.auto_battle_toggle.isChecked()
            and self.settings_window.auto_battle_mode_selection.currentText() == "EV Training Only"
        )

    def _ev_training_complete(self) -> bool:
        return self._ev_training_enabled() and self.settings_window.ev_training_progress().complete

    def _ev_training_actions_paused(self) -> bool:
        return self._ev_training_enabled() and (
            self.settings_window.ev_training_progress().complete
            or self._ev_defeat_tracker.awaiting_next_encounter
        )

    def _auto_run_enabled(self) -> bool:
        # Running from an oversized/unwanted yield is part of EV Training.
        return self.settings_window.auto_run_toggle.isChecked() or self._ev_training_enabled()

    def _update_ev_training_progress(self, state: dict[str, object]) -> None:
        frame_at = state.get("captureStartedAt")
        defeated = self._ev_defeat_tracker.observe(
            state, enabled=self._realtime_capture_requested and self._ev_training_enabled(),
            frame_at=float(frame_at) if isinstance(frame_at, (int, float)) else time.monotonic(),
        )
        if defeated is not None:
            yields = self.settings_window.ev_training_yield(defeated)
            if yields is None:
                self.region_test_log_window.append_entry(
                    "WARNING", f"EV Training could not account for {defeated}: no unambiguous catalog yield.",
                )
            else:
                before = self.settings_window.ev_training_progress()
                progress = before.after_defeat(yields)
                if progress != before:
                    self.settings_window.set_ev_training_progress(progress.current, progress.targets)
                    gains = ", ".join(
                        f"+{progress.current[key] - before.current[key]} {label}"
                        for key, label, _category in EV_STATS if progress.current[key] != before.current[key]
                    )
                    self.region_test_log_window.append_entry(
                        "SUCCESS", f"EV Training: {defeated} defeated — {gains}. "
                        f"Current total: {progress.total}/510 EVs.",
                    )
        if self._ev_training_actions_paused():
            self._cancel_queued_auto_run()
            self._auto_battle_turn = None
            self._auto_battle_fight_clicked = False
            self._auto_battle_move_clicked = False
            self._auto_run_clicked = False
            attempt = self._pending_battle_action
            if attempt is not None and attempt.automation_name in ("Auto Battle", "Auto Run"):
                self._cancel_pending_battle_action()
        if self._ev_training_complete() and self._auto_fishing_monitor is not None:
            self._auto_fishing_monitor.set_configuration_paused(True)

    def _cancel_queued_auto_run(self) -> None:
        self._auto_run_timer.stop()
        self._auto_run_pending = False

    def _on_auto_battle_configuration_changed(self, *_args) -> None:
        # Editing settings never sends input. Re-route on the next fresh frame,
        # and discard an obsolete Fight/Move/Run retry immediately.
        self._cancel_queued_auto_run()
        if self._ev_training_complete():
            self._auto_walk_controller.stop()
            if self._auto_fishing_monitor is not None:
                self._auto_fishing_monitor.set_configuration_paused(True)
        attempt = self._pending_battle_action
        if attempt is None or attempt.automation_name not in ("Auto Battle", "Auto Run"):
            return
        target = self._auto_battle_target_matches(self._latest_realtime_state)
        if self._ev_training_actions_paused() or (
            attempt.automation_name == "Auto Battle" and target is False
        ) or (
            attempt.automation_name == "Auto Run" and (target is True or not self._auto_run_enabled())
        ):
            self._cancel_pending_battle_action()

    def _auto_run_allowed(self, state: dict[str, object]) -> bool:
        settings = self.settings_window
        if (
            not self._auto_run_enabled()
            or self._ev_training_actions_paused()
            or self._auto_battle_target_matches(state) is not False
            or state.get("clickStateReliable") is False
            or self._special_pokemon_message_controller.blocked
            or self._awaiting_target_nature_match(state)
            or (settings.auto_catching_toggle.isChecked() and self._auto_catch_phase != "idle")
        ):
            return False
        if settings.auto_switch_fainted_pokemon_toggle.isChecked() and (
            state.get("yourPokemonHealth") == 0
            or (state.get("yourPokemonHealth") is None and self._is_player_faint_confirmed(state))
        ):
            return False
        stopped = self._auto_battle_stopped_for_desired_pokemon
        identity = self._wild_pokemon_identity(state.get("wildPokemonName"))
        if stopped is not None and (not identity or identity == stopped):
            return False
        frame_at = state.get("captureStartedAt")
        return not (
            isinstance(frame_at, (int, float)) and frame_at <= self._last_input_completed_at
        )

    def _handle_auto_run(self, state: dict[str, object]) -> None:
        if not self._auto_run_allowed(state):
            self._cancel_queued_auto_run()
            return
        if action_menu_visible(state) and not self._is_stop_battle_encounter(state):
            self._auto_run_clicked = False
        if self._auto_run_clicked or state.get("clickedRunButton") is True:
            self._auto_run_clicked = True
            return

        if self._stop_auto_run_for_encounter(state):
            return

        if not self._auto_run_ready(state):
            self._log_battle_wait("a readable turn number and Run button for Auto Run.")
            return

        if not self._auto_run_pending:
            self._auto_run_pending = True
            self._auto_run_timer.start(0)

    def _handle_auto_catching(self, state: dict[str, object]) -> bool:
        if not self.settings_window.auto_catching_toggle.isChecked():
            self._reset_auto_catching_state()
            return False

        chat_snapshot = state.get("chatBoxMessage")
        if (
            self._auto_catch_phase
            in ("item_used", "ready", "selecting_item")
            and isinstance(chat_snapshot, str)
            and self._auto_catch_has_new_catch_confirmation(chat_snapshot)
        ):
            self.region_test_log_window.append_entry(
                "SUCCESS",
                "Auto Catching detected the battle chat confirmation that the "
                "opposing Pokemon was caught.",
            )
            was_battle_active = state.get("onBattleMode") is True
            self._reset_auto_catching_state()
            return was_battle_active

        if state.get("onBattleMode") is not True:
            if self._auto_catch_phase == "item_used":
                turn = state.get("turn")
                if turn is None or turn == self._auto_catch_attempt_turn:
                    self.region_test_log_window.append_entry(
                        "SUCCESS",
                        "Auto Catching detected the battle ended without a "
                        "turn increment; the catch cycle completed.",
                    )
            self._reset_auto_catching_state()
            return False

        wild_name = self._normalise_wild_pokemon_name(
            state.get("wildPokemonName")
        )
        identity = self._wild_pokemon_identity(wild_name)
        if identity and identity != self._auto_catch_identity:
            self._reset_auto_catching_state()
            self._auto_catch_identity = identity

        if self._auto_catch_phase == "idle":
            if not self._is_stop_battle_encounter(state):
                return False
            if not self._auto_catch_matches_filters(state, wild_name):
                return True

        turn = state.get("turn")
        valid_turn = isinstance(turn, int) and not isinstance(turn, bool)
        if self._auto_catch_phase == "item_used":
            if not valid_turn or (
                self._auto_catch_attempt_turn is not None
                and turn <= self._auto_catch_attempt_turn
            ):
                return True
            if self._auto_catch_attempt_turn is None and not action_menu_visible(state):
                return True
            self._auto_catch_phase = "ready"
            self._auto_catch_attempt_turn = turn
        if self._auto_catch_phase == "finished":
            return True

        if (
            valid_turn
            and self._auto_catch_attempt_turn is not None
            and turn > self._auto_catch_attempt_turn
        ):
            self._auto_catch_phase = "ready"
            self._auto_catch_attempt_turn = turn

        if self._auto_catch_phase in ("idle", "ready"):
            if (
                self._auto_catch_phase == "ready"
                and not self._auto_catch_matches_filters(state, wild_name)
            ):
                self._auto_catch_phase = "finished"
                return True
            wild_hp = state.get("enemyPokemonHealth")
            if (
                not isinstance(wild_hp, int)
                or isinstance(wild_hp, bool)
                or wild_hp > self.settings_window.auto_catching_hp_spin.value()
            ):
                if self._auto_catch_phase == "ready":
                    self._auto_catch_phase = "finished"
                return True
            if bag_menu_visible(state):
                # A turn change, interrupted action, or monitor restart can
                # lose the Bag-click flag while the item menu remains open.
                # Resume from the visible menu instead of pressing 2 again.
                if valid_turn:
                    self._auto_catch_attempt_turn = turn
                self._auto_catch_phase = "selecting_item"
                self._auto_catch_last_item_name = None
                self._auto_catch_seen_items.clear()
            elif valid_turn and state.get("showingBagButton") is True:
                self._start_auto_catch_item_selection(turn)
                return True
            else:
                return True

        if self._auto_catch_phase != "selecting_item":
            return True
        if not bag_menu_visible(state):
            if action_menu_visible(state):
                self._auto_catch_phase = "ready"
            return True
        action_turn = self._auto_catch_attempt_turn

        selected_item = self.settings_window.auto_catching_item_selection.currentText()
        if not selected_item.strip():
            self._auto_battle_log_once(
                action_turn or -1,
                "missing-catch-item",
                "Auto Catching requires an item name in Items to Catch.",
            )
            return True

        current_item = state.get("bagItemName")
        if not isinstance(current_item, str) or not current_item.strip():
            return True

        if self._normalise_item_name(current_item) == self._normalise_item_name(
            selected_item
        ):
            if self._submit_battle_action(
                "bag",
                action_turn,
                "Use Item Button",
                automation_name="Auto Catching",
            ):
                chat_snapshot = state.get("chatBoxMessage")
                self._auto_catch_chat_before_throw = (
                    chat_snapshot if isinstance(chat_snapshot, str) else ""
                )
                self._auto_catch_phase = "using_item"
            return True

        current_item_key = self._normalise_item_name(current_item)
        if current_item_key == self._auto_catch_last_item_name:
            return True
        if current_item_key in self._auto_catch_seen_items:
            self._auto_catch_phase = "finished"
            self._auto_battle_log_once(
                action_turn or -1,
                "catch-item-not-found",
                f"Auto Catching could not find the exact item '{selected_item}' "
                "after cycling through the available bag items.",
            )
            return True
        if self._submit_battle_action(
            "bag",
            action_turn,
            "Next Button",
            automation_name="Auto Catching",
        ):
            self._auto_catch_seen_items.add(current_item_key)
            self._auto_catch_last_item_name = current_item_key
        return True

    def _start_auto_catch_item_selection(self, turn: int) -> None:
        if self._submit_battle_action(
            "bag",
            turn,
            "Bag Button",
            automation_name="Auto Catching",
        ):
            self._auto_catch_attempt_turn = turn
            self._auto_catch_phase = "opening_bag"
            self._auto_catch_last_item_name = None
            self._auto_catch_seen_items.clear()

    def _auto_catch_has_new_catch_confirmation(self, chat_snapshot: str) -> bool:
        if not RealtimeRegionMonitor._chat_confirms_catch(chat_snapshot):
            return False
        confirmation_pattern = re.compile(
            r"congratulations,\s*you\s+caught\s+the\s+opposing\s+"
            r"([^.!?\r\n]+)",
            re.IGNORECASE,
        )
        current_confirmations = Counter(
            re.sub(r"\s+", " ", match.group(1)).strip().casefold()
            for match in confirmation_pattern.finditer(chat_snapshot)
        )
        previous_confirmations = Counter(
            re.sub(r"\s+", " ", match.group(1)).strip().casefold()
            for match in confirmation_pattern.finditer(
                self._auto_catch_chat_before_throw or ""
            )
        )
        return any(
            count > previous_confirmations[name]
            for name, count in current_confirmations.items()
        )

    def _auto_catch_matches_filters(
        self,
        state: dict[str, object],
        wild_name: str,
    ) -> bool:
        filters: list[bool] = []
        if (
            self.settings_window.stop_for_desired_pokemon_toggle.isChecked()
            and self.settings_window.auto_catching_desired_toggle.isChecked()
        ):
            selected_names = self.settings_window.desired_pokemon_input.text()
            if not selected_names.strip():
                turn = state.get("turn")
                self._auto_battle_log_once(
                    turn if isinstance(turn, int) and not isinstance(turn, bool) else -1,
                    "missing-catch-desired-pokemon",
                    "Auto Catching's Desired Pokemon filter is enabled but no "
                    "Pokemon are selected.",
                )
            filters.append(
                bool(self._match_selected_pokemon_name(
                    wild_name, selected_names, nature_match=self._encounter_nature_match(state),
                ))
            )
        if (
            self.settings_window.stop_for_shiny_toggle.isChecked()
            and self.settings_window.auto_catching_shiny_toggle.isChecked()
        ):
            filters.append(state.get("wildPokemonIsShiny") is True)
        if (
            self.settings_window.stop_for_elite_toggle.isChecked()
            and self.settings_window.auto_catching_elite_toggle.isChecked()
        ):
            is_elite = state.get("wildPokemonIsElite") is True
            selected_names = self.settings_window.elite_pokemon_input.text()
            filters.append(
                is_elite
                and (
                    not parse_pokemon_targets(selected_names)
                    or match_pokemon_target(
                        wild_name, selected_names,
                        nature_match=self._encounter_nature_match(state), exact_species=True,
                    ) is not None
                )
            )
        return any(filters) if filters else True

    def _is_stop_battle_encounter(self, state: dict[str, object]) -> bool:
        return (
            self._matched_desired_pokemon(state) is not None
            or (
                self.settings_window.stop_for_shiny_toggle.isChecked()
                and state.get("wildPokemonIsShiny") is True
            )
            or self._should_stop_for_elite_pokemon(state)
        )

    @staticmethod
    def _match_selected_pokemon_name(
        wild_name: str,
        selected_names: str,
        *,
        nature_match: object = False,
    ) -> str | None:
        target = match_pokemon_target(wild_name, selected_names, nature_match=nature_match)
        return target.name.casefold() if target is not None else None

    @staticmethod
    def _selected_target_requires_nature_match(selected_names: str, species: str) -> bool:
        return any(
            target.nature_match_only and target.name.casefold() == species.casefold()
            for target in parse_pokemon_targets(selected_names)
        )

    def _update_encounter_nature_match(self, state: dict[str, object]) -> None:
        if state.get("onBattleMode") is not True:
            if self._fishing_battle_active is False:
                self._nature_matched_encounter_identity = None
            return
        identity = self._wild_pokemon_identity(state.get("wildPokemonName"))
        if identity and identity != self._nature_matched_encounter_identity:
            self._nature_matched_encounter_identity = None
        if identity and state.get("wildPokemonNatureMatch") is True:
            # Detection remains live in the monitor and Region Overlay. Once
            # proven, a target stays protected for this encounter even if a
            # fainted replacement or manual switch removes the green S.
            self._nature_matched_encounter_identity = identity

    def _encounter_nature_match(self, state: dict[str, object]) -> bool | None:
        if (
            state.get("onBattleMode") is True
            and self._nature_matched_encounter_identity
            and self._wild_pokemon_identity(state.get("wildPokemonName"))
            == self._nature_matched_encounter_identity
        ):
            return True
        evidence = state.get("wildPokemonNatureMatch")
        return evidence if type(evidence) is bool else None

    def _awaiting_target_nature_match(self, state: dict[str, object]) -> bool:
        if state.get("onBattleMode") is not True or self._encounter_nature_match(state) is not None:
            return False
        wild_name = state.get("wildPokemonName")
        for enabled, selected_names, exact_species in (
            (
                self.settings_window.stop_for_desired_pokemon_toggle.isChecked(),
                self.settings_window.desired_pokemon_input.text(), False,
            ),
            (
                self.settings_window.stop_for_elite_toggle.isChecked()
                and state.get("wildPokemonIsElite") is True,
                self.settings_window.elite_pokemon_input.text(), True,
            ),
        ):
            if not enabled:
                continue
            if not self._wild_pokemon_identity(wild_name):
                if any(target.nature_match_only for target in parse_pokemon_targets(selected_names)):
                    return True
            else:
                if match_pokemon_target(
                    wild_name, selected_names, nature_match=False, exact_species=exact_species,
                ) is not None:
                    continue
                possible_target = match_pokemon_target(
                    wild_name, selected_names, nature_match=True, exact_species=exact_species,
                )
                if possible_target is not None and possible_target.nature_match_only:
                    return True
        return False

    @staticmethod
    def _normalise_item_name(name: str) -> str:
        return re.sub(r"\s+", " ", name).strip().casefold()

    def _reset_auto_catching_state(self) -> None:
        self._auto_catch_phase = "idle"
        self._auto_catch_identity = None
        self._auto_catch_attempt_turn = None
        self._auto_catch_last_item_name = None
        self._auto_catch_chat_before_throw = None
        self._auto_catch_seen_items.clear()

    def _on_auto_catching_toggled(self, enabled: bool) -> None:
        del enabled
        self._reset_auto_catching_state()

    def _stop_auto_run_for_encounter(
        self,
        state: dict[str, object],
    ) -> bool:
        matched_name = self._matched_desired_pokemon(state)
        if matched_name is not None:
            synchronized = self._selected_target_requires_nature_match(
                self.settings_window.desired_pokemon_input.text(), matched_name,
            )
            reason = (
                f"wild {matched_name}"
                + (" with green S (Synchronize)" if synchronized else "")
                + " detected"
            )
        elif (
            self.settings_window.stop_for_shiny_toggle.isChecked()
            and state.get("wildPokemonIsShiny") is True
        ):
            reason = "shiny wild Pokemon detected"
        elif self._should_stop_for_elite_pokemon(state):
            synchronized = self._selected_target_requires_nature_match(
                self.settings_window.elite_pokemon_input.text(),
                self._wild_pokemon_identity(state.get("wildPokemonName")),
            )
            reason = (
                "Elite wild Pokemon "
                + ("with green S (Synchronize) " if synchronized else "")
                + "detected"
            )
        else:
            return False

        self._auto_run_timer.stop()
        self._auto_run_pending = False
        self._auto_run_clicked = True
        self.region_test_log_window.append_entry(
            "INFO",
            f"Auto Run stopped for this encounter: "
            f"{reason}. Auto Run will resume for the next encounter.",
        )
        return True

    def _click_auto_run(self) -> None:
        was_pending = self._auto_run_pending
        self._cancel_queued_auto_run()
        state = self._latest_realtime_state
        if (
            not was_pending
            or not self._realtime_capture_requested
            or self._pending_battle_action is not None
            or not self._auto_run_allowed(state)
            or state.get("clickedRunButton") is True
            or not self._auto_run_ready(state)
        ):
            return
        if self._stop_auto_run_for_encounter(state):
            return

        turn = state.get("turn")
        valid_turn = turn if isinstance(turn, int) and not isinstance(turn, bool) else None
        self._submit_battle_action(
            "run",
            valid_turn,
            "Run Button",
            automation_name="Auto Run",
        )

    @staticmethod
    def _auto_run_ready(state: dict[str, object]) -> bool:
        turn = state.get("turn")
        return (
            state.get("onBattleMode") is True
            and state.get("showingSpecialPokemonMessage", False) is False
            and isinstance(turn, int)
            and not isinstance(turn, bool)
            and turn >= 1
            and state.get("showingRunButton") is True
        )

    def _on_auto_run_toggled(self, enabled: bool) -> None:
        if not enabled and not self._auto_run_enabled():
            self._cancel_queued_auto_run()
            attempt = self._pending_battle_action
            if attempt is not None and attempt.automation_name == "Auto Run":
                self._cancel_pending_battle_action()

    def _matched_desired_pokemon(
        self,
        state: dict[str, object],
    ) -> str | None:
        if not self.settings_window.stop_for_desired_pokemon_toggle.isChecked():
            return None

        normalised_wild_name = self._normalise_wild_pokemon_name(
            state.get("wildPokemonName")
        )
        return self._match_selected_pokemon_name(
            normalised_wild_name, self.settings_window.desired_pokemon_input.text(),
            nature_match=self._encounter_nature_match(state),
        )

    def _should_stop_for_elite_pokemon(
        self,
        state: dict[str, object],
    ) -> bool:
        if (
            not self.settings_window.stop_for_elite_toggle.isChecked()
            or state.get("wildPokemonIsElite") is not True
        ):
            return False

        selected_names = self.settings_window.elite_pokemon_input.text()
        if not parse_pokemon_targets(selected_names):
            return True

        wild_name = self._normalise_wild_pokemon_name(
            state.get("wildPokemonName")
        )
        return match_pokemon_target(
            wild_name, selected_names, nature_match=self._encounter_nature_match(state),
            exact_species=True,
        ) is not None

    def _is_player_faint_confirmed(self, state: dict[str, object]) -> bool:
        if state.get("onBattleMode") is not True:
            self._last_active_pokemon_name = None
            return False

        pokemon_name = state.get("yourPokemonName")
        if isinstance(pokemon_name, str) and pokemon_name.strip():
            self._last_active_pokemon_name = pokemon_name.strip()

        chat_message = state.get("chatBoxMessage")
        if not isinstance(chat_message, str):
            return False
        for line in chat_message.splitlines():
            if re.search(
                r"\b(?:opposing|opponent(?:'s)?|enemy|wild)\b.{0,80}\bfainted\b",
                line,
                re.IGNORECASE,
            ):
                continue
            if re.search(
                r"\byour\s+pok[eé]mon\b.{0,30}\bfainted\b",
                line,
                re.IGNORECASE,
            ):
                return True
            if self._last_active_pokemon_name and re.search(
                rf"(?<!\w){re.escape(self._last_active_pokemon_name)}\s+fainted\b",
                line,
                re.IGNORECASE,
            ):
                return True
        return False

    def _handle_auto_switch(
        self,
        state: dict[str, object],
        *,
        faint_confirmed: bool = False,
    ) -> bool:
        health = state.get("yourPokemonHealth")
        if health is None and faint_confirmed:
            health = 0
        decision = self._auto_switch_controller.update(
            enabled=self.settings_window.auto_switch_fainted_pokemon_toggle.isChecked(),
            on_battle_mode=state.get("onBattleMode") is True,
            health=health,
            turn=state.get("turn"),
            now=time.monotonic(),
        )
        if decision.exhausted:
            self.region_test_log_window.append_entry(
                "WARNING",
                "Auto Switch has no remaining Pokemon slots to select.",
            )
        if decision.slot is None:
            return decision.handled

        turn = decision.turn
        if turn is None:
            return decision.handled
        slot = decision.slot
        submitted = self._submit_battle_action(
            "pokemon-team",
            turn,
            f"Pokemon Team Slot {slot}",
            automation_name="Auto Switch",
        )
        if not submitted:
            self._auto_switch_controller.record_click(slot, turn, succeeded=False)
        return True

    def _reset_auto_switch_state(self) -> None:
        self._auto_switch_controller.reset()

    def _submit_battle_action(
        self,
        feature: str,
        turn: int | None,
        action_name: str,
        *,
        automation_name: str = "Auto Battle",
        settle_delay: float | None = None,
    ) -> bool:
        """Submit one input attempt; only subsequent OCR can complete it."""
        if self._pending_battle_action is not None:
            return False
        if not self._send_battle_action(
            feature, turn, action_name, automation_name=automation_name,
            settle_delay=settle_delay,
        ):
            return False
        self._last_battle_wait_reason = None
        self._pending_battle_action = PendingBattleAction(
            feature, turn, action_name, automation_name, self._last_input_completed_at,
            dict(self._latest_realtime_state), settle_delay,
        )
        return True

    def _send_battle_action(
        self,
        feature: str,
        turn: int | None,
        action_name: str,
        *,
        automation_name: str,
        settle_delay: float | None,
    ) -> bool:
        if (
            getattr(self, "_region_placement_active", False) is True
            or getattr(self, "_region_dialog_active", False) is True
            or getattr(self, "_region_selection_active", False) is True
        ):
            return False
        display_turn = turn if turn is not None else -1
        use_mouse = self.settings_window.use_mouse_clicking_toggle.isChecked()
        number_key = BATTLE_ACTION_KEYS.get((feature, action_name.casefold()))
        if number_key is None:
            self._auto_battle_log_once(
                display_turn,
                f"unsupported-action-{feature}-{action_name}",
                f"{automation_name} cannot select {action_name}: unsupported battle action.",
            )
            return False
        if self._target_window_id <= 0 or (
            use_mouse and not self._target_geometry.isValid()
        ):
            self._auto_battle_log_once(
                display_turn,
                f"game-window-unavailable-{action_name}",
                f"{automation_name} cannot select {action_name}: the game window "
                "is unavailable.",
            )
            return False
        input_description = "click" if use_mouse else f"key {number_key}"
        try:
            if use_mouse:
                region = region_for_role(
                    tuple(self.region_window.regions), feature, action_name,
                )
                if region is None:
                    self._auto_battle_log_once(
                        display_turn,
                        f"missing-click-region-{feature}-{action_name}",
                        f"{automation_name} cannot click {action_name}: configure "
                        "its region first.",
                    )
                    return False
                self._auto_battle_input.click_region(
                    self._target_window_id,
                    region,
                    self._target_geometry,
                    settle_delay=settle_delay,
                )
            else:
                self._auto_battle_input.tap_key(
                    self._target_window_id, ord(number_key),
                    duration_seconds=0.05, extended=False,
                )
        except (OSError, RuntimeError, ValueError) as error:
            self._auto_battle_log_once(
                display_turn,
                f"input-failed-{input_description}-{action_name}",
                f"{automation_name} could not send {input_description} for "
                f"{action_name}: {error}",
            )
            return False

        self._last_input_completed_at = time.monotonic()
        if self._ev_training_enabled():
            self._ev_defeat_tracker.record_action(feature, action_name)
        monitor = self._realtime_region_monitor
        if monitor is not None and monitor.window_handle == self._target_window_id:
            monitor.record_automated_action(feature, action_name)

        turn_message = f" on turn {turn}" if turn is not None else ""
        self.region_test_log_window.append_entry(
            "INFO", f"{automation_name} sent {input_description} for {action_name}{turn_message}; "
            "waiting for the game response."
        )
        return True

    def _update_pending_battle_action(self, state: dict[str, object]) -> bool:
        attempt = self._pending_battle_action
        if attempt is None:
            return False
        enabled = {
            "Auto Battle": self.settings_window.auto_battle_toggle,
            "Auto Run": self.settings_window.auto_run_toggle,
            "Auto Catching": self.settings_window.auto_catching_toggle,
            "Auto Switch": self.settings_window.auto_switch_fainted_pokemon_toggle,
        }[attempt.automation_name].isChecked()
        if attempt.automation_name == "Auto Run":
            enabled = self._auto_run_enabled()
        if attempt.automation_name in ("Auto Battle", "Auto Run") and self._ev_training_actions_paused():
            enabled = False
        stopped_encounter = (
            attempt.automation_name in ("Auto Battle", "Auto Run")
            and state.get("onBattleMode") is True
            and (
                self._is_stop_battle_encounter(state)
                or (
                    self.settings_window.auto_catching_toggle.isChecked()
                    and self._auto_catch_phase != "idle"
                )
            )
        )
        needs_switch = (
            attempt.feature != "pokemon-team"
            and self.settings_window.auto_switch_fainted_pokemon_toggle.isChecked()
            and state.get("onBattleMode") is True
            and (
                state.get("yourPokemonHealth") == 0
                or (
                    state.get("yourPokemonHealth") is None
                    and self._is_player_faint_confirmed(state)
                )
            )
        )
        if not enabled or stopped_encounter or needs_switch:
            outcome = "cancelled"
        else:
            target = (
                self._auto_battle_target_matches(state)
                if state.get("onBattleMode") is True
                and attempt.automation_name in ("Auto Battle", "Auto Run")
                else True
            )
            if target is None:
                self._log_battle_wait("readable Auto Battle target evidence before retrying the last action.")
                return True
            obsolete = state.get("onBattleMode") is True and (
                (attempt.automation_name == "Auto Battle" and target is False)
                or (attempt.automation_name == "Auto Run" and target is True)
            )
            if obsolete:
                self._cancel_pending_battle_action()
                return False
            frame_at = state.get("captureStartedAt")
            outcome = attempt.observe(
                state,
                float(frame_at) if isinstance(frame_at, (int, float)) else time.monotonic(),
                retry_delay_seconds=(
                    ACTION_RETRY_SECONDS
                    if (
                        self.settings_window.use_mouse_clicking_toggle.isChecked()
                        or attempt.automation_name == "Auto Switch"
                    )
                    else 0.0
                ),
            )
        if outcome == "waiting":
            return True
        if outcome == "retry":
            self._auto_battle_log_once(
                attempt.turn if attempt.turn is not None else -1,
                f"retry-{attempt.feature}-{attempt.action_name}",
                f"{attempt.automation_name}: {attempt.action_name} has not "
                "changed the game menu; retrying the action.",
            )
            if self._send_battle_action(
                attempt.feature, attempt.turn, attempt.action_name,
                automation_name=attempt.automation_name, settle_delay=attempt.settle_delay,
            ):
                attempt.mark_submitted(self._last_input_completed_at)
            else:
                attempt.last_attempted_at = time.monotonic()
            return True

        role = attempt.action_name.casefold()
        if outcome == "cancelled":
            self._cancel_pending_battle_action()
            return False

        self._pending_battle_action = None
        turn_message = f" on turn {attempt.turn}" if attempt.turn is not None else ""
        self.region_test_log_window.append_entry(
            "INFO", f"{attempt.automation_name} confirmed {attempt.action_name}"
            f"{turn_message} from the game response.",
        )
        if role == "fight button":
            self._auto_battle_fight_clicked = True
        elif role == "run button":
            self._auto_run_clicked = True
        elif attempt.feature == "moves":
            self._auto_battle_move_clicked = True
        elif attempt.automation_name == "Auto Catching":
            if role == "bag button":
                self._auto_catch_phase = "selecting_item"
            elif role == "use item button":
                self._auto_catch_phase = "item_used"
        elif attempt.automation_name == "Auto Switch":
            slot = int(attempt.action_name.rsplit(" ", 1)[1])
            self._auto_switch_controller.record_click(slot, attempt.turn, succeeded=True)
        return False

    def _cancel_pending_battle_action(self) -> None:
        attempt = self._pending_battle_action
        self._pending_battle_action = None
        if attempt is not None:
            if attempt.feature == "moves":
                self._pending_battle_move = None
            if attempt.automation_name == "Auto Catching":
                self._auto_catch_phase = "ready"

    def _reset_battle_actions(self) -> None:
        self._pending_battle_action = None
        self._last_input_completed_at = 0.0
        self._ev_defeat_tracker.reset()
        self._latest_realtime_state = {}
        self.region_window.update_synchronize_state({})
        self._pending_battle_move = None
        self._auto_battle_turn = None
        self._auto_battle_fight_clicked = False
        self._auto_battle_move_clicked = False
        self._auto_run_timer.stop()
        self._auto_run_pending = False
        self._auto_run_clicked = False
        self._auto_battle_stopped_for_desired_pokemon = None
        self._nature_matched_encounter_identity = None
        self._auto_battle_reported.clear()
        self._last_battle_wait_reason = None
        self._reset_auto_catching_state()
        self._reset_auto_switch_state()
        self._special_pokemon_message_controller.reset()
        self._current_encounter_name = None
        self._current_encounter_category = None

    def _log_battle_wait(self, reason: str) -> None:
        if not any(toggle.isChecked() for toggle in (
            self.settings_window.auto_battle_toggle,
            self.settings_window.auto_run_toggle,
            self.settings_window.auto_catching_toggle,
            self.settings_window.auto_switch_fainted_pokemon_toggle,
        )):
            self._last_battle_wait_reason = None
            return
        if reason == self._last_battle_wait_reason:
            return
        self._last_battle_wait_reason = reason
        self.region_test_log_window.append_entry("INFO", f"Battle automation waiting: {reason}")

    def _auto_battle_log_once(
        self,
        turn: int,
        reason: str,
        message: str,
    ) -> None:
        key = (turn, reason)
        if key in self._auto_battle_reported:
            return
        self._auto_battle_reported.add(key)
        self.region_test_log_window.append_entry("WARNING", message)

    def _update_battle_move_history(self, state: dict[str, object]) -> None:
        pending_move = self._pending_battle_move
        if pending_move is None:
            return

        player, opponent, move_name, move_turn, starting_health = pending_move
        current_opponent = state.get("wildPokemonName")
        if isinstance(current_opponent, str):
            current_identity = self._wild_pokemon_identity(current_opponent)
            if current_identity and current_identity != opponent:
                self._pending_battle_move = None
                return

        current_turn = state.get("turn")
        battle_ended = state.get("onBattleMode") is not True
        turn_advanced = (
            isinstance(current_turn, int)
            and not isinstance(current_turn, bool)
            and current_turn != move_turn
        )
        enemy_health = state.get("enemyPokemonHealth")
        if (
            self._battle_history is None
            or not isinstance(enemy_health, int)
            or isinstance(enemy_health, bool)
        ):
            if battle_ended or self._battle_history is None:
                self._pending_battle_move = None
            return

        if enemy_health >= starting_health:
            if battle_ended or turn_advanced or enemy_health > starting_health:
                self._pending_battle_move = None
            return

        self._pending_battle_move = None

        damage = starting_health - enemy_health
        try:
            count, average_damage = self._battle_history.record_damage(
                player,
                opponent,
                move_name,
                damage,
            )
        except (OSError, ValueError) as error:
            self.region_test_log_window.append_entry(
                "ERROR",
                f"Could not save battle move history: {error}",
            )
            return
        self.region_test_log_window.append_entry(
            "INFO",
            f"Battle history recorded {move_name} vs {opponent}: "
            f"{damage}% damage ({count} observation(s), "
            f"{average_damage:.1f}% average).",
        )

    def stop_realtime_region_capture(
        self,
        wait_timeout_ms: int = 0,
        clear_request: bool = True,
    ) -> None:
        self._auto_walk_controller.stop()
        self._reset_battle_actions()
        self._stop_auto_fishing_monitor(wait_timeout_ms=wait_timeout_ms)
        if clear_request:
            self._realtime_capture_requested = False
            self._realtime_monitor_start_attempted = False
            self.pokemon_encounter_summary_window.stop_session_timer()
            self.set_bot_state(self._bot_running, self._bot_transitioning)
        monitor = self._realtime_region_monitor
        if monitor is None or not monitor.isRunning():
            return

        monitor.requestInterruption()
        self.region_test_log_window.append_entry(
            "INFO", "Realtime capture stop requested."
        )
        if wait_timeout_ms > 0 and not monitor.wait(wait_timeout_ms):
            self.region_test_log_window.append_entry(
                "WARNING",
                "Realtime capture is finishing the current OCR operation; "
                "it will stop before the next scan.",
            )

    def _on_realtime_monitor_finished(
        self, monitor: RealtimeRegionMonitor
    ) -> None:
        self._auto_walk_controller.stop()
        if self._realtime_region_monitor is monitor:
            self._realtime_region_monitor = None
            self._realtime_monitor_start_attempted = False
            self._reset_battle_actions()
            self._reset_fishing_battle_state()

    def _forget_region_test_thread(self, thread: QThread) -> None:
        self._region_test_runs = [
            run for run in self._region_test_runs if run[0] is not thread
        ]

    def _begin_region_selection(self, feature: str) -> None:
        if self._region_placement_active or self._region_dialog_active or self._region_selection_active:
            return
        if not self._game_window_visible or not self._target_geometry.isValid():
            return

        self._pending_region_feature = feature
        self._region_selection_active = True
        self._pause_region_configuration()
        self._apply_visibility()
        if not self._region_selection_active:
            return
        self._window_tracker.set_region_selection_window(
            int(self.region_selector.winId())
        )
        self.region_selector.begin_selection(self._target_geometry)

    def _on_region_selection_completed(self, geometry: QRect) -> None:
        feature = self._pending_region_feature
        self.region_selector.hide()
        self._window_tracker.set_region_selection_window(None)
        # Keep input paused continuously from drawing through role assignment.
        # The dialog's closing signal must not resume the old capture layout.
        try:
            if feature is not None and self._game_window_visible:
                self.region_window.prompt_region_name(feature, geometry)
        finally:
            self._restore_after_region_selection()

    def _on_region_selection_cancelled(self) -> None:
        self._restore_after_region_selection()

    def _restore_after_region_selection(self) -> None:
        self._region_selection_active = False
        self._pending_region_feature = None
        self.region_selector.hide()
        self._window_tracker.set_region_selection_window(None)
        self._resume_region_configuration()
        self._apply_visibility()

    def _sync_region_window(self) -> None:
        if (
            self._region_window_requested
            and self._game_window_visible
            and self._user_requested_visible
            and not self._region_selection_active
            and self._target_geometry.isValid()
        ):
            self.region_window.show_for_game(
                self._target_geometry, ui_geometry=self._target_ui_geometry,
            )
        else:
            self.region_window.hide()

    def _on_region_closed(self) -> None:
        self._region_window_requested = False

    def _on_region_visibility_changed(self, visible: bool) -> None:
        button = self.buttons.get("select")
        if button is not None:
            button.setProperty("panelOpen", visible)
            button.style().unpolish(button)
            button.style().polish(button)
        self._window_tracker.set_region_window(
            int(self.region_window.winId()) if visible else None
        )

    def _show_settings(self) -> None:
        if self.settings_window.isVisible():
            self.settings_window.hide()
        elif self._game_window_visible:
            self.settings_window.show_at(self._target_geometry, self.geometry())

    def _on_settings_visibility_changed(self, visible: bool) -> None:
        button = self.buttons.get("settings")
        if button is not None:
            button.setProperty("panelOpen", visible)
            button.style().unpolish(button)
            button.style().polish(button)
        self._window_tracker.set_auxiliary_window(
            int(self.settings_window.winId()) if visible else None
        )
        self._window_tracker.set_overlay_window(
            int(self.winId()) if visible else None
        )

    def _on_select(self) -> None:
        if self._region_window_requested:
            self._region_window_requested = False
        elif self._game_window_visible:
            self._region_window_requested = True
        self._sync_region_window()

    def _on_play(self) -> None:
        if (
            self._bot_transitioning or self._realtime_capture_requested
            or self._region_placement_active or self._region_dialog_active
            or self._region_selection_active
        ):
            return
        self.pokemon_encounter_summary_window.start_session_timer()
        self._realtime_capture_requested = True
        self._realtime_monitor_start_attempted = False
        self.set_bot_state(self._bot_running, self._bot_transitioning)
        self._start_realtime_region_capture()
        self.start_requested.emit()

    def _on_stop(self) -> None:
        if self._bot_running and not self._bot_transitioning:
            self.stop_realtime_region_capture(
                wait_timeout_ms=5000,
                clear_request=True,
            )

    def _on_auto_walk_toggled(self, enabled: bool) -> None:
        del enabled
        self._sync_auto_walk()

    def _sync_auto_walk(self) -> None:
        state = self._latest_realtime_state
        if (
            not self.settings_window.auto_walk_toggle.isChecked()
            or not self._realtime_capture_requested
            or self._ev_training_complete()
            or getattr(self, "_region_placement_active", False) is True
            or getattr(self, "_region_dialog_active", False) is True
            or getattr(self, "_region_selection_active", False) is True
        ):
            self._auto_walk_controller.stop()
            return
        popup_visible = state.get("showingSpecialPokemonMessage", False)
        if popup_visible is not False or self._special_pokemon_message_controller.blocked:
            if popup_visible is True:
                reason = "paused: Special Pokemon Message is visible."
            elif popup_visible is not False:
                reason = "paused: Special Pokemon Message OCR is uncertain."
            else:
                reason = "paused: confirming Special Pokemon Message dismissal."
            self._auto_walk_controller.stop(reason=reason)
            return
        # The same confirmed battle-end state used by fishing prevents a brief
        # OCR dropout from resuming overworld movement during an encounter.
        if self._fishing_battle_active is True and state.get("onBattleMode") is False:
            self._auto_walk_controller.stop(
                reason="paused: waiting for battle-end confirmation (onBattleMode=false).",
            )
            return
        battle_mode = (
            True if self._fishing_battle_active is True else state.get("onBattleMode")
        )
        self._auto_walk_controller.update(
            enabled=True,
            on_battle_mode=battle_mode,
            window_handle=self._target_window_id,
            direction=self.settings_window.auto_walk_direction_selection.currentText(),
            duration_mode=(
                self.settings_window.auto_walk_duration_mode_selection.currentText()
            ),
            default_seconds=self.settings_window.auto_walk_duration_spin.value(),
            minimum_seconds=self.settings_window.auto_walk_min_duration_spin.value(),
            maximum_seconds=self.settings_window.auto_walk_max_duration_spin.value(),
        )

    def _classify_encounter(
        self, pokemon_name: str, state: dict[str, object] | None = None,
    ) -> str | None:
        if state is None:
            state = self._latest_realtime_state
        if state.get("wildPokemonIsShiny") is True:
            return "shiny"
        if state.get("wildPokemonIsElite") is True:
            return "elite"
        if not (
            self.settings_window.stop_for_desired_pokemon_toggle.isChecked()
            or self.settings_window.desired_pokemon_sound_toggle.isChecked()
        ):
            return None

        return "desired" if match_pokemon_target(
            pokemon_name, self.settings_window.desired_pokemon_input.text(),
            nature_match=self._encounter_nature_match({**state, "wildPokemonName": pokemon_name}),
        ) is not None else None

    def _play_encounter_sound(self, category: str) -> None:
        sound_settings = {
            "desired": (
                self.settings_window.desired_pokemon_sound_toggle,
                self.settings_window.desired_pokemon_sound_path,
            ),
            "shiny": (
                self.settings_window.shiny_pokemon_sound_toggle,
                self.settings_window.shiny_pokemon_sound_path,
            ),
            "elite": (
                self.settings_window.elite_pokemon_sound_toggle,
                self.settings_window.elite_pokemon_sound_path,
            ),
        }
        toggle, path_input = sound_settings[category]
        if not toggle.isChecked():
            return
        sound_path = path_input.text().strip()
        if not sound_path:
            self.region_test_log_window.append_entry(
                "WARNING",
                f"The {category} Pokemon sound is enabled but no audio file is selected.",
            )
            return
        if not Path(sound_path).is_file():
            self.region_test_log_window.append_entry(
                "ERROR",
                f"The selected {category} Pokemon sound file is unavailable: "
                f"{sound_path}",
            )
            return

        cached_player = self._ringtone_players.get(category)
        if cached_player is None or cached_player[0] != sound_path:
            audio_output = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(audio_output)
            player.setLoops(1)
            player.errorOccurred.connect(
                lambda error, error_string, category=category: self._on_ringtone_error(
                    category,
                    error.name,
                    error_string,
                )
            )
            player.setSource(QUrl.fromLocalFile(sound_path))
            self._ringtone_players[category] = (
                sound_path,
                player,
                audio_output,
            )
        else:
            player = cached_player[1]
        player.stop()
        player.setPosition(0)
        player.play()

    def _on_ringtone_error(
        self,
        category: str,
        error_name: str,
        error_string: str,
    ) -> None:
        self.region_test_log_window.append_entry(
            "ERROR",
            f"Could not play the selected {category} Pokemon audio: "
            f"{error_name}: "
            f"{error_string or 'unsupported or invalid audio file'}.",
        )

    @staticmethod
    def _normalise_wild_pokemon_name(name: object) -> str:
        return normalise_wild_pokemon_name(name)

    @staticmethod
    def _wild_pokemon_identity(name: object) -> str:
        return pokemon_species_name(name).casefold()

    def _on_desired_pokemon_stop_toggled(self, enabled: bool) -> None:
        if not enabled:
            return
        if any(
            name.strip()
            for name in self.settings_window.desired_pokemon_input.text().split(",")
        ):
            return
        self.region_test_log_window.append_entry(
            "WARNING",
            "Choose at least one desired Pokemon in Advanced Desired Pokemon "
            "Settings. Auto Battle will continue until a Pokemon is selected.",
        )

    def _log_auto_walk(self, level: str, message: str) -> None:
        self.region_test_log_window.append_entry(level, message)

    def _on_settings(self) -> None:
        self._show_settings()
