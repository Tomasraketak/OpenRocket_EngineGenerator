"""Zpracování naměřené tahové křivky do podoby vhodné pro .eng."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

Point = Tuple[float, float]

# Nabídka kroků pro ruční zadání i pro převzorkování (v milisekundách).
STEP_CHOICES_MS = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]


@dataclass
class ProcessOptions:
    """Volby zpracování surových dat ze siloměru."""

    subtract_baseline: bool = True      # odečíst klidovou hodnotu před zážehem
    trim_to_burn: bool = True           # oříznout na dobu hoření
    threshold_pct: float = 5.0          # práh v % vrcholu pro začátek/konec hoření
    hold_s: float = 0.25                # jak dlouho musí být tah pod prahem, aby hoření skončilo
    shift_to_zero: bool = True          # posunout začátek hoření na t = 0
    clip_negative: bool = True          # záporné hodnoty na nulu
    mode: str = "step"                  # "step" = pevný krok, "reduce" = redukce bodů, "raw"
    step_ms: int = 100
    max_points: int = 32                # cíl pro režim "reduce"
    smooth_window: int = 1              # klouzavý průměr (počet vzorků, 1 = vypnuto)
    end_with_zero: bool = True          # doplnit koncový nulový bod
    preserve_impulse: bool = True       # dorovnat celkový impuls po zjednodušení


def baseline_level(series: Sequence[Point], before_s: float) -> float:
    """Průměr vzorků před daným časem (klidová hodnota siloměru)."""
    values = [f for t, f in series if t < before_s]
    return sum(values) / len(values) if values else 0.0


def smooth(series: Sequence[Point], window: int) -> List[Point]:
    """Klouzavý průměr přes lichý počet vzorků."""
    if window <= 1 or len(series) < window:
        return list(series)
    half = window // 2
    values = [f for _, f in series]
    result: List[Point] = []
    for index, (time_s, _) in enumerate(series):
        low = max(0, index - half)
        high = min(len(values), index + half + 1)
        chunk = values[low:high]
        result.append((time_s, sum(chunk) / len(chunk)))
    return result


def samples_for(series: Sequence[Point], seconds: float) -> int:
    """Kolik vzorků odpovídá zadanému času (podle vzorkovací frekvence dat)."""
    if len(series) < 2 or seconds <= 0:
        return 1
    span = series[-1][0] - series[0][0]
    if span <= 0:
        return 1
    rate = (len(series) - 1) / span
    return max(1, int(round(seconds * rate)))


def burn_window(series: Sequence[Point], threshold: float, hold: int = 3) -> Tuple[int, int]:
    """Okno hoření kolem vrcholu.

    Hledá se od vrcholu na obě strany; okno končí až tam, kde je ``hold``
    po sobě jdoucích vzorků pod prahem. Tím se ignorují krátké špičky
    od zážehové linky nebo od otřesu stojanu před startem.
    """
    if not series:
        return -1, -1
    peak_index = max(range(len(series)), key=lambda i: series[i][1])
    if series[peak_index][1] < threshold:
        return -1, -1

    first = peak_index
    below = 0
    for index in range(peak_index, -1, -1):
        if series[index][1] >= threshold:
            first, below = index, 0
        else:
            below += 1
            if below >= hold:
                break

    last = peak_index
    below = 0
    for index in range(peak_index, len(series)):
        if series[index][1] >= threshold:
            last, below = index, 0
        else:
            below += 1
            if below >= hold:
                break
    return first, last


def interpolate(series: Sequence[Point], time_s: float) -> float:
    """Lineární interpolace tahu v čase."""
    if not series:
        return 0.0
    if time_s <= series[0][0]:
        return series[0][1]
    if time_s >= series[-1][0]:
        return series[-1][1]
    low, high = 0, len(series) - 1
    while high - low > 1:
        mid = (low + high) // 2
        if series[mid][0] <= time_s:
            low = mid
        else:
            high = mid
    t0, f0 = series[low]
    t1, f1 = series[high]
    if t1 == t0:
        return f1
    return f0 + (f1 - f0) * (time_s - t0) / (t1 - t0)


def resample_fixed_step(series: Sequence[Point], step_s: float) -> List[Point]:
    """Převzorkuje na pevný krok; poslední bod je vždy konec dat."""
    if not series or step_s <= 0:
        return list(series)
    start, end = series[0][0], series[-1][0]
    result: List[Point] = []
    steps = int((end - start) / step_s + 1e-9)
    for index in range(steps + 1):
        time_s = start + index * step_s
        result.append((round(time_s, 6), interpolate(series, time_s)))
    if result[-1][0] < end - 1e-9:
        result.append((round(end, 6), series[-1][1]))
    return result


def _rdp(series: Sequence[Point], epsilon: float) -> List[Point]:
    """Ramer-Douglas-Peucker: zachová tvar křivky (vrchol, zlomy) při málo bodech."""
    if len(series) < 3:
        return list(series)
    (x0, y0), (x1, y1) = series[0], series[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = (dx * dx + dy * dy) ** 0.5
    worst, worst_index = 0.0, 0
    for index in range(1, len(series) - 1):
        x, y = series[index]
        distance = (abs(dy * x - dx * y + x1 * y0 - y1 * x0) / norm) if norm else abs(y - y0)
        if distance > worst:
            worst, worst_index = distance, index
    if worst <= epsilon:
        return [series[0], series[-1]]
    left = _rdp(series[:worst_index + 1], epsilon)
    right = _rdp(series[worst_index:], epsilon)
    return left[:-1] + right


def reduce_points(series: Sequence[Point], max_points: int) -> List[Point]:
    """Zredukuje počet bodů na zadaný cíl při zachování tvaru křivky."""
    if len(series) <= max_points or max_points < 2:
        return list(series)
    span = max((f for _, f in series), default=1.0) or 1.0
    low, high = 0.0, span
    best = list(series)
    for _ in range(40):
        mid = (low + high) / 2
        candidate = _rdp(series, mid)
        if len(candidate) > max_points:
            low = mid
        else:
            best = candidate
            high = mid
        if high - low < span * 1e-6:
            break
    return best


def process(series: Sequence[Point], options: ProcessOptions) -> List[Point]:
    """Surová data ze siloměru -> body pro .eng."""
    data = [(float(t), float(f)) for t, f in series]
    if not data:
        return []

    data = smooth(data, options.smooth_window)

    peak = max(f for _, f in data)
    if options.subtract_baseline:
        # Klidová hodnota z části záznamu před prvním výrazným nárůstem.
        first_rise = next((t for t, f in data if f >= 0.1 * peak), data[0][0])
        level = baseline_level(data, first_rise)
        if level:
            data = [(t, f - level) for t, f in data]
            peak = max(f for _, f in data)

    if options.trim_to_burn and peak > 0:
        threshold = peak * max(options.threshold_pct, 0.0) / 100.0
        hold = max(2, samples_for(data, options.hold_s))
        first, last = burn_window(data, threshold, hold)
        if first >= 0 and last > first:
            # Necháme jeden vzorek před a za oknem, aby náběh nebyl useknutý.
            data = data[max(0, first - 1):min(len(data), last + 2)]

    if options.shift_to_zero and data:
        offset = data[0][0]
        data = [(t - offset, f) for t, f in data]

    if options.clip_negative:
        data = [(t, max(0.0, f)) for t, f in data]

    reference_impulse = _impulse(data)

    if options.mode == "step":
        data = resample_fixed_step(data, options.step_ms / 1000.0)
    elif options.mode == "reduce":
        data = reduce_points(data, options.max_points)

    data = dedupe_times(data)

    if options.preserve_impulse and options.mode in ("step", "reduce"):
        data = scale_to_impulse(data, reference_impulse)

    if data and data[0][0] > 0:
        data.insert(0, (0.0, 0.0 if options.end_with_zero else data[0][1]))
    if options.end_with_zero and data and data[-1][1] != 0.0:
        # Dohoření navazuje krokem podle režimu: u nedotčených dat vzorkovacím,
        # u převzorkování zvoleným.
        gap = options.step_ms / 1000.0 if options.mode == "step" else _last_gap(data)
        data.append((round(data[-1][0] + gap, 6), 0.0))
    elif options.end_with_zero and data:
        data[-1] = (data[-1][0], 0.0)
    return [(round(t, 6), round(f, 4)) for t, f in data]


def _last_gap(series: Sequence[Point], fallback: float = 0.01) -> float:
    """Rozestup posledních dvou vzorků - pro doplnění koncového nulového bodu."""
    if len(series) < 2:
        return fallback
    return max(series[-1][0] - series[-2][0], 1e-4)


def _impulse(series: Sequence[Point]) -> float:
    total = 0.0
    for (t0, f0), (t1, f1) in zip(series, series[1:]):
        total += (t1 - t0) * (f0 + f1) / 2.0
    return total


def scale_to_impulse(series: Sequence[Point], target_ns: float) -> List[Point]:
    """Dorovná celkový impuls zjednodušené křivky na hodnotu z původních dat."""
    current = _impulse(series)
    if current <= 0 or target_ns <= 0:
        return list(series)
    factor = target_ns / current
    if not 0.5 <= factor <= 2.0:
        return list(series)  # nesmyslný poměr - raději nesahat na data
    return [(t, f * factor) for t, f in series]


def dedupe_times(series: Sequence[Point]) -> List[Point]:
    """Odstraní body se stejným (nebo klesajícím) časem."""
    result: List[Point] = []
    for time_s, thrust in series:
        if result and time_s <= result[-1][0]:
            continue
        result.append((time_s, thrust))
    return result


def make_grid(step_ms: int, duration_s: float) -> List[Point]:
    """Prázdná mřížka pro ruční zadání: 0, krok, 2*krok, ... , doba hoření."""
    step_s = max(step_ms, 1) / 1000.0
    count = max(1, int(round(duration_s / step_s)))
    return [(round(index * step_s, 6), 0.0) for index in range(count + 1)]


def resnap(points: Sequence[Point], step_ms: int) -> List[Point]:
    """Přepočítá existující body na jiný krok (hodnoty se interpolují)."""
    if not points:
        return []
    return [(t, round(f, 4)) for t, f in resample_fixed_step(points, max(step_ms, 1) / 1000.0)]
