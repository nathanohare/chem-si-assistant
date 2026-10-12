"""The example project shipped with the program (Help ▸ Open example project), made by
tools/make_example_project.py: it is there, opens, has no warnings, compiles in every
journal style, can be rebuilt exactly, and the program only ever opens a copy of it."""

import hashlib
import json
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pypdf import PdfReader

from si_assistant import resources
from si_assistant.core import checks
from si_assistant.core.project import PROJECT_FILE, Project

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import make_example_project as M  # noqa: E402

IDS = ["2a", "2b", "2c", "2d", "2e", "2f", "3"]


def snapshot(folder: Path) -> dict[str, str]:
    """{relative path: SHA-1} of every file in a folder."""
    return {p.relative_to(folder).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


OUT_OF_DATE = "The example is out of date: run  python tools/make_example_project.py"


def _without(data, keys: set[str]):
    """JSON data without the keys in `keys`, at any depth."""
    if isinstance(data, dict):
        return {k: _without(v, keys) for k, v in data.items() if k not in keys}
    if isinstance(data, list):
        return [_without(v, keys) for v in data]
    return data


def comparable(path: Path):
    """What must not change when the example is rebuilt, in a form that does not depend
    on things outside the example's own code:
    * line endings ("\r\n" or "\n": git may change them when it checks files out);
    * the program's version (project.json "saved_by_version") and the wording of a
      dismissed warning (its message and fingerprint; the no-warnings test checks that
      the dismissals still apply);
    * the 2D coordinates RDKit gives a structure (a .mol file is compared as its
      canonical SMILES)."""
    data = path.read_bytes()
    if path.suffix == ".json":
        return _without(json.loads(data.decode("utf-8")),
                        {"saved_by_version", "message", "fingerprint"})
    if path.suffix == ".mol":
        from rdkit import Chem
        mol = Chem.MolFromMolBlock(data.decode("utf-8"), sanitize=False, removeHs=False)
        return Chem.MolToSmiles(mol) if mol is not None else data
    return data.replace(b"\r\n", b"\n")


@pytest.fixture
def short_tmp():
    """A temporary folder with a short path (Windows' 260-character limit: the example's
    folders are a few levels deep)."""
    folder = Path(tempfile.mkdtemp(prefix="csa-ex-"))
    yield folder
    for p in folder.rglob("*"):     # (a test may have made files read-only)
        try:
            p.chmod(stat.S_IMODE(p.stat().st_mode) | stat.S_IWUSR)
        except OSError:
            pass
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def example():
    """The bundled example, opened read-only (as the self-test does); it must not change."""
    folder = resources.example_project()
    before = snapshot(folder)
    p = Project.open(folder, read_only=True)
    yield p
    p.close()
    assert snapshot(folder) == before


def by_id(p) -> dict:
    report = p.id_report()
    return {report.display_id(c.id): c for c in p.all_compounds()}


# ---------------------------------------------------------------------- the shipped files

def test_the_example_is_shipped_complete_and_small():
    folder = resources.example_project()
    assert folder == resources.resource("example", "Example project")
    assert (folder / PROJECT_FILE).is_file()
    assert (folder.parent / M.PDF_NAME).is_file()
    # Only the project's own files: no lock, build/ folder, undo history, snapshots, Trash.
    names = {p.name for p in folder.iterdir()}
    assert not names & {"build", "trash", "snapshots"}
    assert not list(folder.rglob("*.lock")) and not list(folder.rglob(".tmp-*"))
    size = sum(p.stat().st_size for p in folder.parent.rglob("*") if p.is_file())
    assert size < 4_000_000
    # Packaged with the program, and checked by the build and the self-test.
    sys.path.insert(0, str(ROOT / "packaging"))
    import build_app as B
    assert (str(ROOT / "si_assistant" / "example"), "si_assistant/example") in B.data_files()
    assert any("Example project" in m for m in B.verify(Path("nothing"), "win32"))
    # The SI compiled from it is shipped too, and the Help menu opens it.
    assert resources.example_pdf() == folder.parent / M.PDF_NAME


def test_leftovers_of_an_opened_example_are_not_shipped(short_tmp):
    sys.path.insert(0, str(ROOT / "packaging"))
    import build_app as B
    base = "si_assistant/example/Example project/"
    for name in ("build/undo/1.json", "trash/x/compound.json", "snapshots/a.zip",
                 "project.lock", ".tmp-123", "sections/s-1/.tmp-9"):
        assert not B.keep_in_bundle(base + name), name
    for name in ("project.json", "sections/s-1/section.json",
                 "sections/s-1/compounds/c-1_x/spectra/2a_1H.pdf"):
        assert B.keep_in_bundle(base + name), name
    assert B.keep_in_bundle("si_assistant/example/Example SI.pdf")
    # A built program with such leftovers in its example is reported.
    data = B.bundle_data_dir(short_tmp / "app", "win32")
    (data / "si_assistant" / "example" / "Example project" / "build").mkdir(parents=True)
    (data / "si_assistant" / "example" / "Example project" / "project.json").write_text("{}")
    problems = B.verify(short_tmp / "app", "win32")
    assert any("should not be there" in m and "build" in m for m in problems)


def test_the_bundled_example_opens(example):
    assert not example.load_warnings
    assert example.settings["title"] == M.TITLE
    compounds = by_id(example)
    assert list(compounds) == IDS
    for cid, c in compounds.items():
        assert example.read_structure(c) is not None, cid
    assert example.spectra_section is not None
    assert len(example.references) == len(M.SOURCES)


def test_the_self_test_checks_the_example():
    from si_assistant import selftest
    assert "7 compounds" in selftest._example()


def test_every_compound_has_its_data(example):
    compounds = by_id(example)
    for data in M.COMPOUNDS + [M.ACETATE]:
        c = compounds[data["id"]]
        assert c.name == data["name"]
        info = M.info_of(data["smiles"])
        assert example.structure_info(c).formula == info.formula
        reports = {r["nucleus"]: r["text"] for r in c.values["nmr"]}
        assert reports["1H"] == M.h1_report(*data["h1"])
        assert reports["13C"] == M.c13_report(*data["c13"])
        if data.get("f19"):
            assert reports["19F"] == M.f19_report(*data["f19"])
        kinds = {i["type"] for i in c.values["spectra"]}
        assert kinds == ({"1H", "13C", "19F"} if data.get("f19") else {"1H", "13C"})
        for item in c.values["spectra"]:
            assert example.attachment_path(c, "spectra", item["file"]).is_file()
        # The literature the data come from is cited on each compound.
        cited = c.values["references"]["refs"]
        assert [example.reference(r)["doi"] for r in cited] == \
               [M.SOURCES[k]["doi"] for k in data["refs"]]
        assert c.values["hrms"]["found"]
    # Two compounds show the HRMS report files (their found m/z is read from them).
    with_reports = [cid for cid, c in compounds.items() if example.hrms_reports(c)]
    assert with_reports == ["2d", "3"]
    # Every reduction uses General Procedure A; 3 has its own procedure.
    gp = example.all_general_procedures()[0][1]
    assert [cid for cid, c in compounds.items() if example.compound_gp(c) is gp] == IDS[:-1]


def test_the_checks_give_no_warnings(example):
    assert M.all_warnings(example) == []
    # The warnings the literature data rightly raise are dismissed with a reason.
    report = example.id_report()
    dismissed = {(report.display_id(c.id), w.check)
                 for c in example.all_compounds()
                 for w, _info in checks.dismissed_warnings(example, c, report)}
    assert dismissed == set(M.DISMISSED)


def test_general_information_says_what_is_real_and_what_is_not(example):
    text = example.read_front_text("general")
    assert M.NOTE in text
    assert "not a real Supporting" in example.read_front_text("cover")
    for author in example.settings["authors"]:
        assert not author.get("email") or author["email"].endswith("@example.com")


@pytest.mark.parametrize("style", ["ACS", "Wiley", "RSC"])
def test_it_compiles_without_warnings_in_every_style(qapp, short_tmp, style):
    folder = resources.copy_example_project(short_tmp)
    p = Project.open(folder)
    try:
        result = M.compile_si(p, style)
    finally:
        p.close()
    assert result.warnings == []
    reader = PdfReader(str(result.pdf))
    assert len(reader.pages) >= 15
    # (all pages, compared without spaces and line breaks: where the lines break depends on
    # the fonts this computer has, and with some fonts a hyphen is read as a soft hyphen)
    text = "".join(page.extract_text() or "" for page in reader.pages)
    text = "".join(text.replace("\xad", "-").split())
    assert "1-(4-Bromophenyl)ethylacetate" in text
    assert "GeneralProcedureA" in text


def test_the_tool_rebuilds_the_same_project(qapp, short_tmp):
    # The shipped example is what tools/make_example_project.py makes now: the same files,
    # with the same contents (internal IDs included), except the spectrum PDFs (they hold
    # the time they were drawn) and what `comparable` leaves out.
    shipped = resources.example_project()
    rebuilt = M.build(short_tmp, compile_pdf=False, quiet=True, need_fonts=False)
    old, new = snapshot(shipped), snapshot(rebuilt)
    assert set(old) == set(new), OUT_OF_DATE
    for name in old:
        if not name.endswith(".pdf"):
            assert comparable(shipped / name) == comparable(rebuilt / name), \
                f"{OUT_OF_DATE} ({name} differs)"


def test_comparable_ignores_line_endings_versions_and_coordinates(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "x.txt").write_bytes(b"one\ntwo\n")
    (b / "x.txt").write_bytes(b"one\r\ntwo\r\n")
    assert comparable(a / "x.txt") == comparable(b / "x.txt")
    (a / "p.json").write_text('{"saved_by_version": "0.1.0", "title": "T"}')
    (b / "p.json").write_text('{\r\n "saved_by_version": "0.2.0",\r\n "title": "T"}')
    assert comparable(a / "p.json") == comparable(b / "p.json")
    (b / "p.json").write_text('{"saved_by_version": "0.1.0", "title": "U"}')
    assert comparable(a / "p.json") != comparable(b / "p.json")
    from rdkit import Chem
    from rdkit.Chem import AllChem
    mol = Chem.MolFromSmiles("CC(O)c1ccccc1")
    AllChem.Compute2DCoords(mol)
    (a / "s.mol").write_text(Chem.MolToMolBlock(mol))
    conf = mol.GetConformer()
    for i in range(mol.GetNumAtoms()):          # (as another RDKit version might place it)
        pt = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (-pt.y + 1.5, pt.x - 0.5, 0.0))
    (b / "s.mol").write_text(Chem.MolToMolBlock(mol))
    assert (a / "s.mol").read_text() != (b / "s.mol").read_text()
    assert comparable(a / "s.mol") == comparable(b / "s.mol")


