"""Spelling check for the text boxes (SPEC §13k.1a). No Qt here: ui/text_checks.py draws
the underlines and the right-click menu.

A word is accepted if it is in any of (capitals don't matter):
- the English dictionary of pyspellchecker (MIT; works offline, the word list is part of
  the package);
- the chemistry dictionary data/azman_chemistry_dictionary.txt (Adam M. Azman's Open
  Source Chemistry Dictionary v3.0, CC BY 3.0 US, made by tools/get_chem_dictionary.py;
  SPEC §13l.6). It is read the first time a word is checked, not at start-up;
- the built-in chemistry word list data/chem_words.txt (made by tools/build_chem_words.py
  from example SIs and the program's own vocabulary), the names of named reactions in
  data/name_reactions.txt and the hand-made data/lab_terms.txt (NMR multiplicities such as
  dd and dt, reagents such as HOBt and CuTC, supplier and chromatography brand names);
- the project's dictionary.txt (shared by co-authors), "my dictionary" on this computer,
  or the words ignored in this session;
- systematic names built from name fragments ("dichloromethane" = di·chloro·meth·ane).

Words are skipped (never underlined) when they could be compound names or abbreviations:
words with digits, brackets, locants (N-, O-, n-) or Greek letters; capitalised
abbreviations up to six letters (THF, DMSO), also with a locant in front (mCPBA) or a
plural "s" (NMRs); abbreviations made of element symbols and groups with at least two
capitals (MeCN, EtOAc, nBuLi, iPrOH, NaOtBu: `is_abbreviation`); links (mentions and
citations); web addresses and DOIs; single letters.

Other words with capitals inside are checked (SPEC §13l.6; until then they were all
skipped). Besides the dictionaries, one is accepted when it is joined from known words
(ChemDraw, TopSpin, ReactIR: `Speller.accepts`). Phosphine ligands named "…Phos" are
accepted by a rule (`is_phos_ligand`: XantPhos, Xantphos, JosiPhos, SEGPHOS, CyJohnPhos).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .. import resources
from .chemtext import UNITS
from .fileio import atomic_write_text, read_text

DATA_FOLDER = resources.resource("data")
CHEM_WORDS_FILE = DATA_FOLDER / "chem_words.txt"
CHEM_DICTIONARY_FILE = DATA_FOLDER / "azman_chemistry_dictionary.txt"
NAME_REACTIONS_FILE = DATA_FOLDER / "name_reactions.txt"
LAB_TERMS_FILE = DATA_FOLDER / "lab_terms.txt"   # NMR multiplicities, reagents, brand names
MAX_ABBREVIATION = 6       # all-capital words up to this length are abbreviations
MAX_SUGGESTIONS = 6
SLOW_SUGGESTIONS = 12      # longer words get only one-letter-off suggestions (quick)
# Words with capitals inside (not abbreviations, see is_abbreviation) are spell-checked.
# False: skip them all, as before SPEC §13l.6.
CHECK_INNER_CAPITALS = True

# What abbreviations such as MeCN, EtOAc, nBuLi, PhMgBr, NaOtBu are made of (SPEC §13l.6).
# Capitals matter. (T, tritium, is left out so that "THe" is not an abbreviation.)
ABBREVIATION_ELEMENTS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As
Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu
Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np
Pu Am D
""".split())
ABBREVIATION_GROUPS = frozenset("""
Me Et Pr Bu Pent Hex Ph Bn Tol Xyl Mes Cy Cp Ad Ar Het Vin Py R X Nu
Ac Bz Piv Ts Ms Tf Ns Bs Nf Tr Trt Boc Cbz Fmoc Alloc Troc Teoc PMB PMP DMB MOM MEM SEM
BOM THP TMS TES TBS TBDMS TIPS TBDPS HMDS TMP Bpin
""".split())
# Locants written in front of a group: nBu, iPr, sBu, tBu, cHex, pTol, secBu, tertBu.
ABBREVIATION_LOCANTS = ("tert", "sec", "iso", "neo", "n", "i", "s", "t", "c", "m", "o", "p")
_LONGEST_UNIT = max(len(u) for u in ABBREVIATION_ELEMENTS | ABBREVIATION_GROUPS)


