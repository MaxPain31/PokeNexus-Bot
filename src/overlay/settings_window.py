from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.pokemon_rarity import RARITIES, load_pokemon_rarity_data
from overlay.appearance import apply_overlay_stylesheet
from overlay.window_drag import OverlayWindowDrag
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class _PokemonCheckDelegate(QStyledItemDelegate):
    @staticmethod
    def _is_checked(state) -> bool:
        return int(getattr(state, "value", state) or 0) == Qt.CheckState.Checked.value

    @staticmethod
    def _checkbox_rect(option: QStyleOptionViewItem) -> QRect:
        checkbox_size = 16
        checkbox = option.rect.adjusted(9, 0, 0, 0)
        checkbox.setTop(option.rect.top() + (option.rect.height() - checkbox_size) // 2)
        checkbox.setSize(QSize(checkbox_size, checkbox_size))
        return checkbox

    def paint(self, painter: QPainter, option, index) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        if selected:
            painter.fillRect(option.rect, QColor(56, 189, 248, 55))
        elif hovered:
            painter.fillRect(option.rect, QColor(56, 189, 248, 35))

        checkbox = self._checkbox_rect(option)
        checked = self._is_checked(index.data(Qt.ItemDataRole.CheckStateRole))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                QColor("#22C55E" if checked else "#9CA3AF"),
                1.5,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(checkbox, 3, 3)
        if checked:
            painter.setPen(QPen(QColor("#22C55E"), 2))
            painter.drawLine(
                checkbox.left() + 3,
                checkbox.top() + 8,
                checkbox.left() + 6,
                checkbox.top() + 11,
            )
            painter.drawLine(
                checkbox.left() + 6,
                checkbox.top() + 11,
                checkbox.left() + 13,
                checkbox.top() + 4,
            )

        text_option = QStyleOptionViewItem(option)
        self.initStyleOption(text_option, index)
        text_option.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        text_option.rect = option.rect.adjusted(32, 0, -4, 0)
        text_option.state &= ~(
            QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver
        )
        option.widget.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            text_option,
            painter,
            option.widget,
        )
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        mouse_toggle = (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and option.rect.contains(event.position().toPoint())
        )
        keyboard_toggle = (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select)
        )
        if mouse_toggle or keyboard_toggle:
            current_state = index.data(Qt.ItemDataRole.CheckStateRole)
            new_state = (
                Qt.CheckState.Unchecked
                if self._is_checked(current_state)
                else Qt.CheckState.Checked
            )
            return model.setData(
                index,
                new_state,
                Qt.ItemDataRole.CheckStateRole,
            )
        return super().editorEvent(event, model, option, index)


class _DesiredPokemonPicker(QWidget):
    textChanged = Signal(str)
    _RARITIES = tuple((code, label) for code, label, _color in RARITIES)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        popup_title: str = "Choose desired Pokemon",
    ) -> None:
        super().__init__(parent)
        self._pokemon_names, self._rarity_by_name = self._load_pokemon_data()
        self._selected_names: set[str] = set()
        self._items_by_name: dict[str, QListWidgetItem] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.display = QLineEdit(self)
        self.display.setObjectName("desired_pokemon_input")
        self.display.setReadOnly(True)
        self.display.setPlaceholderText("Click to choose Pokemon")
        self.display.setCursor(Qt.CursorShape.PointingHandCursor)
        self.display.setFixedHeight(36)
        self.display.setAccessibleName("Choose desired Pokemon")
        self.display.installEventFilter(self)
        row.addWidget(self.display, 1)

        self.popup = QFrame(
            self,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.popup.setObjectName("desired_pokemon_popup")
        self.popup.setFixedSize(340, 390)
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(10, 10, 10, 10)
        popup_layout.setSpacing(8)

        popup_heading = QLabel(popup_title, self.popup)
        popup_heading.setObjectName("desired_pokemon_popup_title")
        popup_layout.addWidget(popup_heading)
        self.search_input = QLineEdit(self.popup)
        self.search_input.setObjectName("desired_pokemon_search")
        self.search_input.setPlaceholderText("Search Pokemon...")
        self.search_input.setAccessibleName("Search Pokemon by name")
        self.search_input.textChanged.connect(self._filter_pokemon)
        popup_layout.addWidget(self.search_input)
        self.rarity_filter = QComboBox(self.popup)
        self.rarity_filter.setObjectName("desired_pokemon_rarity_filter")
        self.rarity_filter.setAccessibleName("Filter Pokemon by rarity")
        self.rarity_filter.addItem("All rarities", "")
        for code, label in self._RARITIES:
            self.rarity_filter.addItem(f"{code.upper()} - {label}", code)
        self.rarity_filter.currentIndexChanged.connect(self._filter_pokemon)
        popup_layout.addWidget(self.rarity_filter)
        self.select_all = QCheckBox("Select all shown", self.popup)
        self.select_all.setObjectName("desired_pokemon_select_all")
        self.select_all.toggled.connect(self._on_select_all_changed)
        popup_layout.addWidget(self.select_all)
        self.selection_count = QLabel(self.popup)
        self.selection_count.setObjectName("desired_pokemon_selection_count")
        popup_layout.addWidget(self.selection_count)

        self.pokemon_list = QListWidget(self.popup)
        self.pokemon_list.setObjectName("desired_pokemon_list")
        self.pokemon_list.setUniformItemSizes(True)
        self.pokemon_list.setItemDelegate(_PokemonCheckDelegate(self.pokemon_list))
        self.pokemon_list.itemChanged.connect(self._on_item_changed)
        popup_layout.addWidget(self.pokemon_list, 1)
        self.empty_state = QLabel("No Pokemon match these filters.", self.popup)
        self.empty_state.setObjectName("desired_pokemon_empty_state")
        self.empty_state.setWordWrap(True)
        self.empty_state.hide()
        popup_layout.addWidget(self.empty_state)
        self._add_names_to_list(self._pokemon_names)
        self._update_display()

    @staticmethod
    def _load_pokemon_data() -> tuple[list[str], dict[str, str]]:
        return load_pokemon_rarity_data()

    def _add_names_to_list(self, names: list[str]) -> None:
        for name in names:
            key = name.casefold()
            if key in self._items_by_name:
                continue
            item = QListWidgetItem(name)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if key in self._selected_names
                else Qt.CheckState.Unchecked
            )
            self.pokemon_list.addItem(item)
            self._items_by_name[key] = item

    def text(self) -> str:
        return ", ".join(
            name
            for name in self._pokemon_names
            if name.casefold() in self._selected_names
        )

    def setText(self, value: str) -> None:
        selected_names = {
            name.strip().casefold()
            for name in value.split(",")
            if name.strip()
        }
        custom_names = [
            name.strip()
            for name in value.split(",")
            if name.strip() and name.strip().casefold() not in self._items_by_name
        ]
        if custom_names:
            self._pokemon_names.extend(custom_names)
            self._add_names_to_list(custom_names)
        changed = selected_names != self._selected_names
        self._selected_names = selected_names
        for key, item in self._items_by_name.items():
            item.setCheckState(
                Qt.CheckState.Checked
                if key in self._selected_names
                else Qt.CheckState.Unchecked
            )
        self._update_display()
        self._filter_pokemon()
        if changed:
            self.textChanged.emit(self.text())

    def setFocus(self) -> None:
        if not self.isEnabled():
            return
        self.display.setFocus()
        self._show_picker()

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        if not enabled:
            self.popup.hide()

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self.display
            and event.type() == QEvent.Type.MouseButtonPress
            and self.isEnabled()
        ):
            self._show_picker()
        if (
            watched is self.display
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (
                Qt.Key.Key_Space,
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
                Qt.Key.Key_Down,
            )
            and self.isEnabled()
        ):
            self._show_picker()
            return True
        return super().eventFilter(watched, event)

    def _show_picker(self) -> None:
        if not self.isEnabled():
            self.popup.hide()
            return
        anchor = self.mapToGlobal(self.rect().bottomLeft())
        screen = QApplication.screenAt(anchor)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("Cannot open Pokemon picker without a screen.")
        bounds = screen.availableGeometry()
        x = anchor.x()
        x = max(bounds.left(), min(x, bounds.right() - self.popup.width() + 1))
        y = anchor.y() + 8
        if y + self.popup.height() > bounds.bottom() + 1:
            y = self.mapToGlobal(self.rect().topLeft()).y() - self.popup.height() - 8
        y = max(bounds.top(), y)
        self.popup.move(x, y)
        self.popup.show()
        self.popup.raise_()
        self.search_input.setFocus()

    def _filter_pokemon(self, _value=None) -> None:
        normalized_query = self.search_input.text().strip().casefold()
        selected_rarity = self.rarity_filter.currentData()
        for key, item in self._items_by_name.items():
            matches_query = normalized_query in key
            matches_rarity = (
                not selected_rarity
                or self._rarity_by_name.get(key, "") == selected_rarity
            )
            item.setHidden(not (matches_query and matches_rarity))
        self._update_select_all_state()

    def _on_select_all_changed(self, checked: bool) -> None:
        visible_items = [
            (key, item)
            for key, item in self._items_by_name.items()
            if not item.isHidden()
        ]
        if not visible_items:
            return

        selected_names_before = self._selected_names.copy()
        for key, item in visible_items:
            if checked:
                self._selected_names.add(key)
            else:
                self._selected_names.discard(key)

        self.pokemon_list.blockSignals(True)
        try:
            for _, item in visible_items:
                item.setCheckState(
                    Qt.CheckState.Checked
                    if checked
                    else Qt.CheckState.Unchecked
                )
        finally:
            self.pokemon_list.blockSignals(False)

        self._update_display()
        self._update_select_all_state()
        if self._selected_names != selected_names_before:
            self.textChanged.emit(self.text())

    def _update_select_all_state(self) -> None:
        visible_keys = [
            key
            for key, item in self._items_by_name.items()
            if not item.isHidden()
        ]
        selected_count = sum(
            key in self._selected_names for key in visible_keys
        )
        self.select_all.setEnabled(bool(visible_keys))
        self.empty_state.setVisible(not visible_keys)
        is_all_selected = bool(visible_keys) and (
            selected_count == len(visible_keys)
        )
        self.select_all.blockSignals(True)
        self.select_all.setChecked(is_all_selected)
        self.select_all.blockSignals(False)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        key = item.text().casefold()
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_names.add(key)
        else:
            self._selected_names.discard(key)
        self._update_display()
        self._update_select_all_state()
        self.textChanged.emit(self.text())

    def _update_display(self) -> None:
        selected = [
            name
            for name in self._pokemon_names
            if name.casefold() in self._selected_names
        ]
        if not selected:
            display_text = ""
        elif len(selected) <= 2:
            display_text = ", ".join(selected)
        else:
            display_text = f"{len(selected)} Pokemon selected: {', '.join(selected[:2])}..."
        self.display.setText(display_text)
        self.display.setToolTip(", ".join(selected))
        self.selection_count.setText(
            f"{len(selected)} selected" if selected else "No Pokemon selected"
        )


