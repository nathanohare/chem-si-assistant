"""Build the built-in chemistry word list for the spelling check (SPEC §13k.1a).

    python tools/build_chem_words.py

Writes si_assistant/data/chem_words.txt (one lower-case word per line, sorted). Run it
again after adding example SIs or new field choices. The list is made from:

1. the example Supporting Information PDFs in "Example SIs/": every word of 3 or more
   letters that the English dictionary does not know and that occurs at least twice
   (solvents, reagents, abbreviations, name fragments, NMR/TLC/HPLC terms). People's
   names are left out (authors and affiliations on the first page, names in reference
   lists; see `PdfWords`), because the list is published with the program;
2. the words the program itself uses (field choices, solvent and impurity names);
3. short hand-written lists of everyday lab and spectroscopy words (EXTRA below) and of
   instrument makers, suppliers and software (NAMES).

Words the spelling check already knows from elsewhere are left out, so nothing is listed
twice: the English dictionary, the chemistry dictionary (azman_chemistry_dictionary.txt,
made by tools/get_chem_dictionary.py, which must be there first), the names of named
reactions (name_reactions.txt, a hand-made list) and lab_terms.txt (hand-made: NMR
multiplicities, reagents such as HOBt, supplier and chromatography brand names).

Only free, offline sources are used: pypdf reads the PDFs, pyspellchecker (MIT) is the
English dictionary.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "Example SIs"
OUT = ROOT / "si_assistant" / "data" / "chem_words.txt"
MIN_COUNT = 2
MIN_LETTERS = 3

# Everyday lab and spectroscopy words that a general English dictionary may lack.
EXTRA = """
aliquot aliquots alkene alkenes alkyne alkynes amide amides amine amines anhydrous
aqueous aryl azeotroped benzylic biphasic brine carbinol chemoselective chiral
chromatograms chromatographed chromatography concentrated conformer conformers cooled
crude cyclization deprotection deprotonated deprotonation desiccator diastereomer
diastereomeric diastereomers diastereoselective diastereoselectivity diastereotopic
distilled doublet doublets dropwise eluent eluents eluted elution enantioenriched
enantiomer enantiomeric enantiomers enantioselective enantioselectivity endo eq equiv
evaporated evaporator exo flash glovebox heteroaryl hexanes isomer isomers lyophilized
methine methylene multiplet multiplets nitrile nitriles oven precipitate quartet quartets
quenched quintet racemic recrystallization recrystallized regioisomer regioisomeric
regioisomers regioselective regioselectivity rotamer rotameric rotamers rotary
rotavap schlenk septet septum sextet silica singlet singlets sonicated stereocenter
stereocenters stereoisomer stereoisomers stirbar substrate substrates sulfonamide
syringe triplet triplets vacuo vial vials workup
aq calcd conc anhyd soln rt mp rf ee dr er
boc cbz fmoc celite florisil pinacol caesium aluminium
epoxide epoxides diene dienes enone enones allene allenes olefin olefins olefination
epoxidation dihydroxylation chemoselectivity precatalyst photoredox photocatalyst
acetonide portionwise triturated trituration cannula cannulated geminal vicinal
azeotropically semipreparative electrospray sonication satd tetramethylsilane
stilbene chalcone lutidine collidine
phos pka pkb dmac
"""
# ("phos" makes ligand names joined from it known: XPhos, SPhos, RuPhos, JohnPhos.)

# Names that appear in General Information paragraphs: instrument makers and models,
# suppliers and software. (Names of named reactions are in data/name_reactions.txt.)
NAMES = """
bruker avance agilent varian jeol thermo orbitrap waters shimadzu teledyne isco combiflash
biotage kessil mestrenova mnova chemdraw sigma aldrich alfa aesar oakwood tci acros fisher
merck combi kugelrohr silicycle siliaflash redisep gmbh kgaa
"""

# Words glued together by the PDF reader ("areaction", "byield": a footnote letter or a
# short word stuck to the next word) or cut at an apostrophe ("couldn") are left out.
GLUE = ("a", "b", "c", "by", "the", "of", "and")
APOSTROPHE = ("couldn didn doesn don hadn hasn haven isn shouldn wasn weren won wouldn "
              "aren").split()


MAX_CAPS = 6   # longer all-capital words in the PDFs are mostly protein/DNA sequences


def tokens(text: str, sequences: bool = True) -> list[str]:
    """Words of letters only (hyphens, digits and punctuation split words). With
    `sequences=False`, long all-capital words (e.g. peptide sequences) are dropped."""
    return [w.lower() for w in re.findall(r"[^\W\d_]+", text)
            if sequences or not (w.isupper() and len(w) > MAX_CAPS)]


# People's names must not end up in the word list (SPEC §13m.1: the list is published).
# They come from the authors and affiliations on the first page and from reference lists.
# A word counts as a name when it is always written with a capital and
#   - it stands where a name stands in a citation: "Zhu, X.;", "Zhu, X.-Y.", "X. Zhu",
#     "X.-Y. Zhu", or on a reference line ("et al.", or a year followed by a volume); or
#   - it occurs only on the first page of the example SIs (the authors/affiliations).
SURNAME_FIRST = re.compile(r"\b([^\W\d_][^\W\d_'’-]+(?:-[^\W\d_]+)?)\s*,\s*(?:[A-Z]\.\s*-?\s*)+")
INITIALS_FIRST = re.compile(r"(?:\b[A-Z]\.\s*-?\s*)+([A-Z][^\W\d_]+(?:-[A-Z][^\W\d_]+)?)")
REFERENCE_LINE = re.compile(r"\bet al\b|\b(?:19|20)\d\d\s*,\s*\d+|^\s*\(?\d{1,3}[).]\s+[A-Z]")


class PdfWords:
    """Word counts of the example SIs, and the words that look like people's names."""

    def __init__(self) -> None:
        self.counts: Counter = Counter()      # lower case
        self.lower_seen: set[str] = set()     # words seen written in lower case
        self.name_like: set[str] = set()      # (lower case) words in a name position
        self.first_page_only: set[str] = set()

    def names(self) -> set[str]:
        """Words to leave out: name-like or first-page-only, and never written in lower
        case (chemistry words such as "pyridine" are written in lower case somewhere)."""
        return (self.name_like | self.first_page_only) - self.lower_seen


