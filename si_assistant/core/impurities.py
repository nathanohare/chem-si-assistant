"""Chemical shifts of common solvent and impurity peaks, for check 4 (SPEC §8.2).

Source: G. R. Fulmer et al., "NMR Chemical Shifts of Trace Impurities: Common Laboratory
Solvents, Organics, and Gases in Deuterated Solvents Relevant to the Organometallic
Chemist", Organometallics 2010, 29, 2176–2179.

To keep false alarms down, only *singlet* ¹H peaks are listed (a reported singlet at
exactly one of these shifts is suspicious), and only ¹³C peaks that are distinctive
(e.g. not EtOAc's 60.5/14.2, which ethyl esters also show). Warnings say "may be".
"""

from __future__ import annotations

# solvent -> [(shift, what)]: ¹H singlets
PROTON_SINGLETS: dict[str, list[tuple[float, str]]] = {
    "CDCl3": [(7.26, "residual CHCl₃"), (1.56, "water"), (2.17, "acetone"),
              (2.10, "acetonitrile or acetic acid"), (7.36, "benzene"), (5.30, "CH₂Cl₂"),
              (2.62, "DMSO"), (3.71, "1,4-dioxane"), (2.05, "ethyl acetate"),
              (3.49, "methanol"), (2.36, "toluene (CH₃)"), (8.02, "DMF"), (2.96, "DMF"),
              (2.88, "DMF"), (0.07, "silicone grease"), (1.28, "tert-butanol"),
              (4.33, "nitromethane")],
    "DMSO-d6": [(2.50, "residual DMSO"), (3.33, "water"), (2.09, "acetone"),
                (2.07, "acetonitrile"), (7.37, "benzene"), (5.76, "CH₂Cl₂"),
                (8.32, "chloroform"), (1.99, "ethyl acetate"), (3.16, "methanol"),
                (2.30, "toluene (CH₃)"), (7.95, "DMF"), (2.89, "DMF"), (2.73, "DMF"),
                (3.57, "1,4-dioxane")],
    "CD3OD": [(3.31, "residual CHD₂OD"), (4.87, "water"), (2.15, "acetone"),
              (2.03, "acetonitrile"), (5.49, "CH₂Cl₂"), (7.88, "chloroform"),
              (2.01, "ethyl acetate"), (3.34, "methanol"), (2.32, "toluene (CH₃)")],
    "acetone-d6": [(2.05, "residual acetone-d₅"), (2.84, "water"), (5.63, "CH₂Cl₂"),
                   (8.02, "chloroform"), (1.97, "ethyl acetate"), (3.31, "methanol"),
                   (2.32, "toluene (CH₃)"), (2.52, "DMSO")],
    "C6D6": [(7.16, "residual C₆HD₅"), (0.40, "water"), (1.55, "acetone"),
             (4.27, "CH₂Cl₂"), (6.15, "chloroform"), (1.65, "ethyl acetate"),
             (3.07, "methanol"), (2.11, "toluene (CH₃)")],
    "CD2Cl2": [(5.32, "residual CHDCl₂"), (1.52, "water"), (2.12, "acetone"),
               (7.32, "chloroform"), (2.00, "ethyl acetate"), (3.42, "methanol")],
    "CD3CN": [(1.94, "residual CHD₂CN"), (2.13, "water"), (2.08, "acetone"),
              (5.44, "CH₂Cl₂"), (7.58, "chloroform"), (1.97, "ethyl acetate"),
              (3.28, "methanol")],
    "D2O": [(4.79, "residual HDO"), (2.22, "acetone"), (2.06, "acetonitrile"),
            (3.34, "methanol"), (2.71, "DMSO")],
}

# solvent -> [(shift, what)]: distinctive ¹³C peaks
CARBON_PEAKS: dict[str, list[tuple[float, str]]] = {
    "CDCl3": [(77.16, "CDCl₃ (solvent)"), (207.07, "acetone"), (30.92, "acetone"),
              (53.52, "CH₂Cl₂"), (40.76, "DMSO"), (50.41, "methanol"), (116.43, "acetonitrile"),
              (1.89, "acetonitrile"), (128.37, "benzene"), (67.14, "1,4-dioxane")],
    "DMSO-d6": [(39.52, "DMSO-d₆ (solvent)"), (206.31, "acetone"), (54.84, "CH₂Cl₂"),
                (79.16, "chloroform"), (48.59, "methanol"), (117.91, "acetonitrile")],
    "CD3OD": [(49.00, "CD₃OD (solvent)"), (209.67, "acetone"), (54.78, "CH₂Cl₂"),
              (79.44, "chloroform")],
    "acetone-d6": [(29.84, "acetone-d₆ (solvent)"), (206.26, "acetone-d₆ (solvent)"),
                   (54.95, "CH₂Cl₂"), (79.19, "chloroform"), (49.77, "methanol")],
    "C6D6": [(128.06, "C₆D₆ (solvent)"), (204.43, "acetone"), (53.46, "CH₂Cl₂"),
             (77.79, "chloroform"), (49.97, "methanol")],
    "CD2Cl2": [(53.84, "CD₂Cl₂ (solvent)"), (206.78, "acetone"), (77.99, "chloroform"),
               (50.45, "methanol")],
    "CD3CN": [(1.32, "CD₃CN (solvent)"), (118.26, "CD₃CN (solvent)"), (207.43, "acetone"),
              (54.24, "CH₂Cl₂"), (79.17, "chloroform")],
}

