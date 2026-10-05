import re

from si_assistant.core import lock
from si_assistant.core.fileio import atomic_write_json, atomic_write_text, read_json, read_text
from si_assistant.core.ids import new_id, safe_filename, slugify


# ---------------------------------------------------------------------- fileio

def test_atomic_write_text_roundtrip_and_no_temp_files(tmp_path):
    path = tmp_path / "sub" / "a.txt"
    atomic_write_text(path, "δ 7.25 (CDCl₃)\nline 2")
    atomic_write_text(path, "δ 7.26 (CDCl₃)\nline 2")  # overwrite
    assert read_text(path) == "δ 7.26 (CDCl₃)\nline 2"
    assert path.read_bytes().count(b"\r") == 0  # same line endings on every OS
    assert [p.name for p in path.parent.iterdir()] == ["a.txt"]


def test_json_is_human_readable_unicode(tmp_path):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"title": "β-Lactams", "n": [1, 2]})
    assert "β-Lactams" in path.read_text(encoding="utf-8")
    assert read_json(path) == {"title": "β-Lactams", "n": [1, 2]}


def test_read_missing_returns_default(tmp_path):
    assert read_json(tmp_path / "nope.json", {"a": 1}) == {"a": 1}
    assert read_text(tmp_path / "nope.txt") == ""


def test_read_text_normalises_windows_line_endings(tmp_path):
    path = tmp_path / "w.txt"
    path.write_bytes(b"a\r\nb")
    assert read_text(path) == "a\nb"


# ---------------------------------------------------------------------- ids

def test_new_id_format_and_uniqueness():
    ids = {new_id("c") for _ in range(500)}
    assert len(ids) == 500
    assert all(re.fullmatch(r"c-[0-9a-f]{6}", i) for i in ids)


def test_slugify():
    assert slugify("General Information") == "general-information"
    assert slugify("β-Lactam Products (3a–3z)") == "lactam-products-3a-3z"
    assert slugify("!!!") == "untitled"
    assert len(slugify("x" * 100)) <= 40


def test_safe_filename_keeps_name_readable():
    assert safe_filename("spectrum 3a.pdf") == "spectrum 3a.pdf"
    assert safe_filename('a/b:c*?.pdf') == "a_b_c__.pdf"
    assert safe_filename("...") == "file"


# ---------------------------------------------------------------------- lock

def test_lock_write_read_release(tmp_path):
    assert lock.read_lock(tmp_path) is None
    mine = lock.write_lock(tmp_path, "Nathan")
    found = lock.read_lock(tmp_path)
    assert found.user == "Nathan" and found.session == mine.session
    assert lock.is_ours(tmp_path, mine.session)
    lock.release_lock(tmp_path, mine.session)
    assert lock.read_lock(tmp_path) is None


def test_release_does_not_remove_someone_elses_lock(tmp_path):
    mine = lock.write_lock(tmp_path, "Nathan")
    theirs = lock.write_lock(tmp_path, "Alex")  # Alex took over
    assert not lock.is_ours(tmp_path, mine.session)
    lock.release_lock(tmp_path, mine.session)
    assert lock.read_lock(tmp_path).session == theirs.session


def test_corrupt_lock_is_still_reported(tmp_path):
    (tmp_path / lock.LOCK_NAME).write_text("{not json")
    info = lock.read_lock(tmp_path)
    assert info is not None and info.user == "unknown user"


def test_lock_description():
    info = lock.LockInfo("Nathan", "npo-laptop", "2026-09-30T15:12:00", "x")
    assert info.describe() == "Opened by Nathan on npo-laptop since 3:12 PM on 30 Sep 2026"