def is_abbreviation(word: str) -> bool:
    """Is `word` made entirely of element symbols and group abbreviations (a group may have
    a locant in front: n, i, s, t, sec, tert…), with at least two capitals? MeCN, EtOAc,
    nBuLi, iPrOH, tBuOK, PhMe, PhMgBr, LiAlH, NaOtBu, BnBr: yes. A word with one capital
    (a mistyped word at the start of a sentence, "Bu") is not covered."""
    if sum(ch.isupper() for ch in word) < 2:
        return False
    return _made_of_units(word)


def _made_of_units(word: str) -> bool:
    """Is `word` (as written: capitals matter) made entirely of element symbols and group
    abbreviations, a group perhaps with a locant in front (tBu, Cy, AdMe)?"""
    if not (word.isascii() and word.isalpha()):
        return False
    n = len(word)
    reach = [True] + [False] * n          # can the word be split up to here?
    for i in range(n):
        if not reach[i]:
            continue
        starts = [i] + [i + len(loc) for loc in ABBREVIATION_LOCANTS
                        if word.startswith(loc, i)]
        for at, after_locant in zip(starts, [False] + [True] * (len(starts) - 1)):
            for size in range(1, min(_LONGEST_UNIT, n - at) + 1):
                unit = word[at:at + size]
                if unit in ABBREVIATION_GROUPS or (not after_locant
                                                   and unit in ABBREVIATION_ELEMENTS):
                    reach[at + size] = True
    return reach[n]


def _capital_inside(word: str) -> bool:
    return any(ch.isupper() for ch in word[1:])


# Phosphine ligands are named "<name>Phos" (also written -phos or -PHOS): XantPhos,
# Xantphos, JosiPhos, SEGPHOS, DuPhos, DavePhos… The names in front (lower case), for
# is_phos_ligand. Single letters (XPhos, SPhos) and element symbols or groups (RuPhos,
# MePhos) are covered by the rule too.
PHOS_LIGAND_NAMES = frozenset("""
xant nixant josi seg du dave brett john rock tang wal mandy tania trixie dal bippy
dpe am chira x s q a c e g v
""".split())


def is_phos_ligand(word: str) -> bool:
    """A phosphine ligand's name: a known name + "Phos" in any capitals (XantPhos,
    Xantphos, JosiPhos, SegPhos, SEGPHOS, DuPhos), perhaps after groups and locants
    (CyJohnPhos, tBuBrettPhos, AdBrettPhos, tBuXPhos), or a single capital letter, element
    symbol or group + Phos (XPhos, RuPhos, MePhos)."""
    w = word.strip("'’")
    if len(w) < 5 or w[-4:].lower() != "phos":
        return False
    head = w[:-4]
    if head.lower() in PHOS_LIGAND_NAMES or _made_of_units(head):
        return True
    for name in PHOS_LIGAND_NAMES:
        if len(head) <= len(name):
            continue
        # (a one-letter name is a capital after the groups: tBuXPhos, not "Shoephos")
        found = head[-1] == name.upper() if len(name) == 1 else head.lower().endswith(name)
        if found and _made_of_units(head[:-len(name)]):   # (Cy, tBu, Ad in front)
            return True
    return False


# Pieces of systematic names. A word made only of these (at least two) is accepted.
NAME_FRAGMENTS = frozenset("""
mono di tri tetra penta hexa hepta octa nona deca bis tris tetrakis
meth eth prop but pent hex hept oct non dec cyclo iso neo sec tert
yl ylene ylidene ene yne ane anyl enyl ynyl oxy oxo hydroxy amino amido imino cyano
fluoro chloro bromo iodo iodin halo thio sulf sulfo sulfon sulfin sulfonyl sulfinyl sulfonate
phosph phospho phosphon phosphin phosphate boron boro borate silyl sila stann
ol one al ic ate ite ide ine amine amide imide imine nitrile oate oic ium ane
benz benzo phen phenyl naphth pyr pyrid pyrrol pyran furan thiophen thiazol oxazol
imidazol indol quinol quinolin morpholin piperidin piperazin pyrimid pyrazin pyrazol
triazol tetrazol carbazol azet oxet azep oxir aziridin
acet form carb carbox carbonyl carbamate carbamoyl acyl acetyl benzyl tosyl mesyl trifl
formyl vinyl allyl aryl alk propargyl homo nor per anhydr hydr hydro dehydro hydride
oxide aza oxa thia ether ester amin amid fluor chlor brom iod nitr ethan methan
anol anal anone enone enol enal diene triene idine idin aceto sil silane bor borane
pinacol epox lithium sodium potassium magnesium aluminum aluminium zinc copper cesium
caesium titanium tin boronic
""".split())

