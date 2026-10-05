"""Undo / redo for everything saved in a Project Folder (SPEC §13k.11).

It works on files, not on the program's objects: before a change touches a file or
folder in the project, the journal keeps what was there, once per step:

* a file's previous contents, as a copy in build/undo/<step>/ (never in memory, so a
  large spectrum costs no RAM);
* "did not exist" for something new (undo removes it again);
* a folder moved somewhere else (e.g. into trash/): undo moves it back. Folders that are
  deleted are moved into build/undo/<step>/ instead, so they too can come back.

Undoing a step puts all of that back, newest first, while keeping what it replaces in a
new step: that step is the redo. If anything cannot be put back (a file open in another
program), everything done so far is reversed again, so the project is never left half
undone.

Every write in core/fileio.py tells the journal of its project first (`fileio.register`).
build/, snapshots/ and the lock file are not part of the history.

A *step* is opened by the main window around each command (`Journal.step(label)`), and
by the pages around their own separate actions (a field added, a structure drawn, a
file attached…). Saves made without one go into a step described by `context` (a
callable set by the window: which page, which field):

* the autosave of typing (`with journal.autosave(): …`): consecutive autosaves with the
  same description merge into one step, so typing a paragraph is undone in one go;
* any other save (a click, a paste, a choice): a step of its own. All its writes join it
  until the action is over (`end_action`, set by the window: when the event that caused
  it has been handled), another page is opened, a command runs or something is undone.

A step that in the end changes nothing (a command that failed and put everything back,
typing undone again in its box) is dropped, so Edit ▸ Undo never does nothing visible.
"""

from __future__ import annotations

import itertools
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

MAX_STEPS = 50
UNDO_DIR = ("build", "undo")
# Top-level names inside a project that are not part of the history.
EXCLUDED = ("build", "snapshots", ".lock")

_RENAME_ATTEMPTS = 10   # (cloud-sync clients may hold a file for a moment, see fileio)
_RENAME_DELAY_S = 0.1


class UndoError(Exception):
    """An undo or redo that could not be done; the message is for the user."""


@dataclass
class UndoContext:
    """What a save made outside a step is about. `key` decides merging (equal keys merge
    while nothing else happens in between); `label` may be a callable (worked out only
    when a new step starts); `where` = tree places [(kind, id), …], best first."""
    key: Any
    label: str | Callable[[], str]
    where: list = field(default_factory=list)


@dataclass
class Step:
    id: str
    label: str
    folder: Path            # build/undo/<id>/: copies and moved-away folders
    where: list = field(default_factory=list)
    merge_key: Any = None
    # ("file", path, copy) | ("absent", path) | ("move", source, destination), in order.
    records: list = field(default_factory=list)
    _seen: set = field(default_factory=set)
    _count: int = 0

    def _stash_name(self, prefix: str) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._count += 1
        return self.folder / f"{prefix}{self._count:04d}"


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _rename(source: Path, target: Path) -> None:
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.rename(source, target)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(_RENAME_DELAY_S)


def _same_files(a: Path, b: Path, chunk: int = 1 << 20) -> bool:
    """Whether two files (of the same size) hold the same bytes, read a piece at a time."""
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


def _empty_folder(path: Path) -> bool:
    """A folder with no files in it (folders only, at any depth)."""
    try:
        return path.is_dir() and not any(p.is_file() for p in path.rglob("*"))
    except OSError:
        return False


def _same_contents(path: Path, data: bytes) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != len(data):
            return False
        return path.read_bytes() == data
    except OSError:
        return False


