"""One-person-at-a-time lock (SPEC §4.3).

While a project is open for editing, `.lock` in the Project Folder records who
has it open. Each opening gets a random session token so the program can tell
its own lock apart from someone else's (or from a stale lock left by a crash).
"""

from __future__ import annotations

import platform
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .fileio import atomic_write_json, read_json
from .page import MONTHS

# Another program (OneDrive, a backup tool, an indexer) may hold .lock for a moment, or a
# sync may have left it half-written: it is read again a few times before giving up.
_READ_ATTEMPTS = 5
_READ_DELAY_S = 0.1

LOCK_NAME = ".lock"


@dataclass
class LockInfo:
    user: str
    computer: str
    opened_at: str  # ISO 8601, local time
    session: str

    def describe(self) -> str:
        """E.g. "Opened by Nathan on npo-laptop since 3:12 PM on 30 Sep 2026". Written out
        by hand, so it is English whatever the computer's language (as page.date_text)."""
        stamp = self.opened_at.strip()
        try:
            when = datetime.fromisoformat(stamp) if stamp else None
        except ValueError:
            when = None   # (shown as it was recorded)
        if when is not None:
            hour = when.hour % 12 or 12
            half = "AM" if when.hour < 12 else "PM"
            stamp = (f"{hour}:{when.minute:02d} {half} on {when.day} "
                     f"{MONTHS[when.month - 1][:3]} {when.year}")
        text = f"Opened by {self.user} on {self.computer}"
        return f"{text} since {stamp}" if stamp else text


def computer_name() -> str:
    return platform.node() or "unknown computer"


def read_lock_strict(folder: Path) -> LockInfo | None:
    """The lock as saved (None if there is none). Raises OSError when it cannot be read
    right now (another program has it open, or it is empty or half-synced)."""
    try:
        data = read_json(Path(folder) / LOCK_NAME)
    except ValueError as e:
        raise OSError(f"{LOCK_NAME} could not be read: {e}") from e
    if not isinstance(data, dict):
        return None
    return LockInfo(
        user=str(data.get("user", "unknown user")),
        computer=str(data.get("computer", "unknown computer")),
        opened_at=str(data.get("opened_at", "")),
        session=str(data.get("session", "")),
    )


def read_lock(folder: Path) -> LockInfo | None:
    try:
        return read_lock_strict(folder)
    except OSError:
        # Unreadable lock: treat as present but unknown so the user still decides.
        return LockInfo("unknown user", "unknown computer", "", "")


def write_lock(folder: Path, user: str) -> LockInfo:
    """Claim the project (overwriting any existing lock). Returns our lock. Raises
    FileNotFoundError if the folder is gone (moved, renamed or deleted meanwhile): the lock
    is never written into a re-created empty folder."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(2, "The project folder is no longer there", str(folder))
    info = LockInfo(
        user=user,
        computer=computer_name(),
        opened_at=datetime.now().isoformat(timespec="seconds"),
        session=secrets.token_hex(8),
    )
    atomic_write_json(folder / LOCK_NAME, info.__dict__)
    return info


def is_ours(folder: Path, session: str) -> bool:
    """Whether the lock is still ours. Raises OSError if it cannot be read even after a
    few tries: that is "don't know" (try again later), never "someone took over"."""
    for attempt in range(_READ_ATTEMPTS):
        try:
            current = read_lock_strict(folder)
        except OSError:
            if attempt == _READ_ATTEMPTS - 1:
                raise
            time.sleep(_READ_DELAY_S)
            continue
        return current is not None and current.session == session
    return False


def release_lock(folder: Path, session: str) -> None:
    """Remove the lock, but only if it is still ours (someone may have taken over). One
    that cannot be read is left alone (it might be someone else's)."""
    try:
        ours = is_ours(folder, session)
    except OSError:
        return
    if ours:
        try:
            (Path(folder) / LOCK_NAME).unlink()
        except FileNotFoundError:
            pass
