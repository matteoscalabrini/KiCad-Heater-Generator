from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, List, Sequence, Tuple


COPPER_RESISTIVITY_OHM_M = 1.724e-8


Point = Tuple[float, float]


@dataclass
class HeaterParameters:
    voltage_v: float = 5.0
    wattage_w: float = 10.0
    track_width_mm: float = 0.25
    clearance_mm: float = 0.25
    copper_thickness_um: float = 35.0
    outline: str = "rectangle"
    curve: str = "serpentine"
    width_mm: float = 40.0
    height_mm: float = 20.0
    margin_mm: float = 1.0
    hilbert_order: int = 4
    trim_to_target: bool = True


@dataclass
class HeaterResult:
    params: HeaterParameters
    points: List[Point]
    raw_points: List[Point]
    target_resistance_ohm: float
    target_length_mm: float
    path_length_mm: float
    resistance_ohm: float
    wattage_w: float
    current_a: float
    warnings: List[str] = field(default_factory=list)


def normalize_params(params: HeaterParameters) -> HeaterParameters:
    width = max(float(params.width_mm), 0.1)
    height = max(float(params.height_mm), 0.1)
    outline = params.outline.lower()
    curve = params.curve.lower()

    if outline == "square":
        height = width
    elif outline == "circle":
        height = width

    return HeaterParameters(
        voltage_v=max(float(params.voltage_v), 0.001),
        wattage_w=max(float(params.wattage_w), 0.001),
        track_width_mm=max(float(params.track_width_mm), 0.01),
        clearance_mm=max(float(params.clearance_mm), 0.01),
        copper_thickness_um=max(float(params.copper_thickness_um), 1.0),
        outline=outline if outline in {"rectangle", "square", "circle"} else "rectangle",
        curve=curve if curve in {"serpentine", "coil", "hilbert"} else "serpentine",
        width_mm=width,
        height_mm=height,
        margin_mm=max(float(params.margin_mm), 0.0),
        hilbert_order=min(max(int(params.hilbert_order), 1), 8),
        trim_to_target=bool(params.trim_to_target),
    )


def target_resistance(voltage_v: float, wattage_w: float) -> float:
    return voltage_v * voltage_v / wattage_w


def resistance_for_length(
    length_mm: float,
    track_width_mm: float,
    copper_thickness_um: float,
    resistivity_ohm_m: float = COPPER_RESISTIVITY_OHM_M,
) -> float:
    width_m = track_width_mm * 1e-3
    thickness_m = copper_thickness_um * 1e-6
    length_m = length_mm * 1e-3
    return resistivity_ohm_m * length_m / (width_m * thickness_m)


def length_for_resistance(
    resistance_ohm: float,
    track_width_mm: float,
    copper_thickness_um: float,
    resistivity_ohm_m: float = COPPER_RESISTIVITY_OHM_M,
) -> float:
    width_m = track_width_mm * 1e-3
    thickness_m = copper_thickness_um * 1e-6
    length_m = resistance_ohm * width_m * thickness_m / resistivity_ohm_m
    return length_m * 1e3


def generate_heater(params: HeaterParameters) -> HeaterResult:
    p = normalize_params(params)
    warnings: List[str] = []

    target_r = target_resistance(p.voltage_v, p.wattage_w)
    target_len = length_for_resistance(target_r, p.track_width_mm, p.copper_thickness_um)

    raw = _generate_raw_points(p)
    raw = _dedupe_points(raw)
    raw_len = polyline_length(raw)

    if len(raw) < 2:
        warnings.append("The selected geometry is too small for the requested trace width, clearance, and margin.")
        points = raw
    elif p.trim_to_target and raw_len > target_len:
        points = truncate_polyline(raw, target_len)
    else:
        points = raw

    path_len = polyline_length(points)
    if path_len + 0.001 < target_len:
        warnings.append(
            "The layout can only fit %.2f mm of trace, below the %.2f mm needed for the target power."
            % (path_len, target_len)
        )

    actual_r = resistance_for_length(path_len, p.track_width_mm, p.copper_thickness_um) if path_len > 0 else 0.0
    actual_w = (p.voltage_v * p.voltage_v / actual_r) if actual_r > 0 else 0.0
    actual_i = (p.voltage_v / actual_r) if actual_r > 0 else 0.0

    return HeaterResult(
        params=p,
        points=points,
        raw_points=raw,
        target_resistance_ohm=target_r,
        target_length_mm=target_len,
        path_length_mm=path_len,
        resistance_ohm=actual_r,
        wattage_w=actual_w,
        current_a=actual_i,
        warnings=warnings,
    )


def _generate_raw_points(params: HeaterParameters) -> List[Point]:
    if params.curve == "coil":
        if params.outline == "circle":
            return _circle_spiral(params)
        return _rect_spiral(params)
    if params.curve == "hilbert":
        return _hilbert(params)
    return _serpentine(params)


def _edge_clearance(params: HeaterParameters) -> float:
    return params.margin_mm + params.track_width_mm / 2.0


def _pitch(params: HeaterParameters) -> float:
    return params.track_width_mm + params.clearance_mm


def _usable_rect(params: HeaterParameters) -> Tuple[float, float, float, float]:
    edge = _edge_clearance(params)
    left = edge
    top = edge
    right = params.width_mm - edge
    bottom = params.height_mm - edge
    if right <= left or bottom <= top:
        return (0.0, 0.0, 0.0, 0.0)
    return (left, top, right, bottom)


