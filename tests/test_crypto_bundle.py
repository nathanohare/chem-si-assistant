"""PDFs saved with edit restrictions (AES-encrypted with an empty opening password, as much
NMR and mass-spectrometry software saves its reports) are read like any other PDF: pypdf
needs the 'cryptography' package for that (requirements.txt; bundled by the installers,
packaging/chem_si_assistant.spec). A PDF that really needs a password is still reported as
password-protected. Made-up data only."""

import io
import json
from pathlib import Path

import pytest

from si_assistant import selftest
from si_assistant.compile import document as doc
from si_assistant.core import ms_read
from si_assistant.core import ms_reports as ms
from si_assistant.core import spectra as S

REPORT = "HRMS (ESI) m/z: [M+H]+ calcd for C10H13O2 165.0910; found 165.0912"


def write(path: Path, user_password: str = "") -> Path:
    path.write_bytes(selftest.edit_restricted_pdf(REPORT, user_password))
    return path


def test_the_cryptography_package_is_installed_and_used_by_pypdf():
    # Without it, pypdf quietly falls back to reading only RC4 PDFs (pip install -r
    # requirements.txt installs it).
    import cryptography  # noqa: F401
    from pypdf import PdfReader
    from pypdf._crypt_providers import crypt_provider
    assert crypt_provider[0] == "cryptography"
    reader = PdfReader(io.BytesIO(selftest.edit_restricted_pdf(REPORT)))
    assert reader.is_encrypted                     # (really encrypted, with AES-256)
    assert "/AESV3" in str(reader.trailer["/Encrypt"].get_object())


def test_an_edit_restricted_spectrum_pdf_is_counted_and_not_called_encrypted(tmp_path):
    pdf = write(tmp_path / "edit_restricted.pdf")
    assert S.pdf_page_count(pdf) == 2
    assert doc.pdf_page_count(pdf) == 2
    sizes = doc.pdf_page_sizes(pdf)
    assert sizes == [(300.0, 100.0), (300.0, 100.0)]


def test_an_edit_restricted_hrms_report_pdf_is_read(tmp_path):
    pdf = write(tmp_path / "3a_HRMS.pdf")
    assert "found 165.0912" in ms.pdf_text(pdf)
    report = ms.read_report(pdf)
    assert report.readable, report.message
    assert any(abs(v - 165.0912) < 1e-6 for v in report.mz)
    assert report.is_measured(165.0912)
    from si_assistant.ui import hrms_reports_ui
    assert hrms_reports_ui._describe_file(pdf) == "PDF: 2 pages, each as a page"


def test_an_edit_restricted_manuscript_pdf_is_read(tmp_path):
    pdf = write(tmp_path / "manuscript.pdf")
    src = ms_read.read_pdf(pdf)
    assert not src.error and src.pages == 2


def test_an_edit_restricted_spectrum_is_printed_in_the_si(qapp, project, tmp_path):
    from pypdf import PdfReader
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    s = project.add_section("data", "Compounds")
    c = project.add_compound(s.id, name="3-Phenylpropanal")
    if "spectra" not in c.field_keys:
        project.add_field(c, {"key": "spectra"})
    project.attach_compound_file(c, "spectra", write(tmp_path / "c_1H.pdf"), {"type": "1H"})
    problems: list[str] = []
    found = S.spectra_of(project, c, problems=problems)
    assert problems == [] and [sp.page for sp in found] == [1, 2]
    result = compile_pdf(project, CompileOptions.from_settings(project.settings),
                         image_info, convert_to_png)
    assert not any("password-protected" in w or "Could not read" in w
                   for w in result.warnings), result.warnings
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(result.pdf)).pages)
    assert "found 165.0912" in text                # the spectrum page itself is in the SI


def test_a_pdf_that_needs_a_password_is_still_reported_plainly(project, tmp_path):
    pdf = write(tmp_path / "locked.pdf", user_password="secret")
    with pytest.raises(S.PdfUnreadable) as e:
        S.pdf_page_count(pdf)
    assert e.value.encrypted
    with pytest.raises(doc.PdfUnreadable) as e:
        doc.pdf_page_count(pdf)
    assert e.value.encrypted
    b = doc.Builder(project, doc.CompileOptions(), lambda path: None)
    assert b.pdf_pages(pdf) == []
    assert "password-protected" in b.warnings[0] and "damaged" not in b.warnings[0]
    report = ms.read_report(pdf)
    assert not report.readable
    from si_assistant.ui import hrms_reports_ui
    assert hrms_reports_ui._describe_file(pdf) == "password-protected PDF: not printed"
    assert ms_read.read_pdf(pdf).error