_CHUNK = re.compile(r"[^\s/–—]+")
_URL = re.compile(r"^(?:https?:|www\.|doi[:.]|10\.\d)|://|@", re.IGNORECASE)
_LEAD = "([{\"'“‘«*"
_TRAIL = ")]}\"'”’».,;:!?…*"
_LATIN = re.compile(r"^[A-Za-zÀ-ɏ'’-]+$")


def check_spans(text: str) -> list[tuple[int, int]]:
    """Where the words to spell-check are in `text`: (start, end) of each word or each
    part of a hyphenated word. Everything the check skips is left out."""
    out = []
    for m in _CHUNK.finditer(text):
        chunk, start = m.group(0), m.start()
        if _URL.search(chunk):
            continue
        lo, hi = 0, len(chunk)
        while lo < hi and chunk[lo] in _LEAD:
            lo += 1
        while hi > lo and chunk[hi - 1] in _TRAIL:
            hi -= 1
        word = chunk[lo:hi]
        for ending in ("'s", "’s"):
            if word.endswith(ending):
                word, hi = word[:-2], hi - 2
        if not word or not _LATIN.match(word):
            continue   # digits, brackets, Greek letters, link chips, symbols…
        parts = re.split(r"(-)", word)
        pieces = parts[::2]
        if any(len(p) <= 1 for p in pieces):
            continue   # a locant or prefix such as N-, O-, n-, t- (or a stray hyphen)
        pos = start + lo
        for k, piece in enumerate(parts):
            if k % 2 == 0 and _worth_checking(piece):
                out.append((pos, pos + len(piece)))
            pos += len(piece)
    return out


def _worth_checking(word: str) -> bool:
    core = word.strip("'’")
    if len(core) < 2:
        return False
    if core.isupper():
        return len(core) > MAX_ABBREVIATION        # THF, DMSO, HRMS are abbreviations
    if not _capital_inside(core):
        return True
    if not CHECK_INNER_CAPITALS:
        return False
    rest, head = core[1:], core[:-1]
    if core[0].islower() and rest.isupper() and len(rest) <= MAX_ABBREVIATION:
        return False                               # mCPBA, pTSA: a locant + an abbreviation
    if core[-1] == "s" and head.isupper() and len(head) <= MAX_ABBREVIATION:
        return False                               # NMRs, TLCs
    if any(core.startswith(loc) and core[len(loc):] in ABBREVIATION_GROUPS
           for loc in ABBREVIATION_LOCANTS):
        return False                               # iPr, nBu, tBu, cHex, pTol
    return not is_abbreviation(core)               # MeCN, EtOAc, nBuLi, iPrOH


def is_systematic(word: str) -> bool:
    """Made of name fragments? Two or more (tetrahydrofuran, triethylamine), or a single
    long one (tosyl), optionally with a final "e" (thiophene, morpholine)."""
    word = word.lower()
    if word.endswith("e") and len(word) > 5 and _fragments(word[:-1]) >= 1:
        return True
    n = _fragments(word)
    return (n >= 2 and len(word) >= 6) or (n == 1 and len(word) >= 5)


def _fragments(word: str) -> int:
    """The most name fragments `word` splits into exactly (0 = it does not)."""
    n = len(word)
    best = [0] + [-1] * n   # the most fragments that end exactly at each position
    for end in range(1, n + 1):
        for start in range(max(0, end - 12), end):
            if best[start] >= 0 and word[start:end] in NAME_FRAGMENTS:
                best[end] = max(best[end], best[start] + 1)
    return max(best[n], 0) if n >= 4 else 0


