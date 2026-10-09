"""Build the EXAMPLE PROJECT shipped with Chem SI Assistant (Help ▸ Open example project…).
A developer tool: not part of the program.

    python tools/make_example_project.py                (writes si_assistant/example/)
    python tools/make_example_project.py --out DIR      (somewhere else, e.g. to compare)
    python tools/make_example_project.py --png DIR      (also PNG previews of some pages)

It writes si_assistant/example/Example project/ (the project) and
si_assistant/example/Example SI.pdf (the SI compiled from it, ACS style), and nothing
else. The project is made with the program's own Project API, so its files are exactly
what the program writes; it has no lock file, build/ folder, undo history, snapshots or
Trash. Re-running it gives the same project: the internal IDs come from a fixed seed.

What is real and what is not (said in the same words on the cover and in General
Information, NOTE):
* The compounds are real, and their ¹H/¹³C/¹⁹F NMR data, physical states and Rf values
  are those of the cited papers (SOURCES; each compound cites its own). Copied exactly,
  except a few ¹H signals that a source writes in a form the journals discourage (a
  multiplet as one value, a plain doublet as "app. d"): those are written as the
  simulated spectrum shows them (marked REWRITTEN below); and a ¹H frequency a source
  prints as 501 MHz is given as the nominal 500 MHz, like the others.
* The spectra are SIMULATED from those peak lists with tools/nmr_spectra.py, and each
  spectrum says so on the page itself ("Simulated spectrum (not a measurement)").
* The procedures (General Procedure A, the acetylation), the optimization study
  (Table S1), the yields, masses, HRMS "found" values and the two HRMS report files are
  INVENTED for illustration (consistent with the 0.50 mmol scale, the molecular weights
  and the program's calculated m/z); the HRMS "calcd" values are the program's own.
* The authors, affiliation and e-mail are placeholders (example.com).

The build is made in a temporary folder and only moved into place when it has no
warnings, so a failed build never leaves a half-made example behind.

Uses Qt's own platform (for the computer's fonts, as tools/nmr_spectra.py does); only the
program's own dependencies.
"""

from __future__ import annotations

import argparse
import contextlib
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

EXAMPLE_DIR = ROOT / "si_assistant" / "example"
PROJECT_NAME = "Example project"          # the folder inside si_assistant/example/
PDF_NAME = "Example SI.pdf"
SEED = 20261009                            # the internal IDs (c-…, s-…) come from this

TITLE = ("Sodium Borohydride Reduction of Aryl Methyl Ketones: "
         "an Example Project for Chem SI Assistant")
AUTHORS = [("Alex Example", False, ""), ("Blair Sample", True, "blair.sample@example.com")]
AFFILIATION = "Department of Chemistry, Example University, Exampletown, EX 00000, Exampleland"
# What is real and what is not: in General Information and, in the same words, on the
# cover (a cover page may be read on its own).
NOTE = ("This example was made for Chem SI Assistant. The compounds and their NMR data, "
        "R_{f} values and physical states are from the cited literature; the spectra are "
        "simulated from those data. The procedures, the optimization study (Table S1), "
        "the yields, masses and HRMS found values are invented for illustration. Nothing "
        "here is a measurement.")
COVER_TEXT = ("**Example project shipped with Chem SI Assistant — not a real Supporting "
              "Information.** " + NOTE)

DATA_SECTION = "Synthesis and Characterization"
OPT_SECTION = "Reaction Optimization"
SCALE = "0.50"                              # mmol, every reduction (General Procedure A)

# ---------------------------------------------------------------------------------------
# The literature (all DOIs checked against Crossref on 2026-10-09)
# ---------------------------------------------------------------------------------------

SOURCES = {
    "wong2025": dict(
        authors="Wong, W. Y. W.; Wallace, S.; Johnston, C. P.",
        title="Concurrent Linear Deracemization of Secondary Benzylic Alcohols via "
              "Simultaneous Photocatalysis and Whole-cell Biocatalysis",
        journal="ACS Catal.", year="2025", volume="15", issue="17", pages="15195–15210",
        doi="10.1021/acscatal.5c04974"),
    "santiago2026": dict(
        authors="Jimenez Santiago, J. L.; Liang, Q.; Mendez Ocampo, P. A.; Li, M.; Song, D.",
        title="Iron Complexes of N,C-Chelate Ligands toward Catalytic Hydrosilylation of "
              "Ketones",
        journal="Inorg. Chem.", year="2026", volume="65", issue="20", pages="11452–11461",
        doi="10.1021/acs.inorgchem.6c01574"),
    "satoh2025": dict(
        authors="Satoh, I.; Semba, K.; Hasegawa, J.; Fujihara, T.",
        title="Periphery-Arranged Bulky Phosphite Ligands: Synthesis and Steric Effect in "
              "Cu-Catalyzed Hydrosilylation of Ketones",
        journal="Organometallics", year="2025", volume="44", issue="12", pages="1288–1295",
        doi="10.1021/acs.organomet.5c00107"),
    "zhang2025": dict(
        authors="Zhang, X.; Li, J.; Rotella, M. E.; Zhang, R.; Kozlowski, M. C.; Jia, T.",
        title="Dearomative Mislow–Braverman–Evans Rearrangement of Aryl Sulfoxides",
        journal="JACS Au", year="2025", volume="5", issue="2", pages="998–1006",
        doi="10.1021/jacsau.4c01238"),
    "debie2026": dict(
        authors="Debie, L. M.; Gotta, T.; Turberg, M.; Nöthling, N.; List, B.",
        title="The Catalytic Asymmetric Alkylation of Olefins with Alcohol Derivatives",
        journal="J. Am. Chem. Soc.", year="2026", volume="148", issue="33",
        pages="35270–35277", doi="10.1021/jacs.6c12914"),
    # General references (General Information).
    "still1978": dict(
        authors="Still, W. C.; Kahn, M.; Mitra, A.",
        title="Rapid chromatographic technique for preparative separations with moderate "
              "resolution",
        journal="J. Org. Chem.", year="1978", volume="43", issue="14", pages="2923–2925",
        doi="10.1021/jo00408a041"),
    "fulmer2010": dict(
        authors="Fulmer, G. R.; Miller, A. J. M.; Sherden, N. H.; Gottlieb, H. E.; "
                "Nudelman, A.; Stoltz, B. M.; Bercaw, J. E.; Goldberg, K. I.",
        title="NMR Chemical Shifts of Trace Impurities: Common Laboratory Solvents, "
              "Organics, and Gases in Deuterated Solvents Relevant to the Organometallic "
              "Chemist",
        journal="Organometallics", year="2010", volume="29", issue="9", pages="2176–2179",
        doi="10.1021/om100106e"),
}

