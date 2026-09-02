"""Resolve a per-system module temperature coefficient (gamma_pdc) from PVDAQ metadata.

WHY THIS EXISTS. Every PVDAQ soiling label this repo has recorded was produced with
`GAMMA_PDC` hardcoded fleet-wide. Two scripts disagreed about the value
(`pvdaq_daily_srr_probe.py` used -0.0045, `washable_share_probe.py` -0.0035) and the
gap is not cosmetic: measured on system 10109 it moves the annual soiling label by
**1.045 pts**, against a method-noise SD of 0.72 and a within-cell between-system
spread of 1.41. A single unvalidated constant had more leverage on a label than the
physical signal Phase 3 exists to detect. See `docs/HANDOFF_20260827.md`.

The fix is to stop guessing. `pvdaq/csv/system_metadata/<id>_system_metadata.json`
carries module manufacturer, model and cell technology for the residential fleet, and
pvlib ships the CEC module database (21,535 modules, each with a measured `gamma_r` in
%/degC). So the coefficient can be looked up per system.

RESOLUTION LADDER, most specific first. Every result records which tier produced it,
so the fallback rate is reportable rather than hidden:

  1. `cec_module`        part number matched into the CEC database, disambiguated by
                         per-module nameplate watts where the system's capacity and
                         module count allow it
  2. `cec_manufacturer`  only a brand is recorded ("LG", "Panasonic"); median gamma_r
                         over that manufacturer's CEC entries
  3. `cec_technology`    PVDAQ's `type` field (multi-Si, mono-Si, n-PERT, ...) mapped
                         to a CEC `Technology` class; median gamma_r over the class
  4. `fleet_default`     nothing usable recorded

The tier-4 default is **-0.0045**, and that number is not inherited from the old
hardcode by coincidence: the CEC database median gamma_r is -0.450 %/degC for
Mono-c-Si and -0.4515 for Multi-c-Si. The prior fleet-wide constant in
`pvdaq_daily_srr_probe.py` was in fact the right central value; `washable_share_probe.py`'s
-0.0035 is the outlier, a modern high-efficiency figure (SunPower/Panasonic HIT class)
applied to a fleet that is mostly 2010-era x-Si. This module supersedes both.

Usage:
    from src.risk.module_gamma import resolve_system_gamma
    res = resolve_system_gamma(10109, capacity_kw=5.88)
    res.gamma_pdc   # -0.0043
    res.tier        # 'cec_module'
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import requests

S3 = "https://oedi-data-lake.s3.amazonaws.com"
CACHE_DIR = Path(".cache/soiling/pvdaq_sysmeta")

# CEC median for both x-Si classes, to 3 s.f. Labelled, not assumed.
FLEET_DEFAULT_GAMMA = -0.0045

# PVDAQ's free-text `type` field -> CEC `Technology` class. PVDAQ records cell
# architecture (n-PERT, IBC, TOPCon, HJT); CEC records only the crystal class, so
# every crystalline architecture collapses to Mono- or Multi-c-Si. The
# architectures listed here as Mono are all monocrystalline by construction.
TECH_MAP = {
    "mono-si": "Mono-c-Si", "mono-si ibc": "Mono-c-Si", "n-pert": "Mono-c-Si",
    "n-pert ibc": "Mono-c-Si", "n-shj": "Mono-c-Si", "n-topcon": "Mono-c-Si",
    "perc": "Mono-c-Si", "perc ibc": "Mono-c-Si", "perc bifacial": "Mono-c-Si",
    "sjt": "Mono-c-Si",
    "multi-si": "Multi-c-Si", "ribbon polycrystalline si": "Multi-c-Si",
    "amorphous si": "Thin Film", "cis family thin-film": "CIGS",
    "cylindrical cigs": "CIGS", "cdte": "CdTe",
}

# Brand token -> a regex that selects that maker's CEC keys. PVDAQ brand strings are
# uploader-typed and inconsistent ("Solar World", "SolarWorld", "Solarworld"), so the
# key is matched on the normalised (alphanumeric-only) form.
BRAND_PATTERNS = {
    "LG": r"^LGELECTRONICS",
    "PANASONIC": r"^SANYOELECTRIC|^PANASONIC",
    "SANYO": r"^SANYOELECTRIC",
    "SOLARWORLD": r"^SOLARWORLD",
    "TRINA": r"^TRINASOLAR",
    "CANADIANSOLAR": r"^CANADIANSOLAR",
    "SUNPOWER": r"^SUNPOWER",
    "KYOCERA": r"^KYOCERA",
    "SUNIVA": r"^SUNIVA",
    "HYUNDAI": r"^HYUNDAI",
    "REC": r"^RECSOLAR|^REC",
    "SHARP": r"^SHARP",
    "SUNTECH": r"^SUNTECH",
    "YINGLI": r"^YINGLI",
    "JINKO": r"^JINKO",
    "SILIKEN": r"^SILIKEN",
    "SCHOTT": r"^SCHOTT",
    "SANTAN": r"^SANTAN",
    "MITSUBISHI": r"^MITSUBISHI",
    "EVERGREEN": r"^EVERGREEN",
    "SOLARCITY": r"^SOLARCITY",
    "SUNEDISON": r"^SUNEDISON|^MEMC",
    "QCELLS": r"^HANWHA|^QCELLS",
    "HANWHA": r"^HANWHA",
    "SILFAB": r"^SILFAB",
    "SEG": r"^SEG",
    "AXITEC": r"^AXITEC",
    "SOLARIA": r"^SOLARIA",
    "ITEK": r"^ITEK",
    "HELIENE": r"^HELIENE",
    "MISSION": r"^MISSIONSOLAR",
    "SERAPHIM": r"^SERAPHIM",
    "PHONO": r"^PHONOSOLAR",
    "ET": r"^ETSOLAR",
    "ASTRONERGY": r"^ASTRONERGY|^CHINTSOLAR",
}

# Brand strings that are inverter or optimiser makers, not module makers. PVDAQ
# uploaders put these in the module field; matching them to a module would be wrong.
NON_MODULE_BRANDS = {"SOLAREDGE", "ENPHASE", "SMA", "FRONIUS", "ABB", "AURORA",
                     "POWERONE", "OUTBACK", "SOLECTRIA", "PVPOWERED"}

_NULLISH = {"", "unknown", "none", "n/a", "na", "null", "-", "?"}


def _norm(s: str) -> str:
    """Alphanumeric-only uppercase. The only string form compared anywhere here."""
    return re.sub(r"[^A-Za-z0-9]", "", str(s or "")).upper()


@dataclass
class GammaResolution:
    system_id: int | None
    gamma_pdc: float
    tier: str
    matched: str
    n_candidates: int
    technology: str
    raw_manufacturer: str
    raw_model: str

    def as_dict(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=1)
def _cec() -> pd.DataFrame:
    """CEC module database, transposed to one row per module, gamma in fraction/degC."""
    import pvlib

    db = pvlib.pvsystem.retrieve_sam("CECMod").T
    out = pd.DataFrame({
        "technology": db["Technology"].astype(str),
        "gamma_pdc": pd.to_numeric(db["gamma_r"], errors="coerce") / 100.0,
        "stc_w": pd.to_numeric(db["STC"], errors="coerce"),
    })
    out["key_norm"] = [_norm(k) for k in db.index]
    # Drop the handful of entries with no coefficient; they cannot inform anything.
    return out[out.gamma_pdc.notna()]


def fetch_system_metadata(system_id: int, cache_dir: Path | None = None) -> dict:
    """The system's metadata JSON, cached on disk. `{}` when PVDAQ has none."""
    cache_dir = Path(cache_dir or CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{system_id}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except ValueError:
            pass
    if (cache_dir / f"{system_id}.missing").exists():
        return {}
    r = requests.get(f"{S3}/pvdaq/csv/system_metadata/{system_id}_system_metadata.json",
                     timeout=60)
    if not r.ok:
        (cache_dir / f"{system_id}.missing").write_text(str(r.status_code))
        return {}
    f.write_text(r.text)
    try:
        return r.json()
    except ValueError:
        return {}


def primary_module(meta: dict) -> dict:
    """The module record carrying the most panels. Systems with a mixed array are
    rare here (3 of 1,627); taking the largest block is the right single answer for a
    system-level coefficient, and the minority block's gamma is within 0.0005 anyway."""
    mods = (meta or {}).get("Modules") or {}
    best, best_qty = {}, -1.0
    for v in mods.values():
        try:
            q = float(v.get("quantity") or 0)
        except (TypeError, ValueError):
            q = 0.0
        if q > best_qty:
            best, best_qty = v, q
    return best


def _part_tokens(*strings: str) -> list[str]:
    """Candidate part numbers: tokens holding a digit, long enough to be specific.

    'Trina TSM-245PA05' -> ['TSM245PA05']. A bare brand ('LG') yields nothing, which
    is exactly the signal that tier 1 cannot apply.
    """
    toks: list[str] = []
    for s in strings:
        # Split on whitespace and separators that are not part of a part number.
        for raw in re.split(r"[\s/,;|]+", str(s or "")):
            n = _norm(raw)
            if len(n) >= 4 and any(c.isdigit() for c in n) and any(c.isalpha() for c in n):
                toks.append(n)
        # Also try the whole string joined, for 'LG335N1C-A5' style already-joined names.
        n = _norm(s)
        if len(n) >= 6 and any(c.isdigit() for c in n):
            toks.append(n)
    # Longest first: the most specific token that matches wins.
    return sorted(dict.fromkeys(toks), key=len, reverse=True)


def _brand_of(*strings: str) -> str | None:
    joined = " ".join(_norm(s) for s in strings)
    if any(b in joined for b in NON_MODULE_BRANDS):
        # A brand token can still be present alongside; only bail if nothing else hits.
        pass
    for brand in sorted(BRAND_PATTERNS, key=len, reverse=True):
        if brand in joined:
            return brand
    return None


def resolve_gamma(manufacturer: str, model: str, technology: str = "",
                  module_watts: float | None = None,
                  system_id: int | None = None) -> GammaResolution:
    """Resolve one module description to a temperature coefficient. See module docstring."""
    cec = _cec()
    man, mod = str(manufacturer or ""), str(model or "")
    tech_raw = str(technology or "").strip()

    def out(g, tier, matched, n):
        return GammaResolution(system_id, float(g), tier, matched, int(n),
                               tech_raw, man, mod)

    usable = not (_norm(man).lower() in _NULLISH and _norm(mod).lower() in _NULLISH)

    # ── tier 1: part number into the CEC database ────────────────────────────
    if usable:
        for tok in _part_tokens(man, mod):
            hit = cec[cec.key_norm.str.contains(re.escape(tok), regex=True)]
            if hit.empty:
                continue
            # Disambiguate by nameplate watts when the system tells us the per-module
            # rating. Without this a token like 'CS6P' matches 40 wattage variants;
            # their gammas differ little, but the wattage check makes the match real
            # rather than lucky.
            if module_watts and len(hit) > 1:
                near = hit[(hit.stc_w - module_watts).abs() <= max(8.0, 0.06 * module_watts)]
                if not near.empty:
                    hit = near
            return out(hit.gamma_pdc.median(), "cec_module", tok, len(hit))

    # ── tier 2: manufacturer median ──────────────────────────────────────────
    if usable:
        brand = _brand_of(man, mod)
        if brand:
            hit = cec[cec.key_norm.str.match(BRAND_PATTERNS[brand])]
            if not hit.empty:
                return out(hit.gamma_pdc.median(), "cec_manufacturer", brand, len(hit))

    # ── tier 3: cell technology median ───────────────────────────────────────
    cls = TECH_MAP.get(tech_raw.lower())
    if cls:
        hit = cec[cec.technology == cls]
        if not hit.empty:
            return out(hit.gamma_pdc.median(), "cec_technology", cls, len(hit))

    # ── tier 4: labelled fleet default ───────────────────────────────────────
    return out(FLEET_DEFAULT_GAMMA, "fleet_default", "cec_xsi_median", 0)


def resolve_system_gamma(system_id: int, capacity_kw: float | None = None,
                         cache_dir: Path | None = None) -> GammaResolution:
    """Resolve `system_id`'s coefficient from its cached PVDAQ metadata."""
    meta = fetch_system_metadata(system_id, cache_dir)
    m = primary_module(meta)
    watts = None
    try:
        q = float(m.get("quantity") or 0)
        if capacity_kw and q > 0:
            watts = capacity_kw * 1000.0 / q
    except (TypeError, ValueError):
        watts = None
    res = resolve_gamma(m.get("manufacturer", ""), m.get("model", ""),
                        m.get("type", ""), module_watts=watts, system_id=system_id)
    return res