def test_the_shipped_spectra_have_real_text():
    # Each spectrum's numbers and labels are text in a font, not solid boxes (what Qt
    # draws when it has no font, e.g. a build run on Qt's "offscreen" platform on Windows),
    # in the spectrum files and in the compiled example SI.
    spectra = sorted(resources.example_project().rglob("spectra/*.pdf"))
    assert len(spectra) >= 14
    for path in spectra:
        page = PdfReader(str(path)).pages[0]
        assert page["/Resources"]["/Font"], f"{path.name}: no font, so no real text"
        text = page.extract_text() or ""
        assert "Simulated spectrum" in text and "ppm" in text, path.name
        assert sum(ch.isdigit() for ch in text) > 20, path.name   # the peak labels
    reader = PdfReader(str(resources.example_pdf()))
    pages = [page.extract_text() or "" for page in reader.pages]
    assert sum("Simulated spectrum" in t for t in pages) == len(spectra)


def test_without_a_font_the_build_stops_before_changing_anything(qapp, short_tmp, monkeypatch):
    from PySide6.QtGui import QFontDatabase
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a: []))
    with pytest.raises(RuntimeError, match="solid box"):
        M.build(short_tmp, compile_pdf=False, quiet=True)
    assert list(short_tmp.iterdir()) == []