# ---------------------------------------------------------------------------------------
# The compounds
# ---------------------------------------------------------------------------------------
# h1/c13/f19: (frequency MHz, peaks), copied exactly from the source. A ¹H peak is
# (shift or (from, to), multiplicity, [J…], nH) with an optional fifth item: what to
# SIMULATE instead, for signals the source only calls "m" (a para-substituted ring's
# AA'XX' half looks like a doublet). A ¹³C peak is (shift, kind, count[, J(C,F)]): kind
# "C" (quaternary: a weaker line) or "CH"; count = equivalent carbons (for the line height).
# Each shift is kept as the text the source prints ("123.94"), so its decimals stay.
#
# REWRITTEN: a ¹H signal written as the simulated spectrum shows it, not as the source
# prints it (see the docstring); the source's own text is in the comment next to it.
#
# ketone_id: the starting ketone's number (1a–1f, as in the general scheme).
# yield: the invented % yield (the mass is worked out from it, the scale and the MW).
# ppm: the invented error of the HRMS found value; None = the found value comes from the
# compound's (invented) HRMS report file instead.

COMPOUNDS = [
    dict(id="2a", smiles="CC(O)c1ccccc1", name="1-Phenylethan-1-ol",
         ketone="acetophenone", ketone_id="1a", ketone_smiles="CC(=O)c1ccccc1",
         h1=(500, [(("7.39", "7.34"), "m", [], 4), (("7.30", "7.27"), "m", [], 1),
                   ("4.90", "q", ["6.5"], 1), ("1.96", "s", [], 1),
                   ("1.50", "d", ["6.5"], 3)]),
         c13=(126, [("146.0", "C", 1), ("128.6", "CH", 2), ("127.6", "CH", 1),
                    ("125.5", "CH", 2), ("70.5", "CH", 1), ("25.3", "CH", 1)]),
         state="colorless oil", rf=("0.23", "n-hexane/EtOAc 4:1"),
         eluent="n-hexane/EtOAc 4:1", yield_=94, ppm=0.9,
         refs=["santiago2026", "satoh2025"]),
    dict(id="2b", smiles="CC(O)c1ccc(OC)cc1", name="1-(4-Methoxyphenyl)ethan-1-ol",
         ketone="4′-methoxyacetophenone", ketone_id="1b",
         ketone_smiles="CC(=O)c1ccc(OC)cc1",
         # (The aryl ranges are the source's: the integration range around each half of
         # the AA'XX' system, which looks like a doublet, as MestReNova reports it.)
         h1=(500, [(("7.34", "7.28"), "m", [], 2, ("7.31", "d", ["8.7"])),
                   (("6.92", "6.86"), "m", [], 2, ("6.89", "d", ["8.7"])),
                   ("4.87", "qd", ["6.4", "3.4"], 1), ("3.81", "s", [], 3),
                   ("1.69", "d", ["3.5"], 1), ("1.49", "d", ["6.4"], 3)]),
         c13=(126, [("159.2", "C", 1), ("138.1", "C", 1), ("126.8", "CH", 2),
                    ("114.0", "CH", 2), ("70.2", "CH", 1), ("55.5", "CH", 1),
                    ("25.2", "CH", 1)]),
         state="colorless oil", rf=("0.10", "n-hexane/EtOAc 4:1"),
         eluent="n-hexane/EtOAc 3:1", yield_=91, ppm=-0.6,
         refs=["wong2025", "santiago2026", "satoh2025"]),
    dict(id="2c", smiles="CC(O)c1ccc(F)cc1", name="1-(4-Fluorophenyl)ethan-1-ol",
         ketone="4′-fluoroacetophenone", ketone_id="1c",
         ketone_smiles="CC(=O)c1ccc(F)cc1",
         # (The aryl signals simulated as reported resolved elsewhere: 7.31 (dd, J = 8.6,
         # 5.4 Hz) and 7.00 (t, J = 8.7 Hz), Latha & Swamy, J. Org. Chem. 2024, 89, 8376.)
         h1=(400, [(("7.39", "7.30"), "m", [], 2, ("7.345", "dd", ["8.6", "5.4"])),
                   (("7.08", "6.98"), "m", [], 2, ("7.03", "t", ["8.7"])),
                   ("4.90", "q", ["6.4"], 1), ("1.74", "s", [], 1),
                   ("1.48", "d", ["6.4"], 3)]),
         c13=(126, [("162.3", "C", 1, "244.9"), ("141.7", "C", 1, "3.2"),
                    ("127.2", "CH", 2, "8.1"), ("115.4", "CH", 2, "21.4"),
                    ("70.0", "CH", 1), ("25.5", "CH", 1)]),
         f19=(376, [("−115.5", "s", [], 1)]),
         state="colorless oil", rf=("0.13", "n-hexane/EtOAc 4:1"),
         eluent="n-hexane/EtOAc 4:1", yield_=96, ppm=1.2,
         refs=["wong2025", "santiago2026", "satoh2025"]),
    dict(id="2d", smiles="CC(O)c1ccc(Cl)cc1", name="1-(4-Chlorophenyl)ethan-1-ol",
         ketone="4′-chloroacetophenone", ketone_id="1d",
         ketone_smiles="CC(=O)c1ccc(Cl)cc1",
         # REWRITTEN: the source prints "7.31 (m, 4H)" (a multiplet as one value) and
         # "1.48 (app. d, J = 6.5 Hz, 3H)" (a plain doublet: CH3 next to one CH).
         h1=(400, [(("7.35", "7.27"), "m", [], 4),
                   (("4.94", "4.84"), "m", [], 1, ("4.89", "qd", ["6.5", "3.6"])),
                   ("1.79", "d", ["3.6"], 1), ("1.48", "d", ["6.5"], 3)]),
         c13=(126, [("144.4", "C", 1), ("133.2", "C", 1), ("128.8", "CH", 2),
                    ("126.9", "CH", 2), ("69.9", "CH", 1), ("25.4", "CH", 1)]),
         state="pale yellow oil", rf=("0.16", "n-hexane/EtOAc 4:1"),
         eluent="n-hexane/EtOAc 4:1", yield_=93, ppm=None,
         refs=["wong2025", "satoh2025"]),
    dict(id="2e", smiles="CC(O)c1ccc(Br)cc1", name="1-(4-Bromophenyl)ethan-1-ol",
         ketone="4′-bromoacetophenone", ketone_id="1e",
         ketone_smiles="CC(=O)c1ccc(Br)cc1",
         # REWRITTEN: the source prints "7.47 (m, 2H), 7.25 (m, 2H)" (multiplets as one
         # value each); each half of the AA'XX' system is simulated as a doublet.
         h1=(500, [("7.47", "d", ["8.5"], 2), ("7.25", "d", ["8.5"], 2),
                   ("4.87", "qd", ["6.4", "3.6"], 1), ("1.77", "d", ["3.7"], 1),
                   ("1.47", "d", ["6.5"], 3)]),
         c13=(126, [("144.9", "C", 1), ("131.7", "CH", 2), ("127.3", "CH", 2),
                    ("121.3", "C", 1), ("70.0", "CH", 1), ("25.4", "CH", 1)]),
         # (The NMR source calls it a white solid but gives no melting point; the other
         # two sources call it a colorless oil, which is used here.)
         state="colorless oil", rf=("0.17", "n-hexane/EtOAc 4:1"),
         eluent="n-hexane/EtOAc 4:1", yield_=95, ppm=-1.1,
         refs=["wong2025", "santiago2026", "satoh2025"]),
    dict(id="2f", smiles="CC(O)c1ccc2ccccc2c1", name="1-(Naphthalen-2-yl)ethan-1-ol",
         ketone="2′-acetonaphthone", ketone_id="1f",
         ketone_smiles="CC(=O)c1ccc2ccccc2c1",
         h1=(600, [(("7.86", "7.80"), "m", [], 4), (("7.53", "7.44"), "m", [], 3),
                   ("5.07", "q", ["6.4"], 1), ("1.98", "br s", [], 1),
                   ("1.59", "d", ["6.4"], 3)]),
         c13=(150, [("143.3", "C", 1), ("133.4", "C", 1), ("133.1", "C", 1),
                    ("128.4", "CH", 1), ("128.1", "CH", 1), ("127.8", "CH", 1),
                    ("126.3", "CH", 1), ("125.9", "CH", 1), ("123.94", "CH", 1),
                    ("123.93", "CH", 1), ("70.6", "CH", 1), ("25.3", "CH", 1)]),
         # (The source gives no melting point, and none from a peer-reviewed source was
         # found. It writes the Rf eluent as "EtOAc/hexane 1:3": the same mixture.)
         state="colorless solid", rf=("0.50", "n-hexane/EtOAc 3:1"),
         eluent="n-hexane/EtOAc 3:1", yield_=89, ppm=0.5,
         refs=["zhang2025"]),
]

