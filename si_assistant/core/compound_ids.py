"""Compound ID rules (SPEC §6.3.1, §13.1).

* Each Data section has an ID table: row 1, 2, 3… → "3a", "3b", "3c"…
* A compound's ID is the table entry at its position in the section, so
  reordering compounds re-assigns IDs automatically.
* A manual override replaces the ID but the compound still uses up its row,
  so the compounds after it keep their table IDs.
* Duplicates (in a table, or between compounds anywhere in the project) and
  missing IDs produce warnings. Duplicates are compared ignoring case and
  surrounding spaces.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

LETTERS = "letters"
NUMBERS = "numbers"


# ---------------------------------------------------------------------- patterns

def letters_for(index: int) -> str:
    """0 -> "a", 25 -> "z", 26 -> "aa", 27 -> "ab", 701 -> "zz", 702 -> "aaa"."""
    if index < 0:
        raise ValueError("index must be >= 0")
    n, out = index + 1, ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def index_for_letters(letters: str) -> int:
    """Inverse of letters_for: "a" -> 0, "aa" -> 26."""
    letters = letters.strip().lower()
    if not letters or not letters.isascii() or not letters.isalpha():
        raise ValueError(f"Not a letter sequence: {letters!r}")
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("a") + 1)
    return n - 1


def pattern_ids(prefix: str, style: str, start: str, count: int) -> list[str]:
    """IDs for "Fill from pattern", e.g. ("3", LETTERS, "a", 3) -> ["3a", "3b", "3c"]."""
    if count < 0:
        raise ValueError("count must be >= 0")
    if style == LETTERS:
        first = index_for_letters(start or "a")
        return [f"{prefix}{letters_for(first + i)}" for i in range(count)]
    if style == NUMBERS:
        try:
            first = int((start or "1").strip())
        except ValueError:
            raise ValueError(f"Not a whole number: {start!r}") from None
        return [f"{prefix}{first + i}" for i in range(count)]
    raise ValueError(f"Unknown style {style!r}")


# ---------------------------------------------------------------------- assignment

def _text(value) -> str:
    """An ID as text: a hand-edited section.json may hold numbers ([1, 2, 3]) or nulls."""
    return "" if value is None else str(value)


def _table(table) -> list[str]:
    """The ID table as text; a hand-edited value that is not a list counts as empty."""
    return [_text(v) for v in table] if isinstance(table, list) else []


def normalise(compound_id: str) -> str:
    return _text(compound_id).strip().casefold()


def assign_ids(overrides: list[str | None], table: list[str]) -> list[str | None]:
    """IDs for the compounds of one section, in order.

    `overrides[i]` is compound i's manual ID (None/"" if automatic). Returns
    None for a compound that has no ID (table too short or the cell is empty).
    """
    table = _table(table)
    result: list[str | None] = []
    for i, override in enumerate(overrides):
        override = _text(override)
        if override.strip():
            result.append(override.strip())
        elif i < len(table) and table[i].strip():
            result.append(table[i].strip())
        else:
            result.append(None)
    return result


# ---------------------------------------------------------------------- checks

@dataclass
class IdReport:
    """ID problems for a whole project."""
    # compound internal id -> displayed ID (None = missing)
    ids: dict[str, str | None] = field(default_factory=dict)
    # compound internal id -> warning messages
    compound_warnings: dict[str, list[str]] = field(default_factory=dict)
    # (section id, row index) -> warning message, for ID-table cells
    table_warnings: dict[tuple[str, int], str] = field(default_factory=dict)

    def display_id(self, compound_id: str) -> str:
        return self.ids.get(compound_id) or "ID?"


@dataclass
class SectionIds:
    section_id: str
    section_name: str
    table: list[str]
    compound_ids: list[str]          # internal ids, in order
    overrides: list[str | None]
    compound_names: list[str] = field(default_factory=list)


def check_ids(sections: list[SectionIds]) -> IdReport:
    report = IdReport()
    # normalised ID -> places it is used by a compound
    used: dict[str, list[tuple[str, str]]] = defaultdict(list)  # (compound id, where)

    sections = [replace(s, table=_table(s.table)) for s in sections]
    for s in sections:
        assigned = assign_ids(s.overrides, s.table)
        for pos, (cid, display) in enumerate(zip(s.compound_ids, assigned)):
            report.ids[cid] = display
            if display is None:
                why = ("the ID table has no entry for position "
                       f"{pos + 1}" if pos >= len(s.table) else
                       f"row {pos + 1} of the ID table is empty")
                report.compound_warnings.setdefault(cid, []).append(
                    f"No ID: {why} in “{s.section_name}”. To fix it, open the "
                    f"“{s.section_name}” section ▸ ID table tab and use “Fill from "
                    "pattern…” (or type the IDs), or type a Manual ID on this page.")
            else:
                name = s.compound_names[pos] if pos < len(s.compound_names) else ""
                used[normalise(display)].append((cid, f"{display} ({name or 'unnamed'}, "
                                                       f"“{s.section_name}”)"))

        # Duplicates within the table itself, even in rows no compound uses yet.
        rows: dict[str, list[int]] = defaultdict(list)
        for row, value in enumerate(s.table):
            if value.strip():
                rows[normalise(value)].append(row)
        for value, row_list in rows.items():
            if len(row_list) > 1:
                for row in row_list:
                    others = ", ".join(str(r + 1) for r in row_list if r != row)
                    report.table_warnings[(s.section_id, row)] = (
                        f"Duplicate: “{s.table[row].strip()}” is also in row(s) {others}.")

    # Duplicates between compounds anywhere in the project.
    for value, places in used.items():
        if len(places) > 1:
            for cid, _where in places:
                others = "; ".join(w for c, w in places if c != cid)
                report.compound_warnings.setdefault(cid, []).append(
                    f"Duplicate ID: also used by {others}.")

    # Table cells that clash with a compound's ID in another section or an override.
    for s in sections:
        for row, value in enumerate(s.table):
            key = normalise(value)
            if not key or (s.section_id, row) in report.table_warnings:
                continue
            clashes = [w for cid, w in used.get(key, [])
                       if not (row < len(s.compound_ids) and s.compound_ids[row] == cid)]
            if clashes:
                report.table_warnings[(s.section_id, row)] = (
                    f"“{value.strip()}” is already used by {'; '.join(clashes)}.")
    return report
