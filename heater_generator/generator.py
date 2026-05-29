from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Iterable, List, Optional, Sequence, Tuple


COPPER_RESISTIVITY_OHM_M = 1.724e-8
COPPER_UM_PER_OZ = 34.798


Point = Tuple[float, float]


@dataclass(frozen=True)
class PathSegment:
    kind: str
    start: Point
    end: Point
    mid: Optional[Point] = None


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
    adaptive_fill: bool = False


@dataclass
class HeaterResult:
    params: HeaterParameters
    points: List[Point]
    raw_points: List[Point]
    segments: List[PathSegment]
    raw_segments: List[PathSegment]
    target_resistance_ohm: float
    target_length_mm: float
    path_length_mm: float
    resistance_ohm: float
    wattage_w: float
    current_a: float
    trace_overflow_mm: float = 0.0
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
        adaptive_fill=bool(params.adaptive_fill),
    )


def target_resistance(voltage_v: float, wattage_w: float) -> float:
    return voltage_v * voltage_v / wattage_w


def copper_oz_to_um(ounces: float) -> float:
    return max(float(ounces), 0.0) * COPPER_UM_PER_OZ


def copper_um_to_oz(micrometers: float) -> float:
    return max(float(micrometers), 0.0) / COPPER_UM_PER_OZ


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

    if p.adaptive_fill:
        p, adaptive_warnings = _adaptive_fill_params(p)
        warnings.extend(adaptive_warnings)

    target_r = target_resistance(p.voltage_v, p.wattage_w)
    target_len = length_for_resistance(target_r, p.track_width_mm, p.copper_thickness_um)

    raw_segments = _generate_raw_segments(p)
    raw_segments = _dedupe_segments(raw_segments)
    raw_len = path_segments_length(raw_segments)

    if not raw_segments:
        warnings.append("The selected geometry is too small for the requested trace width, clearance, and margin.")
        segments: List[PathSegment] = []
    elif p.trim_to_target and raw_len > target_len:
        segments = truncate_segments(raw_segments, target_len)
    else:
        segments = raw_segments

    points = path_points(segments)
    raw_points = path_points(raw_segments)
    path_len = path_segments_length(segments)
    trace_overflow = outline_overflow_segments_mm(segments, p, p.track_width_mm)
    if trace_overflow > 0.001:
        warnings.append("Trace exceeds the heater outline by up to %.2f mm." % trace_overflow)
    if segments and not segments_are_continuous(segments):
        warnings.append("Generated route is not continuous; check the pattern settings.")

    actual_r = resistance_for_length(path_len, p.track_width_mm, p.copper_thickness_um) if path_len > 0 else 0.0
    actual_w = (p.voltage_v * p.voltage_v / actual_r) if actual_r > 0 else 0.0
    actual_i = (p.voltage_v / actual_r) if actual_r > 0 else 0.0
    resistance_error = abs(actual_r - target_r) / target_r if target_r > 0 else 0.0

    if path_len + 0.001 < target_len and (not p.adaptive_fill or resistance_error > 0.02):
        warnings.append(
            "The layout can only fit %.2f mm of trace, below the %.2f mm needed for the target power."
            % (path_len, target_len)
        )

    return HeaterResult(
        params=p,
        points=points,
        raw_points=raw_points,
        segments=segments,
        raw_segments=raw_segments,
        target_resistance_ohm=target_r,
        target_length_mm=target_len,
        path_length_mm=path_len,
        resistance_ohm=actual_r,
        wattage_w=actual_w,
        current_a=actual_i,
        trace_overflow_mm=trace_overflow,
        warnings=warnings,
    )


def _adaptive_fill_params(params: HeaterParameters) -> Tuple[HeaterParameters, List[str]]:
    warnings: List[str] = []
    target_r = target_resistance(params.voltage_v, params.wattage_w)
    usable_min = _minimum_usable_span(params)
    min_width = max(params.track_width_mm, 0.05)
    min_clearance = max(params.clearance_mm, 0.05)

    if usable_min <= min_width:
        warnings.append("Adaptive fill cannot run because the requested outline is too small.")
        return replace(params, trim_to_target=False), warnings

    max_width = max(min_width, usable_min * 0.45)
    max_clearance = max(min_clearance, usable_min * 0.35)
    width_values = _sample_range(min_width, max_width, 56)
    clearance_values = _sample_range(min_clearance, max_clearance, 36)

    best = None
    best_candidate = params
    for width in width_values:
        for clearance in clearance_values:
            candidate = replace(
                params,
                track_width_mm=width,
                clearance_mm=clearance,
                trim_to_target=False,
            )
            raw_segments = _dedupe_segments(_generate_raw_segments(candidate))
            path_len = path_segments_length(raw_segments)
            if not raw_segments or path_len <= 0:
                continue

            actual_r = resistance_for_length(path_len, width, candidate.copper_thickness_um)
            if actual_r <= 0:
                continue

            error = abs(math.log(actual_r / target_r))
            clearance_ratio = clearance / max(width, 0.001)
            fit_penalty = 0.002 * clearance_ratio
            score = error + fit_penalty
            item = (score, error, -path_len, width, clearance)
            if best is None or item < best:
                best = item
                best_candidate = candidate

    if best is None:
        warnings.append("Adaptive fill could not find a trace width and clearance that fit the outline.")
        return replace(params, trim_to_target=False), warnings

    _, error, _, _, _ = best
    relative_error = abs(math.exp(error) - 1.0)
    if relative_error > 0.05:
        warnings.append(
            "Adaptive fill closest match is %.1f%% away from the requested resistance."
            % (relative_error * 100.0)
        )

    return best_candidate, warnings


