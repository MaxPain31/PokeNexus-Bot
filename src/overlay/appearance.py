from PySide6.QtWidgets import QWidget

from launcher.styles.style_loader import background_alpha, load_stylesheet


_TRANSPARENCY_PROPERTY = "_overlay_background_transparency"
_STYLESHEET_PROPERTY = "_overlay_stylesheet"


def background_transparency(widget: QWidget | None) -> int:
    """New overlay dialogs inherit the appearance of their owning panel."""
    while widget is not None:
        value = widget.property(_TRANSPARENCY_PROPERTY)
        if value is not None:
            return int(value)
        widget = widget.parentWidget()
    return 0


def apply_overlay_stylesheet(widget: QWidget, filename: str = "overlay.qss") -> None:
    transparency = background_transparency(widget)
    widget.setProperty(_STYLESHEET_PROPERTY, filename)
    widget.setProperty(_TRANSPARENCY_PROPERTY, transparency)
    widget.setStyleSheet(
        load_stylesheet(filename, background_transparency=transparency)
    )


def set_background_transparency(root: QWidget, transparency: int) -> None:
    """Update styled panels and open dialogs, including hidden ones."""
    background_alpha(255, transparency)  # Validate before changing any panels.
    stylesheets: dict[str, str] = {}
    for widget in (root, *root.findChildren(QWidget)):
        filename = widget.property(_STYLESHEET_PROPERTY)
        if not filename or widget.property(_TRANSPARENCY_PROPERTY) == transparency:
            continue
        if filename not in stylesheets:
            stylesheets[filename] = load_stylesheet(
                filename, background_transparency=transparency
            )
        widget.setProperty(_TRANSPARENCY_PROPERTY, transparency)
        widget.setStyleSheet(stylesheets[filename])
