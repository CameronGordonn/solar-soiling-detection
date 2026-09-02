"""Cell-level shading model for localized soiling on a residential array.

Why this exists
---------------
`docs/AOI_CLEANING_TARGETING_PLAN.md` v1 asserted, as a derivation rather than a
measurement, that a dead substring costs ~1/60 = 1.7% of a 20-module array and that a
moss strip along the low edge of 4 modules costs ~6.7%. The whole Tier A thesis, and the
"is it worth washing" bar, rests on that arithmetic. It also assumed a 1.5x-3x
amplification for string inverters over MLPE.

This script replaces all of that with a single-diode model at cell granularity, with
bypass diodes and reverse-bias breakdown, so the numbers come out of physics rather than
out of a ratio of integers.

Model
-----
- Cell I-V from `pvlib.singlediode.bishop88`, including reverse-bias avalanche breakdown,
  which is what makes a shaded cell's behaviour physical rather than a clamp.
- Per-cell five parameters derived from CEC module parameters (a_ref, R_s, R_sh scale
  with cells in series; I_L and I_o do not). Half-cells additionally carry half the area,
  so I_L and I_o halve while R_s and R_sh double.
- A module is a list of PARALLEL half-strings, each a list of series substrings, each a
  list of per-cell occluded-area fractions. Full-cell modules have one half-string;
  half-cut modules have two.
- Series aggregation sums V at common I. Parallel aggregation sums I at common V. A
  bypass diode clamps its substring at -V_DIODE.
- Opaque soiling covering fraction f of a cell is modelled as effective irradiance
  (1-f) * Ee, the standard approximation for an opaque occluder on a cell.

Geometry, which is the whole point
----------------------------------
A 60-cell module is 6 x 10 cells and its three substrings are pairs of 10-cell columns
running along the module's LONG axis. So a horizontal band of soiling along the module's
low edge hits:

- PORTRAIT (long axis up the slope): all 3 substrings, 2 cells each.
- LANDSCAPE (long axis across the slope): 1 substring, 10 cells.

A half-cut module splits every cell in two and wires the halves as two PARALLEL
half-strings, one per end of the long axis. That changes the low-edge case again:

- PORTRAIT: the band hits only the lower half-string. The upper one keeps producing, so
  the module floors at roughly half output instead of zero.
- LANDSCAPE: the split runs across the slope, so the band hits the bottom substring of
  BOTH half-strings and the half-cut architecture buys nothing.

Half-cut has been the dominant residential product since roughly 2019, so which case a
roof is in is decided by install year and mounting orientation together.

Usage
-----
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/substring_shade_loss.py
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/substring_shade_loss.py \
        --module Canadian_Solar_Inc__CS6K_300M --n-modules 20 --json out.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

import os
import warnings

import numpy as np
import pvlib
from pvlib.pvsystem import calcparams_desoto
from pvlib.singlediode import bishop88

# Operating point. Mid-afternoon clear-sky plane-of-array, warm cell.
EE_FULL = 800.0  # W/m2 effective irradiance on an unsoiled cell
T_CELL = 45.0  # deg C

V_DIODE = 0.5  # bypass diode forward drop, V
BREAKDOWN_FACTOR = 2e-3  # avalanche fraction, pvlib default-ish for c-Si
BREAKDOWN_VOLTAGE = -5.5
BREAKDOWN_EXP = 3.28

N_CURRENT = 900  # current grid resolution for series aggregation
N_VOLTAGE = 900  # voltage grid resolution for parallel aggregation

# A module is: half-strings -> substrings -> per-cell occluded fraction.
Module = list[list[list[float]]]


@dataclass
class ModuleSpec:
    name: str
    n_cells: int  # full-cell equivalents in series
    n_substrings: int
    cells_per_substring: int
    params: dict
    stc: float
    topology: str = "full"  # "full" or "half-cut"
    grid_short: int = 6  # cells across the short axis
    grid_long: int = 10  # full-cell rows along the long axis

    @property
    def n_half_strings(self) -> int:
        return 2 if self.topology == "half-cut" else 1

    @property
    def cell_area_frac(self) -> float:
        return 0.5 if self.topology == "half-cut" else 1.0


def load_module(name: str | None, topology: str = "full") -> ModuleSpec:
    db = pvlib.pvsystem.retrieve_sam("CECMod")
    t = db.T
    if name is None:
        cands = t[(t["N_s"] == 60) & (t["STC"] > 295) & (t["STC"] < 345)]
        for pref in ("Canadian_Solar", "REC_", "LG_Electronics", "Trina", "Hanwha"):
            hit = [i for i in cands.index if i.startswith(pref)]
            if hit:
                name = hit[0]
                break
        else:
            name = cands.index[0]
    row = t.loc[name]
    n_cells = int(row["N_s"])
    return ModuleSpec(
        name=str(name),
        n_cells=n_cells,
        n_substrings=3,
        cells_per_substring=n_cells // 3,
        params={
            "alpha_sc": float(row["alpha_sc"]),
            "a_ref": float(row["a_ref"]),
            "I_L_ref": float(row["I_L_ref"]),
            "I_o_ref": float(row["I_o_ref"]),
            "R_sh_ref": float(row["R_sh_ref"]),
            "R_s": float(row["R_s"]),
        },
        stc=float(row["STC"]),
        topology=topology,
        grid_short=6,
        grid_long=n_cells // 6,
    )


def cell_vi(spec: ModuleSpec, ee: float, tcell: float = T_CELL):
    """(i, v) samples for ONE cell (or half-cell, per spec.topology).

    Holding the module electrical spec fixed and changing only the internal topology is
    deliberate: it isolates the topology effect from the module-choice effect. A half-cut
    module here has the same Voc, Isc and Pmp as its full-cell twin.
    """
    p = spec.params
    ns = spec.n_cells
    a = spec.cell_area_frac
    ee = max(ee, 1e-6)  # calcparams_desoto is undefined at exactly zero
    IL, I0, Rs, Rsh, nNsVth = calcparams_desoto(
        ee,
        tcell,
        alpha_sc=p["alpha_sc"] * a,
        a_ref=p["a_ref"] / ns,  # per cell; unchanged by halving the cell
        I_L_ref=p["I_L_ref"] * a,  # current scales with area
        I_o_ref=p["I_o_ref"] * a,
        R_sh_ref=p["R_sh_ref"] / ns / a,  # resistance scales inversely with area
        R_s=p["R_s"] / ns / a,
    )
    # Sweep diode voltage into reverse bias so the breakdown branch is captured. Stop
    # just short of BREAKDOWN_VOLTAGE: bishop88's avalanche term raises (1 - vd/Vbr) to a
    # negative fractional power, which is NaN once vd passes Vbr.
    vd = np.linspace(BREAKDOWN_VOLTAGE * 0.995, 0.85, 6000)
    i, v, _ = bishop88(
        vd, IL, I0, Rs, Rsh, nNsVth,
        breakdown_factor=BREAKDOWN_FACTOR,
        breakdown_voltage=BREAKDOWN_VOLTAGE,
        breakdown_exp=BREAKDOWN_EXP,
    )
    return np.asarray(i), np.asarray(v)


#: Set truthy to make silent extrapolation-by-clamping a hard error instead of a warning.
STRICT_RESAMPLE = os.environ.get("SUBSTRING_STRICT_RESAMPLE")


def resample(x_samples, y_samples, x_grid):
    """Linear resample of an IV curve onto `x_grid`.

    `np.interp` CLAMPS outside the sample range rather than extrapolating, and this
    repo has already shipped two silent bugs from exactly that (see trap 9 in
    `docs/HANDOFF_20260827.md`). Here a clamp would flatten the end of a curve into a
    plausible-looking constant with nothing raised. It is not known to fire on any
    current call, so this reports rather than changes behaviour; set
    SUBSTRING_STRICT_RESAMPLE=1 to make it fatal while working on the model.
    """
    xs = np.asarray(x_samples)
    order = np.argsort(xs)
    xs, ys = xs[order], np.asarray(y_samples)[order]
    grid = np.asarray(x_grid)
    if grid.size and (grid.min() < xs.min() - 1e-12 or grid.max() > xs.max() + 1e-12):
        msg = (f"resample would clamp: grid [{grid.min():.4g}, {grid.max():.4g}] "
               f"outside samples [{xs.min():.4g}, {xs.max():.4g}]")
        if STRICT_RESAMPLE:
            raise ValueError(msg)
        warnings.warn(msg, RuntimeWarning, stacklevel=2)
    return np.interp(grid, xs, ys)


class ArrayModel:
    """Caches per-cell V(I) curves by effective irradiance."""

    def __init__(self, spec: ModuleSpec, i_grid: np.ndarray, ee_full: float = EE_FULL,
                 diode_per_substring: bool = False):
        self.spec = spec
        self.i_grid = i_grid
        self.ee_full = ee_full
        self.diode_per_substring = diode_per_substring
        self._cache: dict[float, np.ndarray] = {}
        # Voltage grid for parallel combination, sized off one clean half-string.
        n_series = spec.n_substrings * spec.cells_per_substring
        self.v_grid = np.linspace(-2.0, 0.75 * n_series, N_VOLTAGE)

    def cell_v(self, ee: float) -> np.ndarray:
        key = round(ee, 4)
        if key not in self._cache:
            i, v = cell_vi(self.spec, ee)
            self._cache[key] = resample(i, v, self.i_grid)
        return self._cache[key]

    def substring_v(self, shade_fracs: list[float]) -> np.ndarray:
        """V(I) for one substring, with its bypass diode."""
        assert len(shade_fracs) == self.spec.cells_per_substring
        v = np.zeros_like(self.i_grid)
        for f in shade_fracs:
            v += self.cell_v(self.ee_full * (1.0 - f))
        return np.maximum(v, -V_DIODE)

    def half_string_v(self, substrings: list[list[float]]) -> np.ndarray:
        return sum(self.substring_v(s) for s in substrings)

    def substring_v_raw(self, shade_fracs: list[float]) -> np.ndarray:
        """V(I) for one substring WITHOUT its bypass diode."""
        v = np.zeros_like(self.i_grid)
        for f in shade_fracs:
            v += self.cell_v(self.ee_full * (1.0 - f))
        return v

    def module_v(self, module: Module) -> np.ndarray:
        """V(I) for a whole module.

        Full-cell: three series substrings, one diode each.

        Half-cut: PVsyst's documented twin architecture is "2 sets of 3 strings of half-cells
        connected in parallel, each PAIR of strings sharing the same bypass diode". So the
        diode sits across the parallel pair (A_i || B_i), and the three pairs are in series.
        That is not the same circuit as diode-per-substring-then-parallel-the-half-strings,
        which is what this script did at v3. `--diode-per-substring` reproduces the old
        behaviour so the difference can be measured rather than argued.
        """
        if len(module) == 1:
            return self.half_string_v(module[0])

        if self.diode_per_substring:
            i_total = np.zeros_like(self.v_grid)
            for hs in module:
                v_hs = self.half_string_v(hs)
                i_total += resample(v_hs, self.i_grid, self.v_grid)
            return resample(i_total, self.v_grid, self.i_grid)

        # Shared diode per parallel pair, then pairs in series.
        v_module = np.zeros_like(self.i_grid)
        for s in range(self.spec.n_substrings):
            i_pair = np.zeros_like(self.v_grid)
            for hs in module:
                i_pair += resample(self.substring_v_raw(hs[s]), self.i_grid, self.v_grid)
            v_pair = resample(i_pair, self.v_grid, self.i_grid)
            v_module += np.maximum(v_pair, -V_DIODE)
        return v_module

    def mpp(self, v_curve: np.ndarray) -> float:
        return float(np.max(self.i_grid * v_curve))


def clean_module(spec: ModuleSpec) -> Module:
    """Half-cut splits each series position in two parallel halves, so each half-string
    still has n_substrings substrings of cells_per_substring half-cells."""
    return [
        [[0.0] * spec.cells_per_substring for _ in range(spec.n_substrings)]
        for _ in range(spec.n_half_strings)
    ]


def edge_band_module(spec: ModuleSpec, f: float, orientation: str) -> Module:
    """Soiling band along the module's low edge, covering area fraction f of the cells it
    touches."""
    mod = clean_module(spec)
    half_cut = spec.n_half_strings == 2

    if orientation == "portrait":
        # Long axis up the slope. Band crosses all substrings of the LOWER half-string.
        # With one half-string (full-cell) that is the whole module.
        target = [len(mod) - 1] if half_cut else [0]
        per_sub = spec.grid_short // spec.n_substrings
        for h in target:
            for s in range(spec.n_substrings):
                for c in range(per_sub):
                    mod[h][s][c] = f
    elif orientation == "landscape":
        # Long axis across the slope. The half-cut split is now left/right, so the band
        # hits the bottom substring of EVERY half-string.
        for h in range(len(mod)):
            for c in range(spec.grid_long):
                mod[h][0][c] = f
    else:
        raise ValueError(orientation)
    return mod


def dead_substring_module(spec: ModuleSpec) -> Module:
    """One substring fully out, in one half-string."""
    mod = clean_module(spec)
    mod[0][0] = [1.0] * spec.cells_per_substring
    return mod


def dropping_module(spec: ModuleSpec, f: float) -> Module:
    mod = clean_module(spec)
    mod[0][0][0] = f
    return mod


def array_losses(am: ArrayModel, modules: list[Module], p_clean_module: float):
    """(mlpe_loss, string_loss) as fractions of clean array output."""
    p_clean_array = len(modules) * p_clean_module
    p_mlpe = sum(am.mpp(am.module_v(m)) for m in modules)
    v_string = sum(am.module_v(m) for m in modules)
    p_string = am.mpp(v_string)
    return 1.0 - p_mlpe / p_clean_array, 1.0 - p_string / p_clean_array


def build_model(spec: ModuleSpec, ee: float,
                diode_per_substring: bool = False) -> tuple[ArrayModel, float]:
    i_probe, v_probe = cell_vi(spec, ee)
    # Current grid must span 0 to Isc (current at V=0), NOT max(i) over the sweep: the
    # reverse-bias branch runs to hundreds of amps and would swamp the grid.
    isc = float(resample(v_probe, i_probe, np.array([0.0]))[0])
    n_par = spec.n_half_strings
    i_grid = np.linspace(0.0, isc * n_par * 1.02, N_CURRENT)
    am = ArrayModel(spec, i_grid, ee_full=ee, diode_per_substring=diode_per_substring)
    return am, am.mpp(am.module_v(clean_module(spec)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default=None, help="CEC module name; default picks a 60-cell ~300W")
    ap.add_argument("--n-modules", type=int, default=20)
    ap.add_argument("--json", default=None)
    ap.add_argument("--diode-per-substring", action="store_true",
                    help="v3 behaviour: a diode on every substring, half-strings paralleled "
                         "at the module terminals. PVsyst documents a shared diode per "
                         "parallel pair, which is the default here.")
    ap.add_argument("--compare-diode-topology", action="store_true")
    args = ap.parse_args()

    n = args.n_modules
    out: dict = {"n_modules": n, "ee_full": EE_FULL, "t_cell": T_CELL,
                 "topologies": {}, "sweeps": {}}

    specs = {t: load_module(args.module, topology=t) for t in ("full", "half-cut")}
    print(f"module: {specs['full'].name}  {specs['full'].stc:.0f} W  "
          f"{specs['full'].n_cells} cells  3 substrings x {specs['full'].cells_per_substring}")
    print(f"operating point: Ee={EE_FULL:.0f} W/m2, Tcell={T_CELL:.0f} C, "
          f"{n} modules per array\n")

    models = {}
    for topo, spec in specs.items():
        am, p_clean = build_model(spec, EE_FULL, args.diode_per_substring)
        models[topo] = (spec, am, p_clean)
        print(f"{topo:>9} clean module MPP {p_clean:6.1f} W   "
              f"(array {n * p_clean / 1000:.2f} kW)")
        out["topologies"][topo] = {"clean_module_mpp_w": round(p_clean, 2),
                                   "scenarios": []}
    print()

    def run(topo: str, label: str, builder, k: int = 1):
        spec, am, p_clean = models[topo]
        mods = [builder(spec) if j < k else clean_module(spec) for j in range(n)]
        mlpe, strg = array_losses(am, mods, p_clean)
        amp = strg / mlpe if mlpe > 1e-9 else float("nan")
        print(f"  {topo:>9} {label:<44} MLPE {mlpe*100:6.2f}%  string {strg*100:6.2f}%  "
              f"x{amp:5.2f}")
        out["topologies"][topo]["scenarios"].append({
            "label": label, "n_affected_modules": k,
            "mlpe_loss_pct": round(mlpe * 100, 3),
            "string_loss_pct": round(strg * 100, 3),
            "amplification": None if not np.isfinite(amp) else round(amp, 3),
        })
        return mlpe, strg

    print("=== v1's claimed scenarios, re-measured ===")
    for topo in ("full", "half-cut"):
        run(topo, "1 dead substring (v1 said 1.67%)", dead_substring_module)
        run(topo, "4 modules, 1 dead substring each (v1: 6.7%)", dead_substring_module, k=4)
        run(topo, "8 modules, 1 dead substring each (v1: 13%)", dead_substring_module, k=8)
        run(topo, "1 bird dropping, 60% of one cell",
            lambda s: dropping_module(s, 0.6))

    print("\n=== the same moss line, by orientation AND cell topology ===")
    for topo in ("full", "half-cut"):
        for orient in ("landscape", "portrait"):
            for k in (1, 4, 8):
                run(topo, f"edge band f=0.5, {orient}, {k} module(s)",
                    lambda s, o=orient: edge_band_module(s, 0.5, o), k=k)

    print("\n=== how much of a cell must the band cover before it bites? (4 modules) ===")
    for topo in ("full", "half-cut"):
        spec, am, p_clean = models[topo]
        for orient in ("landscape", "portrait"):
            rows = []
            for f in (0.1, 0.2, 0.3, 0.5, 1.0):
                mods = [edge_band_module(spec, f, orient) if j < 4 else clean_module(spec)
                        for j in range(n)]
                mlpe, strg = array_losses(am, mods, p_clean)
                rows.append({"f": f, "mlpe_loss_pct": round(mlpe * 100, 3),
                             "string_loss_pct": round(strg * 100, 3)})
                print(f"  {topo:>9} {orient:<10} f={f:.1f}   MLPE {mlpe*100:6.2f}%   "
                      f"string {strg*100:6.2f}%")
            out["sweeps"][f"edge_band_4mod_{topo}_{orient}"] = rows

    print("\n=== is the loss fraction stable across the operating range? ===")
    print("(if it is not, none of the above converts to an annual energy number)")
    for topo in ("full", "half-cut"):
        ee_rows = []
        for ee in (200.0, 400.0, 800.0, 1000.0):
            spec = specs[topo]
            am_e, p_clean_e = build_model(spec, ee, args.diode_per_substring)
            row = {"ee": ee, "clean_module_w": round(p_clean_e, 1)}
            for orient in ("landscape", "portrait"):
                mods = [edge_band_module(spec, 0.5, orient) if j < 4 else clean_module(spec)
                        for j in range(n)]
                mlpe, strg = array_losses(am_e, mods, p_clean_e)
                row[f"{orient}_mlpe_pct"] = round(mlpe * 100, 2)
                row[f"{orient}_string_pct"] = round(strg * 100, 2)
            ee_rows.append(row)
            print(f"  {topo:>9} Ee={ee:6.0f}  clean {p_clean_e:6.1f} W/mod   "
                  f"landscape {row['landscape_mlpe_pct']:5.2f}/{row['landscape_string_pct']:6.2f}%   "
                  f"portrait {row['portrait_mlpe_pct']:5.2f}/{row['portrait_string_pct']:6.2f}%")
        out["sweeps"][f"irradiance_{topo}"] = ee_rows

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