def _minimum_usable_span(params: HeaterParameters) -> float:
    if params.outline == "circle":
        return max(params.width_mm - 2.0 * params.margin_mm, 0.0)
    return max(min(params.width_mm, params.height_mm) - 2.0 * params.margin_mm, 0.0)


def _sample_range(minimum: float, maximum: float, count: int) -> List[float]:
    if count <= 1 or maximum <= minimum:
        return [minimum]

    values = []
    for idx in range(count):
        ratio = (idx / (count - 1)) ** 2
        values.append(minimum + (maximum - minimum) * ratio)
    return values


def outline_overflow_mm(
    points: Sequence[Point],
    params: HeaterParameters,
    stroke_width_mm: float,
    sample_step_mm: float = 0.4,
) -> float:
    if not points:
        return 0.0
    return _outline_overflow(_sample_polyline(points, sample_step_mm), params, stroke_width_mm)


def outline_overflow_segments_mm(
    segments: Sequence[PathSegment],
    params: HeaterParameters,
    stroke_width_mm: float,
    sample_step_mm: float = 0.4,
) -> float:
    if not segments:
        return 0.0
    return _outline_overflow(sample_path_segments(segments, sample_step_mm), params, stroke_width_mm)


def _outline_overflow(points: Iterable[Point], params: HeaterParameters, stroke_width_mm: float) -> float:
    p = normalize_params(params)
    stroke_radius = stroke_width_mm / 2.0
    max_overflow = 0.0
    for point in points:
        if p.outline == "circle":
            radius = p.width_mm / 2.0
            center = (radius, radius)
            inside_distance = radius - distance(center, point)
        else:
            x, y = point
            inside_distance = min(x, y, p.width_mm - x, p.height_mm - y)
        max_overflow = max(max_overflow, stroke_radius - inside_distance)

    return max(max_overflow, 0.0)