def pdf_words() -> PdfWords:
    from pypdf import PdfReader
    out = PdfWords()
    first: set[str] = set()
    later: set[str] = set()
    for pdf in sorted(EXAMPLES.glob("*.pdf")):
        try:
            reader = PdfReader(str(pdf))
            for n, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                words = tokens(text, sequences=False)
                out.counts.update(words)
                (first if n == 0 else later).update(words)
                out.lower_seen.update(w for w in re.findall(r"[^\W\d_]+", text) if w.islower())
                for line in text.splitlines():
                    for rx in (SURNAME_FIRST, INITIALS_FIRST):
                        for m in rx.finditer(line):
                            out.name_like.update(tokens(m.group(1)))
                    if REFERENCE_LINE.search(line):
                        out.name_like.update(w.lower() for w in re.findall(r"[^\W\d_]+", line)
                                             if w[:1].isupper() and not w.isupper())
        except Exception as e:  # noqa: BLE001 — a damaged example is just skipped
            print(f"skipped {pdf.name}: {e}")
    out.first_page_only = first - later
    return out


def program_words() -> set[str]:
    from si_assistant.core import fields, impurities, solvents
    text = []
    for f in fields.ALL_FIELDS.values():
        text.append(f.label)
        for part in f.parts + f.item_parts:
            text += [part.label, part.placeholder, *part.choices, *part.choice_labels.values()]
    text += list(fields.PHYSICAL_STATES) + list(fields.TLC_VISUALIZATION)
    for table in (impurities.PROTON_SINGLETS, impurities.CARBON_PEAKS):
        for solvent, peaks in table.items():
            text.append(solvent)
            text += [what for _, what in peaks]
    # The solvents' usual names, and their other spellings without digits ("meod"; the
    # pieces of "cd3cocd3" are not words). Some spellings are written without their spaces
    # ("deuteriumoxide" matches "deuterium oxide"); those are left out in main().
    for name, spellings in solvents.SOLVENT_NAMES.items():
        text += [name] + [s for s in spellings if not re.search(r"\d", s)]
    return set(tokens(" ".join(text)))


def word_list(path: Path) -> set[str]:
    """A word list of si_assistant/data (lower case, "#" lines are comments)."""
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} is missing (see the notes at the top)")
    return {w.strip().lower() for w in path.read_text(encoding="utf-8").splitlines()
            if w.strip() and not w.startswith("#")}


def main() -> None:
    from spellchecker import SpellChecker
    english = SpellChecker()
    known_elsewhere = (word_list(OUT.parent / "azman_chemistry_dictionary.txt")
                       | word_list(OUT.parent / "name_reactions.txt")
                       | word_list(OUT.parent / "lab_terms.txt"))

    def new(word: str) -> bool:
        return (len(word) >= MIN_LETTERS and word.isascii()
                and not english.known([word]) and word not in known_elsewhere)

    def known(word: str) -> bool:
        return bool(english.known([word])) or word in known_elsewhere

    def glued(word: str) -> bool:
        """Two known words of 4 or more letters written together ("methylenechloride")."""
        return any(known(word[:i]) and known(word[i:]) for i in range(4, len(word) - 3))

    def junk(word: str) -> bool:
        """Two words run together by the PDF reader, or a word cut at an apostrophe."""
        if word in APOSTROPHE:
            return True
        return any(word.startswith(g) and len(word) - len(g) >= 4
                   and english.known([word[len(g):]]) for g in GLUE)

    found = pdf_words()
    names = found.names()
    words = {w for w, n in found.counts.items()
             if n >= MIN_COUNT and new(w) and not junk(w) and w not in names}
    from_pdfs = len(words)
    left_out = sum(1 for w, n in found.counts.items()
                   if n >= MIN_COUNT and new(w) and not junk(w) and w in names)
    print(f"{left_out} words from the example SIs left out as possible people's names")
    words |= {w for w in program_words() if new(w) and not glued(w)}
    words |= {w for w in (EXTRA + NAMES).split() if not known(w)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(words)} words ({from_pdfs} from the example SIs) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
