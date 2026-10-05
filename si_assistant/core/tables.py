"""Tables in custom sections and General Information (SPEC §13k.5).

A table is a paragraph "!table{t-1a2b3c} Caption" in the text; its cells are stored in
<owner folder>/tables/t-1a2b3c.json, human-readable:

    {"id": "t-1a2b3c",
     "label": "",                       typed-over label ("" = automatic, e.g. "Table S1")
     "header_rows": 1,                  rows at the top repeated on each page, ruled off
     "columns": ["left", "center"],     alignment of each column
     "rows": [[{"text": "Entry"}, {"text": "Yield^{a}", "align": "right"}],
              [{"text": "1", "rowspan": 2}, {"text": "92%"}],
              [null, {"text": "88%"}]],  null = covered by a merged cell above or to the left
     "footnotes": ["^{a} Isolated yield."],
     "breakable": true}                 optional: may run over a page break even when it
                                        would fit on the next page (see `breakable` below)

Cell texts and footnotes use the stored text format (core/markup.py): bold, italic,
super/subscript, compound mentions and citations. This module has no Qt code, so every
change to the grid (adding, deleting, merging cells) can be unit-tested.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

ALIGNS = ("left", "center", "right")
DEFAULT_ALIGN = "center"
MAX_SIZE = 200          # rows or columns (a guard against damaged files)


@dataclass
class Cell:
    text: str = ""          # stored text format
    colspan: int = 1
    rowspan: int = 1
    align: str = ""         # "" = the column's alignment

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"text": self.text}
        if self.colspan > 1:
            out["colspan"] = self.colspan
        if self.rowspan > 1:
            out["rowspan"] = self.rowspan
        if self.align in ALIGNS:
            out["align"] = self.align
        return out

    @classmethod
    def from_json(cls, data: Any) -> "Cell":
        if isinstance(data, str):
            return cls(data)
        if not isinstance(data, dict):
            return cls()
        align = str(data.get("align", ""))
        return cls(str(data.get("text", "")), _span(data.get("colspan")),
                   _span(data.get("rowspan")), align if align in ALIGNS else "")


def _span(value: Any) -> int:
    try:
        return max(1, min(MAX_SIZE, int(value)))
    except (TypeError, ValueError):
        return 1


@dataclass
class Table:
    rows: list[list[Cell | None]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)   # alignment per column
    header_rows: int = 1
    footnotes: list[str] = field(default_factory=list)
    label: str = ""
    id: str = ""
    # Page breaks: False (the default) = kept whole, moving to the next page if it does not
    # fit where it is; only a table taller than a page then runs over (header repeated).
    # True = may start at the foot of a page and continue on the next.
    breakable: bool = False

    # ------------------------------------------------------------------ making and storing

    @classmethod
    def new(cls, n_rows: int = 3, n_cols: int = 3, table_id: str = "") -> "Table":
        return cls([[Cell() for _ in range(n_cols)] for _ in range(n_rows)],
                   [DEFAULT_ALIGN] * n_cols, 1 if n_rows > 1 else 0, [], "", table_id)

    @classmethod
    def from_json(cls, data: Any) -> "Table":
        data = data if isinstance(data, dict) else {}
        rows = []
        for row in (data.get("rows") or [])[:MAX_SIZE]:
            if isinstance(row, list):
                rows.append([None if c is None else Cell.from_json(c) for c in row[:MAX_SIZE]])
        columns = [str(a) if a in ALIGNS else DEFAULT_ALIGN
                   for a in (data.get("columns") or [])[:MAX_SIZE]]
        try:
            header = max(0, int(data.get("header_rows", 1)))
        except (TypeError, ValueError):
            header = 1
        notes = [str(n) for n in data.get("footnotes") or [] if str(n).strip()]
        table = cls(rows, columns, header, notes, str(data.get("label", "") or "").strip(),
                    str(data.get("id", "") or ""), data.get("breakable") is True)
        table.normalize()
        return table

    def to_json(self) -> dict[str, Any]:
        self.normalize()
        out = {"id": self.id, "label": self.label, "header_rows": self.header_rows,
               "columns": list(self.columns),
               "rows": [[None if c is None else c.to_json() for c in row] for row in self.rows],
               "footnotes": [n for n in self.footnotes if n.strip()]}
        if self.breakable:
            out["breakable"] = True
        return out

    def copy(self) -> "Table":
        return copy.deepcopy(self)

    # ------------------------------------------------------------------ shape

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return len(self.columns)

    def normalize(self) -> None:
        """Make the grid rectangular and the merged cells consistent: spans are cut to fit
        the grid and to not overlap, every covered position is None and every other
        position holds a cell. The header row count is kept within the table."""
        n_cols = max([len(self.columns)] + [len(r) for r in self.rows] + [0])
        self.columns = (self.columns + [DEFAULT_ALIGN] * n_cols)[:n_cols]
        for row in self.rows:
            row.extend([None] * (n_cols - len(row)))
        n_rows = len(self.rows)
        covered = [[False] * n_cols for _ in range(n_rows)]
        for r in range(n_rows):
            for c in range(n_cols):
                cell = self.rows[r][c]
                if covered[r][c]:
                    self.rows[r][c] = None
                    continue
                if cell is None:
                    cell = self.rows[r][c] = Cell()
                cell.colspan = max(1, min(cell.colspan, n_cols - c))
                cell.rowspan = max(1, min(cell.rowspan, n_rows - r))
                # Shrink a span that would run into a cell placed earlier.
                while cell.colspan > 1 and any(covered[r][c + k] for k in range(1, cell.colspan)):
                    cell.colspan -= 1
                while cell.rowspan > 1 and any(covered[r + cell.rowspan - 1][c + k]
                                               for k in range(cell.colspan)):
                    cell.rowspan -= 1
                for dr in range(cell.rowspan):
                    for dc in range(cell.colspan):
                        covered[r + dr][c + dc] = True
        self.header_rows = max(0, min(self.header_rows, n_rows))

    def anchor(self, r: int, c: int) -> tuple[int, int]:
        """The top-left position of the (possibly merged) cell covering (r, c)."""
        for ar in range(r, -1, -1):
            for ac in range(c, -1, -1):
                cell = self.rows[ar][ac]
                if cell is not None and ar + cell.rowspan > r and ac + cell.colspan > c:
                    return ar, ac
        return r, c

    def cell(self, r: int, c: int) -> Cell:
        ar, ac = self.anchor(r, c)
        return self.rows[ar][ac]

    def cells(self):
        """(row, column, cell) for every cell that is not covered, row by row."""
        for r, row in enumerate(self.rows):
            for c, cell in enumerate(row):
                if cell is not None:
                    yield r, c, cell

    def is_empty(self) -> bool:
        return not any(cell.text.strip() for _r, _c, cell in self.cells())

    # ------------------------------------------------------------------ rows and columns

    def insert_row(self, index: int) -> None:
        """A new empty row before `index` (n_rows = at the end). A merged cell that spans
        across that place grows by one row."""
        index = max(0, min(index, self.n_rows))
        new: list[Cell | None] = []
        grown = set()
        for c in range(self.n_cols):
            if 0 < index < self.n_rows:
                ar, ac = self.anchor(index, c)
                if ar < index:   # a merged cell crosses the new row
                    if (ar, ac) not in grown:
                        self.rows[ar][ac].rowspan += 1
                        grown.add((ar, ac))
                    new.append(None)
                    continue
            new.append(Cell())
        self.rows.insert(index, new)
        if index < self.header_rows:   # a row added inside (or above) the header joins it
            self.header_rows += 1
        self.normalize()

    def insert_column(self, index: int) -> None:
        index = max(0, min(index, self.n_cols))
        grown = set()
        anchors = [[self.anchor(r, c) for c in range(self.n_cols)] for r in range(self.n_rows)]
        for r, row in enumerate(self.rows):
            if 0 < index < self.n_cols:
                ar, ac = anchors[r][index]
                if ac < index:   # a merged cell crosses the new column
                    if (ar, ac) not in grown:
                        self.rows[ar][ac].colspan += 1
                        grown.add((ar, ac))
                    row.insert(index, None)
                    continue
            row.insert(index, Cell())
        align = self.columns[index - 1] if 0 < index <= self.n_cols else DEFAULT_ALIGN
        self.columns.insert(index, align)
        self.normalize()

    def delete_rows(self, indices) -> None:
        for index in sorted(set(i for i in indices if 0 <= i < self.n_rows), reverse=True):
            if self.n_rows <= 1:
                break
            for c in range(self.n_cols):
                cell = self.rows[index][c]
                if cell is not None and cell.rowspan > 1 and index + 1 < self.n_rows:
                    # A merged cell starting here moves down into the row below.
                    cell.rowspan -= 1
                    self.rows[index + 1][c] = cell
                elif cell is None:
                    ar, ac = self.anchor(index, c)
                    if ar < index and ac == c:
                        self.rows[ar][ac].rowspan -= 1
            del self.rows[index]
            if index < self.header_rows:
                self.header_rows -= 1
        self.normalize()

    def delete_columns(self, indices) -> None:
        for index in sorted(set(i for i in indices if 0 <= i < self.n_cols), reverse=True):
            if self.n_cols <= 1:
                break
            for r in range(self.n_rows):
                cell = self.rows[r][index]
                if cell is not None and cell.colspan > 1 and index + 1 < self.n_cols:
                    cell.colspan -= 1
                    self.rows[r][index + 1] = cell
                elif cell is None:
                    ar, ac = self.anchor(r, index)
                    if ac < index and ar == r:
                        self.rows[ar][ac].colspan -= 1
            for row in self.rows:
                del row[index]
            del self.columns[index]
        self.normalize()

    # ------------------------------------------------------------------ merging

    def merge(self, r0: int, c0: int, r1: int, c1: int) -> bool:
        """Merge the rectangle (r0, c0)–(r1, c1) into one cell, grown to take in any merged
        cell it cuts through. The texts are joined (separated by spaces) into the top-left
        cell. Returns False if there was nothing to merge."""
        r0, r1 = sorted((max(0, r0), min(self.n_rows - 1, r1)))
        c0, c1 = sorted((max(0, c0), min(self.n_cols - 1, c1)))
        while True:   # grow the rectangle until no merged cell sticks out of it
            grown = False
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    ar, ac = self.anchor(r, c)
                    cell = self.rows[ar][ac]
                    nr0, nc0 = min(r0, ar), min(c0, ac)
                    nr1, nc1 = max(r1, ar + cell.rowspan - 1), max(c1, ac + cell.colspan - 1)
                    if (nr0, nc0, nr1, nc1) != (r0, c0, r1, c1):
                        r0, c0, r1, c1 = nr0, nc0, nr1, nc1
                        grown = True
            if not grown:
                break
        if r0 == r1 and c0 == c1:
            return False
        texts = [cell.text for r, c, cell in self.cells()
                 if r0 <= r <= r1 and c0 <= c <= c1 and cell.text.strip()]
        first = self.rows[r0][c0]
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                self.rows[r][c] = None
        self.rows[r0][c0] = Cell(" ".join(texts), c1 - c0 + 1, r1 - r0 + 1,
                                 first.align if first is not None else "")
        self.normalize()
        return True

    def split(self, r: int, c: int) -> bool:
        """Undo a merge: the merged cell covering (r, c) becomes separate cells again (its
        text stays in the top-left one)."""
        ar, ac = self.anchor(r, c)
        cell = self.rows[ar][ac]
        if cell.rowspan == 1 and cell.colspan == 1:
            return False
        for dr in range(cell.rowspan):
            for dc in range(cell.colspan):
                if dr or dc:
                    self.rows[ar + dr][ac + dc] = Cell(align=cell.align)
        cell.rowspan = cell.colspan = 1
        self.normalize()
        return True

    # ------------------------------------------------------------------ pasting

    def paste(self, r: int, c: int, block: list[list[Cell | None]]) -> None:
        """Put a block of cells (e.g. a table copied from Word or Excel) with its top-left
        corner at (r, c), adding rows and columns as needed. Merged cells already in that
        area are split first."""
        if not block:
            return
        height = len(block)
        width = max(len(row) for row in block)
        while self.n_rows < r + height:
            self.insert_row(self.n_rows)
        while self.n_cols < c + width:
            self.insert_column(self.n_cols)
        for rr in range(r, r + height):
            for cc in range(c, c + width):
                self.split(rr, cc)
        for dr, row in enumerate(block):
            for dc in range(width):
                cell = row[dc] if dc < len(row) else Cell()
                self.rows[r + dr][c + dc] = copy.deepcopy(cell)
        self.normalize()


def block_from_text(text: str) -> list[list[Cell | None]]:
    """Tab-separated text (as Excel and Word put on the clipboard) as a block of cells.
    Characters with a meaning in the stored text format are escaped."""
    from .markup import Paragraph, Run, serialize
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return [[Cell(serialize([Paragraph([Run(t.strip())])]) if t.strip() else "")
             for t in line.split("\t")] for line in lines]
