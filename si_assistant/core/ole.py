"""Reading the streams of an OLE compound file ("Compound File Binary", [MS-CFB]): the
container Word keeps an embedded object in (word/embeddings/oleObject1.bin), e.g. a
ChemDraw drawing pasted into Word, whose "CONTENTS" stream is the ChemDraw (CDX) file.

Only reading, and only what is needed: the directory and the streams' bytes (regular and
mini streams, any sector size, the FAT found through the header and the DIFAT chain).
Damaged files raise OleError, never anything else.
"""

from __future__ import annotations

import struct

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREE, END_OF_CHAIN, FAT_SECT, DIFAT_SECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
NO_STREAM = 0xFFFFFFFF
MAX_CHAIN = 1 << 22        # (a loop in a damaged file can't run forever)


class OleError(ValueError):
    """The bytes are not a readable compound file."""


class OleFile:
    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != MAGIC:
            raise OleError("not an OLE compound file")
        self.data = data
        (sector_shift, mini_shift) = struct.unpack_from("<HH", data, 0x1E)
        if sector_shift not in (9, 12) or mini_shift != 6:
            raise OleError("unexpected sector sizes")
        self.sector_size = 1 << sector_shift
        self.mini_size = 1 << mini_shift
        (n_fat, dir_start, _sig, self.mini_cutoff, minifat_start, n_minifat,
         difat_start, n_difat) = struct.unpack_from("<IIIIIIII", data, 0x2C)
        # The FAT's sectors: 109 in the header, the rest in the DIFAT chain.
        fat_sectors = [s for s in struct.unpack_from("<109I", data, 0x4C) if s < DIFAT_SECT]
        sect, seen = difat_start, 0
        per = self.sector_size // 4 - 1
        while sect < DIFAT_SECT and seen < n_difat + 1:
            values = struct.unpack_from(f"<{per + 1}I", self._sector(sect))
            fat_sectors += [s for s in values[:per] if s < DIFAT_SECT]
            sect = values[per]
            seen += 1
        fat_bytes = b"".join(self._sector(s) for s in fat_sectors[:max(n_fat, 0)] or fat_sectors)
        self.fat = list(struct.unpack(f"<{len(fat_bytes) // 4}I", fat_bytes))
        self.entries = self._directory(dir_start)
        root = self.entries[0] if self.entries else None
        if root is None or root["type"] != 5:
            raise OleError("no root entry")
        self.ministream = self._chain_bytes(root["start"], root["size"]) if root["size"] else b""
        if n_minifat and minifat_start < DIFAT_SECT:
            mf = self._chain_bytes(minifat_start, None)
            self.minifat = list(struct.unpack(f"<{len(mf) // 4}I", mf))
        else:
            self.minifat = []

    # ---- sectors and chains

    def _sector(self, n: int) -> bytes:
        start = (n + 1) * self.sector_size
        if start + self.sector_size > len(self.data) + self.sector_size or start >= len(self.data):
            raise OleError(f"sector {n} is outside the file")
        return self.data[start:start + self.sector_size]

    def _chain(self, start: int, table: list[int]) -> list[int]:
        out, sect = [], start
        while sect < DIFAT_SECT:
            if sect >= len(table) or len(out) > MAX_CHAIN:
                raise OleError("broken sector chain")
            out.append(sect)
            sect = table[sect]
        return out

    def _chain_bytes(self, start: int, size: int | None) -> bytes:
        data = b"".join(self._sector(s) for s in self._chain(start, self.fat))
        return data if size is None else data[:size]

    def _mini_bytes(self, start: int, size: int) -> bytes:
        out = []
        for s in self._chain(start, self.minifat):
            out.append(self.ministream[s * self.mini_size:(s + 1) * self.mini_size])
        return b"".join(out)[:size]

    def _directory(self, start: int) -> list[dict]:
        raw = self._chain_bytes(start, None)
        entries = []
        for off in range(0, len(raw) - 127, 128):
            name_len, kind = struct.unpack_from("<HB", raw, off + 64)
            name = raw[off:off + max(0, min(name_len, 64) - 2)].decode("utf-16-le", "replace")
            left, right, child = struct.unpack_from("<III", raw, off + 68)
            start_sect, = struct.unpack_from("<I", raw, off + 116)
            size, = struct.unpack_from("<Q", raw, off + 120)
            if self.sector_size == 512:
                size &= 0xFFFFFFFF
            entries.append({"name": name, "type": kind, "left": left, "right": right,
                            "child": child, "start": start_sect, "size": size})
        return entries

    # ---- what callers use

    def stream_names(self) -> list[str]:
        """The streams in the root storage (e.g. ["\\x01CompObj", "CONTENTS"])."""
        root = self.entries[0]
        out, todo, seen = [], [root["child"]], set()
        while todo:
            i = todo.pop()
            if i == NO_STREAM or i in seen or i >= len(self.entries):
                continue
            seen.add(i)
            e = self.entries[i]
            if e["type"] == 2:
                out.append(e["name"])
            todo += [e["left"], e["right"]]
        return out

    def read(self, name: str) -> bytes:
        """A stream of the root storage by its name (case ignored). Raises KeyError."""
        for e in self.entries:
            if e["type"] == 2 and e["name"].lower() == name.lower():
                if e["size"] < self.mini_cutoff:
                    return self._mini_bytes(e["start"], e["size"])
                return self._chain_bytes(e["start"], e["size"])
        raise KeyError(name)


def read_stream(data: bytes, name: str) -> bytes | None:
    """`name`'s bytes in the compound file `data`, or None (not one, damaged, no such
    stream)."""
    try:
        return OleFile(data).read(name)
    except (OleError, KeyError, struct.error, IndexError):
        return None
