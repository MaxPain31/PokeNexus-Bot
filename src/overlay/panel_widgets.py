from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from overlay.hover_button import HoverToolButton
from utils.runtime_paths import application_path
from utils.svg_loader import load_svg_icon


class OverlayIconButton(HoverToolButton):
    """Draw icons and captions with the same padding on every Windows style."""

    _PADDING = 6
    _CAPTION_GAP = 5

    def caption_lines(self) -> tuple[str, ...]:
        return tuple(self.text().splitlines())

    def content_rects(self) -> tuple[QRect, QRect]:
        content = self.rect().adjusted(
            self._PADDING, self._PADDING, -self._PADDING, -self._PADDING
        )
        size = self.iconSize().boundedTo(content.size())
        caption_height = (
            self.fontMetrics().height() * len(self.caption_lines())
            if self.text()
            and self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
            else 0
        )
        gap = self._CAPTION_GAP if caption_height else 0
        group_height = size.height() + gap + caption_height
        top = content.top() + max(0, (content.height() - group_height) // 2)
        icon = QRect(
            content.left() + (content.width() - size.width()) // 2,
            top,
            size.width(),
            size.height(),
        )
        caption = QRect(
            content.left(), icon.bottom() + 1 + gap, content.width(), caption_height
        )
        return icon, caption

    def paintEvent(self, event) -> None:
        del event
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.icon = QIcon()
        option.text = ""
        option.toolButtonStyle = Qt.ToolButtonStyle.ToolButtonIconOnly
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)

        icon_rect, caption_rect = self.content_rects()
        self.icon().paint(
            painter,
            icon_rect,
            Qt.AlignmentFlag.AlignCenter,
            QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled,
            QIcon.State.On if self.isChecked() else QIcon.State.Off,
        )
        if caption_rect.height():
            group = (
                QPalette.ColorGroup.Active
                if self.isEnabled()
                else QPalette.ColorGroup.Disabled
            )
            painter.setPen(self.palette().color(group, QPalette.ColorRole.ButtonText))
            line_height = self.fontMetrics().height()
            for index, line in enumerate(self.caption_lines()):
                caption = self.fontMetrics().elidedText(
                    line, Qt.TextElideMode.ElideRight, caption_rect.width()
                )
                painter.drawText(
                    QRect(
                        caption_rect.left(), caption_rect.top() + index * line_height,
                        caption_rect.width(), line_height,
                    ),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextSingleLine,
                    caption,
                )
        painter.end()


class ElidedPanelLabel(QLabel):
    """Keep narrow headings from pushing the panel actions out of view."""

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._update_text()

    def _update_text(self) -> None:
        super().setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight,
                max(0, self.contentsRect().width()),
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_text()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_text()


class OverlayPanelHeader(QWidget):
    """A shared draggable heading, with space for optional panel actions."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        icon_filename: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("overlay_panel_header")
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip(f"{title}\n{subtitle}\nDrag the header to reposition this panel")
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(10)

        self.icon = QLabel(self)
        self.icon.setObjectName("overlay_panel_icon")
        self.icon.setFixedSize(34, 34)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setPixmap(
            load_svg_icon(
                application_path("assets", icon_filename),
                QSize(20, 20),
                fill_color="#7DD3FC",
            ).pixmap(QSize(20, 20))
        )
        self.row.addWidget(self.icon)

        text = QWidget(self)
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self.title = ElidedPanelLabel(title, text)
        self.title.setProperty("panelRole", "title")
        self.subtitle = ElidedPanelLabel(subtitle, text)
        self.subtitle.setObjectName("overlay_panel_subtitle")
        text_layout.addWidget(self.title)
        text_layout.addWidget(self.subtitle)
        text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text.setMinimumWidth(0)
        self.row.addWidget(text, 1)
        for surface in (self.icon, text, self.title, self.subtitle):
            surface.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def add_action(self, widget: QWidget) -> None:
        self.row.addWidget(widget, alignment=Qt.AlignmentFlag.AlignVCenter)
