"""The PubChem name lookup (SPEC §13n.5). No network: every answer comes from a fake
fetcher, and the names and CIDs in them are made up."""

import json
import urllib.error

import pytest
from PySide6.QtWidgets import QMessageBox

from si_assistant.core import chem, pubchem
from si_assistant.ui import name_lookup

ESTER = "COC(=O)c1ccc(N2CCOCC2)cc1"
CHIRAL = "C[C@H](N)C(=O)OCc1ccccc1"
SALT = "C[N+](C)(C)Cc1ccccc1.[Br-]"


def mol(smiles):
    return chem.parse_structure(smiles)


class FakePubChem:
    """Answers by InChIKey (full or first block): a list of properties, or nothing (404),
    or an exception. Remembers every URL asked for."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        assert timeout == pubchem.TIMEOUT_S
        if self.error is not None:
            raise self.error
        key = url.split("/inchikey/")[1].split("/")[0]
        props = self.answers.get(key)
        if not props:
            raise pubchem.NotFound()
        return {"PropertyTable": {"Properties": props}}


def prop(cid, name, title=""):
    return {"CID": cid, "IUPACName": name, "Title": title or name}


def test_found_by_the_full_inchikey():
    key = pubchem.inchikey(mol(ESTER))
    assert len(key) == 27 and key.count("-") == 2
    fake = FakePubChem({key: [prop(101, "methyl 4-morpholin-4-ylbenzoate", "Made-up ester")]})
    r = pubchem.lookup_name(mol(ESTER), fetch=fake)
    assert r.status == "found" and r.name == "methyl 4-morpholin-4-ylbenzoate"
    assert r.cid == 101 and "CID 101" in r.message and r.url.endswith("/101")
    assert len(fake.urls) == 1 and key in fake.urls[0]
    assert fake.urls[0].endswith("/property/IUPACName,Title/JSON")


def test_not_found_says_to_use_chemdraw():
    fake = FakePubChem()
    r = pubchem.lookup_name(mol(ESTER), fetch=fake)
    assert r.status == "not_found"
    assert "not in PubChem" in r.message and "Convert Structure to Name" in r.message
    assert len(fake.urls) == 2   # the full key, then its connectivity part


def test_stereo_fallback_only_with_exactly_one_compound():
    key = pubchem.inchikey(mol(CHIRAL))
    first = key.split("-")[0]
    fake = FakePubChem({first: [prop(7, "benzyl 2-aminopropanoate")]})
    r = pubchem.lookup_name(mol(CHIRAL), fetch=fake)
    assert r.status == "no_stereo" and r.name == "benzyl 2-aminopropanoate"
    assert "stereochemistry" in r.message
    assert fake.urls[0].split("/inchikey/")[1].startswith(key)          # full key first
    assert fake.urls[1].split("/inchikey/")[1].split("/")[0] == first   # then 14 letters
    fake = FakePubChem({first: [prop(7, "benzyl (2S)-2-aminopropanoate"),
                                prop(8, "benzyl (2R)-2-aminopropanoate")]})
    assert pubchem.lookup_name(mol(CHIRAL), fetch=fake).status == "not_found"


def test_salt_is_looked_up_as_a_whole():
    m = mol(SALT)
    assert pubchem.piece_count(m) == 2
    key = pubchem.inchikey(m)
    fake = FakePubChem({key: [prop(55, "benzyl(trimethyl)azanium;bromide")]})
    assert pubchem.lookup_name(m, fetch=fake).status == "found"
    r = pubchem.lookup_name(m, fetch=FakePubChem())
    assert r.status == "not_found" and "2 pieces" in r.message


def test_offline_and_errors_are_plain_messages():
    r = pubchem.lookup_name(mol(ESTER), fetch=FakePubChem(
        error=pubchem.LookupFailed("PubChem could not be reached.")))
    assert r.status == "error" and "could not be reached" in r.message


def test_fetch_json(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"PropertyTable": {"Properties": []}}).encode()

    def ok(request, timeout):
        seen["agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(pubchem.urllib.request, "urlopen", ok)
    assert pubchem.fetch_json("https://example.invalid/x") == {"PropertyTable":
                                                               {"Properties": []}}
    assert seen["agent"].startswith("Chem-SI-Assistant/") and seen["timeout"] == 10

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(pubchem.urllib.request, "urlopen", not_found)
    with pytest.raises(pubchem.NotFound):
        pubchem.fetch_json("https://example.invalid/x")

    def offline(request, timeout):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(pubchem.urllib.request, "urlopen", offline)
    with pytest.raises(pubchem.LookupFailed, match="internet"):
        pubchem.fetch_json("https://example.invalid/x")


# ---------------------------------------------------------------------- the Name field

@pytest.fixture
def page(qapp, project):
    from si_assistant.ui.compound_panel import CompoundPanel
    data = project.add_section("data", "Products")
    project.set_id_table(data.id, ["3a"])
    c = project.add_compound(data.id)
    panel = CompoundPanel(project, c, lambda: "Tester")
    editor = next(b.editor for b in panel.boxes if b.defn.key == "name")
    yield project, c, panel, editor
    panel.deleteLater()


def answer_with(monkeypatch, smiles, name="methyl 4-morpholin-4-ylbenzoate", cid=101):
    key = pubchem.inchikey(mol(smiles))
    fake = FakePubChem({key: [prop(cid, name)]})
    monkeypatch.setattr(name_lookup, "fetcher", fake)
    return fake


def test_button_needs_a_structure(page):
    project, c, panel, editor = page
    assert isinstance(editor, name_lookup.NameEditor)
    assert not editor.lookup_btn.isEnabled()
    project.set_structure(c, ESTER)
    panel.refresh_structure()
    assert editor.lookup_btn.isEnabled()


def test_lookup_fills_the_name_and_notes_where_from(page, monkeypatch):
    project, c, panel, editor = page
    project.set_structure(c, ESTER)
    panel.refresh_structure()
    fake = answer_with(monkeypatch, ESTER)
    asked = []
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: asked.append(1) or
                        (True, False))
    editor.look_up()
    assert editor.widgets["text"].text() == "methyl 4-morpholin-4-ylbenzoate"
    assert "PubChem" in editor.note.text() and editor.note.isVisible() is not None
    assert asked == [1] and len(fake.urls) == 1
    panel.save()
    stored = json.loads((c.folder / "compound.json").read_text(encoding="utf-8"))
    assert stored["values"]["name"]["text"] == "methyl 4-morpholin-4-ylbenzoate"
    assert stored["values"]["name"]["from_pubchem"]["cid"] == 101
    editor.look_up()                 # the note is shown once per session
    assert asked == [1]
    # Edited by hand: no longer "from PubChem".
    editor.widgets["text"].setText("methyl 4-morpholinobenzoate")
    editor.widgets["text"].textEdited.emit("methyl 4-morpholinobenzoate")
    panel.save()
    stored = json.loads((c.folder / "compound.json").read_text(encoding="utf-8"))
    assert "from_pubchem" not in stored["values"]["name"] and editor.note.text() == ""


def test_privacy_note_cancel_and_dont_show_again(page, monkeypatch):
    project, c, panel, editor = page
    project.set_structure(c, ESTER)
    panel.refresh_structure()
    fake = answer_with(monkeypatch, ESTER)
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: (False, False))
    editor.look_up()
    assert not fake.urls and editor.widgets["text"].text() == ""   # nothing sent
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: (True, True))
    editor.look_up()
    assert fake.urls
    assert str(name_lookup._settings().value(name_lookup.SKIP_NOTE_KEY)).lower() == "true"
    monkeypatch.setattr(name_lookup, "_note_shown", False)        # a new session
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: pytest.fail("shown"))
    editor.look_up()


def test_replacing_a_name_asks_first(page, monkeypatch):
    project, c, panel, editor = page
    project.set_structure(c, ESTER)
    panel.refresh_structure()
    answer_with(monkeypatch, ESTER)
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: (True, False))
    editor.widgets["text"].setText("my own name")
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    editor.look_up()
    assert editor.widgets["text"].text() == "my own name"
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    editor.look_up()
    assert editor.widgets["text"].text() == "methyl 4-morpholin-4-ylbenzoate"


def test_not_found_and_offline_messages(page, monkeypatch):
    project, c, panel, editor = page
    project.set_structure(c, ESTER)
    panel.refresh_structure()
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: (True, False))
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda parent, title, text, *a: shown.append(text)))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda parent, title, text, *a: shown.append(text)))
    monkeypatch.setattr(name_lookup, "fetcher", FakePubChem())
    editor.look_up()
    assert "not in PubChem" in shown[-1] and "ChemDraw" in shown[-1]
    monkeypatch.setattr(name_lookup, "fetcher", FakePubChem(
        error=pubchem.LookupFailed("PubChem could not be reached. Check the internet "
                                   "connection and try again.")))
    editor.look_up()
    assert "could not be reached" in shown[-1] and editor.widgets["text"].text() == ""


def test_no_stereo_match_is_offered_with_a_label(page, monkeypatch):
    project, c, panel, editor = page
    project.set_structure(c, CHIRAL)
    panel.refresh_structure()
    first = pubchem.inchikey(mol(CHIRAL)).split("-")[0]
    monkeypatch.setattr(name_lookup, "fetcher",
                        FakePubChem({first: [prop(7, "benzyl 2-aminopropanoate")]}))
    monkeypatch.setattr(name_lookup, "ask_privacy", lambda parent: (True, False))
    questions = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, *a: questions.append(text)
        or QMessageBox.StandardButton.Yes))
    editor.look_up()
    assert "stereochemistry" in questions[-1]
    assert editor.widgets["text"].text() == "benzyl 2-aminopropanoate"
    assert "without stereochemistry" in editor.note.text()


def test_the_lookup_in_a_thread(qapp, monkeypatch):
    monkeypatch.setattr(name_lookup, "USE_THREAD", True)
    fake = answer_with(monkeypatch, ESTER)
    r = name_lookup.run_lookup(mol(ESTER))
    assert r.status == "found" and fake.urls
