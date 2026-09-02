"""Čtení, zápis a kontrola RASP souborů .eng (OpenRocket, openMotor, RockSim)."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Sequence, Tuple


Point = Tuple[float, float]  # (čas [s], tah [N])

# Hranice tříd motorů: třída A končí na 2.5 N.s, každá další je dvojnásobek.
_CLASS_BASE_NS = 2.5


@dataclass
class MotorSpec:
    """Hlavička .eng souboru."""

    name: str = ""
    diameter_mm: float = 0.0
    length_mm: float = 0.0
    delays: str = "P"
    propellant_kg: float = 0.0
    total_kg: float = 0.0
    manufacturer: str = ""
    comments: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MotorSpec":
        spec = cls()
        for key, value in (data or {}).items():
            if not hasattr(spec, key):
                continue
            if key == "comments":
                spec.comments = [str(x) for x in (value or [])]
            elif key in ("name", "delays", "manufacturer"):
                setattr(spec, key, str(value or ""))
            else:
                try:
                    setattr(spec, key, float(value))
                except (TypeError, ValueError):
                    pass
        return spec


# --------------------------------------------------------------------------- #
# Výpočty nad křivkou
# --------------------------------------------------------------------------- #

def total_impulse(points: Sequence[Point]) -> float:
    """Celkový impuls [N.s] lichoběžníkovou integrací."""
    impulse = 0.0
    for (t0, f0), (t1, f1) in zip(points, points[1:]):
        impulse += (t1 - t0) * (f0 + f1) / 2.0
    return impulse


def burn_time(points: Sequence[Point]) -> float:
    if len(points) < 2:
        return 0.0
    return points[-1][0] - points[0][0]


def peak_thrust(points: Sequence[Point]) -> float:
    return max((f for _, f in points), default=0.0)


def average_thrust(points: Sequence[Point]) -> float:
    duration = burn_time(points)
    return total_impulse(points) / duration if duration > 0 else 0.0


def motor_class(impulse_ns: float) -> str:
    """Písmeno třídy motoru podle celkového impulsu."""
    if impulse_ns <= 0:
        return "-"
    if impulse_ns <= _CLASS_BASE_NS / 8:
        return "1/4A"
    if impulse_ns <= _CLASS_BASE_NS / 4:
        return "1/2A"
    index = max(0, math.ceil(math.log2(impulse_ns / _CLASS_BASE_NS)))
    if index < 26:
        return chr(ord("A") + index)
    return "A" * (index // 26 + 1)  # extrémně velké motory (AA, ...)


def class_fraction(impulse_ns: float) -> float:
    """Poloha uvnitř třídy 0-1 (H na 40 % apod.)."""
    letter = motor_class(impulse_ns)
    if len(letter) != 1 or not letter.isalpha():
        return 0.0
    index = ord(letter) - ord("A")
    low = _CLASS_BASE_NS * (2 ** (index - 1)) if index else 0.0
    high = _CLASS_BASE_NS * (2 ** index)
    return (impulse_ns - low) / (high - low) if high > low else 0.0


def designation(points: Sequence[Point]) -> str:
    """Označení typu 'H59' - třída + průměrný tah."""
    impulse = total_impulse(points)
    if impulse <= 0:
        return ""
    avg = average_thrust(points)
    return "%s%d" % (motor_class(impulse), round(avg))


def summary(points: Sequence[Point]) -> Dict[str, float | str]:
    impulse = total_impulse(points)
    return {
        "points": len(points),
        "impulse_ns": impulse,
        "peak_n": peak_thrust(points),
        "average_n": average_thrust(points),
        "burn_s": burn_time(points),
        "class": motor_class(impulse),
        "class_pct": class_fraction(impulse) * 100.0,
        "designation": designation(points),
    }


# --------------------------------------------------------------------------- #
# Zápis
# --------------------------------------------------------------------------- #

def ascii_safe(text: str) -> str:
    """Odstraní diakritiku - .eng soubory čtou programy často jen v ASCII."""
    normalised = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in normalised if not unicodedata.combining(ch))
    return "".join(ch if ord(ch) < 128 else "_" for ch in stripped)


def _fmt(value: float, decimals: int = 3) -> str:
    text = ("%.*f" % (decimals, value)).rstrip("0").rstrip(".")
    return text or "0"


def build_eng_text(spec: MotorSpec, points: Sequence[Point]) -> str:
    """Sestaví obsah .eng souboru."""
    lines: List[str] = []
    for comment in spec.comments:
        for raw in str(comment).splitlines():
            lines.append("; " + ascii_safe(raw.strip()))
    header = " ".join([
        ascii_safe(spec.name.strip()) or "MOTOR",
        _fmt(spec.diameter_mm, 1),
        _fmt(spec.length_mm, 1),
        ascii_safe(spec.delays.strip() or "P").replace(" ", ""),
        "%.4f" % spec.propellant_kg,
        "%.4f" % spec.total_kg,
        ascii_safe(spec.manufacturer.strip()).replace(" ", "_") or "UNKNOWN",
    ])
    lines.append(header)
    for time_s, thrust_n in points:
        lines.append("   %s %s" % (_fmt(time_s, 4), _fmt(thrust_n, 3)))
    lines.append(";")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Čtení
# --------------------------------------------------------------------------- #

def parse_eng(text: str) -> Tuple[MotorSpec, List[Point]]:
    """Načte .eng soubor. Vrací hlavičku a body křivky."""
    spec = MotorSpec()
    points: List[Point] = []
    header_done = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            body = line.lstrip(";").strip()
            if body and not header_done:
                spec.comments.append(body)
            continue
        parts = line.split()
        if not header_done:
            spec = _parse_header(parts, spec)
            header_done = True
            continue
        if len(parts) >= 2:
            try:
                points.append((float(parts[0].replace(",", ".")),
                               float(parts[1].replace(",", "."))))
            except ValueError:
                continue
    return spec, points


def _parse_header(parts: List[str], spec: MotorSpec) -> MotorSpec:
    """Hlavička má 7 polí, ale název motoru občas obsahuje mezeru (např. 'Gragas 40mm')."""
    if len(parts) < 7:
        spec.name = " ".join(parts)
        return spec
    tail = parts[-6:]
    spec.name = " ".join(parts[:-6])
    spec.diameter_mm = _to_float(tail[0])
    spec.length_mm = _to_float(tail[1])
    spec.delays = tail[2]
    spec.propellant_kg = _to_float(tail[3])
    spec.total_kg = _to_float(tail[4])
    spec.manufacturer = tail[5]
    return spec


def _to_float(text: str) -> float:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------- #
# Kontrola před exportem
# --------------------------------------------------------------------------- #

def validate(spec: MotorSpec, points: Sequence[Point]) -> Tuple[List[str], List[str]]:
    """Vrací (chyby, varování). Chyby brání exportu, varování ne."""
    errors: List[str] = []
    warnings: List[str] = []

    if not spec.name.strip():
        errors.append("Chybí název motoru.")
    elif " " in spec.name.strip():
        warnings.append("Název motoru obsahuje mezeru - některé programy načtou jen první slovo.")
    if not re.fullmatch(r"[A-Za-z0-9_\-. ]+", spec.name.strip() or "x"):
        warnings.append("Název motoru obsahuje neobvyklé znaky (doporučeno A-Z, 0-9, '-', '_').")
    if spec.diameter_mm <= 0:
        errors.append("Průměr motoru musí být větší než 0 mm.")
    if spec.length_mm <= 0:
        errors.append("Délka motoru musí být větší než 0 mm.")
    if spec.total_kg <= 0:
        errors.append("Celková hmotnost musí být větší než 0 kg.")
    if spec.propellant_kg <= 0:
        errors.append("Hmotnost paliva musí být větší než 0 kg.")
    elif spec.propellant_kg >= spec.total_kg > 0:
        errors.append("Hmotnost paliva musí být menší než celková hmotnost motoru.")
    if not spec.manufacturer.strip():
        warnings.append("Není vyplněn výrobce, do souboru se zapíše 'UNKNOWN'.")

    if len(points) < 2:
        errors.append("Tahová křivka musí mít alespoň dva body.")
        return errors, warnings

    times = [t for t, _ in points]
    if any(b <= a for a, b in zip(times, times[1:])):
        errors.append("Časy v tahové křivce musí být rostoucí.")
    if times[0] < 0:
        errors.append("Křivka nesmí začínat záporným časem.")
    if any(f < 0 for _, f in points):
        errors.append("Tah nesmí být záporný.")
    if peak_thrust(points) <= 0:
        errors.append("Tahová křivka je celá nulová.")
    if points[-1][1] != 0:
        warnings.append("Poslední bod křivky nemá nulový tah - OpenRocket to očekává.")
    if times[0] > 0 and points[0][1] > 0:
        warnings.append("Křivka nezačíná v nule; přidejte bod (0 s; 0 N).")
    if len(points) > 1000:
        warnings.append("Křivka má %d bodů; zvažte převzorkování, soubor bude velký." % len(points))

    impulse = total_impulse(points)
    if impulse > 0:
        expected = designation(points)
        if expected and expected.lower() not in spec.name.lower().replace(" ", ""):
            warnings.append("Podle křivky jde o motor %s (%.1f N.s)." % (expected, impulse))
    return errors, warnings


def suggest_filename(spec: MotorSpec, points: Sequence[Point]) -> str:
    base = spec.name.strip() or designation(points) or "motor"
    base = re.sub(r"[^A-Za-z0-9_\-.]+", "_", base).strip("_")
    return (base or "motor") + ".eng"
