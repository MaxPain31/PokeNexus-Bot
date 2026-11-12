from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QWidget


def _free_rectangles(bounds: QRect, occupied: tuple[QRect, ...]) -> list[QRect]:
    free = [QRect(bounds)]
    for obstacle in occupied:
        if not obstacle.isValid():
            continue
        obstacle = obstacle.adjusted(-8, -8, 8, 8)
        remaining: list[QRect] = []
        for area in free:
            overlap = area.intersected(obstacle)
            if not overlap.isValid():
                remaining.append(area)
                continue
            remaining.extend(
                (
                    QRect(area.left(), area.top(), area.width(), overlap.top() - area.top()),
                    QRect(
                        area.left(), overlap.bottom() + 1,
                        area.width(), area.bottom() - overlap.bottom(),
                    ),
                    QRect(area.left(), area.top(), overlap.left() - area.left(), area.height()),
                    QRect(
                        overlap.right() + 1, area.top(),
                        area.right() - overlap.right(), area.height(),
                    ),
                )
            )
        free = []
        for area in sorted(
            (area for area in remaining if area.isValid()),
            key=lambda area: area.width() * area.height(),
            reverse=True,
        ):
            if not any(existing.contains(area) for existing in free):
                free.append(area)
    return free


def position_panel(
    window: QWidget,
    bounds: QRect,
    preferred_size: QSize,
    alignment: Qt.AlignmentFlag,
    occupied: tuple[QRect, ...] = (),
) -> None:
    """Dock an overlay inside the client area without disturbing dragged panels."""
    if not bounds.isValid():
        return
    available = bounds.adjusted(12, 12, -12, -12)
    if not available.isValid():
        available = QRect(bounds)
    size = preferred_size.boundedTo(available.size())

    def dock(area: QRect, panel_size: QSize) -> QRect:
        if alignment & Qt.AlignmentFlag.AlignRight:
            x = area.right() - panel_size.width() + 1
        elif alignment & Qt.AlignmentFlag.AlignHCenter:
            x = area.left() + (area.width() - panel_size.width()) // 2
        else:
            x = area.left()
        if alignment & Qt.AlignmentFlag.AlignBottom:
            y = area.bottom() - panel_size.height() + 1
        elif alignment & Qt.AlignmentFlag.AlignVCenter:
            y = area.top() + (area.height() - panel_size.height()) // 2
        else:
            y = area.top()
        return QRect(x, y, panel_size.width(), panel_size.height())

    target = dock(available, size)
    if not window.property("_overlay_user_moved") and occupied:
        candidates = []
        for area in _free_rectangles(available, occupied):
            candidate_size = size.boundedTo(area.size())
            if (
                candidate_size.width() >= min(220, size.width())
                and candidate_size.height() >= min(144, size.height())
            ):
                candidate = dock(area, candidate_size)
                candidates.append(candidate)
        if candidates:
            target = max(
                candidates,
                key=lambda area: (
                    area.width() * area.height(),
                    -(area.topLeft() - target.topLeft()).manhattanLength(),
                ),
            )
    window.setFixedSize(target.size())
    if not window.property("_overlay_user_moved"):
        window.move(target.topLeft())
    window._drag_controller.set_bounds(bounds)
