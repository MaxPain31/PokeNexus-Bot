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
    target_zone = _largest_component_bounds(green_mask)
    if target_zone is None:
        return FishingDetection(None, None, None)

    green_y, _, _, green_height = target_zone
    target_columns = _green_target_columns(green_mask)
    if target_columns is None:
        return FishingDetection(None, None, None)
    green_x, green_right = target_columns
    green_width = green_right - green_x + 1
    y_start = max(0, green_y - 2)
    y_end = min(image.shape[0], green_y + green_height + 2)
    marker_hsv = hsv[y_start:y_end]

    yellow_mask = cv2.inRange(
        marker_hsv,
        np.array((15, 100, 100), dtype=np.uint8),
        np.array((40, 255, 255), dtype=np.uint8),
    )
    return FishingDetection(
        target_zone=(green_x, green_x + green_width - 1),
        yellow_zone=_yellow_zone_columns(yellow_mask),
        moving_hook_x=_find_hook_marker(marker_hsv),
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


def _largest_component_bounds(
    mask: np.ndarray,
) -> tuple[int, int, int, int] | None:
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    components = [
        (int(stats[index, cv2.CC_STAT_AREA]), index)
        for index in range(1, count)
        if stats[index, cv2.CC_STAT_AREA] >= 4
        and stats[index, cv2.CC_STAT_WIDTH] >= 3
        and stats[index, cv2.CC_STAT_HEIGHT] >= 2
    ]
    if not components:
        return None
    _, index = max(components)
    return (
        int(stats[index, cv2.CC_STAT_TOP]),
        int(stats[index, cv2.CC_STAT_LEFT]),
        int(stats[index, cv2.CC_STAT_WIDTH]),
        int(stats[index, cv2.CC_STAT_HEIGHT]),
    )


def _green_target_columns(mask: np.ndarray) -> tuple[int, int] | None:
    column_counts = np.count_nonzero(mask, axis=0)
    active_columns = np.flatnonzero(
        column_counts >= max(2, mask.shape[0] // 10)
    )
    if active_columns.size == 0:
        return None
    groups: list[list[int]] = [[int(active_columns[0])]]
    for column in active_columns[1:]:
        current = int(column)
        if current - groups[-1][-1] <= max(8, mask.shape[1] // 50):
            groups[-1].append(current)
        else:
            groups.append([current])
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
        column_counts >= max(2, mask.shape[0] // 10)
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
    track_mask = cv2.inRange(
        marker_hsv,
        np.array((80, 80, 100), dtype=np.uint8),
        np.array((112, 255, 255), dtype=np.uint8),
    )
    active_columns = np.flatnonzero(
        np.count_nonzero(track_mask, axis=0)
        >= max(4, marker_hsv.shape[0] // 3)
    )
    if active_columns.size == 0:
        return None

    hook_mask = cv2.inRange(
        marker_hsv,
        np.array((0, 0, 20), dtype=np.uint8),
        np.array((179, 110, 195), dtype=np.uint8),
    )
    track_left = int(active_columns[0])
    track_right = int(active_columns[-1]) + 1
    edge_margin = max(6, (track_right - track_left) // 35)

    count, _, stats, _ = cv2.connectedComponentsWithStats(
        hook_mask,
        connectivity=8,
    )
    hook_components = [
        (int(stats[index, cv2.CC_STAT_AREA]), index)
        for index in range(1, count)
        if 30 <= stats[index, cv2.CC_STAT_AREA] <= 900
        and 8 <= stats[index, cv2.CC_STAT_WIDTH] <= 30
        and 8 <= stats[index, cv2.CC_STAT_HEIGHT] <= 28
        and track_left + edge_margin
        <= stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH] // 2
        < track_right - edge_margin
    ]
    if not hook_components:
        return None
    _, index = max(hook_components)
    hook_left = int(stats[index, cv2.CC_STAT_LEFT])
    hook_width = int(stats[index, cv2.CC_STAT_WIDTH])
    return hook_left + hook_width // 2