ACETATE = dict(
    id="3", smiles="CC(OC(C)=O)c1ccc(Br)cc1", name="1-(4-Bromophenyl)ethyl acetate",
    # (The source prints "501 MHz": the nominal 500 MHz is given, as for the others.)
    h1=(500, [(("7.49", "7.45"), "m", [], 2, ("7.47", "d", ["8.5"])),
              (("7.24", "7.20"), "m", [], 2, ("7.22", "d", ["8.5"])),
              ("5.82", "q", ["6.6"], 1), ("2.07", "s", [], 3), ("1.51", "d", ["6.6"], 3)]),
    c13=(126, [("170.5", "C", 1), ("141.1", "C", 1), ("132.0", "CH", 2), ("128.2", "CH", 2),
               ("122.1", "C", 1), ("72.0", "CH", 1), ("22.5", "CH", 1), ("21.6", "CH", 1)]),
    state="colorless oil", eluent="n-hexane/EtOAc 10:1", scale="0.40", yield_=88, ppm=None,
    refs=["debie2026"])

# The invented optimization (4′-bromoacetophenone → 2e): reductant, equiv, solvent,
# temperature, time, yield. Entry 1 is General Procedure A (its yield is 2e's).
OPTIMIZATION = [
    ("NaBH_{4}", "1.5", "MeOH", "0 °C", "30 min", "95"),
    ("NaBH_{4}", "1.0", "MeOH", "0 °C", "30 min", "81"),
    ("NaBH_{4}", "1.5", "EtOH", "0 °C", "30 min", "90"),
    ("NaBH_{4}", "1.5", "THF", "0 °C", "30 min", "23"),
    ("NaBH_{4}", "1.5", "MeOH", "rt", "30 min", "91"),
    ("LiBH_{4}", "1.5", "THF", "0 °C", "1 h", "88"),
    ("NaBH(OAc)_{3}", "1.5", "CH_{2}Cl_{2}", "rt", "24 h", "<5"),
]