@lru_cache(maxsize=1)
def english_dictionary():
    """pyspellchecker's English dictionary (loaded once, about 0.1 s)."""
    from spellchecker import SpellChecker
    return SpellChecker(language="en", distance=2)


def _word_list(path: Path) -> frozenset[str]:
    """A built-in word list (lower case). "#" lines are comments; a missing file gives no
    words (the check then just knows fewer words)."""
    try:
        text = read_text(path)
    except OSError:
        return frozenset()
    return frozenset(w.strip().lower() for w in text.split("\n")
                     if w.strip() and not w.startswith("#"))


@lru_cache(maxsize=1)
def chem_words() -> frozenset[str]:
    """The built-in chemistry words (lower case)."""
    return _word_list(CHEM_WORDS_FILE)


@lru_cache(maxsize=1)
def name_reactions() -> frozenset[str]:
    """Names of people in named reactions (lower case): Suzuki, Buchwald, Heck…"""
    return _word_list(NAME_REACTIONS_FILE)


@lru_cache(maxsize=1)
def lab_terms() -> frozenset[str]:
    """Hand-made: NMR multiplicities (dd, dt, td…), reagent abbreviations with capitals
    inside (HOBt, CuTC) and supplier/chromatography brand names (lower case)."""
    return _word_list(LAB_TERMS_FILE)


@lru_cache(maxsize=1)
def chem_dictionary() -> frozenset[str]:
    """Azman's chemistry dictionary (lower case; about 105,000 words, read once: about
    0.05 s and 12 MB)."""
    return _word_list(CHEM_DICTIONARY_FILE)


