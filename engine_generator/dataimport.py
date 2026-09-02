"""Načtení naměřených dat z .xlsx, .csv/.txt a .eng - bez externích knihoven."""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DOC_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Klíčová slova pro automatické rozpoznání sloupců (česky i anglicky).
TIME_WORDS = ("time", "cas", "čas", "timestamp", "t[s]", "t [s]", "t[ms]", "t [ms]")
THRUST_WORDS = ("thrust", "tah", "force", "sila", "síla", "load")
_PREFERRED_THRUST = ("above rest", "nad klid", "corrected", "net")


@dataclass
class Sheet:
    name: str
    rows: List[List[Any]] = field(default_factory=list)

    def header(self) -> List[str]:
        for row in self.rows[:5]:
            texts = [str(c).strip() for c in row if isinstance(c, str) and str(c).strip()]
            if len(texts) >= 2:
                return [str(c).strip() if c is not None else "" for c in row]
        return []

    def header_index(self) -> int:
        for index, row in enumerate(self.rows[:5]):
            texts = [c for c in row if isinstance(c, str) and str(c).strip()]
            if len(texts) >= 2:
                return index
        return -1


@dataclass
class Workbook:
    path: str
    sheets: List[Sheet] = field(default_factory=list)

    def by_name(self, name: str) -> Optional[Sheet]:
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        return None


# --------------------------------------------------------------------------- #
# XLSX
# --------------------------------------------------------------------------- #

def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    if not letters:
        return 0
    index = 0
    for char in letters.group(0):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> List[str]:
    try:
        data = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    result = []
    for item in root.findall(NS + "si"):
        result.append("".join(node.text or "" for node in item.iter(NS + "t")))
    return result


def _sheet_paths(archive: zipfile.ZipFile) -> List[Tuple[str, str]]:
    """Vrací (název listu, cesta v archivu) v pořadí sešitu."""
    book = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {}
    for rel in rels.findall(REL_NS + "Relationship"):
        target = rel.get("Target", "")
        if target.startswith("/"):
            target = target[1:]
        elif not target.startswith("xl/"):
            target = "xl/" + target
        targets[rel.get("Id")] = target

    result = []
    sheets = book.find(NS + "sheets")
    for sheet in (sheets if sheets is not None else []):
        rid = sheet.get(DOC_REL_NS + "id")
        path = targets.get(rid)
        if path and path in archive.namelist():
            result.append((sheet.get("name", "Sheet"), path))
    return result


def _cell_value(cell: ET.Element, strings: Sequence[str]) -> Any:
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find(NS + "is")
        return "".join(t.text or "" for t in node.iter(NS + "t")) if node is not None else ""
    value = cell.find(NS + "v")
    if value is None or value.text is None:
        return None
    text = value.text
    if kind == "s":
        try:
            return strings[int(text)]
        except (ValueError, IndexError):
            return text
    if kind in ("str", "e"):
        return text
    try:
        return float(text)
    except ValueError:
        return text


def read_xlsx(path: str, max_rows: int = 200000) -> Workbook:
    workbook = Workbook(path=path)
    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        for name, sheet_path in _sheet_paths(archive):
            sheet = Sheet(name=name)
            root = ET.fromstring(archive.read(sheet_path))
            data = root.find(NS + "sheetData")
            for row in (data if data is not None else []):
                cells: List[Any] = []
                for cell in row.findall(NS + "c"):
                    index = _column_index(cell.get("r", ""))
                    while len(cells) < index:
                        cells.append(None)
                    cells.append(_cell_value(cell, strings))
                sheet.rows.append(cells)
                if len(sheet.rows) >= max_rows:
                    break
            workbook.sheets.append(sheet)
    return workbook


# --------------------------------------------------------------------------- #
# CSV / TXT
# --------------------------------------------------------------------------- #

def _detect_delimiter(lines: Sequence[str]) -> Optional[str]:
    """Vybere oddělovač podle toho, který dělí řádky nejkonzistentněji."""
    best, best_score = None, 0
    for candidate in (";", ",", "\t", "|"):
        counts = [line.count(candidate) for line in lines if line.strip()]
        if not counts or min(counts) == 0:
            continue
        score = min(counts) * (1 if len(set(counts)) == 1 else 0.5)
        if score > best_score:
            best, best_score = candidate, score
    return best


def read_delimited(path: str) -> Workbook:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    lines = [line for line in text.splitlines() if line.strip()]
    delimiter = _detect_delimiter(lines[:20])

    sheet = Sheet(name=os.path.basename(path))
    if delimiter:
        rows = csv.reader(io.StringIO(text), delimiter=delimiter, skipinitialspace=True)
        for row in rows:
            sheet.rows.append([_maybe_number(cell) for cell in row])
    else:  # data oddělená mezerami nebo tabulátory
        for line in text.splitlines():
            if line.strip():
                sheet.rows.append([_maybe_number(cell) for cell in re.split(r"\s+", line.strip())])
    return Workbook(path=path, sheets=[sheet])


def _maybe_number(cell: str) -> Any:
    text = (cell or "").strip()
    if not text:
        return None
    try:
        return float(text.replace(",", ".") if text.count(",") == 1 and "." not in text else text)
    except ValueError:
        return text