GP_NAME = "General Procedure A"
GP_TITLE = "Reduction of aryl methyl ketones with NaBH4"   # (plain text: the
# program writes the 4 as a subscript)
GP_TEXT = ("To a solution of the aryl methyl ketone (0.50 mmol, 1.0 equiv) in MeOH (2.5 mL) "
           "at 0 °C was added NaBH_{4} (28.4 mg, 0.75 mmol, 1.5 equiv) in one portion. The "
           "mixture was stirred at 0 °C for 30 min (complete conversion by TLC), then "
           "quenched with saturated aqueous NH_{4}Cl (5 mL) and extracted with EtOAc "
           "(3 × 10 mL). The combined organic layers were washed with brine (10 mL), dried "
           "over Na_{2}SO_{4}, filtered and concentrated under reduced pressure.")
GP_SENTENCE = ("Prepared according to {GP} from {ketone} ({ketone ID}, {mass}, {scale}). "
               "The crude product was purified by {purification}.")
# The general scheme of the reduction (Scheme S1), with the aryl group as a generic atom
# (* in SMILES; the program draws it as "R", so the scheme's name says what R is).
GENERAL_SCHEME = "Reduction of aryl methyl ketones 1a–1f (R = aryl)"
KETONE_GENERIC = "*C(C)=O"
ALCOHOL_GENERIC = "*C(C)O"

# Ions used for the HRMS of every compound.
ADDUCT = "[M+Na]+"


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------

@contextlib.contextmanager
def fixed_ids(seed: int = SEED):
    """The program's internal IDs (core/ids.new_id) from a fixed seed, so that re-running
    this script gives the same project files."""
    from si_assistant.core import ids
    rng = random.Random(seed)

    class _Seeded:
        @staticmethod
        def token_hex(n: int) -> str:
            return "".join(f"{rng.randrange(256):02x}" for _ in range(n))

    old = ids.secrets
    ids.secrets = _Seeded()
    try:
        yield
    finally:
        ids.secrets = old


def info_of(smiles: str):
    from si_assistant.core import chem
    return chem.mol_info(chem.parse_structure(smiles))


def _shift_text(shift) -> str:
    if isinstance(shift, tuple):
        return f"{shift[0]} – {shift[1]}"
    return shift


def h1_report(freq: int, peaks: list) -> str:
    parts = []
    for p in peaks:
        shift, mult, js, n = p[:4]
        j = f", J = {', '.join(js)} Hz" if js else ""
        parts.append(f"{_shift_text(shift)} ({mult}{j}, {n}H)")
    return f"1H NMR ({freq} MHz, CDCl3) δ " + ", ".join(parts) + "."


def c13_report(freq: int, peaks: list) -> str:
    parts = []
    for p in peaks:
        parts.append(f"{p[0]} (d, J = {p[3]} Hz)" if len(p) > 3 else p[0])
    return f"13C NMR ({freq} MHz, CDCl3) δ " + ", ".join(parts) + "."


def f19_report(freq: int, peaks: list) -> str:
    parts = [p[0] for p in peaks]
    return f"19F{{1H}} NMR ({freq} MHz, CDCl3) δ " + ", ".join(parts) + "."


def _num(text: str) -> float:
    return float(str(text).replace("−", "-"))


def h1_spec(freq: int, peaks: list) -> list[dict]:
    """The peaks in tools/nmr_spectra.py's form (the simulated signal where one is given)."""
    out = []
    for p in peaks:
        shift, mult, js, n = p[:4]
        if len(p) > 4:
            shift, mult, js = p[4]
        s = [_num(shift[0]), _num(shift[1])] if isinstance(shift, tuple) else _num(shift)
        out.append({"shift": s, "mult": mult, "J": [float(j) for j in js], "nH": n})
    return out


def c13_spec(peaks: list) -> list[dict]:
    out = []
    for p in peaks:
        d = {"shift": _num(p[0]), "type": p[1], "count": p[2]}
        if len(p) > 3:
            d.update(mult="d", J=[float(p[3])])
        out.append(d)
    return out


def found_mz(smiles: str, ppm: float, tolerance: float = 5.0) -> str:
    """An (invented) found m/z: the program's calculated [M+Na]+ moved by `ppm`, to 4
    decimals (and still between 0.3 and 1.5 ppm away once rounded)."""
    from si_assistant.core import checks
    calcd = checks.hrms_calc(info_of(smiles), ADDUCT, "", tolerance).monoisotopic.mz
    found = round(calcd * (1 + ppm * 1e-6), 4)
    error = (found - round(calcd, 4)) / calcd * 1e6
    assert 0.3 <= abs(error) <= 1.5, (smiles, ppm, error)
    return f"{found:.4f}"