def _read_lines(path: Path) -> list[str]:
    """The lines of a dictionary file. It should be UTF-8, but one saved by another
    program (Notepad's "ANSI", Excel's "Text") is read as Windows-1252 instead of failing."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _is_word_line(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def read_words(path: Path) -> set[str]:
    """A dictionary file: one word per line (blank lines and "#" comments are ignored).
    A missing or unreadable file gives no words; it never stops the program."""
    return {line.strip() for line in _read_lines(path) if _is_word_line(line)}


def add_word(path: Path, word: str) -> bool:
    """Add a word to a dictionary file, one per line, in alphabetical order among the
    words (other lines, such as "#" comments, are kept as they are). False if it was
    already there (in any capitalisation)."""
    word = word.strip()
    lines = [line.rstrip() for line in _read_lines(path)]
    while lines and not lines[-1]:
        lines.pop()
    if not word or word.lower() in {line.strip().lower() for line in lines
                                    if _is_word_line(line)}:
        return False
    at = next((i for i, line in enumerate(lines)
               if _is_word_line(line) and line.strip().casefold() > word.casefold()), len(lines))
    lines.insert(at, word)
    atomic_write_text(path, "\n".join(lines) + "\n")
    return True


def match_case(suggestion: str, original: str) -> str:
    if original.isupper() and len(original) > 1:
        return suggestion.upper()
    if original[:1].isupper():
        return suggestion[:1].upper() + suggestion[1:]
    return suggestion


class Speller:
    """Knows which words are spelled right. Results are cached; the cache is cleared when
    a dictionary changes."""

    def __init__(self, chem: Iterable[str] | None = None,
                 dictionary: Iterable[str] | None = None):
        """`chem` replaces the built-in word lists (chem_words.txt, name_reactions.txt and
        lab_terms.txt) and `dictionary` the chemistry dictionary (tests)."""
        self.chem = (frozenset(w.lower() for w in chem) if chem is not None
                     else chem_words() | name_reactions() | lab_terms())
        self._dictionary = frozenset(w.lower() for w in dictionary) \
            if dictionary is not None else None
        self.units = frozenset(u.lower() for u in UNITS)
        self.project: set[str] = set()     # lower case
        self.personal: set[str] = set()
        self.ignored: set[str] = set()
        self._cache: dict[str, bool] = {}
        self._joined: dict[str, bool] = {}
        self._leave: frozenset[str] | None = None

    @property
    def english(self):
        return english_dictionary()

    @property
    def dictionary(self) -> frozenset[str]:
        """The chemistry dictionary, read when first needed (not at start-up)."""
        if self._dictionary is None:
            self._dictionary = chem_dictionary()
        return self._dictionary

    # ---- dictionaries

    def set_project_words(self, words: Iterable[str]) -> None:
        self.project = {w.lower() for w in words}
        self._changed()

    def set_personal_words(self, words: Iterable[str]) -> None:
        self.personal = {w.lower() for w in words}
        self._changed()

    def ignore(self, word: str) -> None:
        self.ignored.add(word.lower())
        self._changed()

    def _changed(self) -> None:
        self._cache.clear()
        self._joined.clear()
        self._leave = None

    def leave(self) -> frozenset[str]:
        """Words (lower case) the formatting check leaves alone: the project's dictionary
        (compiling leaves those alone too) and the words ignored this session. "My
        dictionary" is not used, so that every co-author's copy compiles alike."""
        if self._leave is None:
            self._leave = frozenset(self.project | self.ignored)
        return self._leave

    def _own(self, word: str) -> bool:
        """In one of the short word lists (built-in, project, mine, ignored, units)?"""
        return (word in self.chem or word in self.project or word in self.personal
                or word in self.ignored or word in self.units)

    def _listed(self, word: str) -> bool:
        return self._own(word) or word in self.dictionary

    # ---- checking

    def known(self, word: str) -> bool:
        word = word.strip("'’").replace("’", "'").lower()
        hit = self._cache.get(word)
        if hit is None:
            hit = (self._listed(word) or bool(self.english.known([word]))
                   or any(word.endswith(s) and self._listed(word[:-len(s)])
                          for s in ("s", "es"))
                   or is_systematic(word))
            if len(self._cache) > 50000:
                self._cache.clear()
            self._cache[word] = hit
        return hit

    def accepts(self, word: str) -> bool:
        """Is `word` (as written) spelled right? Known, or, for a word with capitals
        inside, joined from known words: ChemDraw (Chem·Draw), TopSpin, ReactIR (React·IR),
        XPhos (X·Phos), RuPhos (Ru·Phos), tBuXPhos."""
        if self.known(word) or is_phos_ligand(word):
            return True
        if not _capital_inside(word):
            return False
        hit = self._joined.get(word)
        if hit is None:
            hit = self._joined[word] = self._joined_from_known(word)
        return hit

    def _joined_from_known(self, word: str) -> bool:
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", word.strip("'’"))
        if len(parts) < 2 or "".join(parts) != word.strip("'’"):
            return False
        if parts[0] in ABBREVIATION_LOCANTS:        # tBuXPhos
            parts = parts[1:]
        words = 0
        for part in parts:
            if part.isupper() and len(part) <= MAX_ABBREVIATION:
                continue                            # IR, X (an abbreviation)
            if part in ABBREVIATION_ELEMENTS or part in ABBREVIATION_GROUPS:
                continue                            # Ru, Bu, Me
            if len(part) < 3 or not self.known(part):
                return False
            words += 1
        return words >= 1

    def misspelled(self, text: str) -> list[tuple[int, int]]:
        """(start, end) of every misspelled word in `text`."""
        return [(s, e) for s, e in check_spans(text) if not self.accepts(text[s:e])]

    def suggestions(self, word: str, n: int = MAX_SUGGESTIONS) -> list[str]:
        """Up to `n` likely corrections in the word's capitalisation: words of the short
        lists (the project's, mine, the built-in ones) first, then English words, most
        common first, then words of the chemistry dictionary."""
        low = word.lower()
        english = self.english
        near = english.edit_distance_1(low)
        found = set(english.known(near))
        own = {w for w in near if self._own(w)}
        big = {w for w in near if w in self.dictionary}
        if not found and not own and not big and len(low) <= SLOW_SUGGESTIONS:
            far = english.edit_distance_2(low)
            found = set(english.known(far))
            big = {w for w in far if w in self.dictionary}
        ranked = (sorted(own) + sorted(found - own,
                                       key=lambda w: (-english.word_usage_frequency(w), w))
                  + sorted(big - own - found))
        return [match_case(w, word) for w in ranked[:n]]
