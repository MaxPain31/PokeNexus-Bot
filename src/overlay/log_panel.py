from contextlib import contextmanager

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from overlay.appearance import apply_overlay_stylesheet
from overlay.panel_geometry import position_panel
from overlay.panel_widgets import OverlayIconButton, OverlayPanelHeader
from overlay.window_drag import OverlayWindowDrag
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class OverlayLogPanel(QWidget):
    """Shared read-only log panel with an empty state and optional live scrolling."""

    def __init__(
        self,
        *,
        prefix: str,
        title: str,
        subtitle: str,
        icon_filename: str,
        preferred_size: QSize,
        maximum_blocks: int,
        empty_title: str,
        empty_description: str,
        alignment: Qt.AlignmentFlag,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName(f"{prefix}_window")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle(title)
        self._preferred_size = preferred_size
        self._alignment = alignment
        self._appending = False
        self.setFixedSize(preferred_size)

        self.container = QFrame(self)
        self.container.setObjectName(f"{prefix}_container")
        layout = QVBoxLayout(self.container)
        self._panel_layout = layout
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(10)
        self.header = OverlayPanelHeader(title, subtitle, icon_filename, self.container)
        self.title = self.header.title
        self.title.setObjectName(f"{prefix}_title")
        layout.addWidget(self.header)

        self.clear_button = OverlayIconButton(self.container)
        self.clear_button.setObjectName(f"{prefix}_clear_button")
        self.clear_button.setIcon(
            load_svg_icon(
                application_path(
                    "assets", "delete_24dp_1F1F1F_FILL0_wght400_GRAD0_opsz24.svg"
                ),
                QSize(18, 18),
                fill_color="#C5D3E5",
            )
        )
        self.clear_button.setIconSize(QSize(18, 18))
        self.clear_button.setFixedSize(32, 32)
        self.clear_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_button.setToolTip("Clear this log")
        self.clear_button.setAccessibleName(f"Clear {title.lower()}")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.add_action(self.clear_button)

        self.log_view = QPlainTextEdit(self.container)
        self.log_view.setObjectName(f"{prefix}_text")
        self.log_view.setReadOnly(True)
        self.log_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log_view.setAccessibleName(title)
        self.log_view.setMaximumBlockCount(maximum_blocks)
        layout.addWidget(self.log_view, 1)

        self.empty_state = QWidget(self.log_view.viewport())
        self.empty_state.setObjectName("overlay_log_empty_state")
        self.empty_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_layout = QVBoxLayout(self.empty_state)
        self._empty_layout = empty_layout
        empty_layout.setContentsMargins(16, 12, 16, 12)
        empty_layout.setSpacing(6)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = QLabel(empty_title, self.empty_state)
        self._empty_title_text = empty_title
        self.empty_title.setToolTip(empty_title)
        self.empty_title.setObjectName("overlay_log_empty_title")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setWordWrap(True)
        self.empty_description = QLabel(empty_description, self.empty_state)
        self.empty_description.setObjectName("overlay_log_empty_description")
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description.setWordWrap(True)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_description)
        self.log_view.viewport().installEventFilter(self)

        footer = QHBoxLayout()
        footer.setContentsMargins(1, 0, 0, 0)
        self.line_count_label = QLabel("0 lines")
        self.line_count_label.setObjectName("overlay_log_count")
        footer.addWidget(self.line_count_label)
        footer.addStretch()
        self.follow_button = QToolButton(self.container)
        self.follow_button.setObjectName("overlay_log_follow_button")
        self.follow_button.setCheckable(True)
        self.follow_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.follow_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.follow_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.follow_button.setFixedSize(104, 26)
        self.follow_button.setAccessibleName("Follow the newest log messages")
        self.follow_button.toggled.connect(self._on_follow_toggled)
        self.follow_button.setChecked(True)
        footer.addWidget(self.follow_button)
        layout.addLayout(footer)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(3, 3, 3, 3)
        outer_layout.addWidget(self.container)
        self._drag_controller = OverlayWindowDrag(
            self, (self.container, self.header)
        )
        self.clear_button.clicked.connect(self.log_view.clear)
        self.log_view.textChanged.connect(self._update_log_state)
        self.log_view.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.log_view.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        apply_overlay_stylesheet(self)
        self._update_log_state()
        self.hide()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.log_view.viewport() and event.type() == QEvent.Type.Resize:
            self._resize_empty_state()
        return super().eventFilter(watched, event)

    def _resize_empty_state(self) -> None:
        viewport = self.log_view.viewport().rect()
        self.empty_state.setGeometry(viewport)
        compact = viewport.height() < 96
        self.empty_title.setText(
            "Waiting for messages" if compact else self._empty_title_text
        )
        self.empty_description.setVisible(not compact)
        self._empty_layout.setContentsMargins(
            8 if compact else 16, 4 if compact else 12,
            8 if compact else 16, 4 if compact else 12,
        )

    def show_for_game(
        self, game_geometry: QRect, occupied: tuple[QRect, ...] = (),
        *, size_limit: QSize | None = None,
    ) -> None:
        size = self._preferred_size
        if size_limit is not None:
            size = size.boundedTo(size_limit)
        position_panel(
            self, game_geometry, size, self._alignment, occupied
        )
        self._update_density()
        if not self.isVisible():
            self.show()
            self.raise_()

    def _update_density(self) -> None:
        compact = self.height() < 200
        if self.log_view.property("compact") == compact:
            return
        self._panel_layout.setContentsMargins(
            10 if compact else 12, 10 if compact else 12,
            10 if compact else 12, 8 if compact else 10,
        )
        self._panel_layout.setSpacing(6 if compact else 10)
        self.log_view.setProperty("compact", compact)
        self.log_view.style().unpolish(self.log_view)
        self.log_view.style().polish(self.log_view)

    def _update_log_state(self) -> None:
        empty = self.log_view.document().isEmpty()
        count = 0 if empty else self.log_view.blockCount()
        self.line_count_label.setText(f"{count:,} {'line' if count == 1 else 'lines'}")
        self.clear_button.setEnabled(not empty)
        self.empty_state.setVisible(empty)
        self._resize_empty_state()
        if empty:
            self.follow_button.setChecked(True)

    def _on_follow_toggled(self, follow: bool) -> None:
        self.follow_button.setText("Follow latest" if follow else "Scroll paused")
        self.follow_button.setToolTip(
            "Automatically scroll to new messages"
            if follow
            else "Reading older messages. Click to follow new entries; automation is unchanged."
        )
        if follow:
            self._scroll_to_latest()

    def _scroll_to_latest(self) -> None:
        was_appending = self._appending
        self._appending = True
        try:
            cursor = self.log_view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.log_view.setTextCursor(cursor)
            self.log_view.ensureCursorVisible()
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        finally:
            self._appending = was_appending

    def _on_scroll_range_changed(self, _minimum: int, _maximum: int) -> None:
        if self.follow_button.isChecked() and not self._appending:
            self._scroll_to_latest()

    def _on_scroll_changed(self, value: int) -> None:
        if (
            not self._appending
            and self.follow_button.isChecked()
            and value < self.log_view.verticalScrollBar().maximum()
        ):
            self.follow_button.setChecked(False)

    @contextmanager
    def append_cursor(self):
        scrollbar = self.log_view.verticalScrollBar()
        previous_scroll = scrollbar.value()
        previous_cursor = self.log_view.textCursor()
        self._appending = True
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.log_view.document().isEmpty():
            cursor.insertBlock()
        try:
            yield cursor
        finally:
            if self.follow_button.isChecked():
                self._scroll_to_latest()
            else:
                self.log_view.setTextCursor(previous_cursor)
                scrollbar.setValue(previous_scroll)
            self._appending = False