def hrms_report_csv(smiles: str, ppm: float, sample: str) -> str:
    """An invented centroid peak list (CSV) of the [M+Na]+ ion: its isotope peaks moved by
    `ppm`, plus a few unrelated ions, as an instrument's export might list them."""
    from si_assistant.core import checks
    peaks = checks.hrms_calc(info_of(smiles), ADDUCT, "", 5.0).peaks
    rows = [(p.mz * (1 + ppm * 1e-6), p.abundance * 125000.0) for p in peaks]
    mono = peaks[0].mz
    rows += [(mono - 21.98194 + 0.0004, 18300.0),        # [M+H]+
             (mono + 15.97394 - 0.0002, 9100.0),         # [M+K]+
             (413.26623, 6400.0)]                        # a common background ion
    rows.sort()
    lines = [f"# Simulated HRMS (ESI+) peak list of sample {sample} for the Chem SI Assistant "
             "example project (invented, not a measurement)",
             "Peak,m/z,Intensity"]
    for n, (mz, intensity) in enumerate(rows, 1):
        lines.append(f"{n},{mz:.5f},{intensity:.0f}")
    return "\n".join(lines) + "\n"


def generic_molblock(smiles: str) -> str:
    """A MOL block of a generic structure (with * atoms, which have no formula) for a
    scheme box: the program accepts it there, as a structure drawn in a scheme."""
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    mol = Chem.MolFromSmiles(smiles)
    rdDepictor.Compute2DCoords(mol)
    return Chem.MolToMolBlock(mol)


def yield_mass(smiles: str, percent: int, scale: str) -> str:
    return f"{percent / 100 * float(scale) * info_of(smiles).mw:.1f}"


def ketone_mass(smiles: str, scale: str = SCALE) -> str:
    return f"{float(scale) * info_of(smiles).mw:.1f}"


# ---------------------------------------------------------------------------------------
# Spectra
# ---------------------------------------------------------------------------------------

def make_spectra(compound: dict, folder: Path, seed: int) -> list[tuple[Path, str]]:
    """Simulated ¹H, ¹³C (and ¹⁹F) spectra of one compound as vector PDFs:
    [(file, spectrum type)]."""
    import nmr_spectra as nmr
    out = []
    freq, peaks = compound["h1"]
    spec = {"nucleus": "1H", "mhz": freq, "solvent": "CDCl3", "seed": seed,
            "peaks": h1_spec(freq, peaks)}
    out.append((nmr.render_spec(spec, folder / f"{compound['id']}_1H.pdf"), "1H"))
    freq, peaks = compound["c13"]
    spec = {"nucleus": "13C", "mhz": freq, "solvent": "CDCl3", "seed": seed + 1,
            "peaks": c13_spec(peaks)}
    out.append((nmr.render_spec(spec, folder / f"{compound['id']}_13C.pdf"), "13C"))
    if compound.get("f19"):
        freq, peaks = compound["f19"]
        spec = {"nucleus": "19F", "mhz": freq, "solvent": "CDCl3", "seed": seed + 2,
                "xrange": [-60.0, -170.0],
                "peaks": [{"shift": _num(p[0]), "mult": p[1], "nF": p[3]} for p in peaks]}
        out.append((nmr.render_spec(spec, folder / f"{compound['id']}_19F.pdf"), "19F"))
    return out


# ---------------------------------------------------------------------------------------
# The project
# ---------------------------------------------------------------------------------------

def add_references(p) -> dict[str, str]:
    """Every reference, entered directly (no network); {key: reference id}."""
    from si_assistant.core import references as refs
    out = {}
    for key, s in SOURCES.items():
        ref = refs.blank("article")
        ref.update({k: v for k, v in s.items() if k != "authors"})
        ref["authors"] = [a.strip() for a in s["authors"].split(";")]
        out[key] = p.add_reference(ref)["id"]
    return out


def fill_compound(p, c, data: dict, refs: dict, spectra_dir: Path, seed: int,
                  reports_dir: Path) -> None:
    """Everything but the procedure: structure, scale, yield, state, Rf, purification,
    NMR, HRMS, spectra and the literature references."""
    p.set_structure(c, data["smiles"])
    scale = data.get("scale", SCALE)
    c.values["scale"] = {"value": scale, "unit": "mmol"}
    c.values["yield"] = {"percent": str(data["yield_"]),
                         "mass": yield_mass(data["smiles"], data["yield_"], scale),
                         "mass_unit": "mg"}
    c.values["physical_state"] = {"text": data["state"]}
    if data.get("rf"):
        c.values["rf"] = {"value": data["rf"][0], "eluent": data["rf"][1], "visualization": ""}
    else:
        p.remove_field(c, "rf")
    c.values["purification"] = {"method": "flash column chromatography", "phase": "SiO₂",
                                "eluent": data["eluent"], "sentence": ""}
    texts = {"1H": h1_report(*data["h1"]), "13C": c13_report(*data["c13"])}
    if data.get("f19"):
        texts["19F"] = f19_report(*data["f19"])
    for report in c.values["nmr"]:
        report["text"] = texts.pop(report["nucleus"], report.get("text", ""))
    assert not texts, f"no report for {texts}"
    c.values["hrms"] = {"ionization": "ESI", "adduct": ADDUCT, "found": "",
                        "species": "", "formula": ""}
    if data["ppm"] is not None:
        c.values["hrms"]["found"] = found_mz(data["smiles"], data["ppm"])
    c.values["references"] = {"text": "Spectral data matched those reported in the "
                                      "literature.", "refs": [refs[k] for k in data["refs"]]}
    p.save_compound(c)
    if data["ppm"] is None:
        # The found m/z is read from an (invented) HRMS report file instead.
        csv = reports_dir / f"{data['id']}_HRMS_ESI.csv"
        # (Bytes, so the line endings are "\n" on every system and builds match.)
        csv.write_bytes(hrms_report_csv(data["smiles"], 0.8 if data["id"] == "3" else -0.7,
                                        data["id"]).encode("utf-8"))
        p.add_hrms_report(c, csv)
    for path, kind in make_spectra(data, spectra_dir, seed):
        p.attach_compound_file(c, "spectra", path, {"type": kind})
    if data.get("f19"):
        # The automatic header says "19F NMR"; this spectrum is ¹H-decoupled, as its
        # report says, so its header is typed (Spectra field ▸ Header in the SI).
        item = next(i for i in c.values["spectra"] if i["type"] == "19F")
        item["title"] = (f"{data['id']} — 19F{{1H}} NMR ({data['f19'][0]} MHz, CDCl3)")
        p.save_compound(c)


