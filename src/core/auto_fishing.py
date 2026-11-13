from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class FishingDetection:
    target_zone: tuple[int, int] | None
    yellow_zone: tuple[int, int] | None
    moving_hook_x: int | None

    @property
    def moving_hook_overlaps_target(self) -> bool:
        return (
            self.target_zone is not None
            and self.moving_hook_x is not None
            and self.target_zone[0] <= self.moving_hook_x <= self.target_zone[1]
        )


def detect_fishing_markers(image: np.ndarray) -> FishingDetection:
    if image.ndim != 3 or image.shape[2] < 3 or image.size == 0:
        raise ValueError("Fishing region must be a non-empty BGR image.")

    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(
        hsv,
        np.array((35, 120, 150), dtype=np.uint8),
        np.array((85, 255, 255), dtype=np.uint8),
    )
    yellow_mask = cv2.inRange(
        hsv,
        np.array((15, 100, 100), dtype=np.uint8),
        np.array((40, 255, 255), dtype=np.uint8),
    )
    track_mask = cv2.inRange(
        hsv,
        np.array((80, 80, 100), dtype=np.uint8),
        np.array((112, 255, 255), dtype=np.uint8),
    )
    bounds = _find_gauge_bounds(track_mask, green_mask, yellow_mask)
    if bounds is None:
        return FishingDetection(None, None, None)

    left, top, width, height = bounds
    rows, columns = slice(top, top + height), slice(left, left + width)
    target_zone = _green_target_columns(green_mask[rows, columns])
    if target_zone is None:
        return FishingDetection(None, None, None)
    yellow_zone = _yellow_zone_columns(yellow_mask[rows, columns])
    hook_x = _find_hook_marker(hsv[rows, columns])
    return FishingDetection(
        target_zone=(left + target_zone[0], left + target_zone[1]),
        yellow_zone=(left + yellow_zone[0], left + yellow_zone[1])
        if yellow_zone is not None else None,
        moving_hook_x=left + hook_x if hook_x is not None else None,
    )


def draw_fishing_detection(
    image: np.ndarray,
    detection: FishingDetection,
) -> np.ndarray:
    preview = image.copy()
    height = preview.shape[0]
    if detection.target_zone is not None:
        left, right = detection.target_zone
        cv2.rectangle(preview, (left, 0), (right, height - 1), (0, 255, 0), 1)
    if detection.yellow_zone is not None:
        left, right = detection.yellow_zone
        cv2.rectangle(preview, (left, 0), (right, height - 1), (0, 255, 255), 1)
    if detection.moving_hook_x is not None:
        cv2.line(
            preview,
            (detection.moving_hook_x, 0),
            (detection.moving_hook_x, height - 1),
            (255, 0, 255),
            1,
        )
    return preview