def _serpentine(params: HeaterParameters) -> List[Point]:
    if params.outline == "circle":
        return _circle_serpentine(params)

    left, top, right, bottom = _usable_rect(params)
    if right <= left or bottom <= top:
        return []

    pitch = _pitch(params)
    rows = max(1, int(math.floor((bottom - top) / pitch)) + 1)
    points: List[Point] = []
    for row in range(rows):
        y = min(top + row * pitch, bottom)
        if row % 2 == 0:
            row_points = [(left, y), (right, y)]
        else:
            row_points = [(right, y), (left, y)]
        if not points:
            points.extend(row_points)
        else:
            points.append(row_points[0])
            points.append(row_points[1])
    return points


def _circle_serpentine(params: HeaterParameters) -> List[Point]:
    radius = params.width_mm / 2.0
    center = (radius, radius)
    usable_radius = radius - _edge_clearance(params)
    if usable_radius <= 0:
        return []

    pitch = _pitch(params)
    y_min = center[1] - usable_radius
    y_max = center[1] + usable_radius
    rows = max(1, int(math.floor((2 * usable_radius) / pitch)) + 1)
    points: List[Point] = []
    for row in range(rows):
        y = min(y_min + row * pitch, y_max)
        dy = y - center[1]
        x_span = math.sqrt(max(usable_radius * usable_radius - dy * dy, 0.0))
        left = center[0] - x_span
        right = center[0] + x_span
        if row % 2 == 0:
            row_points = [(left, y), (right, y)]
        else:
            row_points = [(right, y), (left, y)]
        if not points:
            points.extend(row_points)
        else:
            points.append(row_points[0])
            points.append(row_points[1])
    return points


def _rect_spiral(params: HeaterParameters) -> List[Point]:
    left, top, right, bottom = _usable_rect(params)
    if right <= left or bottom <= top:
        return []

    pitch = _pitch(params)
    points: List[Point] = [(left, top)]

    while left <= right and top <= bottom:
        points.append((right, top))
        top += pitch
        if top > bottom:
            break

        points.append((right, bottom))
        right -= pitch
        if left > right:
            break

        points.append((left, bottom))
        bottom -= pitch
        if top > bottom:
            break

        points.append((left, top))
        left += pitch
        if left > right:
            break

        points.append((left, top))

    return points


def _circle_spiral(params: HeaterParameters) -> List[Point]:
    radius = params.width_mm / 2.0
    usable_radius = radius - _edge_clearance(params)
    if usable_radius <= 0:
        return []

    pitch = _pitch(params)
    min_radius = max(pitch * 0.5, params.track_width_mm * 0.75)
    total_turns = max((usable_radius - min_radius) / pitch, 0.5)
    theta_max = total_turns * 2.0 * math.pi
    step = math.radians(8.0)
    count = max(12, int(theta_max / step) + 1)
    center = (radius, radius)

    points: List[Point] = []
    for idx in range(count + 1):
        theta = min(idx * step, theta_max)
        r = usable_radius - (usable_radius - min_radius) * (theta / theta_max)
        points.append((center[0] + r * math.cos(theta), center[1] + r * math.sin(theta)))
    return points


def _hilbert(params: HeaterParameters) -> List[Point]:
    left, top, right, bottom = _usable_rect(params)
    if right <= left or bottom <= top:
        return []

    if params.outline == "circle":
        radius = params.width_mm / 2.0
        center = (radius, radius)
        side = (radius - _edge_clearance(params)) * math.sqrt(2.0)
        if side <= 0:
            return []
        left = center[0] - side / 2.0
        right = center[0] + side / 2.0
        top = center[1] - side / 2.0
        bottom = center[1] + side / 2.0

    n = 2 ** params.hilbert_order
    if n < 2:
        return []

    points: List[Point] = []
    for idx in range(n * n):
        gx, gy = _hilbert_index_to_xy(n, idx)
        x = left + (right - left) * (gx / (n - 1))
        y = top + (bottom - top) * (gy / (n - 1))
        points.append((x, y))
    return points


def _hilbert_index_to_xy(n: int, index: int) -> Tuple[int, int]:
    x = 0
    y = 0
    t = index
    s = 1
    while s < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return x, y


def _dedupe_points(points: Iterable[Point]) -> List[Point]:
    out: List[Point] = []
    for point in points:
        if not out or distance(out[-1], point) > 1e-6:
            out.append(point)
    return out


def polyline_length(points: Sequence[Point]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(distance(points[idx - 1], points[idx]) for idx in range(1, len(points)))


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def truncate_polyline(points: Sequence[Point], max_length_mm: float) -> List[Point]:
    if len(points) < 2 or max_length_mm <= 0:
        return list(points[:1])

    out: List[Point] = [points[0]]
    remaining = max_length_mm
    for idx in range(1, len(points)):
        start = out[-1]
        end = points[idx]
        seg_len = distance(start, end)
        if seg_len <= 1e-9:
            continue
        if seg_len <= remaining:
            out.append(end)
            remaining -= seg_len
            continue

        ratio = remaining / seg_len
        out.append(
            (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        )
        break

    return _dedupe_points(out)


def translated(points: Sequence[Point], dx: float, dy: float) -> List[Point]:
    return [(point[0] + dx, point[1] + dy) for point in points]
