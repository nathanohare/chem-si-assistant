import pytest

from si_assistant.core.compound_ids import (LETTERS, NUMBERS, SectionIds, assign_ids, check_ids,
                                            index_for_letters, letters_for, pattern_ids)


@pytest.mark.parametrize("index,letters", [
    (0, "a"), (1, "b"), (25, "z"), (26, "aa"), (27, "ab"), (51, "az"), (52, "ba"),
    (701, "zz"), (702, "aaa")])
def test_letters_sequence(index, letters):
    assert letters_for(index) == letters
    assert index_for_letters(letters) == index


def test_index_for_letters_rejects_junk():
    for bad in ("", "a1", "é", "-"):
        with pytest.raises(ValueError):
            index_for_letters(bad)


def test_pattern_letters():
    assert pattern_ids("3", LETTERS, "a", 3) == ["3a", "3b", "3c"]
    assert pattern_ids("3", LETTERS, "y", 4) == ["3y", "3z", "3aa", "3ab"]
    assert pattern_ids("S", LETTERS, "", 1) == ["Sa"]


def test_pattern_numbers():
    assert pattern_ids("", NUMBERS, "5", 3) == ["5", "6", "7"]
    assert pattern_ids("SI-", NUMBERS, "", 2) == ["SI-1", "SI-2"]
    with pytest.raises(ValueError):
        pattern_ids("", NUMBERS, "x", 2)


def test_assign_by_position_and_reorder():
    table = ["3a", "3b", "3c"]
    assert assign_ids([None, None, None], table) == ["3a", "3b", "3c"]
    # Reordering compounds = same table applied to the new order; only overrides move.
    assert assign_ids([None, None], table) == ["3a", "3b"]


def test_override_still_uses_its_position():
    # SPEC §13.1: compound 2 is overridden, compound 3 keeps "3c".
    assert assign_ids([None, "7", None], ["3a", "3b", "3c"]) == ["3a", "7", "3c"]


def test_missing_ids():
    assert assign_ids([None, None, None], ["3a", " "]) == ["3a", None, None]


def sec(sid, table, n, overrides=None, name=None):
    cids = [f"{sid}-c{i}" for i in range(n)]
    return SectionIds(sid, name or sid, table, cids, overrides or [None] * n,
                      [f"cmpd{i}" for i in range(n)])


def test_check_clean_project_has_no_warnings():
    report = check_ids([sec("A", ["1a", "1b"], 2), sec("B", ["2a", "2b", "2c"], 1)])
    assert report.compound_warnings == {} and report.table_warnings == {}
    assert report.display_id("A-c1") == "1b"


def test_missing_id_warning():
    report = check_ids([sec("A", ["1a"], 2)])
    assert report.display_id("A-c1") == "ID?"
    assert "No ID" in report.compound_warnings["A-c1"][0]


def test_duplicate_within_table_flagged_even_if_unused():
    report = check_ids([sec("A", ["3a", "3b", "3A ", "3d"], 1)])
    assert set(report.table_warnings) == {("A", 0), ("A", 2)}
    assert report.compound_warnings == {}  # only compound 0 exists, and it's unique


def test_duplicate_between_sections_flags_both_compounds():
    report = check_ids([sec("A", ["3a"], 1), sec("B", ["3a"], 1)])
    assert "Duplicate ID" in report.compound_warnings["A-c0"][0]
    assert "Duplicate ID" in report.compound_warnings["B-c0"][0]


def test_override_clashing_with_another_compound():
    report = check_ids([sec("A", ["3a", "3b"], 2, overrides=[None, "3a"])])
    assert set(report.compound_warnings) == {"A-c0", "A-c1"}


def test_override_clashing_with_unused_table_row_flags_the_row():
    # Compound overridden to "3c", which is row 3 of the table (not yet used).
    report = check_ids([sec("A", ["3a", "3b", "3c"], 2, overrides=[None, "3c"])])
    assert ("A", 2) in report.table_warnings
    assert report.compound_warnings == {}


def test_override_equal_to_own_table_entry_is_fine():
    report = check_ids([sec("A", ["3a"], 1, overrides=["3a"])])
    assert report.compound_warnings == {} and report.table_warnings == {}
