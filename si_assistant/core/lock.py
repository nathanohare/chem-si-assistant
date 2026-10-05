"""One-person-at-a-time lock (SPEC §4.3).

While a project is open for editing, `.lock` in the Project Folder records who
has it open. Each opening gets a random session token so the program can tell
its own lock apart from someone else's (or from a stale lock left by a crash).
"""

from __future__ import annotations

import platform
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .fileio import atomic_write_json, read_json

LOCK_NAME = ".lock"


@dataclass
class LockInfo:
    user: str
    computer: str
    opened_at: str  # ISO 8601, local time
    session: str

    def describe(self) -> str:
        """E.g. "Opened by Nathan on npo-laptop since 3:12 PM on 30 Sep 2026"."""
        try:
            when = datetime.fromisoformat(self.opened_at)
            stamp = when.strftime("%I:%M %p on %d %b %Y").lstrip("0")
        except ValueError:
            stamp = self.opened_at
        return f"Opened by {self.user} on {self.computer} since {stamp}"


def computer_name() -> str:
    return platform.node() or "unknown computer"


def read_lock(folder: Path) -> LockInfo | None:
    try:
        data = read_json(Path(folder) / LOCK_NAME)
    except (ValueError, OSError):
        # Unreadable lock: treat as present but unknown so the user still decides.
        return LockInfo("unknown user", "unknown computer", "", "")
    if not isinstance(data, dict):
        return None
    return LockInfo(
        user=str(data.get("user", "unknown user")),
        computer=str(data.get("computer", "unknown computer")),
        opened_at=str(data.get("opened_at", "")),
        session=str(data.get("session", "")),
    )


def write_lock(folder: Path, user: str) -> LockInfo:
    """Claim the project (overwriting any existing lock). Returns our lock."""
    info = LockInfo(
        user=user,
        computer=computer_name(),
        opened_at=datetime.now().isoformat(timespec="seconds"),
        session=secrets.token_hex(8),
    )
    atomic_write_json(Path(folder) / LOCK_NAME, info.__dict__)
    return info


def is_ours(folder: Path, session: str) -> bool:
    current = read_lock(folder)
    return current is not None and current.session == session


def release_lock(folder: Path, session: str) -> None:
    """Remove the lock, but only if it is still ours (someone may have taken over)."""
    if is_ours(folder, session):
        try:
            (Path(folder) / LOCK_NAME).unlink()
        except FileNotFoundError:
            pass