PROTON_TOLERANCE = 0.010   # ppm (SPEC §13l.9: 0.010 / 0.050 since 2026-10)
CARBON_TOLERANCE = 0.050
# The defaults before that. A project that still has exactly these stored (the settings
# window used to save them even when unchanged) gets the new defaults; any other value
# was chosen by someone and is kept.
OLD_PROTON_TOLERANCE = 0.005
OLD_CARBON_TOLERANCE = 0.03
# Where a project's own tolerances are kept in project.json (only when they differ from
# the defaults). The older keys are read once, when the project is opened (see upgrade).
TOLERANCE_KEYS = ("impurity_tolerance_1H_ppm", "impurity_tolerance_13C_ppm")
OLD_TOLERANCE_KEYS = ("impurity_tolerance_1H", "impurity_tolerance_13C")
DEFAULT_TOLERANCES = (PROTON_TOLERANCE, CARBON_TOLERANCE)
_OLD_DEFAULTS = (OLD_PROTON_TOLERANCE, OLD_CARBON_TOLERANCE)


def default_table() -> list[dict]:
    """The built-in table as rows: {"solvent", "nucleus", "shift", "what", "on"}."""
    rows = []
    for nucleus, table in (("1H", PROTON_SINGLETS), ("13C", CARBON_PEAKS)):
        for solvent, peaks in table.items():
            rows.extend({"solvent": solvent, "nucleus": nucleus, "shift": shift, "what": what,
                         "on": True} for shift, what in peaks)
    return rows


def project_table(settings: dict) -> list[dict]:
    """The project's own table if it has been edited (Project settings → NMR impurities),
    otherwise the built-in one."""
    table = settings.get("impurity_table")
    return table if isinstance(table, list) else default_table()


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def tolerances(settings: dict) -> tuple[float, float]:
    """(¹H, ¹³C) match tolerances in ppm: the project's own, else the defaults. A value
    still under an older key that equals the old default counts as "not changed"."""
    out = []
    for key, old_key, default, old_default in zip(TOLERANCE_KEYS, OLD_TOLERANCE_KEYS,
                                                  DEFAULT_TOLERANCES, _OLD_DEFAULTS):
        value = _number(settings.get(key))
        if value is None:
            value = _number(settings.get(old_key))
            if value is not None and _same(value, old_default):
                value = None
        out.append(default if value is None else value)
    return out[0], out[1]


def set_tolerances(settings: dict, proton: float, carbon: float) -> None:
    """Store the project's tolerances: a value equal to the default is not stored, so the
    project follows the default (also if it changes again)."""
    for key, old_key, value, default in zip(TOLERANCE_KEYS, OLD_TOLERANCE_KEYS,
                                            (proton, carbon), DEFAULT_TOLERANCES):
        settings.pop(old_key, None)
        if _same(float(value), default):
            settings.pop(key, None)
        else:
            settings[key] = float(value)


def upgrade_settings(settings: dict) -> None:
    """Tolerances saved by older versions (under the older keys): the old defaults are
    dropped (the project then uses the new defaults), any other value is kept under the
    new key. Called when a project is opened."""
    if not any(k in settings for k in OLD_TOLERANCE_KEYS):
        return
    proton, carbon = tolerances(settings)
    set_tolerances(settings, proton, carbon)


def normalise_solvent(text: str) -> str:
    """"cdcl3", "Chloroform-d", "MeOD" → "CDCl3", "CD3OD", so typed solvents match the
    parsed ones."""
    from .solvents import canonical
    return canonical(text)


def matches(solvent: str, nucleus: str, shift: float, singlet: bool,
            table: list[dict] | None = None, proton_tol: float = PROTON_TOLERANCE,
            carbon_tol: float = CARBON_TOLERANCE) -> str:
    """What an observed peak may be, or "" if it doesn't match a known impurity.
    ¹H peaks only match if reported as singlets."""
    if nucleus == "1H":
        if not singlet:
            return ""
        tol = proton_tol
    elif nucleus == "13C":
        tol = carbon_tol
    else:
        return ""
    rows = default_table() if table is None else table
    hits = []
    for row in rows:
        if not row.get("on", True) or row.get("nucleus") != nucleus:
            continue
        if normalise_solvent(str(row.get("solvent", ""))) != solvent:
            continue
        try:
            value = float(row.get("shift"))
        except (TypeError, ValueError):
            continue
        if abs(value - shift) <= tol + 1e-9:
            hits.append(str(row.get("what", "")) or "a listed impurity")
    return " / ".join(dict.fromkeys(hits))