def test_a_failed_build_leaves_the_example_as_it_was(qapp, short_tmp, monkeypatch):
    # The build is made elsewhere and only moved into place once it has no warnings.
    target = short_tmp / M.PROJECT_NAME
    target.mkdir()
    (target / "keep.txt").write_text("the example that was there")
    monkeypatch.setattr(M, "all_warnings", lambda p: ["2a: something is wrong"])
    with pytest.raises(RuntimeError, match="something is wrong"):
        M.build(short_tmp, compile_pdf=False, quiet=True, need_fonts=False)
    assert [p.name for p in short_tmp.iterdir()] == [M.PROJECT_NAME]
    assert [p.name for p in target.iterdir()] == ["keep.txt"]


def test_the_builder_writes_the_same_bytes_on_every_system():
    # The HRMS report files are written with "\n" line endings (not the system's own).
    text = M.hrms_report_csv(M.ACETATE["smiles"], 0.8, "3")
    assert "\r" not in text
    source = (ROOT / "tools" / "make_example_project.py").read_text(encoding="utf-8")
    assert "csv.write_text(" not in source


def test_found_values_are_close_to_the_calculated_ones():
    for data in M.COMPOUNDS + [M.ACETATE]:
        if data["ppm"] is None:
            continue
        result = checks.hrms_calc(M.info_of(data["smiles"]), M.ADDUCT,
                                  M.found_mz(data["smiles"], data["ppm"]), 5.0)
        assert 0.3 <= abs(result.ppm) <= 1.5