def read_any(path: str) -> Workbook:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    if ext in (".csv", ".txt", ".tsv", ".dat"):
        return read_delimited(path)
    raise ValueError("Nepodporovaný formát souboru: %s" % (ext or path))


# --------------------------------------------------------------------------- #
# Rozpoznání sloupců a metadat
# --------------------------------------------------------------------------- #

def numeric_columns(sheet: Sheet) -> List[int]:
    """Indexy sloupců, které vypadají jako číselná data."""
    start = sheet.header_index() + 1
    counts: Dict[int, int] = {}
    for row in sheet.rows[start:start + 200]:
        for index, cell in enumerate(row):
            if isinstance(cell, (int, float)):
                counts[index] = counts.get(index, 0) + 1
    return sorted(i for i, n in counts.items() if n >= 3)


def guess_columns(sheet: Sheet) -> Tuple[int, int, float]:
    """Odhad (sloupec času, sloupec tahu, násobič času na sekundy)."""
    header = [str(h).lower() for h in sheet.header()]
    numeric = numeric_columns(sheet)
    time_col, thrust_col, scale = -1, -1, 1.0

    def matches(index: int, words: Sequence[str]) -> bool:
        return index < len(header) and any(word in header[index] for word in words)

    time_candidates = [i for i in numeric
                       if matches(i, TIME_WORDS) and not matches(i, THRUST_WORDS)]
    for index in time_candidates:
        # Preferujeme sekundy před milisekundami.
        if "ms" in header[index] or "milis" in header[index]:
            if time_col == -1:
                time_col, scale = index, 0.001
        else:
            time_col, scale = index, 1.0
            break

    thrust_candidates = [i for i in numeric if matches(i, THRUST_WORDS) and i != time_col]
    for index in thrust_candidates:
        if any(w in header[index] for w in _PREFERRED_THRUST):
            thrust_col = index
            break
    if thrust_col == -1 and thrust_candidates:
        thrust_col = thrust_candidates[0]

    if time_col == -1 and numeric:
        time_col = numeric[0]
    if thrust_col == -1 or thrust_col == time_col:
        rest = [i for i in numeric if i != time_col]
        thrust_col = rest[0] if rest else -1
    return time_col, thrust_col, scale


def extract_series(sheet: Sheet, time_col: int, thrust_col: int,
                   time_scale: float = 1.0) -> List[Tuple[float, float]]:
    """Vytáhne dvojice (čas [s], tah [N]) z listu."""
    start = sheet.header_index() + 1
    series: List[Tuple[float, float]] = []
    for row in sheet.rows[start:]:
        if time_col >= len(row) or thrust_col >= len(row):
            continue
        t, f = row[time_col], row[thrust_col]
        if isinstance(t, (int, float)) and isinstance(f, (int, float)):
            series.append((float(t) * time_scale, float(f)))
    series.sort(key=lambda item: item[0])
    return series


def best_data_sheet(workbook: Workbook) -> Optional[Sheet]:
    """List s nejdelší číselnou tabulkou."""
    best, best_len = None, 0
    for sheet in workbook.sheets:
        time_col, thrust_col, scale = guess_columns(sheet)
        if time_col < 0 or thrust_col < 0:
            continue
        length = len(extract_series(sheet, time_col, thrust_col, scale))
        if length > best_len:
            best, best_len = sheet, length
    return best


_META_KEYS = {
    "propellant_kg": ("propellant mass", "hmotnost paliva", "prop mass"),
    "total_kg": ("total mass", "motor mass", "celkova hmotnost", "celková hmotnost", "loaded mass"),
    "name": ("motor designation", "designation", "oznaceni", "označení", "motor name", "nazev motoru"),
    "diameter_mm": ("diameter", "prumer", "průměr", "casing diameter"),
    "length_mm": ("length", "delka", "délka", "casing length"),
    "manufacturer": ("manufacturer", "vyrobce", "výrobce"),
}


def scan_metadata(workbook: Workbook) -> Dict[str, Any]:
    """Posbírá metadata z listů typu 'Summary' (klíč ve sloupci A, hodnota v B)."""
    found: Dict[str, Any] = {}
    for sheet in workbook.sheets:
        for row in sheet.rows:
            if len(row) < 2 or not isinstance(row[0], str):
                continue
            key = row[0].strip().lower()
            value = row[1]
            for field_name, words in _META_KEYS.items():
                if field_name in found or not any(key.startswith(w) for w in words):
                    continue
                parsed = _parse_quantity(value, field_name)
                if parsed is not None:
                    found[field_name] = parsed
    return found


def _parse_quantity(value: Any, field_name: str) -> Any:
    """Převede '278.0 g' na kilogramy, '40 mm' na milimetry apod."""
    if field_name in ("name", "manufacturer"):
        text = str(value).strip()
        return text or None
    if isinstance(value, (int, float)):
        number, unit = float(value), ""
    else:
        match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*([a-zA-Zμ]*)", str(value))
        if not match:
            return None
        number = float(match.group(1).replace(",", "."))
        unit = match.group(2).lower()
    if field_name.endswith("_kg"):
        if unit in ("g", "gram", "grams"):
            return number / 1000.0
        if unit in ("mg",):
            return number / 1_000_000.0
        return number
    if field_name.endswith("_mm"):
        if unit in ("m",):
            return number * 1000.0
        if unit in ("cm",):
            return number * 10.0
        return number
    return number
