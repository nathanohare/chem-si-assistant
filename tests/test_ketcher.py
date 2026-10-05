"""Ketcher hand-over checks that don't need a browser engine."""

from si_assistant import ketcher_setup
from si_assistant.core import chem
from si_assistant.ui.ketcher_dialog import _has_atoms


def test_has_atoms():
    block = chem.mol_to_block(chem.parse_structure("CCO"))
    assert _has_atoms(block)
    empty = "\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"
    assert not _has_atoms(empty)
    assert _has_atoms("CCO") and not _has_atoms("  ")


def test_install_location_is_inside_the_program():
    assert ketcher_setup.KETCHER_DIR.parts[-3:] == ("si_assistant", "vendor", "ketcher")
    assert ketcher_setup.ZIP_URL.startswith("https://github.com/epam/ketcher/releases/")