def build_project(parent: Path, work: Path):
    """Make the project in `parent`/PROJECT_NAME (spectra and reports are first written
    to `work`). Returns the open Project."""
    from si_assistant.core import scheme_editor as se
    from si_assistant.core.project import GENERAL_OWNER, Project
    from si_assistant.core.tables import Cell, Table

    p = Project.create(parent, PROJECT_NAME)
    p.settings.update({
        "title": TITLE,
        "authors": [{"name": n, "affiliations": ["a"], "corresponding": corr, "email": e}
                    for n, corr, e in AUTHORS],
        "affiliations": [{"marker": "a", "text": AFFILIATION}],
        "journal_style": "ACS",
        # The table of contents lists every compound (Front matter ▸ Table of contents).
        "toc": {"depth": "compounds"},
    })
    p.save_settings()
    p.write_front_text("cover", COVER_TEXT)
    refs = add_references(p)

    # The custom section with the optimization table (made first: General Information
    # mentions its table).
    opt = p.add_section("text", OPT_SECTION)
    data_section = p.add_section("data", DATA_SECTION)
    ids = [c["id"] for c in COMPOUNDS] + [ACETATE["id"]]
    p.set_id_table(data_section.id, ids)
    p.set_default_fields(data_section.id, [
        {"key": "structure", "required": True}, {"key": "name", "required": False},
        {"key": "procedure", "required": True}, {"key": "scale", "required": True},
        {"key": "yield", "required": True}, {"key": "physical_state", "required": True},
        {"key": "purification", "required": False}, {"key": "rf", "required": False},
        {"key": "nmr", "required": True}, {"key": "hrms", "required": True},
        {"key": "spectra", "required": True}, {"key": "references", "required": False}])

    gp = p.add_general_procedure(data_section.id, GP_NAME)
    p.update_general_procedure(gp["id"], title=GP_TITLE, sentence=GP_SENTENCE)
    p.write_gp_text(gp["id"], GP_TEXT)

    spectra_dir = work / "spectra"
    reports_dir = work / "hrms"
    spectra_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    made = {}
    for n, data in enumerate(COMPOUNDS):
        c = p.add_compound(data_section.id, name=data["name"])
        c.values["procedure"] = {"gp": gp["id"], "values": {
            "ketone": data["ketone"], "ketone ID": f"**{data['ketone_id']}**",
            "mass": f"{ketone_mass(data['ketone_smiles'])} mg"}}
        fill_compound(p, c, data, refs, spectra_dir, 100 + 10 * n, reports_dir)
        made[data["id"]] = c

    # The general scheme of the reduction (Scheme S1), before the compounds: generic
    # structures (Ar), with typed labels and the range of yields.
    general_scheme = p.add_scheme(data_section.id, index=0, name=GENERAL_SCHEME)
    start = se.starting(general_scheme)[0]
    se.set_box_structure(p, general_scheme, start["id"], generic_molblock(KETONE_GENERIC))
    se.set_box_label(p, general_scheme, start["id"], "1a–f")
    step = se.steps(general_scheme)[0]
    product = step["products"][0]
    se.set_box_structure(p, general_scheme, product["id"], generic_molblock(ALCOHOL_GENERIC))
    se.set_box_label(p, general_scheme, product["id"], "2a–f")
    yields = [d["yield_"] for d in COMPOUNDS]
    se.set_box_yield(p, general_scheme, product["id"], f"{min(yields)}–{max(yields)}%")
    se.set_conditions(p, general_scheme, step["id"], "top", "NaBH_{4} (1.5 equiv)")
    se.set_conditions(p, general_scheme, step["id"], "bottom", "MeOH, 0 °C, 30 min")
    se.set_scheme_layout(p, general_scheme, {**se.scheme_layout(p.settings, general_scheme),
                                             "show_label": True})

    # The scheme page: 2e → 3 (acetylation), drawn with the scheme editor; 3 is its
    # compound, the starting material's label a mention of 2e.
    e = made["2e"]
    scheme = p.add_scheme(data_section.id, name="Acetylation of 1-(4-bromophenyl)ethan-1-ol")
    acetate = p.add_compound(data_section.id, name=ACETATE["name"], scheme_id=scheme.id)
    p.write_compound_text(
        acetate, "procedure",
        f"To a solution of alcohol **@{{{e.id}}}** (80.4 mg, 0.40 mmol, 1.0 equiv) in "
        "CH_{2}Cl_{2} (2.0 mL) were added Et_{3}N (84 µL, 0.60 mmol, 1.5 equiv), DMAP "
        "(2.4 mg, 0.020 mmol, 5 mol %) and Ac_{2}O (57 µL, 0.60 mmol, 1.5 equiv) at room "
        "temperature. After 2 h (complete conversion by TLC) the mixture was diluted with "
        "CH_{2}Cl_{2} (10 mL), washed with 1 M HCl (5 mL), saturated aqueous NaHCO_{3} "
        "(5 mL) and brine (5 mL), dried over Na_{2}SO_{4}, filtered and concentrated under "
        "reduced pressure.")
    fill_compound(p, acetate, ACETATE, refs, spectra_dir, 200, reports_dir)
    start = se.starting(scheme)[0]
    se.set_box_structure(p, scheme, start["id"], COMPOUNDS[4]["smiles"])
    se.set_box_label(p, scheme, start["id"], f"@{{{e.id}}}")
    step = se.steps(scheme)[0]
    se.link_compound(p, scheme, step["products"][0]["id"], acetate.id)
    se.set_conditions(p, scheme, step["id"], "top", "Ac_{2}O, Et_{3}N, DMAP (5 mol %)")
    se.set_conditions(p, scheme, step["id"], "bottom", "CH_{2}Cl_{2}, rt, 2 h")
    se.set_scheme_layout(p, scheme, {**se.scheme_layout(p.settings, scheme),
                                     "show_label": True})

    # Reaction Optimization: the invented screen, with its table.
    owner = p.text_owner(opt.id)
    table = Table.new(len(OPTIMIZATION) + 1, 6)
    table.rows[0] = [Cell("Entry^{a}"), Cell("Reductant (equiv)"), Cell("Solvent"),
                     Cell("Temperature"), Cell("Time"), Cell(f"**@{{{e.id}}}** (%)^{{b}}")]
    for i, (red, eq, solvent, temp, time, y) in enumerate(OPTIMIZATION, 1):
        entry = f"**{i}**" if i == 1 else str(i)
        table.rows[i] = [Cell(entry), Cell(f"{red} ({eq})"), Cell(solvent), Cell(temp),
                         Cell(time), Cell(f"**{y}**" if i == 1 else y)]
    table.footnotes = ["^{a} Reaction conditions: 4′-bromoacetophenone (0.50 mmol), "
                       "reductant, solvent (2.5 mL).", "^{b} Isolated yield."]
    table_id = p.add_table(owner, table)
    p.write_section_text(
        opt, f"The conditions of the reduction were optimized with 4′-bromoacetophenone, "
             f"which gives alcohol **@{{{e.id}}}** (@{{{table_id}}}). Sodium borohydride "
             "(1.5 equiv) in methanol at 0 °C gave the highest yield (entry 1) and became "
             f"{GP_NAME}. In THF the reduction was slow (entry 4), and sodium "
             "triacetoxyborohydride did not reduce the ketone (entry 7).\n"
             f"!table{{{table_id}}} Optimization of the reduction of "
             "4′-bromoacetophenone")

    # General Information (front matter), with the note on what is real.
    general = (
        f"**About this example.** {NOTE}\n"
        "## General methods\n"
        "Unless otherwise noted, reactions were carried out under air in oven-dried "
        "glassware with magnetic stirring. Reagents and solvents were purchased from "
        "commercial suppliers and used as received. Reactions were monitored by thin-layer "
        "chromatography (TLC) on silica gel 60 F_{254} plates (visualized with UV light, "
        "254 nm, and KMnO_{4} stain). Flash column chromatography was performed on silica "
        f"gel (40–63 µm).@cite{{{refs['still1978']}}} The reduction conditions were "
        f"optimized as shown in @{{{table_id}}}.\n"
        "## NMR spectroscopy\n"
        "^{1}H, ^{13}C{^{1}H} and ^{19}F{^{1}H} NMR spectra were recorded at 298 K in "
        "CDCl_{3} on 400, 500 and 600 MHz spectrometers (^{1}H frequencies; ^{13}C at 126 "
        "and 150 MHz, ^{19}F at 376 MHz). Chemical shifts (δ) are given in ppm, referenced "
        "to the residual solvent signals (CHCl_{3}: δ_{H} 7.26; CDCl_{3}: δ_{C} 77.16)"
        f"@cite{{{refs['fulmer2010']}}} and, for ^{{19}}F, to external CFCl_{{3}} "
        "(δ_{F} 0.00). Multiplicities are given as s (singlet), d (doublet), "
        "q (quartet), qd (quartet of doublets), m (multiplet) and br (broad); coupling "
        "constants (*J*) are given in Hz.\n"
        "## Mass spectrometry\n"
        "High-resolution mass spectra (HRMS) were recorded with electrospray ionization "
        "(ESI) in positive-ion mode; the [M+Na]^{+} ions are reported.\n"
        "## Abbreviations\n"
        "Ac_{2}O, acetic anhydride; DMAP, 4-(dimethylamino)pyridine; rt, room temperature.")
    p.write_owner_text(p.text_owner(GENERAL_OWNER), general)

    # The Spectra section: landscape pages (the spectra are landscape) and the NMR reports
    # linked to their spectra. No caption under the structure: the header above each
    # spectrum already gives the nucleus, frequency and solvent.
    p.ensure_spectra_section()
    p.set_spectra_defaults({**p.spectra_defaults(), "orientation": "landscape",
                            "caption": False, "nmr_links": True, "link_back": True})

    made[ACETATE['id']] = acetate
    dismiss_expected(p, made)
    return p


