"""Permanent internal IDs and folder-name slugs.

Internal IDs never change, unlike the displayed compound IDs ("3g"), so they
are what folder names and cross-references are built from (SPEC §4.2).
"""

from __future__ import annotations

import re
import secrets
import unicodedata


def new_id(prefix: str) -> str:
    """E.g. new_id("s") -> "s-7f3a2c91". Eight hex digits (4 billion possibilities) make a
    clash vanishingly unlikely even in very large projects; IDs made by older versions
    (six digits) keep working unchanged."""
    return f"{prefix}-{secrets.token_hex(4)}"


def slugify(name: str, max_len: int = 40) -> str:
    """Turn a display name into a short, filesystem-safe folder suffix."""
    # Drop accents (é -> e); any other non-ASCII character (β, –) becomes a separator.
    text = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return avoid_device_name(text[:max_len].rstrip("-") or "untitled")


_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    """Keep the user's filename readable but legal on Windows, macOS and Linux."""
    cleaned = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    return avoid_device_name(cleaned or "file")


# Names Windows keeps for devices (CON, aux.png, com1.tar.gz...): Typst refuses to read
# such a file or folder, and Windows may not even create one. (¹ etc.: ¹ ² ³)
_DEVICE_NAME = re.compile("con|prn|aux|nul|conin\\$|conout\\$|(com|lpt)[0-9¹²³]",
                          re.IGNORECASE)


def is_device_name(name: str) -> bool:
    """Whether a file or folder name is one of Windows' device names: the part before the
    first dot (trailing spaces ignored) is CON, PRN, AUX, NUL, COM1, LPT1..."""
    return bool(_DEVICE_NAME.fullmatch(name.split(".", 1)[0].rstrip(" ")))


def avoid_device_name(name: str) -> str:
    """'aux.png' -> 'aux_.png', 'con' -> 'con_'; any other name is left as it is."""
    if not is_device_name(name):
        return name
    base = name.split(".", 1)[0].rstrip(" ")
    return base + "_" + name[len(base):]