class Journal:
    """The undo and redo history of one open project."""

    def __init__(self, root: Path, max_steps: int = MAX_STEPS):
        self.root = Path(root)
        self._root = _norm(root)
        self.folder = self.root.joinpath(*UNDO_DIR)
        self.max_steps = max_steps
        self.undo_stack: list[Step] = []
        self.redo_stack: list[Step] = []
        self.context: Callable[[], UndoContext | None] | None = None
        self.listeners: list[Callable[[], None]] = []
        # Called with a function to run once the action being saved is over (the window:
        # when the event loop is free again). Without it, an action lasts until the next
        # break_merge(), step, autosave or undo.
        self.end_action: Callable[[Callable[[], None]], None] | None = None
        self._open: Step | None = None     # the step a `with step(...)` has open
        self._depth = 0
        self._merge = False                # may the next autosave join the newest step?
        self._merge_once = False           # … only that one (then_merge)
        self._autosaving = 0               # inside `with autosave()`
        self._typed: Step | None = None    # the step the current autosave went into
        self._action: Step | None = None   # the step of an action still being saved
        self._ids = itertools.count(1)
        self._busy = False                 # undoing or redoing

    # ------------------------------------------------------------------ life cycle

    def start(self) -> None:
        """Begin recording (a project opened for editing); an old history is removed."""
        self.clear()
        from . import fileio
        fileio.register(self)

    def close(self, remove: bool = True) -> None:
        """Stop recording and remove the history (the project is closed). With
        remove=False the folder is left alone: someone else has taken the project over,
        and build/undo/ now holds their history."""
        from . import fileio
        fileio.unregister(self)
        if remove:
            self.clear()
        else:
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._notify()

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._merge = self._merge_once = False
        self._action = None
        shutil.rmtree(self.folder, ignore_errors=True)
        self._notify()

    def _notify(self) -> None:
        for listener in list(self.listeners):
            listener()

    # ------------------------------------------------------------------ what is recorded

    def claims(self, path: Path | str) -> bool:
        """Whether `path` is part of the project's history."""
        p = _norm(path)
        if not p.startswith(self._root + os.sep):
            return False
        first = p[len(self._root) + 1:].split(os.sep, 1)[0]
        return first not in tuple(os.path.normcase(e) for e in EXCLUDED)

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack) and not self._busy

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_stack) and not self._busy

    @property
    def undo_label(self) -> str:
        return self.undo_stack[-1].label if self.undo_stack else ""

    @property
    def redo_label(self) -> str:
        return self.redo_stack[-1].label if self.redo_stack else ""

    def break_merge(self) -> None:
        """The next save starts a step of its own (e.g. another page was opened)."""
        self._merge = self._merge_once = False
        self._finish_action()

    def _new_step(self, label: str, where=None, merge_key=None) -> Step:
        sid = f"{next(self._ids):05d}"
        return Step(sid, label, self.folder / sid, list(where or []), merge_key)

    @contextmanager
    def step(self, label: str, where=None, then_merge: bool = False) -> Iterator[Step | None]:
        """Everything saved inside is one step, undone together. Nested steps join the
        outer one. With `then_merge`, the page's next autosave joins this step too (only
        that one), e.g. a figure removed and its paragraph taken out of the text. Without
        `where`, the step is about the page on show (`context`)."""
        if self._open is not None:
            self._depth += 1
            try:
                yield self._open
            finally:
                self._depth -= 1
            return
        self._finish_action()
        context = self.context() if self.context and (then_merge or where is None) else None
        if where is None and context is not None:
            where = context.where
        step = self._new_step(label, where, context.key if then_merge and context else None)
        self._open = step
        try:
            yield step
        finally:
            self._open = None
            if step.records and not self._changes_nothing(step):
                self._push(step)
                self._merge = self._merge_once = step.merge_key is not None
            else:
                # Nothing saved, or nothing changed in the end (e.g. a command that failed
                # and put everything back): no step, and what can be redone is kept.
                shutil.rmtree(step.folder, ignore_errors=True)
                self._merge = self._merge_once = False

    @contextmanager
    def autosave(self) -> Iterator[None]:
        """A page saving what was typed on it: consecutive autosaves of the same page (and
        field) merge into one step. If the step then changes nothing any more (the typing
        was undone in its box and saved again), it is dropped."""
        self._autosaving += 1
        try:
            yield
        finally:
            self._autosaving -= 1
            if not self._autosaving:
                typed, self._typed = self._typed, None
                if self._merge_once:   # (a then_merge step takes this one save only)
                    self._merge = self._merge_once = False
                if typed is not None:
                    self._drop_if_nothing(typed)

    def _push(self, step: Step) -> None:
        self.undo_stack.append(step)
        for old in self.redo_stack:   # a new change: what was undone can't be redone
            shutil.rmtree(old.folder, ignore_errors=True)
        self.redo_stack.clear()
        while len(self.undo_stack) > self.max_steps:
            shutil.rmtree(self.undo_stack.pop(0).folder, ignore_errors=True)
        self._notify()

    def _current(self) -> Step:
        """The step a change goes into: the open one, or one for a save made outside a
        step (see the module's description)."""
        if self._open is not None:
            return self._open
        top = self.undo_stack[-1] if self.undo_stack else None
        if not self._autosaving and self._action is not None and self._action is top:
            return top   # (another write of the same action)
        context = self.context() if self.context else None
        key = context.key if context else ("change",)
        if self._autosaving:
            self._finish_action()
            top = self.undo_stack[-1] if self.undo_stack else None
            if self._merge and top is not None and top.merge_key == key:
                self._typed = top
                return top
        label = context.label if context else "Change"
        step = self._new_step(label() if callable(label) else label,
                              context.where if context else None,
                              key if self._autosaving else None)
        self._push(step)
        self._merge_once = False
        if self._autosaving:
            self._merge, self._typed = True, step
        else:   # an action of its own: its writes join it until it is over
            self._merge, self._action = False, step
            if self.end_action is not None:
                self.end_action(lambda: self._finish_action(step))
        return step

    def _finish_action(self, step: Step | None = None) -> None:
        """The action being saved is over (`step`: only if it is still that one)."""
        action = self._action
        if action is None or (step is not None and action is not step):
            return
        self._action = None
        self._drop_if_nothing(action)

    def _drop_if_nothing(self, step: Step) -> None:
        """Remove `step` (the newest) if it changes nothing any more."""
        if self._busy or not self.undo_stack or self.undo_stack[-1] is not step:
            return
        if self._changes_nothing(step):
            self.undo_stack.pop()
            shutil.rmtree(step.folder, ignore_errors=True)
            self._merge = self._merge_once = False
            self._notify()

    def _changes_nothing(self, step: Step) -> bool:
        """Whether the project's files are now as they were before `step`: every file has
        its old contents again, nothing made in it is left, and every move was moved back
        (or was of something made in the step and deleted again)."""
        made = [_norm(r[1]) for r in step.records if r[0] == "absent"]

        def made_in_step(path: Path) -> bool:
            p = _norm(path)
            return any(p == m or p.startswith(m + os.sep) for m in made)

        stash = _norm(step.folder)
        moves: list[tuple[str, str, Path]] = []
        for record in step.records:
            kind, path = record[0], Path(record[1])
            if kind == "absent":   # (an empty folder left behind, e.g. trash/, is nothing)
                if path.exists() and not _empty_folder(path):
                    return False
            elif kind == "file":
                copy = record[2]
                try:
                    if (not path.is_file()
                            or path.stat().st_size != copy.stat().st_size
                            or not _same_files(path, copy)):
                        return False
                except OSError:
                    return False
            else:
                src, dst = _norm(record[1]), _norm(record[2])
                back = next((m for m in moves if m[0] == dst and m[1] == src), None)
                if back is not None:   # moved there and back again
                    moves.remove(back)
                else:
                    moves.append((src, dst, path))
        for _src, dst, path in moves:
            if not (made_in_step(path) and (dst == stash or dst.startswith(stash + os.sep))):
                return False
        return True

    # ---- called by core/fileio.py before or after it changes something

    def before_write(self, path: Path, data: bytes | None = None) -> None:
        """`path` is about to be written (or made, if a folder). Writing the same bytes
        again changes nothing, so it is not a step."""
        if self._busy:
            return
        path = Path(path)
        if data is not None and _same_contents(path, data):
            return
        if path.is_dir():
            return
        self._record_state(self._current(), path)

    def moved(self, source: Path, target: Path) -> None:
        if not self._busy:
            self._current().records.append(("move", Path(source), Path(target)))

    def remove(self, path: Path) -> None:
        """Delete a file or folder by moving it into the step (so it can come back)."""
        self._stash(self._current(), Path(path))

    # ---- the three kinds of record

    def _covered(self, step: Step, path: Path) -> bool:
        p = Path(path)
        for q in (p, *p.parents):
            if _norm(q) in step._seen:
                return True
            if _norm(q) == self._root:
                return False
        return False

    def _record_state(self, step: Step, path: Path) -> None:
        if self._covered(step, path):
            return
        top = None   # the outermost folder that doesn't exist yet
        p = path
        while not p.exists() and _norm(p) != self._root and self.claims(p):
            top, p = p, p.parent
        if top is not None:
            step.records.append(("absent", top))
            step._seen.add(_norm(top))
        elif path.is_file():
            copy = step._stash_name("f")
            shutil.copy2(path, copy)   # (file to file: never read into memory)
            step.records.append(("file", path, copy))
            step._seen.add(_norm(path))

    def _stash(self, step: Step, path: Path) -> None:
        target = step._stash_name("d")
        _rename(path, target)
        step.records.append(("move", path, target))

    def _make_dirs(self, step: Step, folder: Path) -> None:
        if not folder.is_dir():
            self._record_state(step, folder)
            folder.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ undo and redo

    def undo(self) -> Step:
        """Undo the newest step; returns its redo step (label and `where`)."""
        return self._swap(self.undo_stack, self.redo_stack, "undo")

    def redo(self) -> Step:
        return self._swap(self.redo_stack, self.undo_stack, "redo")

    def _swap(self, source: list[Step], target: list[Step], what: str) -> Step:
        if self._open is not None or self._busy:
            raise UndoError(f"Cannot {what} while a change is being saved.")
        self._finish_action()   # (dropped if it changed nothing)
        if not source:
            raise UndoError(f"There is nothing to {what}.")
        step = source[-1]
        opposite = self._new_step(step.label, step.where, None)
        self._busy = True
        try:
            try:
                self._apply(step, opposite)
            except Exception as e:   # noqa: BLE001 — any failure: put everything back
                scratch = self._new_step("rollback")
                try:
                    self._apply(opposite, scratch)
                    back = True
                except Exception:   # noqa: BLE001
                    back = False
                shutil.rmtree(scratch.folder, ignore_errors=True)
                shutil.rmtree(opposite.folder, ignore_errors=True)
                raise UndoError(self._failure(what, step, e, back)) from e
        finally:
            self._busy = False
        source.pop()
        shutil.rmtree(step.folder, ignore_errors=True)
        target.append(opposite)
        self._merge = self._merge_once = False
        self._notify()
        return opposite

    def _apply(self, step: Step, into: Step) -> None:
        """Put back what `step` recorded, newest first, recording what it replaces in
        `into` (so that it can be reversed in turn)."""
        for record in reversed(step.records):
            try:
                self._apply_one(record, into)
            except OSError as e:
                e.project_path = record[1]   # (the place in the project, for the message)
                raise

    def _apply_one(self, record: tuple, into: Step) -> None:
        kind, path = record[0], record[1]
        if kind == "absent":          # it was made in the step: take it away again
            if path.exists():
                self._stash(into, path)
        elif kind == "file":          # its earlier contents
            if not record[2].is_file():
                raise FileNotFoundError(2, "The copy kept for undo is gone", str(record[2]))
            self._record_state(into, path)
            self._make_dirs(into, path.parent)
            tmp = path.with_name(f".tmp-undo-{path.name}")
            shutil.copy2(record[2], tmp)
            for attempt in range(_RENAME_ATTEMPTS):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == _RENAME_ATTEMPTS - 1:
                        tmp.unlink(missing_ok=True)
                        raise
                    time.sleep(_RENAME_DELAY_S)
        elif kind == "move":          # moved elsewhere: move it back
            moved_to = record[2]
            if not moved_to.exists():
                raise FileNotFoundError(2, "It is no longer there", str(moved_to))
            if path.exists():
                self._stash(into, path)
            self._make_dirs(into, path.parent)
            _rename(moved_to, path)
            into.records.append(("move", moved_to, path))

    def _failure(self, what: str, step: Step, error: Exception, back: bool) -> str:
        place = getattr(error, "project_path", None) or getattr(error, "filename", None)
        if place:
            try:
                place = Path(place).relative_to(self.root).as_posix()
            except ValueError:
                place = Path(place).name
            where = f"\n\n({place}: {getattr(error, 'strerror', None) or error})"
        else:
            where = f"\n\n({error})"
        missing = getattr(error, "filename", None)
        if back and isinstance(error, FileNotFoundError) and missing:
            if _norm(missing).startswith(_norm(self.folder) + os.sep):
                return (f"“{step.label}” could not be {what}ne, so nothing was changed: the "
                        "copy kept for it in the undo history is gone. This happens when the "
                        "project was opened somewhere else (another window or computer), which "
                        "starts a new undo history.")
            try:
                gone = Path(missing).relative_to(self.root).as_posix()
            except ValueError:
                gone = Path(missing).name
            return (f"“{step.label}” could not be {what}ne, so nothing was changed: “{gone}” "
                    "is no longer in the project folder (it was moved or deleted outside the "
                    "program).")
        if back:
            return (f"“{step.label}” could not be {what}ne, so nothing was changed. A file in "
                    "the project is probably open in another program (e.g. a PDF viewer) or "
                    "being synced. Close it and try again." + where)
        return (f"“{step.label}” could not be {what}ne completely, and the program could not "
                "put everything back as it was. Please close and reopen the project and "
                "check your latest changes." + where)