def test_yields_match_the_masses():
    for data in M.COMPOUNDS + [M.ACETATE]:
        scale = data.get("scale", M.SCALE)
        mass = float(M.yield_mass(data["smiles"], data["yield_"], scale))
        assert abs(mass / (float(scale) * M.info_of(data["smiles"]).mw) * 100 - data["yield_"]) < 0.2


# ---------------------------------------------------------------------- copying it

def test_free_folder_never_writes_over(tmp_path):
    assert resources.free_folder(tmp_path) == tmp_path / "Chem SI Assistant example"
    (tmp_path / "Chem SI Assistant example").mkdir()
    (tmp_path / "Chem SI Assistant example (2)").write_text("")
    assert resources.free_folder(tmp_path) == tmp_path / "Chem SI Assistant example (3)"


def test_a_copy_from_a_read_only_install_can_be_edited(short_tmp):
    # The program's own folder can be read-only (Program Files, a macOS app): the copy
    # must still be writable.
    installed = short_tmp / "app"
    shutil.copytree(resources.example_project(), installed)
    for p in [installed, *installed.rglob("*")]:
        if p.is_file():
            p.chmod(stat.S_IREAD)
    (installed / "build").mkdir(mode=0o777)   # left by mistake: not copied
    (short_tmp / "mine").mkdir()
    copy = resources.copy_example_project(short_tmp / "mine", installed)
    assert copy == short_tmp / "mine" / "Chem SI Assistant example"
    assert not (copy / "build").exists()
    p = Project.open(copy)
    try:
        p.acquire_lock("Tester")
        p.settings["title"] = "My copy"
        p.save_settings()
        c = p.all_compounds()[0]
        c.values["yield"]["percent"] = "50"
        p.save_compound(c)
    finally:
        p.close()
    again = Project.open(copy, read_only=True)
    assert again.settings["title"] == "My copy"
    again.close()


def test_a_missing_example_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        resources.copy_example_project(tmp_path, tmp_path / "nothing here")
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def window(qapp, short_tmp, monkeypatch):
    from si_assistant.ui import main_window as mw
    monkeypatch.setattr(mw, "QSettings", lambda *a: QSettings(str(short_tmp / "prefs.ini"),
                                                              QSettings.Format.IniFormat))
    # The user's copy of the example (conftest keeps it in a temporary folder); a folder
    # dialog would be a mistake: it is never asked where.
    monkeypatch.setattr(resources, "example_copy_folder",
                        lambda: short_tmp / "appdata" / resources.EXAMPLE_COPY_DIR)
    asked = []
    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a: asked.append(a) or ""))
    questions = []
    answers = []

    def question(parent, title, text, *args):
        questions.append(title)
        return answers.pop(0)
    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(question))
    win = mw.MainWindow()
    win.prefs.user_name = "Tester"
    yield win, asked, questions, answers
    win.close_project()
    win.close()


def edit_title(win, title: str) -> None:
    win.project.settings["title"] = title
    win.project.save_settings()


def test_the_example_opens_without_asking_where(window):
    win, asked, questions, answers = window
    shipped = resources.example_project()
    before = snapshot(shipped)
    win.welcome.example_btn.click()
    copy = resources.example_copy_folder()
    assert win.project is not None and win.project.folder == copy
    assert not win.project.read_only
    assert not asked and not questions
    assert "copy" in win.statusBar().currentMessage()
    assert resources.example_copy_state() == "current"
    # The example shipped with the program was not opened or changed.
    win.close_project()
    assert snapshot(shipped) == before


def test_the_users_copy_keeps_their_changes(window):
    win, asked, questions, answers = window
    win.act_open_example.trigger()
    edit_title(win, "My changes")
    win.close_project()
    win.act_open_example.trigger()                  # the same copy, not a new one
    assert win.project.folder == resources.example_copy_folder()
    assert win.project.settings["title"] == "My changes"
    win.act_open_example.trigger()                  # already open: left as it is
    assert "already open" in win.statusBar().currentMessage()
    assert not asked and not questions


