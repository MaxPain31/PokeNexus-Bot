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
    QGridLayout,
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

from core.auto_battle import AUTO_BATTLE_MODES
from core.ev_training import (
    EV_STATS, EV_STAT_LIMIT, EV_TOTAL_LIMIT, EVProgress,
    default_ev_values, ev_yield_for_pokemon, load_ev_training_data,
)
from core.pokemon_rarity import RARITIES, load_pokemon_rarity_data
from core.pokemon_targets import PokemonTarget, parse_pokemon_targets
from overlay.appearance import apply_overlay_stylesheet, background_transparency
from overlay.window_drag import OverlayWindowDrag
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class _SettingsComboBox(QComboBox):
    """Keep mouse-only dropdowns non-activating when used over the game."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._popup: QWidget | None = None

    def showPopup(self) -> None:
        popup = self.view().window()
        self._popup = popup
        accepts_focus = not bool(
            self.window().windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Qt.Popup grabs keyboard focus even with WindowDoesNotAcceptFocus.
        # A tool window keeps mouse selection working without that activation.
        flags = (
            (Qt.WindowType.Popup if accepts_focus else Qt.WindowType.Tool)
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if not accepts_focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        popup.setWindowFlags(flags)
        popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not accepts_focus)
        popup.installEventFilter(self)
        animated = QApplication.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
        if not accepts_focus and animated:
            # The native roll animation can show the tool again after its
            # owning tab was hidden or an outside click dismissed it.
            QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
        try:
            super().showPopup()
        finally:
            if not accepts_focus and animated:
                QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, True)
        if not accepts_focus and popup.isVisible():
            QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        popup = self._popup
        if popup is not None and popup.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus:
            if watched is popup and event.type() == QEvent.Type.Hide:
                QApplication.instance().removeEventFilter(self)
            elif (
                event.type() == QEvent.Type.MouseButtonPress
                and isinstance(watched, QWidget)
                and watched is not self
                and not self.isAncestorOf(watched)
                and not popup.geometry().contains(event.globalPosition().toPoint())
            ):
                self.hidePopup()
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:
        self.hidePopup()
        super().hideEvent(event)


class _EVNumberInput(QSpinBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if background_transparency(self) == 100:
            # Windows sends clicks through zero-alpha pixels in a layered
            # window. An imperceptible fill keeps the whole number editable.
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(15, 23, 42, 1))


class _EVSpinBox(_EVNumberInput):
    """Type EV numbers in a focused editor while in-game Settings stays passive."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ev_training_spin")
        self.setRange(0, EV_STAT_LIMIT)
        self.setKeyboardTracking(False)
        self.lineEdit().installEventFilter(self)
        self._closing_editor = False
        self.popup = QFrame(
            self,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.popup.setObjectName("ev_training_number_popup")
        self.popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self.popup)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(self.popup)
        panel.setObjectName("ev_training_number_panel")
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        self.editor_title = QLabel(panel)
        self.editor_title.setObjectName("ev_training_number_title")
        self.editor_title.setWordWrap(True)
        layout.addWidget(self.editor_title)
        self.editor = _EVNumberInput(panel)
        self.editor.setObjectName("ev_training_spin")
        self.editor.setKeyboardTracking(False)
        self.editor.installEventFilter(self)
        self.editor.lineEdit().installEventFilter(self)
        self.editor.editingFinished.connect(self._finish_editor)
        layout.addWidget(self.editor)
        self.editor_help = QLabel(panel)
        self.editor_help.setObjectName("settings_help")
        self.editor_help.setWordWrap(True)
        layout.addWidget(self.editor_help)
        self.popup.installEventFilter(self)
        apply_overlay_stylesheet(self.popup, "settings.qss")

    def _show_editor(self) -> None:
        if not self.isEnabled():
            return
        self.editor.setRange(self.minimum(), self.maximum())
        self.editor.setValue(self.value())
        self.editor.setAccessibleName(self.accessibleName())
        self.editor_title.setText(self.accessibleName())
        self.editor_help.setText(f"0–{self.maximum()} · Enter to apply")
        self.popup.setFixedWidth(max(190, self.width()))
        self.popup.adjustSize()
        bounds = self.screen().availableGeometry()
        position = self.mapToGlobal(QPoint(0, self.height()))
        if position.y() + self.popup.height() > bounds.bottom():
            position.setY(self.mapToGlobal(QPoint()).y() - self.popup.height())
        position.setX(max(bounds.left(), min(position.x(), bounds.right() - self.popup.width() + 1)))
        position.setY(max(bounds.top(), min(position.y(), bounds.bottom() - self.popup.height() + 1)))
        self.popup.move(position)
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()
        self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.editor.selectAll()

    def _finish_editor(self, *, apply: bool = True) -> None:
        if self._closing_editor or not self.popup.isVisible():
            return
        self._closing_editor = True
        try:
            if apply and self.isEnabled():
                self.editor.interpretText()
                self.setValue(self.editor.value())
            self.popup.hide()
        finally:
            self._closing_editor = False

    def eventFilter(self, watched, event) -> bool:
        popup = getattr(self, "popup", None)
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and self.window().windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus:
                self._show_editor()
                return True
        if watched is self.lineEdit() and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.keyPressEvent(event)
                return True
        if popup is not None:
            if watched is popup and event.type() == QEvent.Type.Hide and not self._closing_editor:
                # Clicking outside a Qt.Popup closes it before editingFinished.
                if self.isEnabled():
                    self.editor.interpretText()
                    self.setValue(self.editor.value())
            if event.type() == QEvent.Type.KeyPress and watched in (self.editor, self.editor.lineEdit()):
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                    self._finish_editor(apply=event.key() != Qt.Key.Key_Escape)
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Enter commits an inline launcher edit without accepting Settings.
            self.interpretText()
            self.editingFinished.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        if hasattr(self, "popup"):
            self._finish_editor()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled() and hasattr(self, "popup"):
            self._finish_editor(apply=False)


