import re
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvgWidgets import QSvgWidget


def load_svg_widget(
    svg_path: str | Path,
    size: QSize | None = None,
    fill_color: str | None = None,
) -> QSvgWidget:
    """Load an SVG file into a widget, optionally setting its size and fill color."""
    path = Path(svg_path)
    if not path.is_file():
        raise FileNotFoundError(f"SVG file not found: {path}")

    if fill_color is None:
        widget = QSvgWidget(str(path))
    else:
        color = QColor(fill_color)
        if not color.isValid():
            raise ValueError(f"Invalid SVG fill color: {fill_color}")

        svg_content = path.read_text(encoding="utf-8")
        svg_content, replacements = re.subn(
            r"""fill\s*=\s*(["'])[^"']*\1""",
            f'fill="{color.name()}"',
            svg_content,
            count=1,
        )
        if replacements == 0:
            raise ValueError(f"SVG file has no fill attribute to recolor: {path}")

        widget = QSvgWidget()
        widget.load(svg_content.encode("utf-8"))

    if not widget.renderer().isValid():
        raise ValueError(f"Invalid SVG file: {path}")

    if size is not None:
        widget.setFixedSize(size)

    return widget


def load_svg_icon(
    svg_path: str | Path,
    size: QSize,
    fill_color: str | None = None,
) -> QIcon:
    """Load an SVG file as a rasterized icon at the requested size."""
    widget = load_svg_widget(svg_path, size, fill_color)
    pixmap = QPixmap(size)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    widget.renderer().render(painter)
    painter.end()

    return QIcon(pixmap)
