from html import escape

from PySide6.QtCore import QElapsedTimer, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from core.pokemon_rarity import RARITIES
from launcher.styles.style_loader import background_alpha
from overlay.appearance import apply_overlay_stylesheet, background_transparency
from overlay.panel_geometry import position_panel
from overlay.panel_widgets import OverlayIconButton, OverlayPanelHeader
from overlay.window_drag import OverlayWindowDrag
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class PokemonEncounterSummaryWindow(QWidget):
    """Show the current wild encounter, daily count, and app session duration."""

    preferred_size_changed = Signal()
    _EXPANDED_HEIGHT = 328
    _COLLAPSED_HEIGHT = 204

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("pokemon_encounter_summary_window")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Pokémon Summary")
        self._preferred_size = QSize(360, self._EXPANDED_HEIGHT)
        self._last_game_geometry: QRect | None = None
        self._last_occupied: tuple[QRect, ...] = ()
        self._last_size_limit: QSize | None = None
        self.setFixedSize(self._preferred_size)
        apply_overlay_stylesheet(self)

        self.container = QFrame(self)
        self.container.setObjectName("pokemon_encounter_summary_container")
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.header = OverlayPanelHeader(
            "Pokémon Summary", "Live encounter & today's totals", "nexus-mark.svg",
            self.container,
        )
        self.title = self.header.title
        self.title.setObjectName("pokemon_encounter_summary_title")
        self.session_status_label = QLabel("Paused")
        self.session_status_label.setObjectName("summary_session_status")
        self.session_status_label.setProperty("running", False)
        self.session_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.session_status_label.setFixedSize(54, 24)
        self.header.add_action(self.session_status_label)
        self.collapse_button = OverlayIconButton(self.header)
        self.collapse_button.setObjectName("pokemon_summary_collapse_button")
        self.collapse_button.setFixedSize(28, 28)
        self.collapse_button.setIconSize(QSize(14, 14))
        self.collapse_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.collapse_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_button.setCheckable(True)
        self.collapse_button.toggled.connect(self._on_collapse_toggled)
        self._update_collapse_button(False)
        self.header.add_action(self.collapse_button)
        layout.addWidget(self.header)

        self.content_scroll_area = QScrollArea(self.container)
        self.content_scroll_area.setObjectName("summary_scroll_area")
        self.content_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll_area.setWidgetResizable(True)
        self.content_scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.content_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content.setObjectName("summary_content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content_scroll_area.setWidget(content)
        layout.addWidget(self.content_scroll_area, 1)

        encounter_card = QFrame(content)
        encounter_card.setObjectName("summary_encounter_card")
        encounter_layout = QVBoxLayout(encounter_card)
        encounter_layout.setContentsMargins(10, 8, 10, 8)
        encounter_layout.setSpacing(3)
        encounter_caption = QLabel("CURRENT ENCOUNTER")
        encounter_caption.setObjectName("summary_eyebrow")
        encounter_layout.addWidget(encounter_caption)
        self.current_pokemon_label = QLabel("Waiting for an encounter")
        self.current_pokemon_label.setObjectName("pokemon_encounter_name")
        self.current_pokemon_label.setTextFormat(Qt.TextFormat.RichText)
        self.current_pokemon_label.setWordWrap(True)
        encounter_layout.addWidget(self.current_pokemon_label)
        content_layout.addWidget(encounter_card)

        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        self.daily_count_label = self._add_metric(metrics, "Encounters today", "0")
        self.daily_count_label.setObjectName("summary_daily_count")
        self.timer_label = self._add_metric(metrics, "Session time", "00:00:00")
        self.timer_label.setObjectName("summary_session_time")
        content_layout.addLayout(metrics)

        self.rarity_section = QWidget(content)
        self.rarity_section.setObjectName("summary_rarity_section")
        rarity_layout = QVBoxLayout(self.rarity_section)
        rarity_layout.setContentsMargins(0, 0, 0, 0)
        rarity_layout.setSpacing(3)
        rarity_heading = QLabel("RARITY BREAKDOWN")
        rarity_heading.setObjectName("summary_eyebrow")
        rarity_layout.addWidget(rarity_heading)
        self.rarity_counts_label = QLabel()
        self.rarity_counts_label.setObjectName("pokemon_encounter_rarity_counts")
        self.rarity_counts_label.setTextFormat(Qt.TextFormat.RichText)
        rarity_layout.addWidget(self.rarity_counts_label)
        content_layout.addWidget(self.rarity_section)
        self.set_rarity_counts({})

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(3, 3, 3, 3)
        outer_layout.addWidget(self.container)

        self._drag_controller = OverlayWindowDrag(self, (self.container, self.header))
        self._session_timer = QElapsedTimer()
        self._elapsed_session_ms = 0
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(1000)
        self._display_timer.timeout.connect(self._update_timer_label)
        self._display_timer.start()
        self.hide()

    def show_for_game(
        self, game_geometry: QRect, occupied: tuple[QRect, ...] = (),
        *, size_limit: QSize | None = None,
    ) -> None:
        self._last_game_geometry = QRect(game_geometry)
        self._last_occupied = tuple(QRect(area) for area in occupied)
        self._last_size_limit = QSize(size_limit) if size_limit is not None else None
        self._position_for_game()
        if not self.isVisible():
            self.show()
            self.raise_()

    def _position_for_game(self) -> None:
        if self._last_game_geometry is None:
            self.setFixedSize(self._preferred_size)
            return
        size = self._preferred_size
        if self._last_size_limit is not None:
            size = size.boundedTo(self._last_size_limit)
        position_panel(
            self, self._last_game_geometry, size,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            self._last_occupied,
        )

    def _on_collapse_toggled(self, collapsed: bool) -> None:
        self.rarity_section.setVisible(not collapsed)
        self._preferred_size.setHeight(
            self._COLLAPSED_HEIGHT if collapsed else self._EXPANDED_HEIGHT
        )
        self._update_collapse_button(collapsed)
        self._position_for_game()
        self.content_scroll_area.verticalScrollBar().setValue(0)
        self.preferred_size_changed.emit()

    def _update_collapse_button(self, collapsed: bool) -> None:
        action = "Expand" if collapsed else "Collapse"
        self.collapse_button.setToolTip(f"{action} Pokémon Summary")
        self.collapse_button.setAccessibleName(f"{action} Pokémon Summary")
        self.collapse_button.setIcon(
            load_svg_icon(
                application_path(
                    "assets", "chevron-down.svg" if collapsed else "chevron-up.svg",
                ),
                self.collapse_button.iconSize(),
            )
        )

    @staticmethod
    def _add_metric(layout: QHBoxLayout, caption: str, value: str) -> QLabel:
        card = QFrame()
        card.setObjectName("summary_metric_card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(3)
        label = QLabel(caption)
        label.setObjectName("summary_metric_caption")
        number = QLabel(value)
        number.setAccessibleName(caption)
        card_layout.addWidget(label)
        card_layout.addWidget(number)
        layout.addWidget(card, 1)
        return number

    def set_daily_count(self, count: int) -> None:
        self.daily_count_label.setText(f"{count:,}")
        self.daily_count_label.setAccessibleName(f"Encounters today: {count:,}")

    def set_rarity_counts(self, counts: dict[str, int]) -> None:
        self._rarity_counts = counts.copy()
        alpha = background_alpha(255, background_transparency(self))
        rows: list[str] = []
        for index in range(0, len(RARITIES), 2):
            cells = []
            for code, label, color in RARITIES[index : index + 2]:
                cells.append(
                    "<td width='50%' "
                    f"style='background-color:rgba(25,38,58,{alpha});'>"
                    f"<span style='color:{color};font-size:11px;font-weight:600;'>"
                    f"{label}: {counts.get(code, 0):,}</span></td>"
                )
            rows.append(f"<tr>{''.join(cells)}</tr>")
        self.rarity_counts_label.setText(
            "<table width='100%' cellspacing='4' cellpadding='4'>"
            f"{''.join(rows)}</table>"
        )

    def refresh_background_transparency(self) -> None:
        self.set_rarity_counts(self._rarity_counts)

    def start_encounter(
        self,
        pokemon_name: str,
        name_color: str = "#4ADE80",
    ) -> None:
        safe_pokemon_name = escape(pokemon_name)
        safe_name_color = escape(name_color, quote=True)
        self.current_pokemon_label.setText(
            f'<span style="color: {safe_name_color}; font-weight: 600;">'
            f'{safe_pokemon_name}</span>'
        )
        self.current_pokemon_label.setToolTip(pokemon_name)
        self.current_pokemon_label.setAccessibleName(f"Current encounter: {pokemon_name}")
        self._update_timer_label()

    def start_session_timer(self) -> None:
        if not self._session_timer.isValid():
            self._session_timer.start()
        self._set_session_status(True)
        self._update_timer_label()

    def stop_session_timer(self) -> None:
        if self._session_timer.isValid():
            self._elapsed_session_ms += self._session_timer.elapsed()
            self._session_timer.invalidate()
        self._set_session_status(False)
        self._update_timer_label()

    def _set_session_status(self, running: bool) -> None:
        self.session_status_label.setText("Active" if running else "Paused")
        self.session_status_label.setProperty("running", running)
        self.session_status_label.setToolTip(
            "The session timer is running" if running else "The session timer is paused"
        )
        self.session_status_label.style().unpolish(self.session_status_label)
        self.session_status_label.style().polish(self.session_status_label)

    def _update_timer_label(self) -> None:
        elapsed_ms = self._elapsed_session_ms
        if self._session_timer.isValid():
            elapsed_ms += self._session_timer.elapsed()
        elapsed_seconds = max(0, elapsed_ms // 1000)
        hours, remaining_seconds = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remaining_seconds, 60)
        self.timer_label.setText(
            f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        )