class _SlidingTabBar(QWidget):
    currentChanged = Signal(int)

    def __init__(self, labels: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settings_tab_bar")
        self.setFixedHeight(40)
        self._buttons: list[QPushButton] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(2)
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._indicator = QFrame(self)
        self._indicator.setObjectName("settings_tab_indicator")

        for index, label in enumerate(labels):
            button = QPushButton(label, self)
            button.setObjectName("settings_tab_button")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setAccessibleName(f"{label} settings")
            button.installEventFilter(self)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button, 1)
            self._button_group.addButton(button, index)
            self._buttons.append(button)
            button.clicked.connect(
                lambda checked=False, selected=index: self._on_tab_clicked(
                    selected,
                    checked,
                )
            )
        self.setCurrentIndex(0, animate=False)

    def _on_tab_clicked(self, index: int, checked: bool) -> None:
        del checked
        self.setCurrentIndex(index, animate=False)
        self.currentChanged.emit(index)

    def eventFilter(self, watched, event) -> bool:
        if watched in self._buttons and event.type() == QEvent.Type.KeyPress:
            index = self._buttons.index(watched)
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                step = -1 if event.key() == Qt.Key.Key_Left else 1
                index = (index + step) % len(self._buttons)
            elif event.key() == Qt.Key.Key_Home:
                index = 0
            elif event.key() == Qt.Key.Key_End:
                index = len(self._buttons) - 1
            else:
                return super().eventFilter(watched, event)
            self._buttons[index].setFocus()
            self._on_tab_clicked(index, checked=True)
            return True
        return super().eventFilter(watched, event)

    def setCurrentIndex(self, index: int, *, animate: bool = True) -> None:
        if not 0 <= index < len(self._buttons):
            return
        self._buttons[index].setChecked(True)
        self._move_indicator(index, animate)

    def _move_indicator(self, index: int, animate: bool) -> None:
        del animate
        if not 0 <= index < len(self._buttons):
            return
        button = self._buttons[index]
        position = button.mapTo(self, QPoint(0, 0))
        target = QRect(position.x(), self.height() - 3, button.width(), 2)
        self._indicator.setGeometry(target)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._move_indicator(self._button_group.checkedId(), animate=False)


class _SlidingSettingsPages(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages: list[QWidget] = []
        self._current_index = -1

    def addPage(self, page: QWidget) -> None:
        page.setParent(self)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        page.hide()
        self._pages.append(page)
        if self._current_index == -1:
            self._current_index = 0
            page.setGeometry(self.rect())
            page.show()
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        if not self._pages:
            return QSize(1, 1)
        current_page = self._pages[self._current_index]
        return QSize(
            current_page.sizeHint().width(),
            current_page.sizeHint().height(),
        )

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._pages) or index == self._current_index:
            return
        if self._current_index >= 0:
            self._pages[self._current_index].hide()
        self._current_index = index
        current_page = self._pages[index]
        current_page.setGeometry(self.rect())
        current_page.show()
        self.setMinimumHeight(current_page.sizeHint().height())
        self.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._current_index >= 0:
            self._pages[self._current_index].setGeometry(self.rect())