def test_start_the_example_over(window):
    win, asked, questions, answers = window
    win.act_open_example.trigger()
    edit_title(win, "My changes")
    answers.append(mw_button("No"))
    win.act_example_over.trigger()                  # "No": nothing changes
    assert win.project.settings["title"] == "My changes"
    answers.append(mw_button("Yes"))
    win.act_example_over.trigger()                  # replaced while it was open
    assert questions == ["Start the example over"] * 2
    assert win.project.folder == resources.example_copy_folder()
    assert win.project.settings["title"] != "My changes"
    leftovers = [p.name for p in resources.example_copy_folder().parent.iterdir()]
    assert sorted(leftovers) == sorted([resources.EXAMPLE_COPY_DIR, resources.EXAMPLE_STAMP])


def test_an_updated_example_is_offered_once(window):
    win, asked, questions, answers = window
    win.act_open_example.trigger()
    edit_title(win, "My changes")
    win.close_project()
    stamp = resources.example_copy_folder().parent / resources.EXAMPLE_STAMP
    stamp.write_text("an older example\n", encoding="utf-8")     # as after an update
    assert resources.example_copy_state() == "outdated"
    answers.append(mw_button("No"))                 # keep my copy
    win.act_open_example.trigger()
    assert questions == ["Updated example project"]
    assert win.project.settings["title"] == "My changes"
    win.close_project()
    win.act_open_example.trigger()                  # not asked again for this example
    assert len(questions) == 1 and win.project.settings["title"] == "My changes"
    win.close_project()
    stamp.write_text("another older example\n", encoding="utf-8")   # the next update
    answers.append(mw_button("Yes"))                # take the new example
    win.act_open_example.trigger()
    assert len(questions) == 2
    assert win.project.settings["title"] != "My changes"
    assert resources.example_copy_state() == "current"
    win.close_project()
    win.act_open_example.trigger()                  # not asked again
    assert len(questions) == 2


def test_a_failed_copy_leaves_the_open_project_open(window, monkeypatch, short_tmp):
    # The copy is made before the open project is closed: if it fails (a full disk…),
    # the project the user had open stays open.
    from si_assistant.ui import main_window as mw
    win, asked, questions, answers = window
    mine = Project.create(short_tmp, "Mine")
    mine.close()
    assert win.open_project(mine.folder)
    said = []
    monkeypatch.setattr(mw.QMessageBox, "warning", staticmethod(
        lambda parent, title, text: said.append(title)))

    def fail(source=None):
        raise PermissionError("no permission to write here")
    monkeypatch.setattr(resources, "prepare_example_copy", fail)
    win.open_example_project()
    assert said == ["Could not copy the example project"]
    assert win.project is not None and win.project.folder == mine.folder


def test_an_old_copy_that_cannot_be_replaced_is_kept_whole(short_tmp, monkeypatch):
    monkeypatch.setattr(resources, "example_copy_folder",
                        lambda: short_tmp / "appdata" / resources.EXAMPLE_COPY_DIR)
    folder = resources.install_example_copy(resources.prepare_example_copy())
    (folder / "mine.txt").write_text("my notes", encoding="utf-8")
    new = resources.prepare_example_copy()
    real_rename = Path.rename

    def rename(self, target):
        if self == new:
            raise PermissionError("in use")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(PermissionError):
        resources.install_example_copy(new)
    assert (folder / "mine.txt").read_text(encoding="utf-8") == "my notes"
    assert (folder / PROJECT_FILE).is_file()


def mw_button(name: str):
    from PySide6.QtWidgets import QMessageBox
    return getattr(QMessageBox.StandardButton, name)


def test_help_opens_the_example_si(window, monkeypatch):
    from si_assistant.ui import main_window as mw
    win, asked, questions, answers = window
    opened = []
    monkeypatch.setattr(mw.QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url) or True))
    win.act_open_example_pdf.trigger()
    assert [Path(u.toLocalFile()) for u in opened] == [resources.example_pdf()]


def test_help_says_so_when_the_example_is_missing(window, monkeypatch, short_tmp):
    from si_assistant.ui import main_window as mw
    win, asked, questions, answers = window
    monkeypatch.setattr(resources, "example_project", lambda: short_tmp / "gone")
    said = []
    monkeypatch.setattr(mw.QMessageBox, "warning", staticmethod(
        lambda parent, title, text: said.append(title)))
    win.open_example_project()
    assert said == ["Example project not found"] and win.project is None and not asked
