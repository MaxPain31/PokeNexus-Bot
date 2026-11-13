from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hotkeys.hotkey_manager import GlobalHotkeyManager
from launcher.executable_picker import ExecutablePicker
from launcher.process_controller import ProcessController
from launcher.settings_store import AppSettings, SettingsStore
from launcher.styles.style_loader import load_stylesheet
from launcher.system_tray import SystemTrayController
from launcher.widgets.log_viewer import LogViewer
from models.region_config import SavedRegion
from overlay.overlay_window import OverlayWindow
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


ASSETS_DIR = application_path("assets")


class MainWindow(QWidget):
    """Main launcher window for the gaming bot."""

    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        super().__init__()
        application = QApplication.instance()
        if application is not None:
            application.setApplicationName("Game Bot")
            application.setOrganizationName("Poke Nexus")

        self.settings_store = settings_store or SettingsStore()
        self.setObjectName("main_window")
        self.setWindowTitle("Game Bot")
        self.setWindowIcon(
            load_svg_icon(ASSETS_DIR / "nexus-mark.svg", QSize(32, 32))
        )
        self.setFixedWidth(460)

        self.selected_executable: Path | None = None
        self._launcher_running = False
        self._launch_transition: str | None = None
        self._hidden_to_tray = False
        self._restore_when_process_exits = False
        self._legacy_synchronize_pokemon = ""
        self._setup_style()
        self._setup_ui()
        self._setup_controllers()
        self.overlay_window = OverlayWindow()
        self._load_saved_settings()
        self._sync_launcher_state()
        self._update_session_overview()
        self._connect_signals()
        self._fit_height_to_contents()

    def _setup_style(self) -> None:
        self.setStyleSheet(load_stylesheet("main_window.qss"))

    def _setup_ui(self) -> None:
        brand_mark = QLabel()
        brand_mark.setObjectName("brand_mark")
        brand_mark.setFixedSize(48, 48)
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setPixmap(self.windowIcon().pixmap(QSize(30, 30)))

        title = QLabel("Game Bot")
        title.setObjectName("title")

        subtitle = QLabel("Your Poke Nexus companion")
        subtitle.setObjectName("subtitle")

        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(brand_mark)
        header.addLayout(brand_text)
        header.addStretch()

        session_card = QFrame()
        session_card.setObjectName("session_card")
        session_layout = QVBoxLayout(session_card)
        session_layout.setContentsMargins(16, 14, 16, 14)
        session_layout.setSpacing(8)
        session_heading = QLabel("SESSION OVERVIEW")
        session_heading.setObjectName("eyebrow")
        session_layout.addWidget(session_heading)

        self.status_indicator = QLabel("●")
        self.status_indicator.setObjectName("status_indicator")
        self.status_indicator.setFixedWidth(12)
        self.status_label = QLabel()
        self.status_label.setObjectName("status_text")
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        status_row.addWidget(self.status_indicator)
        status_row.addWidget(self.status_label, 1)
        session_layout.addLayout(status_row)

        self.status_detail = QLabel()
        self.status_detail.setObjectName("status_detail")
        self.status_detail.setWordWrap(True)
        session_layout.addWidget(self.status_detail)

        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.regions_count_label = self._add_overview_metric(
            metrics, "Saved regions"
        )
        self.automation_count_label = self._add_overview_metric(
            metrics, "Enabled modes"
        )
        self.encounter_count_label = self._add_overview_metric(
            metrics, "Encounters today"
        )
        session_layout.addLayout(metrics)

        self.executable_picker = ExecutablePicker(expected_filename="Poke Nexus.exe")
        self.start_button = QPushButton("Launch game")
        self.start_button.setObjectName("start_button")
        self.start_button.setProperty("state", "stopped")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setFixedHeight(44)
        self.start_button.setIconSize(QSize(20, 20))
        self._launch_icon = load_svg_icon(
            ASSETS_DIR / "play_circle_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            QSize(20, 20),
            fill_color="#062D2A",
        )
        self._stop_icon = load_svg_icon(
            ASSETS_DIR / "stop_circle_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            QSize(20, 20),
            fill_color="#FDA4AF",
        )

        self.settings_button = QPushButton("Bot settings")
        self.settings_button.setObjectName("launcher_settings_button")
        self.settings_button.setFixedHeight(44)
        self.settings_button.setIcon(
            load_svg_icon(
                ASSETS_DIR / "settings_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
                QSize(18, 18),
                fill_color="#C5D3E5",
            )
        )
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setToolTip(
            "Configure automation before or during your session"
        )
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.start_button, 3)
        actions.addWidget(self.settings_button, 2)

        self.log_viewer = LogViewer()
        self.log_panel = self.log_viewer.text_edit
        self._activity_card = QFrame()
        self._activity_card.setObjectName("activity_card")
        activity_layout = QVBoxLayout(self._activity_card)
        activity_layout.setContentsMargins(14, 8, 14, 8)
        activity_layout.setSpacing(8)
        activity_header = QHBoxLayout()
        activity_title = QLabel("Activity log")
        activity_title.setObjectName("activity_title")
        activity_header.addWidget(activity_title)
        activity_header.addStretch()
        activity_header.addWidget(self.log_viewer.controls)
        activity_layout.addLayout(activity_header)
        activity_layout.addWidget(self.log_panel)

        shortcuts = QWidget()
        shortcuts.setObjectName("shortcut_panel")
        shortcut_layout = QGridLayout(shortcuts)
        shortcut_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_layout.setHorizontalSpacing(18)
        shortcut_layout.setVerticalSpacing(6)
        for index, (keys, description) in enumerate(
            (
                ("Alt+S", "Show launcher"),
                ("Alt+T", "Toggle overlay"),
                ("Alt+P", "Stop automation"),
                ("Alt+X", "Quit application"),
            )
        ):
            shortcut = QWidget()
            row = QHBoxLayout(shortcut)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            keycap = QLabel(keys)
            keycap.setObjectName("shortcut_key")
            keycap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            keycap.setFixedSize(50, 22)
            description_label = QLabel(description)
            description_label.setObjectName("shortcut_description")
            row.addWidget(keycap)
            row.addWidget(description_label, 1)
            shortcut_layout.addWidget(shortcut, index // 2, index % 2)

        footer = QHBoxLayout()
        credit = QLabel("Made by MaxPain")
        credit.setObjectName("footer_text")
        tray_hint = QLabel("Closing the window keeps it in the tray")
        tray_hint.setObjectName("footer_text")
        footer.addWidget(credit)
        footer.addStretch()
        footer.addWidget(tray_hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(session_card)
        layout.addWidget(self.executable_picker)
        layout.addLayout(actions)
        layout.addWidget(self._activity_card)
        layout.addWidget(shortcuts)
        layout.addLayout(footer)

    @staticmethod
    def _add_overview_metric(layout: QHBoxLayout, caption: str) -> QLabel:
        metric = QWidget()
        metric_layout = QVBoxLayout(metric)
        metric_layout.setContentsMargins(0, 4, 0, 0)
        metric_layout.setSpacing(2)
        value = QLabel("0")
        value.setObjectName("overview_value")
        value.setAccessibleName(caption)
        label = QLabel(caption)
        label.setObjectName("overview_label")
        metric_layout.addWidget(value)
        metric_layout.addWidget(label)
        layout.addWidget(metric, 1)
        return value

    def _update_session_overview(self) -> None:
        settings = self.overlay_window.settings_window
        enabled_modes = sum(
            toggle.isChecked()
            for toggle in (
                settings.auto_battle_toggle,
                settings.auto_switch_fainted_pokemon_toggle,
                settings.auto_run_toggle,
                settings.auto_catching_toggle,
                settings.auto_fishing_toggle,
                settings.auto_walk_toggle,
            )
        )
        self.regions_count_label.setText(
            str(len(self.overlay_window.region_window.regions))
        )
        self.automation_count_label.setText(str(enabled_modes))
        self.encounter_count_label.setText(f"{self.overlay_window.pokemon_encounter_count:,}")

    def _sync_launcher_state(self) -> None:
        running = self._launcher_running
        transitioning = self._launch_transition is not None
        if transitioning:
            status = "transitioning"
            starting = self._launch_transition == "starting"
            title = "Launching game…" if starting else "Closing game…"
            detail = (
                "Please wait while Poke Nexus opens."
                if starting
                else "Finishing your session safely."
            )
        elif running:
            status = "running"
            title = "Game is running"
            detail = "Use Start on the overlay to begin automation."
        elif self.selected_executable is not None:
            status = "ready"
            title = "Ready to launch"
            detail = "Launch your game, then start automation from the overlay."
        else:
            status = "stopped"
            title = "Choose your game"
            detail = "Locate Poke Nexus.exe to get started."

        self.status_label.setText(title)
        self.status_detail.setText(detail)
        self.status_indicator.setProperty("status", status)
        self.status_indicator.setAccessibleName(title)
        self.status_indicator.style().unpolish(self.status_indicator)
        self.status_indicator.style().polish(self.status_indicator)

        self.start_button.setProperty(
            "state", "running" if running else "stopped"
        )
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        action_text = "Close game" if running else "Launch game"
        self.start_button.setText(title if transitioning else action_text)
        self.start_button.setIcon(self._stop_icon if running else self._launch_icon)
        self.start_button.setEnabled(
            not transitioning and (running or self.selected_executable is not None)
        )
        self.start_button.setAccessibleName(
            "Close Poke Nexus" if running else "Launch Poke Nexus"
        )
        self.start_button.setToolTip(
            "Close the game and stop automation"
            if running
            else "Launch the selected game"
            if self.selected_executable is not None
            else "Browse for Poke Nexus.exe first"
        )
        self.executable_picker.setEnabled(not running and not transitioning)

    def _on_settings_clicked(self) -> None:
        settings = self.overlay_window.settings_window
        settings.show_for_launcher(QRect(self.frameGeometry()))

    def _setup_controllers(self) -> None:
        self.process_controller = ProcessController(self)
        self.tray_controller = SystemTrayController(self)
        self.hotkey_manager = GlobalHotkeyManager(self)
        application = QApplication.instance()
        if application is not None:
            application.installNativeEventFilter(self.hotkey_manager)
            application.aboutToQuit.connect(self.hotkey_manager.unregister)

    def _connect_signals(self) -> None:
        self.executable_picker.executable_selected.connect(self._on_executable_selected)
        self.overlay_window.settings_window.auto_battle_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_battle_configuration_changed.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.auto_switch_fainted_pokemon_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_run_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_catching_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_fishing_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_fishing_press_f_on_missing_zone_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_fishing_reaction_lead_spin.valueChanged.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.auto_fishing_space_hold_spin.valueChanged.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.auto_catching_hp_spin.valueChanged.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.auto_catching_item_selection.currentTextChanged.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.auto_catching_desired_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_catching_shiny_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_catching_elite_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.stop_for_desired_pokemon_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.desired_pokemon_input.textChanged.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.stop_for_shiny_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.stop_for_elite_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.elite_pokemon_input.textChanged.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.sound_settings_changed.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.auto_walk_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_walk_configuration_changed.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.show_chat_battle_logs_toggle.toggled.connect(
            self._on_chat_battle_logs_toggled
        )
        self.overlay_window.settings_window.show_pokemon_encounter_summary_toggle.toggled.connect(
            self._on_encounter_summary_toggled
        )
        self.overlay_window.encounter_statistics_changed.connect(
            self._on_encounter_statistics_changed
        )
        self.overlay_window.settings_window.show_test_region_logs_toggle.toggled.connect(
            self._on_test_region_logs_toggled
        )
        self.overlay_window.settings_window.save_test_region_screenshots_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.overlay_background_transparency_spin.valueChanged.connect(
            self._save_settings
        )
        self.overlay_window.settings_window.use_mouse_clicking_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.overlay_window.settings_window.auto_close_special_pokemon_message_toggle.toggled.connect(
            self._on_settings_toggled
        )
        self.start_button.clicked.connect(self._on_start_clicked)
        self.settings_button.clicked.connect(self._on_settings_clicked)
        self.log_viewer.visibility_changed.connect(self._on_log_visibility_changed)
        self.process_controller.log_message.connect(self.log_viewer.append)
        self.process_controller.running_changed.connect(self._set_running_state)
        self.process_controller.started.connect(self._on_process_started)
        self.process_controller.warning_requested.connect(self._show_warning)
        self.overlay_window.tracking_error.connect(
            lambda message: self.log_viewer.append("ERROR", message)
        )
        self.tray_controller.restore_requested.connect(self._restore_from_tray)
        self.tray_controller.quit_requested.connect(self._quit_application)
        self.hotkey_manager.show_requested.connect(self._restore_from_tray)
        self.hotkey_manager.quit_requested.connect(self._quit_application)
        self.hotkey_manager.overlay_requested.connect(
            self.overlay_window.toggle_overlay_visibility
        )
        self.hotkey_manager.realtime_capture_stop_requested.connect(
            self.overlay_window._on_stop
        )
        self.overlay_window.start_requested.connect(self._on_start_requested)
        self.overlay_window.regions_changed.connect(self._on_regions_changed)
        self.hotkey_manager.registration_failed.connect(self._on_hotkey_error)
        if self.hotkey_manager.register():
            self.log_viewer.append(
                "INFO",
                "Global shortcuts registered: Alt+S to show, Alt+X to quit, "
                "Alt+T to show the overlay, Alt+P to stop realtime capture.",
            )

    def _on_executable_selected(self, executable: Path) -> None:
        self.selected_executable = executable
        self._sync_launcher_state()
        self._fit_height_to_contents()
        self.log_viewer.append("INFO", f"Selected application: {executable}")
        self._save_settings()

    def _load_saved_settings(self) -> None:
        try:
            settings = self.settings_store.load()
        except (OSError, ValueError) as error:
            self.log_viewer.append(
                "ERROR", f"Could not load saved settings: {error}"
            )
            return

        # The former single-choice setting is not a wild-encounter target.
        # Preserve it without importing it into either user's target list.
        self._legacy_synchronize_pokemon = settings.synchronize_pokemon
        self.overlay_window.settings_window.auto_battle_toggle.setChecked(
            settings.auto_battle
        )
        self.overlay_window.settings_window.auto_switch_fainted_pokemon_toggle.setChecked(
            settings.auto_switch_fainted_pokemon
        )
        self.overlay_window.settings_window.auto_run_toggle.setChecked(
            settings.auto_run
        )
        self.overlay_window.settings_window.auto_catching_toggle.setChecked(
            settings.auto_catching
        )
        self.overlay_window.settings_window.auto_fishing_toggle.setChecked(
            settings.auto_fishing
        )
        self.overlay_window.settings_window.auto_fishing_press_f_on_missing_zone_toggle.setChecked(
            settings.auto_fishing_press_f_on_missing_zone
        )
        self.overlay_window.settings_window.auto_fishing_reaction_lead_spin.setValue(
            settings.auto_fishing_reaction_lead_ms
        )
        self.overlay_window.settings_window.auto_fishing_space_hold_spin.setValue(
            settings.auto_fishing_space_hold_ms
        )
        self.overlay_window.settings_window.auto_catching_hp_spin.setValue(
            settings.auto_catching_wild_hp
        )
        self.overlay_window.settings_window.auto_catching_item_selection.setCurrentText(
            settings.auto_catching_item
        )
        # Restore Desired first so contradictory saved battle targets cannot
        # displace a protected species. Each picker then enforces the exclusions.
        battle_settings = self.overlay_window.settings_window
        battle_settings.specific_pokemon_input.setText("")
        battle_settings.ev_training_pokemon_input.setText("")
        self.overlay_window.settings_window.desired_pokemon_input.setText(
            settings.desired_pokemon
        )
        battle_settings.specific_pokemon_input.setText(settings.auto_battle_specific_pokemon)
        battle_settings.ev_training_pokemon_input.setText(settings.ev_training_pokemon)
        battle_settings.ev_training_category_filter.setCurrentIndex(
            battle_settings.ev_training_category_filter.findData(settings.ev_training_category)
        )
        battle_settings.set_ev_training_progress(settings.ev_training_current, settings.ev_training_targets)
        battle_settings.ev_training_pokemon_input.search_input.setText(settings.ev_training_search)
        self.overlay_window.settings_window.elite_pokemon_input.setText(
            settings.elite_pokemon_names
        )
        self.overlay_window.settings_window.desired_pokemon_sound_path.setText(
            settings.desired_pokemon_sound_path
        )
        self.overlay_window.settings_window.shiny_pokemon_sound_path.setText(
            settings.shiny_pokemon_sound_path
        )
        self.overlay_window.settings_window.elite_pokemon_sound_path.setText(
            settings.elite_pokemon_sound_path
        )
        self.overlay_window.settings_window.stop_for_desired_pokemon_toggle.setChecked(
            settings.stop_auto_battle_for_desired_pokemon
        )
        self.overlay_window.settings_window.stop_for_shiny_toggle.setChecked(
            settings.stop_auto_battle_for_shiny
        )
        self.overlay_window.settings_window.stop_for_elite_toggle.setChecked(
            settings.stop_auto_battle_for_elite
        )
        self.overlay_window.settings_window.auto_catching_desired_toggle.setChecked(
            settings.auto_catching_desired_filter
            and settings.stop_auto_battle_for_desired_pokemon
        )
        self.overlay_window.settings_window.auto_catching_shiny_toggle.setChecked(
            settings.auto_catching_shiny_filter
            and settings.stop_auto_battle_for_shiny
        )
        self.overlay_window.settings_window.auto_catching_elite_toggle.setChecked(
            settings.auto_catching_elite_filter
            and settings.stop_auto_battle_for_elite
        )
        # Apply Elite-only after the encounter rules so it also resolves an
        # incompatible saved Elite stop/catch configuration.
        battle_settings.auto_battle_mode_selection.setCurrentText(settings.auto_battle_mode)
        battle_settings.auto_battle_elite_only_toggle.setChecked(settings.auto_battle_elite_only)
        battle_settings._on_auto_battle_mode_changed()
        self.overlay_window.settings_window.desired_pokemon_sound_toggle.setChecked(
            settings.desired_pokemon_sound_enabled
        )
        self.overlay_window.settings_window.shiny_pokemon_sound_toggle.setChecked(
            settings.shiny_pokemon_sound_enabled
        )
        self.overlay_window.settings_window.elite_pokemon_sound_toggle.setChecked(
            settings.elite_pokemon_sound_enabled
        )
        self.overlay_window.settings_window.auto_walk_toggle.setChecked(
            settings.auto_walk
        )
        self.overlay_window.settings_window.auto_walk_direction_selection.setCurrentText(
            settings.auto_walk_direction
        )
        self.overlay_window.settings_window.auto_walk_duration_mode_selection.setCurrentText(
            settings.auto_walk_duration_mode
        )
        self.overlay_window.settings_window.auto_walk_duration_spin.setValue(
            settings.auto_walk_duration_seconds
        )
        self.overlay_window.settings_window.auto_walk_min_duration_spin.setValue(
            settings.auto_walk_min_duration_seconds
        )
        self.overlay_window.settings_window.auto_walk_max_duration_spin.setValue(
            settings.auto_walk_max_duration_seconds
        )
        self.overlay_window.settings_window.show_chat_battle_logs_toggle.setChecked(
            settings.show_chat_battle_logs
        )
        self.overlay_window.settings_window.show_pokemon_encounter_summary_toggle.setChecked(
            settings.show_pokemon_encounter_summary
        )
        self.overlay_window.set_encounter_statistics(
            settings.pokemon_encounter_count,
            settings.pokemon_encounter_date,
            settings.pokemon_encounter_rarity_counts,
        )
        self.overlay_window.settings_window.show_test_region_logs_toggle.setChecked(
            settings.show_test_region_logs
        )
        self.overlay_window.settings_window.save_test_region_screenshots_toggle.setChecked(
            settings.save_test_region_screenshots
        )
        self.overlay_window.settings_window.overlay_background_transparency_spin.setValue(
            settings.overlay_background_transparency
        )
        self.overlay_window.settings_window.use_mouse_clicking_toggle.setChecked(
            settings.use_mouse_clicking
        )
        self.overlay_window.settings_window.auto_close_special_pokemon_message_toggle.setChecked(
            settings.auto_close_special_pokemon_message
        )
        self.overlay_window.set_test_region_logs_visible(
            settings.show_test_region_logs
        )
        self.overlay_window.region_window.set_regions(settings.regions)

        if not settings.executable_path:
            return

        executable = Path(settings.executable_path)
        if not self.executable_picker.set_selected_path(executable):
            self.log_viewer.append(
                "WARNING",
                f"Saved Poke Nexus location is unavailable or invalid: {executable}",
            )
            return

        self.selected_executable = executable

    def _save_settings(self) -> None:
        self._update_session_overview()
        settings_window = self.overlay_window.settings_window
        settings = AppSettings(
            executable_path=(
                str(self.selected_executable)
                if self.selected_executable is not None
                else ""
            ),
            auto_battle=settings_window.auto_battle_toggle.isChecked(),
            auto_battle_mode=settings_window.auto_battle_mode_selection.currentText(),
            auto_battle_specific_pokemon=settings_window.specific_pokemon_input.text(),
            auto_battle_elite_only=settings_window.auto_battle_elite_only_toggle.isChecked(),
            ev_training_pokemon=settings_window.ev_training_pokemon_input.text(),
            ev_training_category=settings_window.ev_training_category_filter.currentData(),
            ev_training_current=settings_window.ev_training_progress().current,
            ev_training_targets=settings_window.ev_training_progress().targets,
            ev_training_search=settings_window.ev_training_pokemon_input.search_input.text().strip(),
            auto_switch_fainted_pokemon=(
                settings_window.auto_switch_fainted_pokemon_toggle.isChecked()
            ),
            auto_run=settings_window.auto_run_toggle.isChecked(),
            auto_catching=settings_window.auto_catching_toggle.isChecked(),
            auto_fishing=settings_window.auto_fishing_toggle.isChecked(),
            auto_fishing_press_f_on_missing_zone=(
                settings_window.auto_fishing_press_f_on_missing_zone_toggle.isChecked()
            ),
            auto_fishing_reaction_lead_ms=(
                settings_window.auto_fishing_reaction_lead_spin.value()
            ),
            auto_fishing_space_hold_ms=(
                settings_window.auto_fishing_space_hold_spin.value()
            ),
            auto_catching_wild_hp=settings_window.auto_catching_hp_spin.value(),
            auto_catching_item=settings_window.auto_catching_item_selection.currentText().strip(),
            auto_catching_desired_filter=(
                settings_window.auto_catching_desired_toggle.isChecked()
            ),
            auto_catching_shiny_filter=(
                settings_window.auto_catching_shiny_toggle.isChecked()
            ),
            auto_catching_elite_filter=(
                settings_window.auto_catching_elite_toggle.isChecked()
            ),
            stop_auto_battle_for_desired_pokemon=(
                settings_window.stop_for_desired_pokemon_toggle.isChecked()
            ),
            desired_pokemon=(
                settings_window.desired_pokemon_input.text().strip()
            ),
            desired_pokemon_sound_enabled=(
                settings_window.desired_pokemon_sound_toggle.isChecked()
            ),
            desired_pokemon_sound_path=(
                settings_window.desired_pokemon_sound_path.text()
            ),
            shiny_pokemon_sound_enabled=(
                settings_window.shiny_pokemon_sound_toggle.isChecked()
            ),
            shiny_pokemon_sound_path=(
                settings_window.shiny_pokemon_sound_path.text()
            ),
            elite_pokemon_sound_enabled=(
                settings_window.elite_pokemon_sound_toggle.isChecked()
            ),
            elite_pokemon_sound_path=(
                settings_window.elite_pokemon_sound_path.text()
            ),
            stop_auto_battle_for_shiny=(
                settings_window.stop_for_shiny_toggle.isChecked()
            ),
            stop_auto_battle_for_elite=(
                settings_window.stop_for_elite_toggle.isChecked()
            ),
            elite_pokemon_names=(
                settings_window.elite_pokemon_input.text().strip()
            ),
            auto_walk=settings_window.auto_walk_toggle.isChecked(),
            auto_walk_direction=(
                settings_window.auto_walk_direction_selection.currentText()
            ),
            auto_walk_duration_mode=(
                settings_window.auto_walk_duration_mode_selection.currentText()
            ),
            auto_walk_duration_seconds=(
                settings_window.auto_walk_duration_spin.value()
            ),
            auto_walk_min_duration_seconds=(
                settings_window.auto_walk_min_duration_spin.value()
            ),
            auto_walk_max_duration_seconds=(
                settings_window.auto_walk_max_duration_spin.value()
            ),
            show_chat_battle_logs=settings_window.show_chat_battle_logs_toggle.isChecked(),
            show_pokemon_encounter_summary=(
                settings_window.show_pokemon_encounter_summary_toggle.isChecked()
            ),
            pokemon_encounter_count=(
                self.overlay_window.pokemon_encounter_count
            ),
            pokemon_encounter_rarity_counts=(
                self.overlay_window.pokemon_encounter_rarity_counts
            ),
            pokemon_encounter_date=(
                self.overlay_window.pokemon_encounter_date
            ),
            show_test_region_logs=settings_window.show_test_region_logs_toggle.isChecked(),
            save_test_region_screenshots=(
                settings_window.save_test_region_screenshots_toggle.isChecked()
            ),
            overlay_background_transparency=(
                settings_window.overlay_background_transparency_spin.value()
            ),
            use_mouse_clicking=settings_window.use_mouse_clicking_toggle.isChecked(),
            auto_close_special_pokemon_message=(
                settings_window.auto_close_special_pokemon_message_toggle.isChecked()
            ),
            synchronize_pokemon=self._legacy_synchronize_pokemon,
            regions=tuple(self.overlay_window.region_window.regions),
        )
        try:
            self.settings_store.save(settings)
        except (OSError, ValueError) as error:
            self.log_viewer.append(
                "ERROR", f"Could not save settings to {self.settings_store.path}: {error}"
            )

    def _on_settings_toggled(self, checked: bool) -> None:
        del checked
        self._save_settings()

    def _on_chat_battle_logs_toggled(self, checked: bool) -> None:
        self.overlay_window.set_chat_battle_logs_visible(checked)
        self._save_settings()

    def _on_encounter_summary_toggled(self, checked: bool) -> None:
        del checked
        self._save_settings()

    def _on_encounter_statistics_changed(self, count: int, encounter_date: str) -> None:
        del count, encounter_date
        self._save_settings()

    def _on_test_region_logs_toggled(self, checked: bool) -> None:
        self.overlay_window.set_test_region_logs_visible(checked)
        self._save_settings()

    def _on_regions_changed(self, regions: tuple[SavedRegion, ...]) -> None:
        del regions
        self._save_settings()

    def _on_start_clicked(self) -> None:
        if self.process_controller.is_running:
            self._on_stop_requested()
            return

        if self.selected_executable is None:
            self.log_viewer.append("ERROR", "Cannot start: no application is selected.")
            self._show_warning(
                "No application selected", "Select Poke Nexus.exe first."
            )
            return

        self._launch_transition = "starting"
        self._sync_launcher_state()
        self.overlay_window.set_bot_state(False, transitioning=True)
        self.process_controller.start(self.selected_executable)

    def _on_start_requested(self) -> None:
        if not self.process_controller.is_running:
            self._on_start_clicked()

    def _on_stop_requested(self) -> None:
        if self.process_controller.is_running:
            self.overlay_window.stop_realtime_region_capture(
                wait_timeout_ms=5000
            )
            self._launch_transition = "stopping"
            self._sync_launcher_state()
            self.overlay_window.set_bot_state(True, transitioning=True)
            self.process_controller.stop()

    def _on_process_started(self) -> None:
        process_id = self.process_controller.process_id
        if process_id is None:
            self.log_viewer.append(
                "ERROR", "Could not attach the overlay: game process ID is unavailable."
            )
        else:
            self.overlay_window.attach_to_process(process_id)
        if not self.tray_controller.is_available():
            self.log_viewer.append(
                "WARNING", "System tray is unavailable; launcher will remain visible."
            )
            return

        self.tray_controller.show()
        self._hidden_to_tray = True
        self._restore_when_process_exits = True
        self.hide()
        self.log_viewer.append("INFO", "Launcher hidden to the system tray.")

    def _set_running_state(self, running: bool) -> None:
        self.overlay_window.set_bot_state(running)
        self._launcher_running = running
        self._launch_transition = None
        self._sync_launcher_state()
        self._fit_height_to_contents()

        if not running:
            self.overlay_window.stop_tracking()
            if self._restore_when_process_exits:
                self.tray_controller.hide()
                self._hidden_to_tray = False
                self._restore_when_process_exits = False
                self._restore_from_tray()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.tray_controller.is_available():
            self.log_viewer.append(
                "WARNING", "Cannot hide to tray because the system tray is unavailable."
            )
            event.ignore()
            return

        self.tray_controller.show()
        self._hidden_to_tray = True
        self._restore_when_process_exits = False
        self.hide()
        self.log_viewer.append("INFO", "Launcher minimized to the system tray.")
        event.ignore()

    def _on_log_visibility_changed(self) -> None:
        self._fit_height_to_contents()

    def _fit_height_to_contents(self) -> None:
        layout = self.layout()
        if not self.log_panel.isHidden():
            self.log_panel.setFixedHeight(140)
        # Refresh the nested card's cached size before measuring the window.
        self._activity_card.updateGeometry()
        layout.invalidate()
        layout.activate()
        screen = self.screen()
        if screen is not None and not self.log_panel.isHidden():
            frame_height = max(32, self.frameGeometry().height() - self.height())
            available_height = screen.availableGeometry().height() - frame_height - 24
            overflow = max(0, layout.sizeHint().height() - available_height)
            self.log_panel.setFixedHeight(max(72, 140 - overflow))
            self._activity_card.updateGeometry()
            layout.invalidate()
            layout.activate()
        self.setFixedHeight(layout.sizeHint().height())

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def _quit_application(self) -> None:
        self.overlay_window.cancel_battle_region_placement(wait=True)
        self.overlay_window.stop_realtime_region_capture(
            wait_timeout_ms=5000
        )
        QApplication.quit()

    def _on_hotkey_error(self, message: str) -> None:
        self.log_viewer.append("ERROR", message)