class SettingsWindow(QDialog):
    """Glass settings panel for the game overlay."""

    visibility_changed = Signal(bool)
    auto_walk_configuration_changed = Signal()
    sound_settings_changed = Signal()
    _DEFAULT_WIDTH = 500
    _PAGE_DESCRIPTIONS = (
        "Choose how the bot handles battles and catching.",
        "Protect special encounters and choose your catch filters.",
        "Fine-tune the timing of automatic fishing inputs.",
        "Choose audio notifications for special encounters.",
        "Set your walking direction and movement timing.",
        "Adjust overlay appearance, battle input, and diagnostics.",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("settings_window")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle("Game Bot Settings")
        self.setFixedWidth(self._DEFAULT_WIDTH)
        self._last_game_geometry: QRect | None = None
        self._last_overlay_geometry: QRect | None = None

        self.glass_container = QFrame(self)
        self.glass_container.setObjectName("settings_container")

        panel_layout = QVBoxLayout(self.glass_container)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(10)

        self.header_widget = QWidget(self.glass_container)
        self.header_widget.setObjectName("settings_header")
        self._drag_controller = OverlayWindowDrag(
            self,
            (self.header_widget,),
        )
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        header_icon = QLabel()
        header_icon.setObjectName("settings_header_icon")
        header_icon.setFixedSize(38, 38)
        header_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_icon.setPixmap(
            load_svg_icon(
                application_path(
                    "assets", "settings_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg"
                ),
                QSize(22, 22),
                fill_color="#7DD3FC",
            ).pixmap(QSize(22, 22))
        )
        header.addWidget(header_icon)
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel("Settings")
        title.setObjectName("settings_title")
        header_text.addWidget(title)
        subtitle = QLabel("Make the bot work your way")
        subtitle.setObjectName("settings_subtitle")
        header_text.addWidget(subtitle)
        header.addLayout(header_text)
        header.addStretch()
        live_badge = QLabel("LIVE")
        live_badge.setObjectName("settings_live_badge")
        live_badge.setFixedHeight(24)
        live_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        live_badge.setToolTip("Settings take effect as you edit them")
        header.addWidget(live_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        panel_layout.addWidget(self.header_widget)
        header_divider = QFrame(self.glass_container)
        header_divider.setObjectName("settings_header_divider")
        header_divider.setFrameShape(QFrame.Shape.HLine)
        panel_layout.addWidget(header_divider)

        self.settings_tabs = _SlidingTabBar(
            ("Battle", "Encounters", "Fishing", "Alerts", "Movement", "More"),
            self.glass_container,
        )
        self.settings_tabs.currentChanged.connect(self._select_settings_page)
        panel_layout.addWidget(self.settings_tabs)
        self.page_description = QLabel(self._PAGE_DESCRIPTIONS[0])
        self.page_description.setObjectName("settings_page_description")
        self.page_description.setWordWrap(True)
        description_policy = QSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        description_policy.setHeightForWidth(True)
        self.page_description.setSizePolicy(description_policy)
        panel_layout.addWidget(self.page_description)
        content = _SlidingSettingsPages()
        content.setObjectName("settings_content")
        self._settings_pages = content
        page_layouts: dict[str, QVBoxLayout] = {}
        for page_name in (
            "Battle",
            "Encounters",
            "Fishing",
            "Alerts",
            "Movement",
            "More",
        ):
            page = QWidget()
            page.setObjectName(f"settings_page_{page_name.casefold()}")
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(10)
            page_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            content.addPage(page)
            page_layouts[page_name] = page_layout

        battle_layout = page_layouts["Battle"]
        automation_layout = self._add_settings_section(
            battle_layout,
            "Battle automation",
        )
        self.auto_battle_toggle = self._add_toggle(
            automation_layout, "Auto Battle", checked=True
        )
        self.auto_switch_fainted_pokemon_toggle = self._add_toggle(
            automation_layout, "Auto Switch Fainted Pokemon", checked=False
        )
        self.auto_run_toggle = self._add_toggle(
            automation_layout, "Auto Run", checked=False
        )
        self.auto_battle_toggle.toggled.connect(
            self._on_auto_battle_toggled
        )
        self.auto_run_toggle.toggled.connect(self._on_auto_run_toggled)
        self.auto_switch_fainted_pokemon_toggle.toggled.connect(
            self._on_auto_switch_toggled
        )
        self.auto_catching_toggle = self._add_toggle(
            automation_layout, "Auto Catching", checked=False
        )

        fishing_layout = self._add_settings_section(
            page_layouts["Fishing"],
            "Fishing automation",
        )
        self.auto_fishing_toggle = self._add_toggle(
            fishing_layout, "Auto Fishing", checked=False
        )
        self.auto_fishing_press_f_on_missing_zone_toggle = self._add_toggle(
            fishing_layout,
            "Press F after more than 10 seconds without a green zone",
            checked=False,
        )
        fallback_description = QLabel(
            "Green detection and Space reset the timer. Fishing pauses in battle "
            "and restarts the 10 seconds afterward. F is sent once per cycle "
            "while Auto Fishing is on."
        )
        fallback_description.setObjectName("auto_fishing_fallback_description")
        fallback_description.setWordWrap(True)
        fishing_layout.addWidget(fallback_description)
        self.auto_fishing_press_f_on_missing_zone_toggle.setToolTip(
            fallback_description.text()
        )
        self.auto_fishing_reaction_lead_spin = QSpinBox()
        self.auto_fishing_reaction_lead_spin.setObjectName(
            "auto_fishing_reaction_lead_spin"
        )
        self.auto_fishing_reaction_lead_spin.setRange(0, 500)
        self.auto_fishing_reaction_lead_spin.setSingleStep(10)
        self.auto_fishing_reaction_lead_spin.setSuffix(" ms")
        self.auto_fishing_reaction_lead_spin.setValue(50)
        self.auto_fishing_reaction_lead_spin.setToolTip(
            "Anticipate the hook reaching the green zone by this many milliseconds. "
            "Use 0 ms for detected overlap."
        )
        self._add_auto_walk_setting_row(
            fishing_layout,
            "Space press lead",
            self.auto_fishing_reaction_lead_spin,
        )
        self.auto_fishing_space_hold_spin = QSpinBox()
        self.auto_fishing_space_hold_spin.setObjectName(
            "auto_fishing_space_hold_spin"
        )
        self.auto_fishing_space_hold_spin.setRange(0, 100)
        self.auto_fishing_space_hold_spin.setSingleStep(5)
        self.auto_fishing_space_hold_spin.setSuffix(" ms")
        self.auto_fishing_space_hold_spin.setValue(10)
        self._add_auto_walk_setting_row(
            fishing_layout,
            "Space hold duration",
            self.auto_fishing_space_hold_spin,
        )
        fishing_description = QLabel(
            "Use Fishing Bar plus Battle Chat or Enemy Health regions. "
            "Space press lead: 0 ms uses detected overlap."
        )
        fishing_description.setObjectName("auto_fishing_description")
        fishing_description.setWordWrap(True)
        fishing_layout.addWidget(fishing_description)

        encounter_page_layout = page_layouts["Encounters"]
        encounter_layout = self._add_settings_section(
            encounter_page_layout,
            "Encounter rules",
        )
        self.stop_for_desired_pokemon_toggle = self._add_toggle(
            encounter_layout, "Stop Battle for Desired Pokemon", checked=False
        )
        self.desired_pokemon_advanced_toggle = QToolButton()
        self.desired_pokemon_advanced_toggle.setObjectName(
            "desired_pokemon_advanced_toggle"
        )
        self.desired_pokemon_advanced_toggle.setText(
            "Advanced Desired Pokemon Settings"
        )
        self.desired_pokemon_advanced_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.desired_pokemon_advanced_toggle.setArrowType(
            Qt.ArrowType.RightArrow
        )
        self.desired_pokemon_advanced_toggle.setCheckable(True)
        self.desired_pokemon_advanced_toggle.setChecked(False)
        self.desired_pokemon_advanced_toggle.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        encounter_layout.addWidget(self.desired_pokemon_advanced_toggle)

        self._desired_pokemon_advanced_settings = QWidget()
        self._desired_pokemon_advanced_settings.setObjectName(
            "desired_pokemon_advanced_settings"
        )
        desired_advanced_layout = QVBoxLayout(
            self._desired_pokemon_advanced_settings
        )
        desired_advanced_layout.setContentsMargins(0, 0, 0, 0)
        desired_advanced_layout.setSpacing(8)
        self._desired_pokemon_advanced_settings.setVisible(False)
        encounter_layout.addWidget(self._desired_pokemon_advanced_settings)
        self.desired_pokemon_advanced_toggle.toggled.connect(
            self._on_desired_pokemon_advanced_toggled
        )

        desired_pokemon_label = QLabel("Desired Pokemon (required when enabled)")
        desired_pokemon_label.setObjectName("desired_pokemon_setting_label")
        desired_pokemon_label.setContentsMargins(8, 0, 0, 0)
        desired_advanced_layout.addWidget(desired_pokemon_label)
        self.desired_pokemon_input = _DesiredPokemonPicker()
        desired_advanced_layout.addWidget(self.desired_pokemon_input)
        self.stop_for_desired_pokemon_toggle.toggled.connect(
            self._on_desired_pokemon_stop_toggled
        )
        self._on_desired_pokemon_stop_toggled(
            self.stop_for_desired_pokemon_toggle.isChecked()
        )

        self.stop_for_shiny_toggle = self._add_toggle(
            encounter_layout, "Stop Battle for Shiny Encounters", checked=False
        )
        self.stop_for_shiny_toggle.toggled.connect(
            self._on_shiny_pokemon_stop_toggled
        )
        self.stop_for_elite_toggle = self._add_toggle(
            encounter_layout, "Stop Battle for Elite Pokemon", checked=False
        )
        self.elite_pokemon_advanced_toggle = QToolButton()
        self.elite_pokemon_advanced_toggle.setObjectName(
            "elite_pokemon_advanced_toggle"
        )
        self.elite_pokemon_advanced_toggle.setText(
            "Advanced Elite Pokemon Settings"
        )
        self.elite_pokemon_advanced_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.elite_pokemon_advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.elite_pokemon_advanced_toggle.setCheckable(True)
        self.elite_pokemon_advanced_toggle.setChecked(False)
        self.elite_pokemon_advanced_toggle.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        encounter_layout.addWidget(self.elite_pokemon_advanced_toggle)

        self._elite_pokemon_advanced_settings = QWidget()
        self._elite_pokemon_advanced_settings.setObjectName(
            "elite_pokemon_advanced_settings"
        )
        elite_advanced_layout = QVBoxLayout(
            self._elite_pokemon_advanced_settings
        )
        elite_advanced_layout.setContentsMargins(0, 0, 0, 0)
        elite_advanced_layout.setSpacing(8)
        self._elite_pokemon_advanced_settings.setVisible(False)
        encounter_layout.addWidget(self._elite_pokemon_advanced_settings)
        self.elite_pokemon_advanced_toggle.toggled.connect(
            self._on_elite_pokemon_advanced_toggled
        )

        elite_pokemon_label = QLabel(
            "Elite Pokemon to stop for (optional; empty = all)"
        )
        elite_pokemon_label.setObjectName("elite_pokemon_setting_label")
        elite_pokemon_label.setContentsMargins(8, 0, 0, 0)
        elite_advanced_layout.addWidget(elite_pokemon_label)
        self.elite_pokemon_input = _DesiredPokemonPicker(
            popup_title="Choose elite Pokemon"
        )
        elite_advanced_layout.addWidget(self.elite_pokemon_input)
        self.stop_for_elite_toggle.toggled.connect(
            self._on_elite_pokemon_stop_toggled
        )
        self._on_elite_pokemon_stop_toggled(
            self.stop_for_elite_toggle.isChecked()
        )

        auto_catching_layout = self._add_settings_section(
            encounter_page_layout,
            "Auto Catching Configuration",
        )
        self.auto_catching_configuration = auto_catching_layout.parentWidget()
        self.auto_catching_configuration.setObjectName(
            "auto_catching_configuration"
        )
        self.auto_catching_configuration.setVisible(False)

        hp_row = QHBoxLayout()
        hp_row.setContentsMargins(8, 0, 8, 0)
        hp_label = QLabel("Wild HP (To Catch)")
        hp_label.setObjectName("auto_catching_setting_label")
        hp_row.addWidget(hp_label, 1)
        self.auto_catching_hp_spin = QSpinBox()
        self.auto_catching_hp_spin.setObjectName("auto_catching_hp_spin")
        self.auto_catching_hp_spin.setRange(1, 100)
        self.auto_catching_hp_spin.setValue(100)
        self.auto_catching_hp_spin.setSuffix("%")
        hp_row.addWidget(self.auto_catching_hp_spin)
        auto_catching_layout.addLayout(hp_row)

        item_row = QHBoxLayout()
        item_row.setContentsMargins(8, 0, 8, 0)
        item_label = QLabel("Items to Catch")
        item_label.setObjectName("auto_catching_setting_label")
        item_row.addWidget(item_label, 1)
        self.auto_catching_item_selection = QComboBox()
        self.auto_catching_item_selection.setObjectName(
            "auto_catching_item_selection"
        )
        self.auto_catching_item_selection.setEditable(True)
        self.auto_catching_item_selection.addItems(
            (
                "Poke Ball",
                "Great Ball",
                "Ultra Ball",
                "Master Ball",
                "Premier Ball",
                "Luxury Ball",
                "Dive Ball",
                "Dusk Ball",
                "Heal Ball",
                "Net Ball",
                "Nest Ball",
                "Quick Ball",
                "Repeat Ball",
                "Timer Ball",
            )
        )
        self.auto_catching_item_selection.setCurrentText("Ultra Ball")
        item_row.addWidget(self.auto_catching_item_selection, 1)
        auto_catching_layout.addLayout(item_row)

        catch_help = QLabel(
            "Catch at or below the selected HP. Any enabled filter matches; "
            "no filters means all Pokemon."
        )
        catch_help.setObjectName("auto_catching_description")
        catch_help.setWordWrap(True)
        auto_catching_layout.addWidget(catch_help)

        filters_label = QLabel("Catch filters")
        filters_label.setObjectName("auto_catching_setting_label")
        filters_label.setContentsMargins(8, 4, 0, 0)
        auto_catching_layout.addWidget(filters_label)
        self.auto_catching_desired_toggle = self._add_toggle(
            auto_catching_layout, "Desired Pokemon", checked=False
        )
        self.auto_catching_shiny_toggle = self._add_toggle(
            auto_catching_layout, "Shiny Pokemon", checked=False
        )
        self.auto_catching_elite_toggle = self._add_toggle(
            auto_catching_layout, "Elite Pokemon", checked=False
        )
        catch_filter_help = QLabel(
            "Enable the matching Stop Battle rule above to use each filter. "
            "Desired and Elite selections use their Advanced Settings."
        )
        catch_filter_help.setObjectName("auto_catching_description")
        catch_filter_help.setWordWrap(True)
        auto_catching_layout.addWidget(catch_filter_help)

        self._on_desired_pokemon_stop_toggled(
            self.stop_for_desired_pokemon_toggle.isChecked()
        )
        self._on_shiny_pokemon_stop_toggled(
            self.stop_for_shiny_toggle.isChecked()
        )
        self._on_elite_pokemon_stop_toggled(
            self.stop_for_elite_toggle.isChecked()
        )

        self.auto_catching_toggle.toggled.connect(
            self._on_auto_catching_configuration_toggled
        )
        self._on_auto_catching_configuration_toggled(
            self.auto_catching_toggle.isChecked()
        )

        alerts_page_layout = page_layouts["Alerts"]
        alerts_layout = self._add_settings_section(
            alerts_page_layout,
            "Encounter alerts",
        )
        (
            self.desired_pokemon_sound_toggle,
            self.desired_pokemon_sound_path,
        ) = self._add_sound_setting(
            alerts_layout, "Play sound for Desired Pokemon"
        )
        (
            self.shiny_pokemon_sound_toggle,
            self.shiny_pokemon_sound_path,
        ) = self._add_sound_setting(
            alerts_layout, "Play sound for Shiny Pokemon"
        )
        (
            self.elite_pokemon_sound_toggle,
            self.elite_pokemon_sound_path,
        ) = self._add_sound_setting(
            alerts_layout, "Play sound for Elite Pokemon"
        )

        movement_page_layout = page_layouts["Movement"]
        self.auto_walk_toggle = self._add_toggle(
            movement_page_layout, "Auto Walk", checked=False
        )
        movement_settings_layout = self._add_settings_section(
            movement_page_layout,
            "Walking options",
        )
        self._add_auto_walk_settings(movement_settings_layout)

        more_page_layout = page_layouts["More"]
        appearance_layout = self._add_settings_section(
            more_page_layout, "Overlay appearance"
        )
        appearance_layout.setSpacing(6)
        transparency_label = QLabel("Background transparency")
        transparency_label.setObjectName("overlay_transparency_label")
        appearance_layout.addWidget(transparency_label)
        transparency_row = QHBoxLayout()
        transparency_row.setSpacing(12)
        self.overlay_background_transparency_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.overlay_background_transparency_slider.setObjectName(
            "overlay_transparency_slider"
        )
        self.overlay_background_transparency_slider.setRange(0, 100)
        self.overlay_background_transparency_slider.setPageStep(10)
        self.overlay_background_transparency_slider.setAccessibleName(
            "Overlay background transparency"
        )
        self.overlay_background_transparency_spin = QSpinBox()
        self.overlay_background_transparency_spin.setObjectName(
            "overlay_transparency_spin"
        )
        self.overlay_background_transparency_spin.setRange(0, 100)
        self.overlay_background_transparency_spin.setSuffix("%")
        self.overlay_background_transparency_spin.setFixedWidth(78)
        self.overlay_background_transparency_spin.setKeyboardTracking(False)
        self.overlay_background_transparency_spin.setAccessibleName(
            "Overlay background transparency percentage"
        )
        transparency_label.setBuddy(self.overlay_background_transparency_spin)
        self.overlay_background_transparency_slider.valueChanged.connect(
            self.overlay_background_transparency_spin.setValue
        )
        self.overlay_background_transparency_spin.valueChanged.connect(
            self.overlay_background_transparency_slider.setValue
        )
        transparency_row.addWidget(self.overlay_background_transparency_slider, 1)
        transparency_row.addWidget(self.overlay_background_transparency_spin)
        appearance_layout.addLayout(transparency_row)
        appearance_help = QLabel(
            "Fade panel backgrounds while keeping text and controls clear. "
            "0% keeps the original theme; 100% clears the backgrounds."
        )
        appearance_help.setObjectName("settings_help")
        appearance_help.setWordWrap(True)
        appearance_layout.addWidget(appearance_help)

        input_layout = self._add_settings_section(more_page_layout, "Battle input")
        self.use_mouse_clicking_toggle = self._add_toggle(
            input_layout, "Use Mouse Clicking", checked=False
        )
        self.use_mouse_clicking_toggle.setToolTip(
            "Off uses number keys; on uses mouse clicks. Keep Poke Nexus active. "
            "Saved regions are used to read the game in both modes."
        )
        input_help = QLabel(
            "Number keys are the default. Mouse mode returns the pointer after "
            "each click, unless you move it yourself.\n"
            "Battle: 1 Fight · 2 Bag · 3 Swap · 4 Run\n"
            "Moves: 1–4 · Bag: 1 Previous · 2 Next · 3 Use Item\n"
            "After fainting: select slots 2–6 directly."
        )
        input_help.setObjectName("settings_help")
        input_help.setWordWrap(True)
        input_layout.addWidget(input_help)

        overlay_layout = self._add_settings_section(
            more_page_layout,
            "Overlay and diagnostics",
        )
        self.show_pokemon_encounter_summary_toggle = self._add_toggle(
            overlay_layout, "Show Pokemon Encounter Summary", checked=False
        )
        self.show_chat_battle_logs_toggle = self._add_toggle(
            overlay_layout, "Show Chat Battle Logs", checked=False
        )
        self.show_test_region_logs_toggle = self._add_toggle(
            overlay_layout, "Show Test Region Configuration Logs", checked=True
        )
        self.save_test_region_screenshots_toggle = self._add_toggle(
            overlay_layout, "Save Test Region Images", checked=True
        )
        future_layout = self._add_settings_section(
            more_page_layout,
            "Planned features",
        )
        self._add_unavailable_setting(future_layout, "Auto Fishing with Battle")
        self._add_unavailable_setting(future_layout, "Auto Mining")

        self.content_scroll_area = QScrollArea()
        self.content_scroll_area.setObjectName("settings_scroll_area")
        self.content_scroll_area.setWidgetResizable(True)
        self.content_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_scroll_area.setWidget(content)
        content.setAutoFillBackground(False)
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        self.content_scroll_area.viewport().setAutoFillBackground(False)
        self.content_scroll_area.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self.content_scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.content_scroll_area.setMinimumHeight(0)
        panel_layout.addWidget(self.content_scroll_area)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self.glass_container,
        )
        self.buttons.setObjectName("settings_button_box")
        self.ok_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        )
        self.ok_button.setText("Okay")
        self.ok_button.setObjectName("settings_ok_button")
        self.cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.cancel_button.setObjectName("settings_cancel_button")
        self.cancel_button.setToolTip(
            "Close settings; changes already applied are kept"
        )
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer = QWidget()
        footer.setObjectName("settings_footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 2, 0, 0)
        footer_layout.setSpacing(8)
        save_hint = QLabel("Changes apply as you edit.")
        save_hint.setObjectName("settings_save_hint")
        save_hint.setWordWrap(True)
        footer_layout.addWidget(save_hint, 1)
        footer_layout.addWidget(self.buttons)
        panel_layout.addWidget(footer)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(2, 2, 2, 2)
        outer_layout.addWidget(self.glass_container)

        apply_overlay_stylesheet(self, "settings.qss")

    def _select_settings_page(self, index: int) -> None:
        if not 0 <= index < len(self._PAGE_DESCRIPTIONS):
            return
        self._settings_pages.setCurrentIndex(index)
        self.settings_tabs.setCurrentIndex(index, animate=False)
        self.page_description.setText(self._PAGE_DESCRIPTIONS[index])
        if self.isVisible():
            self.content_scroll_area.setFixedHeight(
                max(0, self.height() - self._fixed_panel_height())
            )
        content = self.content_scroll_area.widget()
        if content is not None:
            content.setMinimumHeight(
                max(
                    self.content_scroll_area.viewport().height(),
                    self._settings_pages.sizeHint().height(),
                )
            )
        self.content_scroll_area.verticalScrollBar().setValue(0)

    @staticmethod
    def _add_settings_section(
        layout: QVBoxLayout,
        title: str,
    ) -> QVBoxLayout:
        section = QFrame()
        section.setObjectName("settings_section")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(12, 10, 12, 12)
        section_layout.setSpacing(8)
        heading = QLabel(title, section)
        heading.setObjectName("settings_section_title")
        section_layout.addWidget(heading)
        layout.addWidget(section, alignment=Qt.AlignmentFlag.AlignTop)
        return section_layout

    def _on_auto_catching_configuration_toggled(self, enabled: bool) -> None:
        self.auto_catching_configuration.setVisible(enabled)
        self._refresh_settings_geometry()

    def _on_auto_battle_toggled(self, enabled: bool) -> None:
        if enabled and self.auto_run_toggle.isChecked():
            self.auto_run_toggle.setChecked(False)

    def _on_auto_run_toggled(self, enabled: bool) -> None:
        if enabled:
            if self.auto_battle_toggle.isChecked():
                self.auto_battle_toggle.setChecked(False)
            if self.auto_switch_fainted_pokemon_toggle.isChecked():
                self.auto_switch_fainted_pokemon_toggle.setChecked(False)

    def _on_auto_switch_toggled(self, enabled: bool) -> None:
        if enabled and self.auto_run_toggle.isChecked():
            self.auto_run_toggle.setChecked(False)

    def _add_toggle(self, layout: QVBoxLayout, label: str, checked: bool) -> QCheckBox:
        toggle = ToggleSwitch(label)
        toggle.setObjectName("setting_toggle")
        toggle.setAccessibleName(label)
        toggle.setToolTip(label)
        toggle.setChecked(checked)
        layout.addWidget(toggle)
        return toggle

    def _add_sound_setting(
        self,
        layout: QVBoxLayout,
        label: str,
    ) -> tuple[QCheckBox, QLineEdit]:
        toggle = self._add_toggle(layout, label, checked=False)
        file_row = QHBoxLayout()
        file_row.setContentsMargins(8, 0, 0, 0)
        path_input = QLineEdit()
        path_input.setObjectName("ringtone_path")
        path_input.setReadOnly(True)
        path_input.setPlaceholderText("No sound selected")
        file_row.addWidget(path_input, 1)
        browse_button = QPushButton("Choose Audio")
        browse_button.setObjectName("ringtone_browse_button")
        browse_button.setAutoDefault(False)
        browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_button.clicked.connect(
            lambda: self._select_ringtone(path_input)
        )
        file_row.addWidget(browse_button)
        layout.addLayout(file_row)
        toggle.toggled.connect(lambda: self.sound_settings_changed.emit())
        return toggle, path_input

    def _select_ringtone(self, path_input: QLineEdit) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a ringtone",
            path_input.text(),
            "Audio files (*.wav *.mp3);;WAV audio (*.wav);;MP3 audio (*.mp3)",
        )
        if not selected_path:
            return
        path_input.setText(selected_path)
        self.sound_settings_changed.emit()

    def _add_auto_walk_settings(self, layout: QVBoxLayout) -> None:
        self.auto_walk_advanced_toggle = QToolButton()
        self.auto_walk_advanced_toggle.setObjectName(
            "auto_walk_advanced_toggle"
        )
        self.auto_walk_advanced_toggle.setText("Advanced Auto Walk Settings")
        self.auto_walk_advanced_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.auto_walk_advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.auto_walk_advanced_toggle.setCheckable(True)
        self.auto_walk_advanced_toggle.setChecked(False)
        self.auto_walk_advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.auto_walk_advanced_toggle)

        self._auto_walk_advanced_settings = QWidget()
        self._auto_walk_advanced_settings.setObjectName(
            "auto_walk_advanced_settings"
        )
        advanced_layout = QVBoxLayout(self._auto_walk_advanced_settings)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)
        self._auto_walk_advanced_settings.setVisible(False)
        layout.addWidget(self._auto_walk_advanced_settings)
        self.auto_walk_toggle.toggled.connect(
            self._on_auto_walk_toggle_changed
        )
        self._on_auto_walk_toggle_changed(self.auto_walk_toggle.isChecked())
        self.auto_walk_advanced_toggle.toggled.connect(
            self._on_advanced_settings_toggled
        )

        self.auto_walk_direction_selection = RadioSelection(
            ("Horizontal", "Vertical", "Random"), "Horizontal"
        )
        self._add_radio_setting(
            advanced_layout, "Walk Direction", self.auto_walk_direction_selection
        )

        self.auto_walk_duration_mode_selection = RadioSelection(
            ("Default", "Random"), "Default"
        )
        self._add_radio_setting(
            advanced_layout, "Walk Duration", self.auto_walk_duration_mode_selection
        )

        self.auto_walk_duration_spin = self._create_duration_spin(0.5)
        self.auto_walk_duration_row = self._add_auto_walk_setting_row(
            advanced_layout, "Duration (seconds)", self.auto_walk_duration_spin
        )

        self.auto_walk_min_duration_spin = self._create_duration_spin(0.5)
        self.auto_walk_min_duration_row = self._add_auto_walk_setting_row(
            advanced_layout, "Minimum (seconds)", self.auto_walk_min_duration_spin
        )
        self.auto_walk_max_duration_spin = self._create_duration_spin(3.0)
        self.auto_walk_max_duration_row = self._add_auto_walk_setting_row(
            advanced_layout, "Maximum (seconds)", self.auto_walk_max_duration_spin
        )

        self.auto_walk_duration_mode_selection.currentTextChanged.connect(
            self._update_duration_controls
        )
        self.auto_walk_duration_mode_selection.currentTextChanged.connect(
            self._on_auto_walk_option_changed
        )
        self.auto_walk_direction_selection.currentTextChanged.connect(
            self._on_auto_walk_option_changed
        )
        self.auto_walk_min_duration_spin.valueChanged.connect(
            self._ensure_maximum_duration
        )
        self.auto_walk_max_duration_spin.valueChanged.connect(
            self._ensure_minimum_duration
        )
        for spin_box in (
            self.auto_walk_duration_spin,
            self.auto_walk_min_duration_spin,
            self.auto_walk_max_duration_spin,
        ):
            spin_box.valueChanged.connect(
                self._on_auto_walk_duration_changed
            )
        self._update_duration_controls(
            self.auto_walk_duration_mode_selection.currentText()
        )

    def _on_advanced_settings_toggled(self, expanded: bool) -> None:
        self._auto_walk_advanced_settings.setVisible(expanded)
        self.auto_walk_advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._refresh_settings_geometry()

    def _on_auto_walk_toggle_changed(self, enabled: bool) -> None:
        self._auto_walk_advanced_settings.setEnabled(enabled)

    def _on_elite_pokemon_advanced_toggled(self, expanded: bool) -> None:
        self._elite_pokemon_advanced_settings.setVisible(expanded)
        self.elite_pokemon_advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._refresh_settings_geometry()

    def _on_desired_pokemon_advanced_toggled(self, expanded: bool) -> None:
        self._desired_pokemon_advanced_settings.setVisible(expanded)
        self.desired_pokemon_advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._refresh_settings_geometry()

    def _on_desired_pokemon_stop_toggled(self, enabled: bool) -> None:
        self.desired_pokemon_input.setEnabled(enabled)
        catch_toggle = getattr(self, "auto_catching_desired_toggle", None)
        if catch_toggle is not None:
            catch_toggle.setEnabled(enabled)
            if not enabled:
                catch_toggle.setChecked(False)

    def _on_shiny_pokemon_stop_toggled(self, enabled: bool) -> None:
        catch_toggle = getattr(self, "auto_catching_shiny_toggle", None)
        if catch_toggle is not None:
            catch_toggle.setEnabled(enabled)
            if not enabled:
                catch_toggle.setChecked(False)

    def _on_elite_pokemon_stop_toggled(self, enabled: bool) -> None:
        self.elite_pokemon_input.setEnabled(enabled)
        catch_toggle = getattr(self, "auto_catching_elite_toggle", None)
        if catch_toggle is not None:
            catch_toggle.setEnabled(enabled)
            if not enabled:
                catch_toggle.setChecked(False)

    def _refresh_settings_geometry(self) -> None:
        if (
            self._last_game_geometry is not None
            and self._last_overlay_geometry is not None
        ):
            self.show_at(
                self._last_game_geometry,
                self._last_overlay_geometry,
            )

    @staticmethod
    def _add_radio_setting(
        layout: QVBoxLayout,
        label_text: str,
        selection: QWidget,
    ) -> None:
        label = QLabel(label_text)
        label.setObjectName("auto_walk_setting_label")
        label.setContentsMargins(8, 0, 0, 0)
        layout.addWidget(label)
        layout.addWidget(selection)

    @staticmethod
    def _create_duration_spin(value: float) -> QDoubleSpinBox:
        spin_box = QDoubleSpinBox()
        spin_box.setRange(0.5, 3600.0)
        spin_box.setDecimals(1)
        spin_box.setSingleStep(0.5)
        spin_box.setSuffix(" s")
        spin_box.setValue(value)
        return spin_box

    @staticmethod
    def _add_auto_walk_setting_row(
        layout: QVBoxLayout,
        label_text: str,
        control: QWidget,
    ) -> tuple[QLabel, QWidget]:
        row = QHBoxLayout()
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(8)
        label = QLabel(label_text)
        label.setObjectName("auto_walk_setting_label")
        row.addWidget(label, 1)
        row.addWidget(control)
        layout.addLayout(row)
        return label, control

    def _update_duration_controls(self, duration_mode: str) -> None:
        is_default = duration_mode == "Default"
        self.auto_walk_duration_row[0].setEnabled(is_default)
        self.auto_walk_duration_row[1].setEnabled(is_default)
        self.auto_walk_min_duration_row[0].setEnabled(not is_default)
        self.auto_walk_min_duration_row[1].setEnabled(not is_default)
        self.auto_walk_max_duration_row[0].setEnabled(not is_default)
        self.auto_walk_max_duration_row[1].setEnabled(not is_default)

    def _ensure_maximum_duration(self, minimum: float) -> None:
        if minimum > self.auto_walk_max_duration_spin.value():
            self.auto_walk_max_duration_spin.setValue(minimum)

    def _ensure_minimum_duration(self, maximum: float) -> None:
        if maximum < self.auto_walk_min_duration_spin.value():
            self.auto_walk_min_duration_spin.setValue(maximum)

    def _on_auto_walk_option_changed(self, _text: str) -> None:
        del _text
        self.auto_walk_configuration_changed.emit()

    def _on_auto_walk_duration_changed(self, _value: float) -> None:
        del _value
        self.auto_walk_configuration_changed.emit()

    @staticmethod
    def _add_unavailable_setting(layout: QVBoxLayout, label: str) -> None:
        item = QLabel(f"{label}   •   Unavailable")
        item.setObjectName("unavailable_setting")
        item.setWordWrap(True)
        item.setMinimumWidth(0)
        item.setFixedHeight(34)
        item.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(item)

    def show_at(self, game_geometry: QRect, overlay_geometry: QRect) -> None:
        self._last_game_geometry = QRect(game_geometry)
        self._last_overlay_geometry = QRect(overlay_geometry)
        self._drag_controller.set_bounds(game_geometry)
        margin = 8
        self.setFixedWidth(
            min(self._DEFAULT_WIDTH, max(1, game_geometry.width() - 2 * margin))
        )
        content = self.content_scroll_area.widget()
        content.setMinimumHeight(0)
        content.setMaximumHeight(16777215)
        content.adjustSize()
        content.setMinimumHeight(content.sizeHint().height())
        self.setMinimumHeight(0)
        self.content_scroll_area.setMinimumHeight(0)
        self.content_scroll_area.setMaximumHeight(16777215)
        self.setMaximumHeight(16777215)
        self.adjustSize()

        fixed_height = self._fixed_panel_height()

        below_y = overlay_geometry.bottom() + margin
        below_height = game_geometry.bottom() - below_y - margin
        above_height = overlay_geometry.top() - game_geometry.top() - 2 * margin
        if below_height < 160 and above_height > below_height:
            available_height = above_height
            y = overlay_geometry.top() - margin - available_height
        elif below_height > 0:
            available_height = below_height
            y = below_y
        else:
            available_height = game_geometry.height() - 2 * margin
            y = game_geometry.top() + margin

        max_game_height = max(1, game_geometry.height() - 2 * margin)
        available_height = min(
            560,
            max_game_height,
            max(fixed_height + 1, available_height),
        )
        visible_content_height = max(0, available_height - fixed_height)
        self.content_scroll_area.setFixedHeight(visible_content_height)
        self.setFixedHeight(available_height)

        x = overlay_geometry.x() + (overlay_geometry.width() - self.width()) // 2
        x = max(
            game_geometry.left() + margin,
            min(x, game_geometry.right() - self.width() - margin),
        )
        y = max(
            game_geometry.top() + margin,
            min(y, game_geometry.bottom() - self.height() - margin),
        )
        if not self.property("_overlay_user_moved"):
            self.move(x, y)
        self.show()
        self._drag_controller.set_bounds(game_geometry)
        self.raise_()
        self.visibility_changed.emit(True)

    def _fixed_panel_height(self) -> int:
        """Measure the header and footer at their real width, including wrapped text."""
        outer_margins = self.layout().contentsMargins()
        panel_layout = self.glass_container.layout()
        panel_margins = panel_layout.contentsMargins()
        frame_width = self.glass_container.frameWidth() * 2
        content_width = max(
            1,
            self.width()
            - outer_margins.left()
            - outer_margins.right()
            - panel_margins.left()
            - panel_margins.right()
            - frame_width,
        )
        fixed_height = (
            outer_margins.top()
            + outer_margins.bottom()
            + panel_margins.top()
            + panel_margins.bottom()
            + frame_width
        )
        fixed_items = 0
        for index in range(panel_layout.count()):
            item = panel_layout.itemAt(index)
            if item.widget() is self.content_scroll_area:
                continue
            item_height = (
                item.heightForWidth(content_width)
                if item.hasHeightForWidth()
                else item.sizeHint().height()
            )
            fixed_height += max(0, item_height)
            fixed_items += 1
        return fixed_height + panel_layout.spacing() * fixed_items

    def show_for_launcher(self, launcher_geometry: QRect) -> None:
        screen = QApplication.screenAt(launcher_geometry.center()) or self.screen()
        bounds = screen.availableGeometry()
        anchor = QRect(
            launcher_geometry.x(),
            launcher_geometry.y(),
            launcher_geometry.width(),
            1,
        )
        self.show_at(bounds, anchor)
        if not self.property("_overlay_user_moved"):
            self.move(
                launcher_geometry.center().x() - self.width() // 2,
                launcher_geometry.center().y() - self.height() // 2,
            )
            self._drag_controller.set_bounds(bounds)
        self.activateWindow()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.visibility_changed.emit(False)


