"""Permanent internal IDs and folder-name slugs.

Internal IDs never change, unlike the displayed compound IDs ("3g"), so they
are what folder names and cross-references are built from (SPEC §4.2).
"""

from __future__ import annotations

import re
import secrets
import unicodedata


def new_id(prefix: str) -> str:
    """E.g. new_id("s") -> "s-7f3a2c"."""
    return f"{prefix}-{secrets.token_hex(3)}"


def slugify(name: str, max_len: int = 40) -> str:
    """Turn a display name into a short, filesystem-safe folder suffix."""
    # Drop accents (é -> e); any other non-ASCII character (β, –) becomes a separator.
    text = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "untitled"


_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    """Keep the user's filename readable but legal on Windows, macOS and Linux."""
    cleaned = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    return cleaned or "file"