def _sample_polyline(points: Sequence[Point], sample_step_mm: float) -> Iterable[Point]:
    if len(points) == 1:
        yield points[0]
        return

    step = max(sample_step_mm, 0.05)
    for idx in range(1, len(points)):
        start = points[idx - 1]
        end = points[idx]
        seg_len = distance(start, end)
        samples = max(1, int(math.ceil(seg_len / step)))
        for sample_idx in range(samples):
            ratio = sample_idx / samples
            yield (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
    yield points[-1]


def _generate_raw_segments(params: HeaterParameters) -> List[PathSegment]:
    if params.outline == "circle" and params.curve == "coil":
        return _arc_segments_from_points(_circle_spiral(params))
    return _segments_from_points(_generate_raw_points(params))


def _segments_from_points(points: Sequence[Point]) -> List[PathSegment]:
    clean = _dedupe_points(points)
    return [PathSegment("line", clean[idx - 1], clean[idx]) for idx in range(1, len(clean))]


def _arc_segments_from_points(points: Sequence[Point]) -> List[PathSegment]:
    clean = _dedupe_points(points)
    segments: List[PathSegment] = []
    idx = 0
    while idx + 2 < len(clean):
        start = clean[idx]
        mid = clean[idx + 1]
        end = clean[idx + 2]
        if _arc_geometry(PathSegment("arc", start, end, mid)) is None:
            segments.append(PathSegment("line", start, mid))
            segments.append(PathSegment("line", mid, end))
        else:
            segments.append(PathSegment("arc", start, end, mid))
        idx += 2

    if idx + 1 < len(clean):
        segments.append(PathSegment("line", clean[idx], clean[idx + 1]))
    return segments


def _dedupe_segments(segments: Iterable[PathSegment]) -> List[PathSegment]:
    out: List[PathSegment] = []
    for segment in segments:
        if segment_length(segment) <= 1e-6:
            continue
        if out and distance(out[-1].end, segment.start) <= 1e-6:
            out.append(segment)
        else:
            out.append(segment)
    return out


def path_points(segments: Sequence[PathSegment]) -> List[Point]:
    if not segments:
        return []
    points = [segments[0].start]
    points.extend(segment.end for segment in segments)
    return _dedupe_points(points)


def path_segments_length(segments: Sequence[PathSegment]) -> float:
    return sum(segment_length(segment) for segment in segments)


def segments_are_continuous(segments: Sequence[PathSegment], tolerance_mm: float = 1e-6) -> bool:
    return all(distance(segments[idx - 1].end, segments[idx].start) <= tolerance_mm for idx in range(1, len(segments)))


def segment_length(segment: PathSegment) -> float:
    if segment.kind == "arc" and segment.mid is not None:
        geometry = _arc_geometry(segment)
        if geometry is not None:
            _, radius, sweep = geometry
            return abs(radius * sweep)
    return distance(segment.start, segment.end)


def truncate_segments(segments: Sequence[PathSegment], max_length_mm: float) -> List[PathSegment]:
    if max_length_mm <= 0:
        return []

    out: List[PathSegment] = []
    remaining = max_length_mm
    for segment in segments:
        seg_len = segment_length(segment)
        if seg_len <= 1e-9:
            continue
        if seg_len <= remaining:
            out.append(segment)
            remaining -= seg_len
            continue

        ratio = remaining / seg_len
        truncated = _partial_segment(segment, ratio)
        if segment_length(truncated) > 1e-6:
            out.append(truncated)
        break

    return out


def sample_path_segments(segments: Sequence[PathSegment], sample_step_mm: float = 0.4) -> Iterable[Point]:
    if not segments:
        return

    step = max(sample_step_mm, 0.05)
    for segment in segments:
        seg_len = segment_length(segment)
        samples = max(1, int(math.ceil(seg_len / step)))
        for sample_idx in range(samples):
            yield point_on_segment(segment, sample_idx / samples)
    yield segments[-1].end


def translated_segments(segments: Sequence[PathSegment], dx: float, dy: float) -> List[PathSegment]:
    return [
        PathSegment(
            segment.kind,
            (segment.start[0] + dx, segment.start[1] + dy),
            (segment.end[0] + dx, segment.end[1] + dy),
            (segment.mid[0] + dx, segment.mid[1] + dy) if segment.mid is not None else None,
        )
        for segment in segments
    ]


def point_on_segment(segment: PathSegment, ratio: float) -> Point:
    ratio = min(max(ratio, 0.0), 1.0)
    if segment.kind == "arc" and segment.mid is not None:
        geometry = _arc_geometry(segment)
        if geometry is not None:
            center, radius, sweep = geometry
            start_angle = math.atan2(segment.start[1] - center[1], segment.start[0] - center[0])
            angle = start_angle + sweep * ratio
            return (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))

    return (
        segment.start[0] + (segment.end[0] - segment.start[0]) * ratio,
        segment.start[1] + (segment.end[1] - segment.start[1]) * ratio,
    )


def _partial_segment(segment: PathSegment, ratio: float) -> PathSegment:
    end = point_on_segment(segment, ratio)
    if segment.kind == "arc" and segment.mid is not None:
        mid = point_on_segment(segment, ratio / 2.0)
        return PathSegment("arc", segment.start, end, mid)
    return PathSegment("line", segment.start, end)


def _arc_geometry(segment: PathSegment) -> Optional[Tuple[Point, float, float]]:
    if segment.mid is None:
        return None

    center = _circle_center(segment.start, segment.mid, segment.end)
    if center is None:
        return None

    radius = distance(center, segment.start)
    if radius <= 1e-9:
        return None

    start_angle = math.atan2(segment.start[1] - center[1], segment.start[0] - center[0])
    mid_angle = math.atan2(segment.mid[1] - center[1], segment.mid[0] - center[0])
    end_angle = math.atan2(segment.end[1] - center[1], segment.end[0] - center[0])
    ccw_sweep = _positive_angle(end_angle - start_angle)
    mid_ccw = _positive_angle(mid_angle - start_angle)
    if mid_ccw <= ccw_sweep:
        sweep = ccw_sweep
    else:
        sweep = -_positive_angle(start_angle - end_angle)
    if abs(sweep) <= 1e-9:
        return None
    return center, radius, sweep


def _circle_center(a: Point, b: Point, c: Point) -> Optional[Point]:
    ax, ay = a
    bx, by = b
    cx, cy = c
    denominator = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(denominator) <= 1e-9:
        return None

    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / denominator
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / denominator
    return (ux, uy)


def _positive_angle(angle: float) -> float:
    wrapped = angle % (2.0 * math.pi)
    return wrapped if wrapped >= 0.0 else wrapped + 2.0 * math.pi


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