class _PokemonCheckDelegate(QStyledItemDelegate):
    NATURE_MATCH_ROLE = Qt.ItemDataRole.UserRole.value + 1

    def __init__(self, parent=None, *, allow_synchronize: bool = True) -> None:
        super().__init__(parent)
        self.allow_synchronize = allow_synchronize

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

    @staticmethod
    def _synchronize_checkbox_rect(option: QStyleOptionViewItem) -> QRect:
        return QRect(
            option.rect.right() - 75,
            option.rect.top() + (option.rect.height() - 16) // 2,
            16, 16,
        )

    @staticmethod
    def _paint_checkbox(painter: QPainter, checkbox: QRect, checked: bool) -> None:
        painter.setPen(QPen(QColor("#22C55E" if checked else "#9CA3AF"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(checkbox, 3, 3)
        if checked:
            painter.setPen(QPen(QColor("#22C55E"), 2))
            painter.drawLine(
                checkbox.left() + 3, checkbox.top() + 8,
                checkbox.left() + 6, checkbox.top() + 11,
            )
            painter.drawLine(
                checkbox.left() + 6, checkbox.top() + 11,
                checkbox.left() + 13, checkbox.top() + 4,
            )

    def paint(self, painter: QPainter, option, index) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            painter.setOpacity(0.45)
        if selected:
            painter.fillRect(option.rect, QColor(56, 189, 248, 55))
        elif hovered:
            painter.fillRect(option.rect, QColor(56, 189, 248, 35))

        checkbox = self._checkbox_rect(option)
        checked = self._is_checked(index.data(Qt.ItemDataRole.CheckStateRole))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_checkbox(painter, checkbox, checked)
        if self.allow_synchronize:
            synchronize_checkbox = self._synchronize_checkbox_rect(option)
            synchronize_only = bool(index.data(self.NATURE_MATCH_ROLE))
            self._paint_checkbox(painter, synchronize_checkbox, synchronize_only)
            painter.setPen(QColor("#4ADE80" if synchronize_only else "#A6B7CD"))
            painter.drawText(
                QRect(synchronize_checkbox.right() + 6, option.rect.top(), 52, option.rect.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "S only",
            )

        text_option = QStyleOptionViewItem(option)
        self.initStyleOption(text_option, index)
        text_option.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        text_option.rect = option.rect.adjusted(32, 0, -82 if self.allow_synchronize else -6, 0)
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
        if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            return False
        mouse_toggle = (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and option.rect.contains(event.position().toPoint())
        )
        keyboard_toggle = (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select)
        )
        synchronize_toggle = (
            mouse_toggle
            and event.position().x() >= self._synchronize_checkbox_rect(option).left() - 4
        ) or (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_S
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        )
        if self.allow_synchronize and synchronize_toggle:
            return model.setData(
                index, not bool(index.data(self.NATURE_MATCH_ROLE)), self.NATURE_MATCH_ROLE,
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
    _POPUP_WIDTH = 340
    _POPUP_HEIGHT = 390

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        popup_title: str = "Choose desired Pokemon",
        allow_synchronize: bool = True,
        pokemon_names: list[str] | None = None,
        show_rarity: bool = True,
    ) -> None:
        super().__init__(parent)
        self._pokemon_names, self._rarity_by_name = self._load_pokemon_data()
        self._allow_custom_names = pokemon_names is None
        if pokemon_names is not None:
            self._pokemon_names = pokemon_names.copy()
        self._allow_synchronize = allow_synchronize
        self._excluded_names: set[str] = set()
        self._exclusion_reason = ""
        self._eligible_names: set[str] | None = None
        self._item_descriptions: dict[str, str] = {}
        self._selected_names: set[str] = set()
        self._synchronized_names: set[str] = set()
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
        self.display.setAccessibleName(popup_title)
        self.display.installEventFilter(self)
        row.addWidget(self.display, 1)

        self.popup = QFrame(
            self,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.popup.setObjectName("desired_pokemon_popup")
        self.popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        apply_overlay_stylesheet(self.popup, "settings.qss")
        self.popup.setFixedSize(self._POPUP_WIDTH, self._POPUP_HEIGHT)
        popup_outer_layout = QVBoxLayout(self.popup)
        popup_outer_layout.setContentsMargins(0, 0, 0, 0)
        # The native translucent Popup does not paint its own QSS background.
        # Use the same inner glass-panel pattern as the other overlay windows.
        self.popup_panel = QFrame(self.popup)
        self.popup_panel.setObjectName("desired_pokemon_popup_panel")
        popup_outer_layout.addWidget(self.popup_panel)
        popup_layout = QVBoxLayout(self.popup_panel)
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
        self.rarity_filter = _SettingsComboBox(self.popup)
        self.rarity_filter.setObjectName("desired_pokemon_rarity_filter")
        self.rarity_filter.setAccessibleName("Filter Pokemon by rarity")
        self.rarity_filter.addItem("All rarities", "")
        for code, label in self._RARITIES:
            self.rarity_filter.addItem(f"{code.upper()} - {label}", code)
        self.rarity_filter.currentIndexChanged.connect(self._filter_pokemon)
        popup_layout.addWidget(self.rarity_filter)
        self.rarity_filter.setVisible(show_rarity)
        self.synchronize_help = QLabel(
            "Check S only beside a Pokemon to require the green Synchronize S "
            "(Nature Match, not Shiny).", self.popup,
        )
        self.synchronize_help.setObjectName("desired_pokemon_synchronize_help")
        self.synchronize_help.setWordWrap(True)
        popup_layout.addWidget(self.synchronize_help)
        self.synchronize_help.setVisible(allow_synchronize)
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
        self.pokemon_list.setAccessibleDescription(
            "Select target Pokemon. S only requires Nature Match; Ctrl+S toggles it on the current row."
            if allow_synchronize else "Select target Pokemon. Space toggles the current row.",
        )
        self.pokemon_list.setItemDelegate(_PokemonCheckDelegate(
            self.pokemon_list, allow_synchronize=allow_synchronize,
        ))
        self.pokemon_list.installEventFilter(self)
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
            item.setData(_PokemonCheckDelegate.NATURE_MATCH_ROLE, key in self._synchronized_names)
            self._item_descriptions[key] = (
                f"Select {name} normally for any encounter, or check S only to require "
                "its green Synchronize marker. S only is Nature Match, not Shiny. "
                "Keyboard: Space selects; Ctrl+S toggles S only."
                if self._allow_synchronize else f"Select {name}. Keyboard: Space selects."
            )
            item.setToolTip(self._item_descriptions[key])
            self.pokemon_list.addItem(item)
            self._items_by_name[key] = item

    def text(self) -> str:
        return ", ".join(
            PokemonTarget(name, name.casefold() in self._synchronized_names).text
            for name in self._pokemon_names
            if name.casefold() in self._selected_names
        )

    def setText(self, value: str) -> None:
        targets = tuple(
            PokemonTarget(target.name, self._allow_synchronize and target.nature_match_only)
            for target in parse_pokemon_targets(value)
            if target.name.casefold() not in self._excluded_names
            and (self._allow_custom_names or target.name.casefold() in self._items_by_name)
        )
        selected_names = {
            target.name.casefold() for target in targets
        }
        synchronized_names = {
            target.name.casefold() for target in targets if target.nature_match_only
        }
        custom_names = [
            target.name for target in targets
            if target.name.casefold() not in self._items_by_name
        ]
        if custom_names:
            self._pokemon_names.extend(custom_names)
            self._add_names_to_list(custom_names)
        changed = (
            selected_names != self._selected_names
            or synchronized_names != self._synchronized_names
        )
        self._selected_names = selected_names
        self._synchronized_names = synchronized_names
        self.pokemon_list.blockSignals(True)
        try:
            for key, item in self._items_by_name.items():
                item.setCheckState(
                    Qt.CheckState.Checked
                    if key in self._selected_names
                    else Qt.CheckState.Unchecked
                )
                item.setData(_PokemonCheckDelegate.NATURE_MATCH_ROLE, key in self._synchronized_names)
        finally:
            self.pokemon_list.blockSignals(False)
        self._update_display()
        self._refresh_item_availability()
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

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            # Disabling the containing Auto Battle section must dismiss its
            # focusable popup just like disabling this picker directly.
            self.popup.hide()

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is getattr(self, "pokemon_list", None)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_S
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
            and self.isEnabled()
            and self._allow_synchronize
        ):
            item = self.pokemon_list.currentItem()
            if item is not None and not item.isHidden() and item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setData(
                    _PokemonCheckDelegate.NATURE_MATCH_ROLE,
                    not bool(item.data(_PokemonCheckDelegate.NATURE_MATCH_ROLE)),
                )
            return True
        if (
            watched is self.display
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and self.isEnabled()
        ):
            self._show_picker()
            return True
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

    def hideEvent(self, event) -> None:
        self.popup.hide()
        super().hideEvent(event)

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
        # High Windows display scaling can leave less logical screen space
        # than the normal popup height. Keep the search and list on-screen.
        self.popup.setFixedSize(
            max(1, min(self._POPUP_WIDTH, bounds.width())),
            max(1, min(self._POPUP_HEIGHT, bounds.height())),
        )
        x = anchor.x()
        x = max(bounds.left(), min(x, bounds.right() - self.popup.width() + 1))
        y = anchor.y() + 8
        if y + self.popup.height() > bounds.bottom() + 1:
            y = self.mapToGlobal(self.rect().topLeft()).y() - self.popup.height() - 8
        y = max(bounds.top(), min(y, bounds.bottom() - self.popup.height() + 1))
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
            matches_training = self._eligible_names is None or key in self._eligible_names
            item.setHidden(not (matches_query and matches_rarity and matches_training))
        self._update_select_all_state()

    def set_excluded_names(self, names: set[str], reason: str) -> None:
        """Block conflicting choices without changing the other selection."""
        self._excluded_names = names - self._selected_names
        self._exclusion_reason = reason
        self._refresh_item_availability()
        self._update_select_all_state()

    def _refresh_item_availability(self) -> None:
        self.pokemon_list.blockSignals(True)
        try:
            for key, item in self._items_by_name.items():
                flags = item.flags()
                if key in self._excluded_names:
                    flags &= ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setToolTip(self._exclusion_reason)
                else:
                    flags |= Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                    item.setToolTip(self._item_descriptions[key])
                item.setFlags(flags)
        finally:
            self.pokemon_list.blockSignals(False)
        self.pokemon_list.viewport().update()

    def set_eligible_names(self, names: set[str]) -> None:
        self._eligible_names = names
        self._filter_pokemon()

    def _on_select_all_changed(self, checked: bool) -> None:
        visible_items = [
            (key, item)
            for key, item in self._items_by_name.items()
            if not item.isHidden() and item.flags() & Qt.ItemFlag.ItemIsEnabled
        ]
        if not visible_items:
            return

        selected_names_before = self._selected_names.copy()
        for key, item in visible_items:
            if checked:
                self._selected_names.add(key)
            else:
                self._selected_names.discard(key)
                self._synchronized_names.discard(key)

        self.pokemon_list.blockSignals(True)
        try:
            for key, item in visible_items:
                item.setCheckState(
                    Qt.CheckState.Checked
                    if checked
                    else Qt.CheckState.Unchecked
                )
                item.setData(_PokemonCheckDelegate.NATURE_MATCH_ROLE, key in self._synchronized_names)
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
            key in self._selected_names for key in visible_keys if key not in self._excluded_names
        )
        selectable_keys = [key for key in visible_keys if key not in self._excluded_names]
        self.select_all.setEnabled(bool(selectable_keys))
        self.empty_state.setVisible(not visible_keys)
        is_all_selected = bool(selectable_keys) and (
            selected_count == len(selectable_keys)
        )
        self.select_all.blockSignals(True)
        self.select_all.setChecked(is_all_selected)
        self.select_all.blockSignals(False)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        key = item.text().casefold()
        if key in self._excluded_names:
            self.pokemon_list.blockSignals(True)
            try:
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(_PokemonCheckDelegate.NATURE_MATCH_ROLE, False)
            finally:
                self.pokemon_list.blockSignals(False)
            return
        selected = item.checkState() == Qt.CheckState.Checked
        synchronized = self._allow_synchronize and bool(item.data(_PokemonCheckDelegate.NATURE_MATCH_ROLE))
        if synchronized != (key in self._synchronized_names):
            # Checking S only also selects that species. Unchecking S only
            # keeps it selected normally; unchecking the main box removes both.
            selected = selected or synchronized
        elif not selected:
            synchronized = False
        if selected:
            self._selected_names.add(key)
        else:
            self._selected_names.discard(key)
        if synchronized:
            self._synchronized_names.add(key)
        else:
            self._synchronized_names.discard(key)
        self.pokemon_list.blockSignals(True)
        try:
            item.setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
            item.setData(_PokemonCheckDelegate.NATURE_MATCH_ROLE, synchronized)
        finally:
            self.pokemon_list.blockSignals(False)
        self._update_display()
        self._update_select_all_state()
        self.textChanged.emit(self.text())

    def _update_display(self) -> None:
        selected = [
            f"{name} (S only)" if name.casefold() in self._synchronized_names else name
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
        count_text = f"{len(selected)} selected" if selected else "No Pokemon selected"
        if self._synchronized_names:
            count_text += f" · {len(self._synchronized_names)} S only"
        self.selection_count.setText(count_text)


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
        page.installEventFilter(self)
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
        self.refreshHeight()

    def refreshHeight(self) -> None:
        if self._current_index < 0:
            return
        current_page = self._pages[self._current_index]
        current_page.ensurePolished()
        current_page.layout().activate()
        height = (
            current_page.heightForWidth(self.width())
            if current_page.hasHeightForWidth()
            else current_page.sizeHint().height()
        )
        self.setMinimumHeight(max(0, height))
        self.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._current_index >= 0:
            self._pages[self._current_index].setGeometry(self.rect())
            self.refreshHeight()

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.LayoutRequest
            and self._current_index >= 0
            and watched is self._pages[self._current_index]
        ):
            self.refreshHeight()
        return super().eventFilter(watched, event)


class SettingsWindow(QDialog):
    """Glass settings panel for the game overlay."""

    visibility_changed = Signal(bool)
    auto_walk_configuration_changed = Signal()
    auto_battle_configuration_changed = Signal()
    sound_settings_changed = Signal()
    _DEFAULT_WIDTH = 500
    _MINIMUM_CONTENT_HEIGHT = 80
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
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("settings_window")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle("Game Bot Settings")
        self.setFixedWidth(self._DEFAULT_WIDTH)
        self._last_game_geometry: QRect | None = None
        self._last_overlay_geometry: QRect | None = None
        self._updating_auto_battle_config = False

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
        self._add_auto_battle_settings(automation_layout)
        self.auto_switch_fainted_pokemon_toggle = self._add_toggle(
            automation_layout, "Auto Switch Fainted Pokemon", checked=False
        )
        self.auto_run_toggle = self._add_toggle(
            automation_layout, "Auto Run", checked=False
        )
        self.auto_battle_toggle.toggled.connect(
            self._on_auto_battle_toggled
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
        special_message_layout = self._add_settings_section(
            encounter_page_layout, "Special encounter popup",
        )
        self.auto_close_special_pokemon_message_toggle = self._add_toggle(
            special_message_layout, "Auto Close Special Pokemon Message", checked=True,
        )
        special_message_help = QLabel(
            'Closes the "You have encountered a special pokemon!" popup using '
            "the Message Box and Close Button regions, including in keyboard mode. "
            "When off, battle actions wait for you or the game timer to close it."
        )
        special_message_help.setObjectName("settings_help")
        special_message_help.setWordWrap(True)
        special_message_layout.addWidget(special_message_help)
        self.auto_close_special_pokemon_message_toggle.setToolTip(special_message_help.text())
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
        self.auto_catching_item_selection = _SettingsComboBox()
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
            "Desired and Elite selections use their Advanced Settings, including "
            "each Pokemon's S only requirement."
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
            QSizePolicy.Policy.Expanding,
        )
        self.content_scroll_area.viewport().setAutoFillBackground(False)
        self.content_scroll_area.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self.content_scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        self.content_scroll_area.setMinimumHeight(0)
        panel_layout.addWidget(self.content_scroll_area, 1)

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

        for picker in (self.specific_pokemon_input, self.ev_training_pokemon_input, self.desired_pokemon_input):
            picker.textChanged.connect(self._sync_battle_target_exclusions)
        self.specific_pokemon_input.textChanged.connect(self._emit_auto_battle_configuration_changed)
        self.ev_training_pokemon_input.textChanged.connect(self._on_ev_filters_changed)
        self.ev_training_pokemon_input.search_input.textChanged.connect(self._emit_auto_battle_configuration_changed)
        self.auto_battle_mode_selection.currentTextChanged.connect(self._on_auto_battle_mode_changed)
        self.auto_battle_elite_only_toggle.toggled.connect(self._on_auto_battle_mode_changed)
        self._sync_battle_target_exclusions()
        self._on_auto_battle_mode_changed()

    def _add_auto_battle_settings(self, layout: QVBoxLayout) -> None:
        self.auto_battle_configuration = QWidget()
        config = QVBoxLayout(self.auto_battle_configuration)
        config.setContentsMargins(0, 0, 0, 0)
        config.setSpacing(8)
        layout.addWidget(self.auto_battle_configuration)
        label = QLabel("Auto Battle Mode")
        label.setObjectName("auto_catching_setting_label")
        config.addWidget(label)
        self.auto_battle_mode_selection = _SettingsComboBox()
        self.auto_battle_mode_selection.setObjectName("auto_battle_mode_selection")
        self.auto_battle_mode_selection.setAccessibleName("Auto Battle Mode")
        self.auto_battle_mode_selection.addItems(AUTO_BATTLE_MODES)
        config.addWidget(self.auto_battle_mode_selection)
        help_label = QLabel(
            "Auto Battle takes priority over Auto Run. Specific Only uses Auto Run when enabled. "
            "EV Training automatically runs from unsuitable encounters. "
            "Stop Battle and Auto Catching rules still apply."
        )
        help_label.setObjectName("settings_help")
        help_label.setWordWrap(True)
        config.addWidget(help_label)

        self.specific_battle_settings = QWidget()
        specific = QVBoxLayout(self.specific_battle_settings)
        specific.setContentsMargins(0, 0, 0, 0)
        specific.setSpacing(8)
        specific.addWidget(QLabel("Specific Pokemon to battle"))
        self.specific_pokemon_input = _DesiredPokemonPicker(
            popup_title="Choose specific battle Pokemon", allow_synchronize=False,
        )
        specific.addWidget(self.specific_pokemon_input)
        conflict_help = QLabel(
            "Pokemon selected in Stop Battle → Desired Pokemon are unavailable. "
            "Uncheck them there first."
        )
        conflict_help.setObjectName("settings_help")
        conflict_help.setWordWrap(True)
        specific.addWidget(conflict_help)
        self.auto_battle_elite_only_toggle = self._add_toggle(
            specific, "Elite Pokemon Only", checked=False,
        )
        self.auto_battle_elite_only_toggle.setToolTip(
            "Battle selected species only when Elite. Disables Stop Battle for Elite Pokemon."
        )
        config.addWidget(self.specific_battle_settings)

        self._ev_training_entries = load_ev_training_data()
        self.ev_training_settings = QWidget()
        ev_layout = QVBoxLayout(self.ev_training_settings)
        ev_layout.setContentsMargins(0, 0, 0, 0)
        ev_layout.setSpacing(8)
        ev_layout.addWidget(QLabel("EV Training Pokemon"))
        self.ev_training_category_filter = _SettingsComboBox()
        self.ev_training_category_filter.setObjectName("ev_training_category_filter")
        self.ev_training_category_filter.setAccessibleName("Browse EV training category")
        self.ev_training_category_filter.setToolTip(
            "Browse the catalog by category. Selected Pokemon from every category stay active."
        )
        self.ev_training_category_filter.addItem("All training categories", "")
        for category in dict.fromkeys(entry.training_category for entry in self._ev_training_entries):
            self.ev_training_category_filter.addItem(category, category)
        ev_layout.addWidget(self.ev_training_category_filter)
        self.ev_training_pokemon_input = _DesiredPokemonPicker(
            popup_title="Choose EV training Pokemon", allow_synchronize=False, show_rarity=False,
            pokemon_names=list(dict.fromkeys(entry.pokemon for entry in self._ev_training_entries)),
        )
        for key in self.ev_training_pokemon_input._items_by_name:
            self.ev_training_pokemon_input._item_descriptions[key] = "\n".join(dict.fromkeys(
                f"{entry.training_category}: {entry.ev_yield} · {entry.region}, {entry.location}"
                for entry in self._ev_training_entries if entry.pokemon.casefold() == key
            ))
        ev_layout.addWidget(self.ev_training_pokemon_input)
        ev_help = QLabel(
            "Enter the current EVs of the Pokemon you are training, then its final targets. "
            "Click a number to type. Maximum: 252 per stat, 510 total. "
            "A target of 0 skips that stat; its current EVs still count toward 510.\n"
            "Selected Pokemon from all categories train the remaining stats together. "
            "Desired Pokemon selections are unavailable until unchecked there."
        )
        ev_help.setObjectName("settings_help")
        ev_help.setWordWrap(True)
        ev_layout.addWidget(ev_help)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self.ev_training_current_inputs: dict[str, _EVSpinBox] = {}
        self.ev_training_target_inputs: dict[str, _EVSpinBox] = {}
        self.ev_training_remaining_labels: dict[str, QLabel] = {}
        for column, text in enumerate(("Stat", "Current EV", "Target EV", "Remaining EV")):
            header = QLabel(text)
            header.setObjectName("ev_training_column_header")
            header.setWordWrap(True)
            header.setAlignment(Qt.AlignmentFlag.AlignCenter if column else Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(header, 0, column)
            grid.setColumnStretch(column, 1)
        for row, (key, label, _category) in enumerate(EV_STATS, start=1):
            grid.addWidget(QLabel(label), row, 0)
            for column, (kind, inputs) in enumerate((
                ("Current", self.ev_training_current_inputs), ("Target", self.ev_training_target_inputs),
            ), start=1):
                spin = _EVSpinBox()
                spin.setAccessibleName(f"{label} {kind} EV")
                spin.setToolTip(f"Type {label} {kind.lower()} EVs. Values share the 510 total EV limit.")
                inputs[key] = spin
                grid.addWidget(spin, row, column)
                spin.valueChanged.connect(self._on_ev_filters_changed)
            remaining = QLabel("0")
            remaining.setObjectName("ev_training_remaining")
            remaining.setAccessibleName(f"{label} Remaining EV")
            remaining.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.ev_training_remaining_labels[key] = remaining
            grid.addWidget(remaining, row, 3)
        ev_layout.addLayout(grid)
        self.ev_training_totals = QLabel()
        self.ev_training_totals.setObjectName("settings_help")
        self.ev_training_totals.setWordWrap(True)
        ev_layout.addWidget(self.ev_training_totals)
        self.ev_training_status = QLabel()
        self.ev_training_status.setObjectName("ev_training_status")
        self.ev_training_status.setWordWrap(True)
        ev_layout.addWidget(self.ev_training_status)
        self.ev_training_match_count = QLabel()
        self.ev_training_match_count.setObjectName("settings_help")
        self.ev_training_match_count.setWordWrap(True)
        ev_layout.addWidget(self.ev_training_match_count)
        self.ev_training_reset_button = QPushButton("Reset EV Training")
        self.ev_training_reset_button.setObjectName("ev_training_reset_button")
        self.ev_training_reset_button.setAutoDefault(False)
        self.ev_training_reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ev_training_reset_button.setToolTip(
            "Clear EV progress, targets, selections, and browsing filters. "
            "Enter the new Pokemon's current EVs before training."
        )
        self.ev_training_reset_button.clicked.connect(self._reset_ev_training)
        ev_layout.addWidget(self.ev_training_reset_button)
        self.ev_training_category_filter.currentIndexChanged.connect(self._on_ev_filters_changed)
        config.addWidget(self.ev_training_settings)
        self.specific_battle_settings.hide()
        self.ev_training_settings.hide()
        self._on_ev_filters_changed()

    def ev_training_progress(self) -> EVProgress:
        return EVProgress(
            {key: spin.value() for key, spin in self.ev_training_current_inputs.items()},
            {key: spin.value() for key, spin in self.ev_training_target_inputs.items()},
        )

    def ev_training_yield(self, pokemon: object) -> tuple[int, ...] | None:
        return ev_yield_for_pokemon(pokemon, self._ev_training_entries)

    def ev_training_target_names(self) -> str:
        """All selected yields that fit, independent of category/search browsing."""
        progress = self.ev_training_progress()
        return ", ".join(
            target.name for target in parse_pokemon_targets(self.ev_training_pokemon_input.text())
            if (yields := self.ev_training_yield(target.name)) is not None and progress.fits(yields)
        )

    def set_ev_training_progress(self, current: dict[str, int], targets: dict[str, int]) -> None:
        progress = EVProgress(current, targets)
        updating = self._updating_auto_battle_config
        self._updating_auto_battle_config = True
        try:
            for inputs, values in (
                (self.ev_training_current_inputs, progress.current),
                (self.ev_training_target_inputs, progress.targets),
            ):
                for key, spin in inputs.items():
                    spin.setMaximum(EV_STAT_LIMIT)
                    spin.setValue(values[key])
        finally:
            self._updating_auto_battle_config = updating
        self._on_ev_filters_changed()

    def _on_ev_filters_changed(self, *_args) -> None:
        if self._updating_auto_battle_config:
            return
        progress = self.ev_training_progress()
        for inputs, values in (
            (self.ev_training_current_inputs, progress.current),
            (self.ev_training_target_inputs, progress.targets),
        ):
            for key, spin in inputs.items():
                spin.setMaximum(min(EV_STAT_LIMIT, EV_TOTAL_LIMIT - sum(values.values()) + values[key]))
        for key, remaining in progress.remaining.items():
            self.ev_training_remaining_labels[key].setText(str(remaining))
        self.ev_training_totals.setText(
            f"Total EVs: {progress.total} / 510 · Target: {sum(progress.targets.values())} / 510\n"
            f"Remaining to targets: {sum(progress.remaining.values())} · Available: {510 - progress.total}"
        )
        if progress.total >= EV_TOTAL_LIMIT:
            status = "Complete — 510 total EVs reached. EV training is stopped."
        elif not any(progress.targets.values()):
            status = "Enter target EVs to start training."
        elif progress.complete:
            status = "Complete — all target EVs reached. EV training is stopped."
        else:
            status = "Confirmed defeats update progress automatically. Oversized yields use Auto Run."
        self.ev_training_status.setText(status)
        category = self.ev_training_category_filter.currentData()
        shown = {
            entry.pokemon.casefold() for entry in self._ev_training_entries
            if not category or entry.training_category == category
        }
        picker = self.ev_training_pokemon_input
        picker.set_eligible_names(shown)
        active = len(parse_pokemon_targets(self.ev_training_target_names()))
        self.ev_training_match_count.setText(
            f"{active} of {len(picker._selected_names)} selected Pokemon fit the remaining EVs. "
            "Category and search filter the catalog only."
        )
        self._emit_auto_battle_configuration_changed()

    def _reset_ev_training(self) -> None:
        self._updating_auto_battle_config = True
        try:
            picker = self.ev_training_pokemon_input
            picker.setText("")
            picker.search_input.clear()
            picker.rarity_filter.setCurrentIndex(0)
            self.ev_training_category_filter.setCurrentIndex(0)
            self.set_ev_training_progress(default_ev_values(), default_ev_values())
        finally:
            self._updating_auto_battle_config = False
        self._on_ev_filters_changed()

    def _sync_battle_target_exclusions(self, *_args) -> None:
        desired = self.desired_pokemon_input
        specific = self.specific_pokemon_input
        training = self.ev_training_pokemon_input
        for picker in (specific, training):
            picker.set_excluded_names(
                desired._selected_names,
                "Selected in Stop Battle → Desired Pokemon. Uncheck it there first.",
            )
        desired.set_excluded_names(
            specific._selected_names | training._selected_names,
            "Selected in Specific Only or EV Training Only. Uncheck it there first.",
        )

    def _on_auto_battle_mode_changed(self, *_args) -> None:
        mode = self.auto_battle_mode_selection.currentText()
        self.specific_battle_settings.setVisible(mode == "Specific Only")
        self.ev_training_settings.setVisible(mode == "EV Training Only")
        elite_only = mode == "Specific Only" and self.auto_battle_elite_only_toggle.isChecked()
        if elite_only:
            self.stop_for_elite_toggle.setChecked(False)
        self.stop_for_elite_toggle.setEnabled(not elite_only)
        self.elite_pokemon_advanced_toggle.setEnabled(not elite_only)
        self.stop_for_elite_toggle.setToolTip(
            "Disabled by Specific Only → Elite Pokemon Only."
            if elite_only else "Stop Battle for Elite Pokemon"
        )
        self._refresh_settings_geometry()
        self._emit_auto_battle_configuration_changed()

    def _emit_auto_battle_configuration_changed(self, *_args) -> None:
        if not self._updating_auto_battle_config:
            self.auto_battle_configuration_changed.emit()

    def _select_settings_page(self, index: int) -> None:
        if not 0 <= index < len(self._PAGE_DESCRIPTIONS):
            return
        self._settings_pages.setCurrentIndex(index)
        self.settings_tabs.setCurrentIndex(index, animate=False)
        self.page_description.setText(self._PAGE_DESCRIPTIONS[index])
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
        self.auto_battle_configuration.setEnabled(enabled)

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
        self._settings_pages.refreshHeight()
        if (
            self.isVisible()
            and self._last_game_geometry is not None
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
        spin_box.setRange(0.1, 3600.0)
        spin_box.setDecimals(1)
        spin_box.setSingleStep(0.1)
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

    def show_at(
        self,
        game_geometry: QRect,
        overlay_geometry: QRect,
        *,
        activate: bool = False,
    ) -> None:
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, not activate)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not activate)
        self._last_game_geometry = QRect(game_geometry)
        self._last_overlay_geometry = QRect(overlay_geometry)
        self._drag_controller.set_bounds(game_geometry)
        margin = 8
        width = min(self._DEFAULT_WIDTH, max(1, game_geometry.width() - 2 * margin))
        self.ensurePolished()
        fixed_height = self._fixed_panel_height(width)

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
            # Keep a complete control reachable in the scroll viewport when
            # a short game window leaves little room below the overlay.
            max(fixed_height + self._MINIMUM_CONTENT_HEIGHT, available_height),
        )
        # Apply the final native size once. The layout allocates the scroll
        # viewport; fixing its height too creates a conflicting minimum size.
        self.setFixedSize(width, available_height)
        self.layout().activate()
        self._settings_pages.refreshHeight()

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

    def _fixed_panel_height(self, width: int | None = None) -> int:
        """Measure the header and footer at their real width, including wrapped text."""
        outer_margins = self.layout().contentsMargins()
        panel_layout = self.glass_container.layout()
        panel_margins = panel_layout.contentsMargins()
        frame_width = self.glass_container.frameWidth() * 2
        content_width = max(
            1,
            (self.width() if width is None else width)
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
        self.show_at(bounds, anchor, activate=True)
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