def test_the_self_test_checks_edit_restricted_pdfs(monkeypatch, tmp_path):
    assert "edit-restricted (AES) PDFs open; cryptography" in selftest._pdf()
    # Only the PDF check is real here; the others (the example project among them) have
    # their own tests, so a problem there does not make this one fail.
    for name in ("_files", "_python", "_spelling", "_rdkit", "_typst", "_qt", "_example"):
        monkeypatch.setattr(selftest, name, lambda: "fine")
    report = tmp_path / "r.json"
    assert selftest.run(report) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["checks"]["pdf"]["ok"], data["checks"]["pdf"]


def test_the_installers_bring_cryptography_along(tmp_path):
    import importlib.util
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("build_app_crypto",
                                                  root / "packaging" / "build_app.py")
    B = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(B)
    recipe = (root / "packaging" / "chem_si_assistant.spec").read_text(encoding="utf-8")
    hidden = recipe.split("hiddenimports")[1].split("]")[0]
    assert '"cryptography"' in hidden and '"_cffi_backend"' in hidden
    assert "cryptography" not in recipe.split("excludes")[1].split("]")[0]
    assert any(line.startswith("cryptography") for line in
               (root / "requirements.txt").read_text(encoding="utf-8").splitlines())
    # Their licence texts are copied in (Apache-2.0 asks for the licence to go along).
    assert 'copy_metadata("cryptography")' in recipe and 'copy_metadata("cffi")' in recipe

    def crypto_problems(missing):
        return [m for m in missing if "_rust" in m or "_cffi_backend" in m
                or ".dist-info" in m]

    def add_licences(place):
        lic = place / "cryptography-50.0.2.dist-info" / "licenses"
        lic.mkdir(parents=True)
        for name in ("LICENSE", "LICENSE.APACHE", "LICENSE.BSD"):
            (lic / name).write_text("licence", encoding="utf-8")
        (place / "cffi-2.1.1.dist-info" / "licenses").mkdir(parents=True, exist_ok=True)
        (place / "cffi-2.1.1.dist-info" / "licenses" / "LICENSE").write_text(
            "licence", encoding="utf-8")

    # `verify` says when it is missing from a built program…
    app = tmp_path / "Chem SI Assistant"
    (app / "_internal").mkdir(parents=True)
    missing = B.verify(app, "win32")
    assert any("_rust" in m for m in missing) and any("_cffi_backend" in m for m in missing)
    assert any("LICENSE.APACHE" in m for m in missing)
    assert any("cffi-" in m and "LICENSE" in m for m in missing)
    # …and not when it is there.
    rust = app / "_internal" / "cryptography" / "hazmat" / "bindings" / "_rust.pyd"
    rust.parent.mkdir(parents=True)
    rust.write_bytes(b"")
    (app / "_internal" / "_cffi_backend.cp312-win_amd64.pyd").write_bytes(b"")
    missing = B.verify(app, "win32")
    assert crypto_problems(missing) and all(".dist-info" in m
                                            for m in crypto_problems(missing))
    # (An empty "licenses" folder is not mistaken for the licence file.)
    (app / "_internal" / "cffi-2.1.1.dist-info" / "licenses").mkdir(parents=True)
    assert any("cffi-" in m for m in B.verify(app, "win32"))
    add_licences(app / "_internal")
    assert not crypto_problems(B.verify(app, "win32"))
    # (On macOS PyInstaller keeps compiled parts in Contents/Frameworks and data such as
    # the package information in Contents/Resources.)
    mac = tmp_path / "Chem SI Assistant.app"
    (mac / "Contents" / "Frameworks" / "cryptography" / "hazmat" / "bindings").mkdir(
        parents=True)
    (mac / "Contents" / "Frameworks" / "cryptography" / "hazmat" / "bindings" /
     "_rust.abi3.so").write_bytes(b"")
    (mac / "Contents" / "Frameworks" / "_cffi_backend.cpython-312-darwin.so").write_bytes(b"")
    add_licences(mac / "Contents" / "Resources")
    assert not crypto_problems(B.verify(mac, "darwin"))


def test_cryptography_has_an_upper_version_limit():
    """Like the other main parts, a new major version of 'cryptography' is only used
    after it has been tested (the limit is raised by hand)."""
    root = Path(__file__).resolve().parent.parent
    line = next(line for line in
                (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
                if line.startswith("cryptography"))
    assert "<" in line.split("#")[0]