# Warnings the literature data rightly raise, dismissed with a reason as a user would
# (Warnings panel ▸ Dismiss): (compound ID, check) -> reason. Any other warning stops the
# build.
DISMISSED = {
    ("2c", "frequency"): "As in the cited source: the ¹H and ¹³C spectra were recorded on "
                         "different spectrometers (400 and 500 MHz).",
    ("2d", "frequency"): "As in the cited source: the ¹H and ¹³C spectra were recorded on "
                         "different spectrometers (400 and 500 MHz).",
    ("2f", "impurity"): "δ 128.4 is a naphthalene CH of the compound (as in the cited "
                        "source), not benzene.",
}


def dismiss_expected(p, made: dict) -> None:
    """Dismiss the DISMISSED warnings (with a fixed date, so re-running gives the same
    files)."""
    from si_assistant.core import checks
    report = p.id_report()
    for (cid, check), reason in DISMISSED.items():
        c = made[cid]
        found = [w for w in checks.all_warnings(p, c, report) if w.check == check]
        assert found, f"{cid}: no {check} warning to dismiss"
        for w in found:
            p.dismiss_warning(c, w, reason)
            c.data["dismissed"][w.key].update(by="Example", at="2026-10-09T12:00:00")
        p.save_compound(c)


# ---------------------------------------------------------------------------------------
# Checking and compiling
# ---------------------------------------------------------------------------------------