class RadioSelection(QWidget):
    currentTextChanged = Signal(str)

    def __init__(
        self,
        options: tuple[str, ...],
        default: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(14)
        for option in options:
            button = QRadioButton(option, self)
            self._button_group.addButton(button)
            row.addWidget(button)
            button.toggled.connect(
                lambda checked, text=option: (
                    self.currentTextChanged.emit(text) if checked else None
                )
            )
        row.addStretch()
        self.setCurrentText(default)

    def currentText(self) -> str:
        button = self._button_group.checkedButton()
        if button is None:
            raise RuntimeError("Settings radio selection has no selected option.")
        return button.text()

    def setCurrentText(self, text: str) -> None:
        for button in self._button_group.buttons():
            if button.text() == text:
                button.setChecked(True)
                return


class ToggleSwitch(QCheckBox):
    """A checkbox rendered as a full-width setting row with a sliding switch."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(46)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def sizeHint(self) -> QSize:
        return QSize(360, self.heightForWidth(360))

    def minimumSizeHint(self) -> QSize:
        return QSize(160, 46)

    def heightForWidth(self, width: int) -> int:
        label_width = max(1, width - 80)
        text_height = self.fontMetrics().boundingRect(
            QRect(0, 0, label_width, 4096),
            Qt.TextFlag.TextWordWrap,
            self.text(),
        ).height()
        return max(46, text_height + 18)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        height = self.heightForWidth(self.width())
        if self.height() != height:
            self.setFixedHeight(height)

    def hitButton(self, position: QPoint) -> bool:
        return self.rect().contains(position)

    def paintEvent(self, _event: QPaintEvent) -> None:
        del _event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        enabled = self.isEnabled()
        hovered = enabled and self.underMouse()
        background = QColor(255, 255, 255, 20 if hovered else 8)
        if enabled and self.isChecked():
            background = QColor(45, 212, 191, 24 if hovered else 12)
        painter.setBrush(background)
        border = (
            QColor("#7DD3FC")
            if self.hasFocus()
            else QColor(125, 153, 187, 80 if hovered else 45)
        )
        painter.setPen(QPen(border, 1.5 if self.hasFocus() else 1))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8
        )

        switch = QRectF(self.width() - 54, (self.height() - 20) / 2, 42, 20)
        track_color = QColor("#2DD4BF") if self.isChecked() else QColor("#46566E")
        if not enabled:
            track_color = QColor("#344156")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(switch, 10, 10)

        knob_x = switch.right() - 17 if self.isChecked() else switch.left() + 3
        painter.setBrush(QColor("#F1F5FB" if enabled else "#8295AE"))
        painter.drawEllipse(QRectF(knob_x, switch.top() + 3, 14, 14))

        painter.setPen(QColor("#E6EDF7" if enabled else "#8295AE"))
        label_area = QRectF(12, 8, switch.left() - 24, self.height() - 16)
        painter.drawText(
            label_area,
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.TextFlag.TextWordWrap,
            self.text(),
        )
