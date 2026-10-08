"""Looking up a compound's name in PubChem (SPEC §13n.5).

Only when the user clicks "Look up name (PubChem)": the structure's InChIKey (worked out by
RDKit; the structure itself is not sent) goes to PubChem's PUG REST service, an NIH
database, which answers with the compound's IUPAC name and its common name ("Title"):

    https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/<KEY>/property/IUPACName,Title/JSON

* The full InChIKey first (stereochemistry included).
* If PubChem has no such compound, the connectivity part of the key (its first 14
  characters: the same atoms and bonds, any stereochemistry) is looked up. That is only
  offered when PubChem has exactly one compound for it, and clearly labelled "without
  stereochemistry".
* A salt or mixture (several pieces) is looked up as a whole; if it isn't found, the
  answer says so (its pieces are not looked up one by one).

The fetching is a function argument, so tests never use the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .. import __version__

BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/"
PROPERTIES = "IUPACName,Title"
TIMEOUT_S = 10
PUBCHEM_PAGE = "https://pubchem.ncbi.nlm.nih.gov/compound/"
NOT_FOUND_TEXT = ("This exact structure is not in PubChem — paste the name from ChemDraw "
                  "(Structure ▸ Convert Structure to Name).")


class NotFound(Exception):
    """PubChem has no compound for the key (HTTP 404)."""


class LookupFailed(Exception):
    """No answer from PubChem (offline, timeout, server error); the message is for the user."""


Fetcher = Callable[[str, float], dict]


def user_agent() -> str:
    return f"Chem-SI-Assistant/{__version__}"


def fetch_json(url: str, timeout: float = TIMEOUT_S) -> dict:
    """GET `url` and read its JSON. Raises NotFound for "no such compound" and
    LookupFailed (with a plain message) for anything else that goes wrong."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent(),
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound() from e
        if e.code in (503, 504):
            raise LookupFailed("PubChem is busy at the moment. Please try again in a "
                               "minute.") from e
        raise LookupFailed(f"PubChem answered with an error (HTTP {e.code}). Please try "
                           "again later.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LookupFailed("PubChem could not be reached. Check the internet connection "
                           "and try again.") from e
    except ValueError as e:
        raise LookupFailed("PubChem's answer could not be read. Please try again "
                           "later.") from e


@dataclass
class NameResult:
    """The outcome of a lookup.

    status: "found" (the exact structure), "no_stereo" (only the connectivity matched, one
    compound), "not_found", or "error" (offline, …). `message` says it in plain words."""
    status: str
    name: str = ""            # the IUPAC name (or the Title when there is none)
    title: str = ""           # PubChem's common name, e.g. "Aspirin"
    cid: int = 0
    inchikey: str = ""
    message: str = ""
    pieces: int = 1           # the structure's pieces (a salt has several)

    @property
    def url(self) -> str:
        return f"{PUBCHEM_PAGE}{self.cid}" if self.cid else ""


def inchikey(mol) -> str:
    """The standard InChIKey of the whole structure ("" if it can't be made)."""
    from rdkit import Chem
    try:
        return Chem.MolToInchiKey(mol) or ""
    except Exception:  # noqa: BLE001 — RDKit without InChI, or a structure it can't do
        return ""


def piece_count(mol) -> int:
    from rdkit import Chem
    return len(Chem.GetMolFrags(mol))


def _properties(answer: dict) -> list[dict]:
    try:
        props = answer["PropertyTable"]["Properties"]
    except (KeyError, TypeError):
        return []
    return [p for p in props if isinstance(p, dict)] if isinstance(props, list) else []


def lookup_name(mol, fetch: Fetcher = fetch_json) -> NameResult:
    """The PubChem name of `mol`: the exact structure (full InChIKey), or else the one
    compound with the same connectivity, "without stereochemistry"."""
    key = inchikey(mol)
    pieces = piece_count(mol)
    if not key:
        return NameResult("error", message="An InChIKey could not be made for this "
                                           "structure, so it can't be looked up.",
                          pieces=pieces)
    url = f"{BASE_URL}{urllib.parse.quote(key)}/property/{PROPERTIES}/JSON"
    try:
        props = _properties(fetch(url, TIMEOUT_S))
        if props:
            return _result("found", props[0], key, pieces)
    except NotFound:
        pass
    except LookupFailed as e:
        return NameResult("error", inchikey=key, message=str(e), pieces=pieces)
    # Not there as drawn: the same atoms and bonds, any stereochemistry.
    first = key.split("-")[0]
    url = f"{BASE_URL}{urllib.parse.quote(first)}/property/{PROPERTIES}/JSON"
    try:
        props = _properties(fetch(url, TIMEOUT_S))
    except NotFound:
        props = []
    except LookupFailed as e:
        return NameResult("error", inchikey=key, message=str(e), pieces=pieces)
    if len(props) == 1:
        return _result("no_stereo", props[0], key, pieces)
    message = NOT_FOUND_TEXT
    if pieces > 1:
        message = (f"This structure has {pieces} pieces (e.g. a salt), and PubChem has no "
                   "compound with exactly these pieces together. " + NOT_FOUND_TEXT)
    return NameResult("not_found", inchikey=key, message=message, pieces=pieces)


def _result(status: str, prop: dict, key: str, pieces: int) -> NameResult:
    iupac = str(prop.get("IUPACName", "") or "").strip()
    title = str(prop.get("Title", "") or "").strip()
    try:
        cid = int(prop.get("CID", 0) or 0)
    except (TypeError, ValueError):
        cid = 0
    name = iupac or title
    if not name:
        return NameResult("not_found", inchikey=key, message=NOT_FOUND_TEXT, pieces=pieces)
    where = f"PubChem CID {cid}" + (f", “{title}”" if title and title != name else "")
    if status == "found":
        message = f"Name from PubChem ({where})."
    else:
        message = (f"PubChem has this structure only without (or with other) "
                   f"stereochemistry ({where}). The name is for the structure without "
                   "stereochemistry: check or add the stereodescriptors (R/S, E/Z).")
    return NameResult(status, name, title, cid, key, message, pieces)
