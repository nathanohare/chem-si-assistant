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


def test_install_unpacks_into_the_ketcher_folder(tmp_path, monkeypatch):
    """The whole download-and-unpack step, with a small made-up release zip."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<html></html>")
        zf.writestr("static/js/main.js", "// app")
        zf.writestr("popup.html", "skipped")
    target = tmp_path / "vendor" / "ketcher"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("from an older copy")
    monkeypatch.setattr(ketcher_setup, "ketcher_dir", lambda: target)
    monkeypatch.setattr(ketcher_setup, "_download",
                        lambda url, progress=None: buf.getvalue() if url.endswith(".zip")
                        else b"Apache License")
    assert ketcher_setup.install() == target
    assert (target / "index.html").is_file() and (target / "static" / "js" / "main.js").is_file()
    assert not (target / "popup.html").exists() and not (target / "old.txt").exists()
    assert (target / "LICENSE").read_bytes() == b"Apache License"
    assert not (tmp_path / "vendor" / "ketcher.partial").exists()


def test_install_location_is_inside_the_program():
    assert ketcher_setup.KETCHER_DIR.parts[-3:] == ("si_assistant", "vendor", "ketcher")
    assert ketcher_setup.ZIP_URL.startswith("https://github.com/epam/ketcher/releases/")