def _find_gauge_bounds(
    track_mask: np.ndarray,
    green_mask: np.ndarray,
    yellow_mask: np.ndarray,
) -> tuple[int, int, int, int] | None:
    """Find a long, bordered cyan rail before interpreting any green scenery."""
    image_height, image_width = track_mask.shape
    colors = track_mask | green_mask | yellow_mask
    _, labels, stats, _ = cv2.connectedComponentsWithStats(track_mask, connectivity=8)
    for x, y, width, height, area in sorted(
        stats[1:].tolist(), key=lambda component: -component[cv2.CC_STAT_WIDTH],
    ):
        # The target and yellow block split the rail into cyan components.
        # At least one must still be a dense horizontal strip inside the frame.
        if (
            width < max(24, image_width // 5)
            or height < 6
            or width < 3 * height
            or area < width * height * 0.65
            or y == 0
            or y + height >= image_height
        ):
            continue
        counts = np.count_nonzero(colors[y:y + height], axis=0)
        active_columns = np.flatnonzero(counts >= max(3, height * 0.55))
        if active_columns.size == 0:
            continue
        for group in _column_groups(active_columns, maximum_gap=max(3, height * 8 // 5)):
            if not group[0] <= x + width // 2 <= group[-1]:
                continue
            # Both ends must be cyan. This excludes grass/sand beyond the rim.
            # Cyan water outside the rounded end caps must not extend the
            # rail. Its components continue above/below the detected band.
            aligned = (
                (np.abs(stats[:, cv2.CC_STAT_TOP] - y) <= max(1, height // 5))
                & (np.abs(stats[:, cv2.CC_STAT_TOP] + stats[:, cv2.CC_STAT_HEIGHT] - y - height)
                   <= max(1, height // 5))
            )
            aligned[0] = False
            cyan_counts = np.count_nonzero(
                aligned[labels[y:y + height, group[0]:group[-1] + 1]], axis=0,
            )
            cyan_columns = np.flatnonzero(cyan_counts >= max(3, height * 0.4))
            if cyan_columns.size == 0:
                continue
            left = group[0] + int(cyan_columns[0])
            right = group[0] + int(cyan_columns[-1]) + 1
            rail_width = right - left
            if rail_width < max(60, image_width * 0.65, height * 5):
                continue
            if np.count_nonzero(colors[y:y + height, left:right]) < rail_width * height * 0.65:
                continue
            if np.count_nonzero(track_mask[y:y + height, left:right]) < rail_width * height * 0.4:
                continue
            # Water can be cyan too, but it lacks the rail's straight upper
            # and lower edges. Ignore rounded end caps when checking the rim.
            inset = max(2, rail_width // 10)
            if any(
                np.count_nonzero(colors[row, left + inset:right - inset])
                > (rail_width - 2 * inset) * 0.35
                for row in (y - 1, y + height)
            ):
                continue
            return left, y, rail_width, height
    return None


def _green_target_columns(mask: np.ndarray) -> tuple[int, int] | None:
    column_counts = np.count_nonzero(mask, axis=0)
    active_columns = np.flatnonzero(
        column_counts >= max(2, mask.shape[0] * 0.3)
    )
    if active_columns.size == 0:
        return None
    groups = [
        group
        for group in _column_groups(
            active_columns, maximum_gap=max(3, mask.shape[0] * 3 // 4),
        )
        if 3 <= group[-1] - group[0] + 1 <= min(mask.shape[0] * 2, mask.shape[1] * 0.18)
    ]
    if not groups:
        return None
    group = max(
        groups,
        key=lambda columns: int(column_counts[columns].sum()),
    )
    return group[0], group[-1]


def _yellow_zone_columns(mask: np.ndarray) -> tuple[int, int] | None:
    column_counts = np.count_nonzero(mask, axis=0)
    active_columns = np.flatnonzero(
        column_counts >= max(2, mask.shape[0] * 0.4)
    )
    if active_columns.size == 0:
        return None
    groups = _column_groups(
        active_columns,
        maximum_gap=max(2, mask.shape[1] // 100),
    )
    group = max(groups, key=lambda columns: int(column_counts[columns].sum()))
    return group[0], group[-1]


def _column_groups(
    active_columns: np.ndarray,
    *,
    maximum_gap: int,
) -> list[list[int]]:
    groups: list[list[int]] = [[int(active_columns[0])]]
    for column in active_columns[1:]:
        current = int(column)
        if current - groups[-1][-1] <= maximum_gap:
            groups[-1].append(current)
        else:
            groups.append([current])
    return groups


def _find_hook_marker(marker_hsv: np.ndarray) -> int | None:
    # Only inspect the rail interior. Including its gray border can join the
    # hook to the frame or a zone outline and make the icon look too large.
    height, width = marker_hsv.shape[:2]
    hook_mask = cv2.inRange(
        marker_hsv,
        np.array((0, 0, 20), dtype=np.uint8),
        np.array((179, 110, 195), dtype=np.uint8),
    )
    # Scaled captures can blend a thin rim/zone outline into the first or
    # last interior row and connect it to the hook.
    rim = max(1, height // 12)
    hook_mask[:rim] = 0
    hook_mask[-rim:] = 0
    edge_margin = max(3, height // 4)

    count, _, stats, _ = cv2.connectedComponentsWithStats(
        hook_mask,
        connectivity=8,
    )
    hook_components = [
        (int(stats[index, cv2.CC_STAT_AREA]), index)
        for index in range(1, count)
        if max(8, height * height * 0.06) <= stats[index, cv2.CC_STAT_AREA] <= height * height * 2
        and max(4, height // 4) <= stats[index, cv2.CC_STAT_WIDTH] <= height * 1.6
        and max(4, height // 3) <= stats[index, cv2.CC_STAT_HEIGHT] <= height
        and edge_margin
        <= stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH] // 2
        < width - edge_margin
    ]
    if not hook_components:
        return None
    _, index = max(hook_components)
    hook_left = int(stats[index, cv2.CC_STAT_LEFT])
    hook_width = int(stats[index, cv2.CC_STAT_WIDTH])
    return hook_left + hook_width // 2