def all_warnings(p) -> list[str]:
    """Every active check warning of every compound, and the project's own warnings."""
    from si_assistant.core import checks
    from si_assistant.core.labels import compute
    labels = compute(p)
    report = p.id_report()
    out = []
    for c in p.all_compounds():
        for w in checks.active_warnings(p, c, report, labels):
            out.append(f"{c.name}: {w.message}")
    out += [w.message for w in checks.project_warnings(p, labels)]
    return out


def compile_si(p, style: str = "ACS"):
    """Compile the SI (in the project's build/ folder); returns the CompileResult."""
    from si_assistant.compile.document import CompileOptions
    from si_assistant.compile.qt_images import content_box, convert_to_png, image_info
    from si_assistant.compile.run import compile_pdf
    old = p.settings.get("journal_style")
    p.settings["journal_style"] = style
    try:
        options = CompileOptions.from_settings(p.settings)
        return compile_pdf(p, options, image_info, convert_to_png, crop=content_box)
    finally:
        p.settings["journal_style"] = old


def save_pngs(pdf: Path, out: Path, pages: list[int] | None = None, dpi: float = 80) -> None:
    from pypdf import PdfReader
    from si_assistant.compile.qt_images import render_page
    out.mkdir(parents=True, exist_ok=True)
    count = len(PdfReader(str(pdf)).pages)
    for n in pages or range(1, count + 1):
        if n <= count:
            render_page(pdf, n, dpi=dpi).save(str(out / f"page{n:02d}.png"))


def clean_project_folder(folder: Path) -> None:
    """Leave only the project's own files: no build/ folder (with the undo history),
    snapshots, Trash, lock file or half-written temporary files (resources: what a copy
    of the example leaves out anyway)."""
    from si_assistant import resources
    for pattern in resources.NOT_COPIED:
        for path in list(folder.rglob(pattern)):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()


def remove_tree(folder: Path, tries: int = 10) -> None:
    """shutil.rmtree, also of read-only files and folders (Windows refuses to remove those;
    a cloud-sync program can mark a folder read-only), tried again for a few seconds: a
    sync program or virus scanner may hold a file for a moment ("Access is denied")."""
    import os
    import stat
    import time

    def make_writable_and_retry(func, path, _exc):
        os.chmod(path, stat.S_IMODE(os.stat(path).st_mode) | stat.S_IWRITE)
        func(path)

    handler = ({"onexc": make_writable_and_retry} if sys.version_info >= (3, 12)
               else {"onerror": make_writable_and_retry})
    for n in range(tries):
        try:
            shutil.rmtree(folder, **handler)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if n == tries - 1:
                raise
            time.sleep(0.5)


def build(out_dir: Path = EXAMPLE_DIR, compile_pdf: bool = True,
          png_dir: Path | None = None, quiet: bool = False) -> Path:
    """Write out_dir/PROJECT_NAME (replacing it) and, with `compile_pdf`, out_dir/PDF_NAME.
    Returns the project folder. Raises RuntimeError if a check or the compile warns.

    Everything is made in a temporary folder first and only moved into out_dir once it
    has no warnings: a build that stops leaves the example that was there as it was."""
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / PROJECT_NAME
    say = (lambda *a: None) if quiet else print
    # (A short path: Windows' 260-character limit, the project's folders are deep.)
    with tempfile.TemporaryDirectory(prefix="csa-ex-") as tmp, fixed_ids():
        tmp = Path(tmp)
        made_dir, work = tmp / "out", tmp / "work"
        made_dir.mkdir()
        p = build_project(made_dir, work)
        try:
            problems = all_warnings(p)
            if problems:
                raise RuntimeError("The example project has warnings:\n" + "\n".join(problems))
            say(f"Built {len(p.all_compounds())} compounds, no warnings.")
            pdf = None
            if compile_pdf:
                result = compile_si(p)
                if result.warnings:
                    raise RuntimeError("Compiling gave warnings:\n"
                                       + "\n".join(result.warnings))
                pdf = made_dir / PDF_NAME
                shutil.copyfile(result.pdf, pdf)
        finally:
            p.close()
        made = made_dir / PROJECT_NAME
        clean_project_folder(made)
        # All went well: put it in place of the old one.
        if target.exists():
            remove_tree(target)
        shutil.move(str(made), str(target))
        if pdf is not None:
            shutil.copyfile(pdf, out_dir / PDF_NAME)
            say(f"Compiled {out_dir / PDF_NAME} "
                f"({(out_dir / PDF_NAME).stat().st_size / 1000:.0f} KB).")
            if png_dir is not None:
                save_pngs(out_dir / PDF_NAME, Path(png_dir))
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    say(f"Project folder {target}: {size / 1000:.0f} KB.")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the example project shipped with "
                                                 "Chem SI Assistant.")
    parser.add_argument("--out", type=Path, default=EXAMPLE_DIR,
                        help="where to write it (default: si_assistant/example)")
    parser.add_argument("--no-pdf", action="store_true", help="don't compile the SI")
    parser.add_argument("--png", type=Path, default=None, help="save PNGs of the SI's pages here")
    args = parser.parse_args(argv)
    build(args.out, compile_pdf=not args.no_pdf, png_dir=args.png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
