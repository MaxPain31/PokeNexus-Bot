import re

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from launcher.styles.style_loader import load_stylesheet
from models.region_config import REGION_FEATURES, REGION_ROLE_LABELS, SavedRegion
from overlay.appearance import apply_overlay_stylesheet
from overlay.panel_geometry import position_panel
from overlay.panel_widgets import ElidedPanelLabel, OverlayIconButton, OverlayPanelHeader
from overlay.region_edit_dialog import RegionEditDialog
from overlay.region_marker import RegionMarkerWindow
from overlay.region_name_dialog import RegionNameDialog
from overlay.window_drag import OverlayWindowDrag
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon

ASSETS_DIR = application_path("assets")
REGION_ICONS = REGION_FEATURES


class _RegionDetailsLabel(ElidedPanelLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _RegionFeatureButton(OverlayIconButton):
    """A labeled feature tile with its configured-role count."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setFixedHeight(70)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.count_label = QLabel(self)
        self.count_label.setObjectName("region_feature_count")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setFixedSize(28, 16)
        self.count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_role_count(self, configured: int, total: int) -> None:
        self.count_label.setText(f"{configured}/{total}")
        description = f"{configured} of {total} roles configured"
        self.setAccessibleDescription(description)
        self.setToolTip(f"{self.text()} — {description}")
        status = "complete" if configured == total else "partial" if configured else "empty"
        for widget in (self, self.count_label):
            if widget.property("status") != status:
                widget.setProperty("status", status)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.count_label.move(self.width() - self.count_label.width() - 5, 5)


class RegionWindow(QWidget):
    """A compact feature grid and per-feature capture-region editor."""

    visibility_changed = Signal(bool)
    closed = Signal()
    add_region_requested = Signal(str)
    test_region_requested = Signal(object)
    regions_changed = Signal(object)
    naming_dialog_changed = Signal(int)
    marker_windows_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("region_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Region Overlay")
        apply_overlay_stylesheet(self)

        self.glass_container = QFrame(self)
        self.glass_container.setObjectName("region_overlay_container")
        container_layout = QVBoxLayout(self.glass_container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.setSpacing(10)
        container_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(3, 3, 3, 3)
        outer_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        outer_layout.addWidget(self.glass_container)

        self.hover_label = QLabel(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.hover_label.setObjectName("region_hover_label")
        self.hover_label.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.hover_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hover_label.setStyleSheet(load_stylesheet("overlay.qss"))
        self.hover_label.hide()

        self.buttons: dict[str, _RegionFeatureButton] = {}
        self.regions: list[SavedRegion] = []
        self.current_feature: str | None = None
        self._game_geometry = QRect()
        self._ui_geometry = QRect()
        self._markers: list[RegionMarkerWindow] = []
        self._listed_feature: str | None = None
        self._hidden_for_dialog = False
        self.header = OverlayPanelHeader(
            "Region Overlay", "Select a feature to configure",
            "select_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg", self.glass_container,
        )
        self.feature_title = self.header.title
        self.feature_title.setObjectName("region_feature_title")
        self.back_button = self._icon_button(
            self.header, "region_back_button", "chevron-left.svg",
            "Back to all region features",
        )
        self.back_button.clicked.connect(self._close_feature)
        self.header.row.insertWidget(0, self.back_button)
        self.back_button.hide()
        self.close_button = self._icon_button(
            self.header, "region_close_button", "close.svg", "Close Region Overlay",
        )
        self.close_button.clicked.connect(self.close)
        self.header.add_action(self.close_button)
        container_layout.addWidget(self.header)
        self.stack = QStackedWidget(self.glass_container)
        self.stack.setMinimumSize(0, 0)
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.stack.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        container_layout.addWidget(self.stack, 1)
        self.toolbar_page = self._create_toolbar_page()
        self.feature_page = self._create_feature_page()
        self.stack.addWidget(self.toolbar_page)
        self.stack.addWidget(self.feature_page)
        self.stack.setCurrentWidget(self.toolbar_page)

        self._drag_controller = OverlayWindowDrag(
            self, (self.glass_container, self.header)
        )
        self._set_panel_size(toolbar=True)
        self._refresh_feature_page()
        self.hide()

    @staticmethod
    def _icon_button(
        parent: QWidget, name: str, icon_filename: str, tooltip: str,
    ) -> OverlayIconButton:
        button = OverlayIconButton(parent)
        button.setObjectName(name)
        button.setIcon(load_svg_icon(ASSETS_DIR / icon_filename, QSize(18, 18), "#C5D3E5"))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(32, 32)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        return button

    def _create_toolbar_page(self) -> QWidget:
        page = QWidget(self.stack)
        page.setObjectName("region_toolbar_page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        self.toolbar_scroll_area = QScrollArea(page)
        self.toolbar_scroll_area.setObjectName("region_feature_scroll")
        self.toolbar_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.toolbar_scroll_area.setWidgetResizable(True)
        self.toolbar_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.toolbar_scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toolbar_scroll_area.setMinimumSize(0, 0)
        content = QWidget()
        content.setObjectName("region_feature_grid")
        self._feature_grid = QGridLayout(content)
        self._feature_grid.setContentsMargins(0, 0, 0, 0)
        self._feature_grid.setSpacing(8)
        self._feature_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._toolbar_columns = 0

        for feature, label in REGION_FEATURES:
            button = _RegionFeatureButton(content)
            button.setObjectName(f"region_{feature.replace('-', '_')}_button")
            button.setIcon(
                load_svg_icon(
                    ASSETS_DIR / f"{feature}.svg",
                    QSize(24, 24),
                    fill_color="#C5D3E5",
                )
            )
            button.setIconSize(QSize(24, 24))
            button.setText(label)
            button.setAccessibleName(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda checked=False, feature=feature: self._on_feature_icon_clicked(
                    feature, checked
                )
            )
            button.hovered.connect(
                lambda button=button: self._show_hover_label(
                    button, button.toolTip()
                )
            )
            button.unhovered.connect(self.hover_label.hide)
            self.buttons[feature] = button
        self._reflow_feature_grid(3)
        self.toolbar_scroll_area.setWidget(content)
        self.toolbar_scroll_area.viewport().installEventFilter(self)
        layout.addWidget(self.toolbar_scroll_area, 1)
        self.configuration_label = QLabel(page)
        self.configuration_label.setObjectName("region_configuration_status")
        self.configuration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.configuration_label)
        return page

    def _reflow_feature_grid(self, columns: int) -> None:
        if columns == self._toolbar_columns:
            return
        for column in range(self._toolbar_columns):
            self._feature_grid.setColumnStretch(column, 0)
        while self._feature_grid.count():
            self._feature_grid.takeAt(0)
        for index, button in enumerate(self.buttons.values()):
            self._feature_grid.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self._feature_grid.setColumnStretch(column, 1)
        self._toolbar_columns = columns

    def _on_feature_icon_clicked(self, feature: str, checked: bool) -> None:
        del checked
        self._open_feature(feature)

    def _create_feature_page(self) -> QWidget:
        page = QWidget(self.stack)
        page.setObjectName("region_feature_page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        self.region_hint = QLabel(
            "Add a capture region and assign its role. Use Edit for exact "
            "position and size, or Test to check detection."
        )
        self.region_hint.setObjectName("region_hint")
        self.region_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.region_hint.setWordWrap(True)
        self.region_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.region_hint)
        self._default_region_hint = self.region_hint.text()

        self.region_list = QListWidget()
        self.region_list.setObjectName("region_list")
        self.region_list.setMinimumSize(0, 0)
        self.region_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.region_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.region_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.region_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.region_list.setSpacing(4)
        layout.addWidget(self.region_list, 1)

        self.empty_state = QWidget(self.region_list.viewport())
        self.empty_state.setObjectName("region_empty_state")
        self.empty_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(16, 10, 16, 10)
        empty_layout.setSpacing(6)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_icon = QLabel(self.empty_state)
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_icon.setPixmap(load_svg_icon(
            ASSETS_DIR / "select_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
            QSize(32, 32), "#7DD3FC",
        ).pixmap(QSize(32, 32)))
        self.empty_title = QLabel("No regions yet", self.empty_state)
        self.empty_title.setObjectName("overlay_log_empty_title")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description = QLabel(
            "Click Add Region, then drag over the part of the game you want to capture.",
            self.empty_state,
        )
        self.empty_description.setObjectName("overlay_log_empty_description")
        self.empty_description.setWordWrap(True)
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_icon)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_description)
        self.region_list.viewport().installEventFilter(self)

        self.add_region_button = QPushButton("+  Add Region")
        self.add_region_button.setObjectName("region_add_button")
        self.add_region_button.setFixedHeight(36)
        self.add_region_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_region_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_region_button.clicked.connect(self._request_add_region)
        layout.addWidget(self.add_region_button)
        self.outline_hint = QLabel("Drag outlines to move · Arrow keys to nudge")
        self.outline_hint.setObjectName("region_outline_hint")
        self.outline_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.outline_hint.setWordWrap(True)
        self.outline_hint.setToolTip(
            "Click an outline, then use arrow keys to move it 1 px (Shift = 10 px).\n"
            "Alt-click an outline, or use its Remove button, to delete the region."
        )
        layout.addWidget(self.outline_hint)
        return page

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            if watched is self.toolbar_scroll_area.viewport():
                width = self.toolbar_scroll_area.viewport().width()
                self._reflow_feature_grid(3 if width >= 310 else 2 if width >= 200 else 1)
            elif hasattr(self, "region_list") and watched is self.region_list.viewport():
                self._resize_empty_state()
        return super().eventFilter(watched, event)

    def _resize_empty_state(self) -> None:
        viewport = self.region_list.viewport().rect()
        self.empty_state.setGeometry(viewport)
        self.empty_icon.setVisible(viewport.height() >= 140)
        self.empty_description.setVisible(viewport.height() >= 100)

    def show_for_game(self, game_geometry: QRect, *, ui_geometry: QRect | None = None) -> None:
        if not game_geometry.isValid():
            return
        self._game_geometry = QRect(game_geometry)
        self._ui_geometry = QRect(
            ui_geometry if ui_geometry is not None and ui_geometry.isValid() else game_geometry
        )
        if self._hidden_for_dialog:
            return
        self._position_for_game()
        was_visible = self.isVisible()
        if not was_visible:
            self.show()
            self.raise_()
        self._sync_markers()
        if not was_visible:
            self.visibility_changed.emit(True)

    def _position_for_game(self) -> None:
        bounds = self._ui_geometry if self._ui_geometry.isValid() else self._game_geometry
        if bounds.isValid():
            position_panel(
                self, bounds, self._preferred_size,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        else:
            self.setFixedSize(self._preferred_size)
        self.region_hint.setVisible(self.height() >= 300)
        self.outline_hint.setVisible(self.height() >= 380)
        self.layout().activate()

    def set_regions(self, regions: tuple[SavedRegion, ...]) -> None:
        self.regions = list(regions)
        self._refresh_feature_page()

    def prompt_region_name(self, feature: str, geometry: QRect) -> None:
        if feature not in dict(REGION_FEATURES):
            raise ValueError(f"Unknown region feature: {feature}")

        role_names = self._available_role_names(feature)
        if not role_names:
            self.region_hint.setText(
                f"All roles for {dict(REGION_FEATURES)[feature]} are already "
                "configured. Remove an existing region to replace it."
            )
            return

        self._reset_region_hint()
        dialog = RegionNameDialog(role_names, self.parentWidget())
        self._center_dialog(dialog)
        if not self._exec_dialog(dialog):
            return

        rect = geometry.normalized()
        region = SavedRegion(
            feature=feature,
            label=dialog.region_name,
            x=rect.x(),
            y=rect.y(),
            width=rect.width(),
            height=rect.height(),
        )
        self.regions.append(region)
        self._reset_region_hint()
        self._refresh_feature_page()
        self.regions_changed.emit(tuple(self.regions))

    def edit_region(self, region: SavedRegion) -> None:
        if region not in self.regions or not self._game_geometry.isValid():
            return

        dialog = RegionEditDialog(region, self._game_geometry, self.parentWidget())
        self._center_dialog(dialog)
        dialog.geometry_changed.connect(
            lambda geometry, region=region: self._preview_region_geometry(
                region,
                geometry,
            )
        )
        if not self._exec_dialog(dialog, hide_region_window=False):
            self._preview_region_geometry(
                region,
                QRect(region.x, region.y, region.width, region.height),
            )
            return
        self._set_region_geometry(region, dialog.edited_geometry)

    def _set_region_geometry(self, region: SavedRegion, geometry: QRect) -> None:
        rect = geometry.normalized()
        if not self._game_geometry.contains(rect):
            self.region_hint.setText(
                "The region must stay within the game window."
            )
            return
        try:
            region_index = self.regions.index(region)
        except ValueError:
            return

        updated_region = SavedRegion(
            feature=region.feature,
            label=region.label,
            x=rect.x(),
            y=rect.y(),
            width=rect.width(),
            height=rect.height(),
        )
        self.regions[region_index] = updated_region
        for marker in self._markers:
            if marker.region == region:
                marker.region = updated_region
                marker.setGeometry(rect)
        self._reset_region_hint()
        self._refresh_feature_page()
        self.regions_changed.emit(tuple(self.regions))

    def _preview_region_geometry(
        self,
        region: SavedRegion,
        geometry: QRect,
    ) -> None:
        rect = geometry.normalized()
        if not self._game_geometry.contains(rect):
            return
        for marker in self._markers:
            if marker.region == region:
                marker.setGeometry(rect)

    def _on_marker_selected(self, selected_marker: RegionMarkerWindow) -> None:
        for marker in self._markers:
            marker.set_selected(marker is selected_marker)

    def _center_dialog(self, dialog: QWidget) -> None:
        dialog.adjustSize()
        bounds = self._ui_geometry if self._ui_geometry.isValid() else self._game_geometry
        if bounds.isValid():
            center = bounds.center()
        else:
            center = self.geometry().center()
        dialog.move(
            center.x() - dialog.width() // 2,
            center.y() - dialog.height() // 2,
        )

    def _exec_dialog(
        self,
        dialog: RegionNameDialog | RegionEditDialog,
        *,
        hide_region_window: bool = True,
    ) -> bool:
        region_window_was_visible = self.isVisible()
        self._hidden_for_dialog = hide_region_window
        if hide_region_window and region_window_was_visible:
            self.hide()
        if hide_region_window:
            self._hide_markers()

        dialog_id = int(dialog.winId())
        self.naming_dialog_changed.emit(dialog_id)
        try:
            dialog.raise_()
            dialog.activateWindow()
            accepted = dialog.exec() == dialog.DialogCode.Accepted
        finally:
            self._hidden_for_dialog = False
            self.naming_dialog_changed.emit(0)
            if hide_region_window and region_window_was_visible:
                self.show()
                self.raise_()
            if hide_region_window:
                self._sync_markers()
        return accepted

    def remove_region(self, region: SavedRegion) -> None:
        try:
            self.regions.remove(region)
        except ValueError:
            return
        self._refresh_feature_page()
        self.regions_changed.emit(tuple(self.regions))

    def _open_feature(self, feature: str) -> None:
        self.current_feature = feature
        self.feature_title.setText(dict(REGION_FEATURES)[feature])
        self.header.icon.hide()
        self.back_button.show()
        self.stack.setCurrentWidget(self.feature_page)
        self._set_panel_size(toolbar=False)
        self.hover_label.hide()
        self._refresh_feature_page()
        self._sync_markers()
        self.raise_()

    def _close_feature(self) -> None:
        self.stack.setCurrentWidget(self.toolbar_page)
        self.current_feature = None
        self.header.icon.show()
        self.back_button.hide()
        self._set_panel_size(toolbar=True)
        self._refresh_feature_page()
        self._hide_markers()
        self.raise_()

    def _request_add_region(self) -> None:
        if self.current_feature is not None and self._available_role_names(self.current_feature):
            self.add_region_requested.emit(self.current_feature)

    def _set_panel_size(self, toolbar: bool) -> None:
        self._preferred_size = QSize(368, 420 if toolbar else 456)
        self._position_for_game()

    def _reset_region_hint(self) -> None:
        self.region_hint.setText(
            "Select the full fishing gauge, including the green zone and both "
            "markers. Preview shows live detection."
            if self.current_feature == "fishing"
            else self._default_region_hint
        )

    def _refresh_feature_page(self) -> None:
        configured = 0
        total = 0
        for feature, button in self.buttons.items():
            role_count = len(REGION_ROLE_LABELS[feature])
            used = role_count - len(self._available_role_names(feature))
            button.set_role_count(used, role_count)
            configured += used
            total += role_count
        self.configuration_label.setText(f"{configured} of {total} roles configured")
        scroll_value = (
            self.region_list.verticalScrollBar().value()
            if self._listed_feature == self.current_feature else 0
        )
        self._listed_feature = self.current_feature
        self.region_list.clear()
        if self.current_feature is None:
            self.feature_title.setText("Region Overlay")
            self.header.subtitle.setText("Select a feature to configure")
            return
        self._reset_region_hint()
        available_roles = self._available_role_names(self.current_feature)
        role_count = len(REGION_ROLE_LABELS[self.current_feature])
        self.header.subtitle.setText(
            f"{role_count - len(available_roles)} of {role_count} roles configured"
        )
        self.add_region_button.setEnabled(bool(available_roles))
        self.add_region_button.setText("+  Add Region" if available_roles else "All roles configured")
        self.add_region_button.setToolTip(
            "Available roles: " + ", ".join(available_roles)
            if available_roles else "Edit or remove an existing region to change its capture area."
        )
        for region in self.regions:
            if region.feature != self.current_feature:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, region)
            row = QFrame(self.region_list)
            row.setObjectName("region_card")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(4)

            details = _RegionDetailsLabel(region.label, row)
            details.setObjectName("region_item_details")
            details.setCursor(Qt.CursorShape.PointingHandCursor)
            details.setToolTip(f"{region.label}\nClick to edit position and size")
            details.clicked.connect(
                lambda region=region: self.edit_region(region)
            )
            row_layout.addWidget(details)
            geometry_label = ElidedPanelLabel(
                f"{region.width} × {region.height} px  ·  X {region.x}, Y {region.y}", row,
            )
            geometry_label.setObjectName("region_item_geometry")
            row_layout.addWidget(geometry_label)

            actions = QHBoxLayout()
            actions.setContentsMargins(0, 4, 0, 0)
            actions.setSpacing(6)
            edit_button = QPushButton("Edit", row)
            edit_button.setObjectName("region_edit_button")
            edit_button.setFixedSize(64, 28)
            edit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_button.setAccessibleName(f"Edit {region.label}")
            edit_button.setToolTip("Edit the exact position and size")
            edit_button.clicked.connect(lambda checked=False, region=region: self.edit_region(region))
            actions.addWidget(edit_button)

            test_button = QPushButton("Preview" if region.feature == "fishing" else "Test", row)
            test_button.setObjectName("region_test_button")
            test_button.setCursor(Qt.CursorShape.PointingHandCursor)
            test_button.setFixedSize(80, 28)
            test_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            test_button.setAccessibleName(f"Test {region.label}")
            test_button.setToolTip("Test detection in this capture region")
            if region.feature == "fishing":
                test_button.setToolTip(
                    "Show a realtime fishing preview; this test does not press Space."
                )
            test_button.clicked.connect(
                lambda checked=False, region=region: self._on_test_region_clicked(
                    region, checked
                )
            )
            actions.addWidget(test_button)
            actions.addStretch(1)
            remove_button = self._icon_button(
                row, "region_remove_button",
                "delete_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg",
                f"Remove {region.label}",
            )
            remove_button.setFixedSize(28, 28)
            remove_button.setIconSize(QSize(16, 16))
            remove_button.clicked.connect(
                lambda checked=False, region=region: self.remove_region(region)
            )
            actions.addWidget(remove_button)
            row_layout.addLayout(actions)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.region_list.addItem(item)
            self.region_list.setItemWidget(item, row)
        self.empty_state.setVisible(self.region_list.count() == 0)
        self._resize_empty_state()
        self.region_list.verticalScrollBar().setValue(scroll_value)
        self._sync_markers()

    def _on_test_region_clicked(self, region: SavedRegion, checked: bool) -> None:
        del checked
        self.test_region_requested.emit(region)

    def _sync_markers(self) -> None:
        if not self.isVisible() or self.stack.currentWidget() is not self.feature_page:
            self._hide_markers()
            return
        desired = [
            region for region in self.regions if region.feature == self.current_feature
        ]
        if len(desired) == len(self._markers) and all(
            marker.isVisible() and marker.region == region
            for marker, region in zip(self._markers, desired)
        ):
            return
        self._hide_markers()
        for region in desired:
            marker = RegionMarkerWindow(region, self._game_geometry)
            marker.remove_requested.connect(self.remove_region)
            marker.move_requested.connect(self._set_region_geometry)
            marker.selected_changed.connect(self._on_marker_selected)
            marker.show()
            marker.raise_()
            self._markers.append(marker)
        self.marker_windows_changed.emit(
            tuple(int(marker.winId()) for marker in self._markers)
        )
        if desired:
            self.raise_()

    def _hide_markers(self) -> None:
        markers, self._markers = self._markers, []
        self.marker_windows_changed.emit(())
        for marker in markers:
            marker.close()
            marker.deleteLater()

    def _show_hover_label(self, button: QToolButton, text: str) -> None:
        if not self.isVisible() or self.stack.currentWidget() is not self.toolbar_page:
            return

        self.hover_label.setText(text)
        self.hover_label.adjustSize()
        button_origin = button.mapToGlobal(QPoint(0, 0))
        screen = button.screen()
        if screen is not None:
            bounds = screen.availableGeometry()
        else:
            bounds = QRect(button_origin.x(), button_origin.y(), 1, 1)

        x = button_origin.x() - self.hover_label.width() - 8
        if x < bounds.left():
            x = button_origin.x() + button.width() + 8
        x = max(
            bounds.left(),
            min(x, bounds.right() - self.hover_label.width() + 1),
        )
        y = button_origin.y() + (button.height() - self.hover_label.height()) // 2
        y = max(
            bounds.top(),
            min(y, bounds.bottom() - self.hover_label.height() + 1),
        )
        self.hover_label.move(x, y)
        self.hover_label.show()
        self.hover_label.raise_()

    def _available_role_names(self, feature: str) -> tuple[str, ...]:
        used_names = {
            self._canonical_role_name(region)
            for region in self.regions
            if region.feature == feature
        }
        return tuple(
            role_name
            for role_name in REGION_ROLE_LABELS[feature]
            if role_name not in used_names
        )

    @staticmethod
    def _canonical_role_name(region: SavedRegion) -> str | None:
        label = region.label.casefold()
        roles = REGION_ROLE_LABELS[region.feature]
        exact_match = next(
            (role for role in roles if role.casefold() == label),
            None,
        )
        if exact_match is not None:
            return exact_match

        if len(roles) == 1:
            return roles[0]

        if region.feature == "health":
            if "enemy" in label or "opponent" in label:
                return "Enemy Health"
            if any(token in label for token in ("ally", "player", "your")):
                return "Ally Health"
            if re.search(r"(?:pokemon|health)\s*1\b", label):
                return "Ally Health"
            if re.search(r"(?:pokemon|health)\s*2\b", label):
                return "Enemy Health"
        elif region.feature == "pokemon-name":
            if "wild" in label:
                return "Wild Pokemon"
            if any(token in label for token in ("your", "current", "player", "ally")):
                return "Your Pokemon"
            if re.search(r"pokemon name\s*1\b", label):
                return "Your Pokemon"
            if re.search(r"pokemon name\s*2\b", label):
                return "Wild Pokemon"
        elif region.feature == "pokemon-team":
            match = re.search(r"(?:pokemon team|team)(?: slot)?\s*(\d+)\b", label)
            if match:
                slot = int(match.group(1))
                role = f"Pokemon Team Slot {slot}"
                return role if role in roles else None
        elif region.feature == "moves":
            match = re.search(r"moves?(?: slot)?\s*(\d+)\b", label)
            if match:
                slot = int(match.group(1))
                role = f"Move Slot {slot}"
                return role if role in roles else None
        return None

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.hover_label.hide()
        self._hide_markers()
        self.visibility_changed.emit(False)

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
        self.closed.emit()
