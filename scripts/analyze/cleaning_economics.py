#!/usr/bin/env python3
"""Cleaning-economics: net-$ returns of professional vs. rinse-service vs. no-clean.

Answers risk-register item **C1 (unit economics)** — "the addressable soiling
saving may be too small to justify a subscription; run the $ math first" — and
supports **M2** (ship a dollar-based clean/wait recommendation, not a soiling %).

For a grid of system size x electricity rate x peak sun hours x soiling-loss
rate, this computes the annual net dollar benefit of each cleaning strategy and
locates where the monetary incentive actually lives (system size / rate /
soiling exposure), including the breakeven thresholds.

The gross-loss formula mirrors the one already shipped in
``scripts/outreach/generate_mailers.py`` and the dashboard calculator:
    annual_loss_$ = system_kw * sun_hours * 365 * SYSTEM_DERATE * soiling_loss_fraction * elec_rate
The new piece is the *cost* side (cleaning is not free), which lets us turn a
gross loss into a net return per strategy.

Run:
    python scripts/analyze/cleaning_economics.py
    python scripts/analyze/cleaning_economics.py --pro-cost 300 --rinse-cost 75

Outputs (under --out-dir, default outputs/economics/):
    returns_grid.csv  - one row per (grid point x scenario)
    report.md         - written report: assumptions, headline tables, breakevens, conclusions
    *.png             - charts (skipped with a warning if matplotlib is unavailable)
    manifest.json     - run provenance (assumptions + grid)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Shared economics core (pure-stdlib). Add src/ to the path so this runs
# standalone (`python scripts/analyze/cleaning_economics.py`) without an install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from risk.economics import (  # noqa: E402
    BASE_RATE,
    BASE_SOILING_PCT,
    BASE_SUN,
    DEFAULT_SCENARIOS,
    annual_loss_usd,
    breakeven_soiling_pct,
    breakeven_system_kw,
    panel_count,
    per_panel_rate,
    professional_cost,
    rinse_cost,
    scenario_net,
)

# ── default sweep grid (the four axes the team asked for) ──────────────────────
DEFAULT_SYSTEM_KW = [4, 6, 10, 20, 50, 100]
DEFAULT_ELEC_RATE = [0.15, 0.25, 0.35]
DEFAULT_SUN_HOURS = [4.5, 5.5, 6.5]
DEFAULT_SOILING_PCT = [1, 3, 5, 8]


def build_grid(scenarios, system_kw, elec_rate, sun_hours, soiling_pct):
    """Return list of row dicts for every (grid point x scenario)."""
    rows = []
    for kw in system_kw:
        for rate in elec_rate:
            for sun in sun_hours:
                for spct in soiling_pct:
                    loss = annual_loss_usd(kw, sun, spct / 100.0, rate)
                    nets = {}
                    for key, scen in scenarios.items():
                        rec, cost, net = scenario_net(loss, scen, kw)
                        nets[key] = net
                        rows.append({
                            "system_kw": kw,
                            "elec_rate": rate,
                            "sun_hours": sun,
                            "soiling_loss_pct": spct,
                            "scenario": key,
                            "scenario_label": scen["label"],
                            "annual_loss_usd": round(loss, 2),
                            "recovered_usd": round(rec, 2),
                            "cost_usd": round(cost, 2),
                            "net_benefit_usd": round(net, 2),
                        })
                    best = max(nets, key=nets.get)
                    for r in rows[-len(scenarios):]:
                        r["is_best"] = (r["scenario"] == best)
    return rows


def write_csv(rows, path: Path):
    fields = ["system_kw", "elec_rate", "sun_hours", "soiling_loss_pct", "scenario",
              "scenario_label", "annual_loss_usd", "recovered_usd", "cost_usd",
              "net_benefit_usd", "is_best"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _fmt(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.0f}"


def make_charts(scenarios, out_dir: Path, args) -> list[str]:
    """Write PNG charts. Returns list of filenames written (empty if matplotlib missing)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap
        import numpy as np
    except ImportError:
        print("[warn] matplotlib/numpy unavailable — skipping charts (CSV + report still written)")
        return []

    written = []
    order = list(scenarios.keys())
    colors = {"no_clean": "#9aa7b0", "rinse_service": "#2C5364", "professional": "#F7B731"}

    # Chart 1: net benefit vs system size at the base case + a high-soiling case.
    for spct, tag in [(BASE_SOILING_PCT, "base3pct"), (8, "high8pct")]:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        kws = sorted(set(args.system_kw))
        for key in order:
            scen = scenarios[key]
            ys = [scenario_net(annual_loss_usd(kw, BASE_SUN, spct / 100.0, BASE_RATE), scen, kw)[2] for kw in kws]
            ax.plot(kws, ys, marker="o", label=scen["label"], color=colors.get(key))
        ax.axhline(0, color="#444", lw=0.8, ls="--")
        ax.set_xscale("log")
        ax.set_xlabel("System size (kW, log scale)")
        ax.set_ylabel("Annual net benefit ($)")
        ax.set_title(f"Net benefit vs. system size\n(rate ${BASE_RATE}/kWh, {BASE_SUN} sun-h, {spct}% soiling loss)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fn = f"net_benefit_vs_size_{tag}.png"
        fig.savefig(out_dir / fn, dpi=130)
        plt.close(fig)
        written.append(fn)

    # Chart 2: best-scenario heatmap over system_kw x soiling_loss (base rate/sun).
    fig, ax = plt.subplots(figsize=(7, 4.5))
    kws = sorted(set(args.system_kw))
    spcts = sorted(set(args.soiling_pct))
    idx = {k: i for i, k in enumerate(order)}
    grid = np.zeros((len(spcts), len(kws)))
    for i, spct in enumerate(spcts):
        for j, kw in enumerate(kws):
            loss = annual_loss_usd(kw, BASE_SUN, spct / 100.0, BASE_RATE)
            nets = {k: scenario_net(loss, scenarios[k], kw)[2] for k in order}
            grid[i, j] = idx[max(nets, key=nets.get)]
    cmap = ListedColormap([colors[k] for k in order])
    ax.imshow(grid, aspect="auto", cmap=cmap, origin="lower", vmin=0, vmax=len(order) - 1)
    ax.set_xticks(range(len(kws)), kws)
    ax.set_yticks(range(len(spcts)), spcts)
    ax.set_xlabel("System size (kW)")
    ax.set_ylabel("Soiling loss (%)")
    ax.set_title(f"Best strategy by size x soiling\n(rate ${BASE_RATE}/kWh, {BASE_SUN} sun-h)")
    for i in range(len(spcts)):
        for j in range(len(kws)):
            ax.text(j, i, scenarios[order[int(grid[i, j])]]["label"].split()[0],
                    ha="center", va="center", fontsize=8,
                    color="white" if order[int(grid[i, j])] != "professional" else "#333")
    fig.tight_layout()
    fig.savefig(out_dir / "best_strategy_heatmap.png", dpi=130)
    plt.close(fig)
    written.append("best_strategy_heatmap.png")
    return written


def write_report(scenarios, rows, charts, out_dir: Path, args):
    S = scenarios
    # Headline base case (typical residential).
    base_loss = annual_loss_usd(6, BASE_SUN, BASE_SOILING_PCT / 100.0, BASE_RATE)
    base = {k: scenario_net(base_loss, S[k], 6) for k in S}

    # Breakevens at the base case.
    be_kw = {k: breakeven_system_kw(S[k], BASE_SUN, BASE_SOILING_PCT / 100.0, BASE_RATE) for k in S}
    be_kw_high = {k: breakeven_system_kw(S[k], BASE_SUN, 0.08, BASE_RATE) for k in S}
    be_soil_6kw = {k: breakeven_soiling_pct(S[k], 6, BASE_SUN, BASE_RATE) for k in S}

    lines = []
    lines.append("# SolarSoiled — Cleaning Economics: where the money is (and isn't)\n")
    lines.append(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d} · addresses risk **C1 (unit economics)** "
                 "and supports **M2** (ship a $-based clean/wait call, not a soiling %)._\n")
    lines.append("## TL;DR\n")
    pro_be = be_kw["professional"]
    rinse_be = be_kw["rinse_service"]
    _be = lambda x: f"above ~{x:.0f} kW" if x else "at **no** system size"
    lines.append(
        f"- At **typical CA residential** soiling (~{BASE_SOILING_PCT}% annual loss, ${BASE_RATE}/kWh, "
        f"{BASE_SUN} sun-h) with **per-panel cleaning costs**, a **professional clean pays for itself {_be(pro_be)}** "
        f"and a **rinse service {_be(rinse_be)}**. A typical 6 kW rooftop is net-negative for professional "
        f"({_fmt(base['professional'][2])}/yr) and "
        f"{'roughly breakeven' if abs(base['rinse_service'][2]) < 10 else 'net-negative'} for a rinse "
        f"({_fmt(base['rinse_service'][2])}/yr).")
    lines.append(
        "- The monetary incentive is **thin for generic small rooftops** and concentrates where soiling loss, "
        "system size, and electricity rate are all high — i.e. **larger systems and high-soiling sites** "
        "(low tilt, near agriculture/highway, dry/low-rain climates). This is the same population "
        "`recommend.py` already escalates to professional.")
    lines.append(
        "- **Implication:** the residential dashboard's own caveat (\"professional cleaning is rarely cost-effective "
        "for residential systems\") is correct — and that's a segmentation signal, not a failure. See the gap plan.\n")

    # Assumptions
    lines.append("## Assumptions — 2026 per-panel cost model\n")
    lines.append("Cleaning is priced **per panel** (≈320 W panels) with a bulk discount as systems grow, "
                 "and a **minimum service charge** — the latter is what makes small residential uneconomic. "
                 "Recovery is the share of annual soiling loss each strategy claws back.\n")
    lines.append("| System size | Panels | Professional ($) | Rinse service ($) | Pro recovery | Rinse recovery |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for kw in (5, 10, 20, 50, 100, 500):
        lines.append(f"| {kw} kW | {panel_count(kw)} | {_fmt(professional_cost(kw))} "
                     f"| {_fmt(rinse_cost(kw))} | {S['professional']['recovery_frac']*100:.0f}% "
                     f"| {S['rinse_service']['recovery_frac']*100:.0f}% |")
    lines.append("")
    lines.append(f"_Per-panel professional rate runs ~$8 (small) → ~$5 (large); minimum service ~$150 pro / "
                 f"$50 rinse. 'Rinse service' is a paid lighter rinse (~{S['rinse_service']['recovery_frac']*100:.0f}% "
                 "recovery), **not** homeowner DIY, per team pushback. Editable in `src/risk/economics.py`._\n")
    lines.append("```\nannual_loss_$ = system_kw × sun_hours × 365 × soiling_loss_fraction × elec_rate")
    lines.append("net_benefit_$ = annual_loss_$ × recovery_frac − cleaning_cost(system_kw)\n```")
    lines.append("Because cost is **per panel**, it scales with system size too — so size largely cancels in the "
                 "net, and the decision becomes per-panel economics (panel energy value vs. ~$5–8 cleaning) with "
                 "the minimum charge sinking small systems.\n")

    # Headline table
    lines.append("## Headline — a typical 6 kW residential array\n")
    lines.append(f"_({BASE_SUN} sun-h, ${BASE_RATE}/kWh, {BASE_SOILING_PCT}% annual soiling loss → "
                 f"{_fmt(base_loss)} of energy lost/yr if never cleaned)_\n")
    lines.append("| Strategy | Recovered | Cost | **Net benefit / yr** |")
    lines.append("|---|---:|---:|---:|")
    for k in S:
        rec, cost, net = base[k]
        lines.append(f"| {S[k]['label']} | {_fmt(rec)} | {_fmt(cost)} | **{_fmt(net)}** |")
    lines.append("")

    # Breakevens
    lines.append("## Breakeven thresholds\n")
    lines.append(f"At base conditions (${BASE_RATE}/kWh, {BASE_SUN} sun-h):\n")
    lines.append("| Strategy | Breakeven system size @ 3% soiling | @ 8% soiling | Breakeven soiling @ 6 kW |")
    lines.append("|---|---:|---:|---:|")
    for k in S:
        if k == "no_clean":
            continue
        a = f"{be_kw[k]:.1f} kW" if be_kw[k] else "—"
        b = f"{be_kw_high[k]:.1f} kW" if be_kw_high[k] else "—"
        c = f"{be_soil_6kw[k]:.1f}%" if be_soil_6kw[k] else "—"
        lines.append(f"| {S[k]['label']} | {a} | {b} | {c} |")
    lines.append("")
    lines.append("Reading: at ordinary 3% residential soiling a rinse service needs a mid-size system to pay off "
                 "and professional needs a large one; once soiling is high (8%), the rinse pays for almost any "
                 "system and professional pays from small-commercial sizes up.\n")

    # Where it lies — net benefit of the best non-no-clean strategy across sizes & soiling
    lines.append("## Where the incentive lies — best-strategy net benefit ($/yr)\n")
    lines.append(f"_Best of rinse/professional, at ${BASE_RATE}/kWh, {BASE_SUN} sun-h. Negative = don't clean._\n")
    sizes = sorted(set(args.system_kw))
    soils = sorted(set(args.soiling_pct))
    header = "| Soiling \\ kW | " + " | ".join(str(s) for s in sizes) + " |"
    lines.append(header)
    lines.append("|---|" + "---:|" * len(sizes))
    for spct in soils:
        cells = []
        for kw in sizes:
            loss = annual_loss_usd(kw, BASE_SUN, spct / 100.0, BASE_RATE)
            best = max((scenario_net(loss, S[k], kw)[2] for k in S if k != "no_clean"))
            cells.append(_fmt(best))
        lines.append(f"| {spct}% | " + " | ".join(cells) + " |")
    lines.append("")

    if charts:
        lines.append("## Charts\n")
        for fn in charts:
            lines.append(f"![{fn}]({fn})\n")

    # Conclusions + gap plan
    lines.append("## Where the incentive lies — and the plan if it's too thin\n")
    lines.append(
        "**Finding.** Net savings scale with `system_kw × sun_hours × soiling_loss × rate`. For generic small "
        "residential arrays in a rain-reset climate the recoverable dollars (a few tens of $/yr) barely clear "
        "even a cheap rinse and are clearly below a professional visit — confirming risk **C1** for that segment.\n")
    lines.append("**Gap plan (if the residential delta won't clear a price):**")
    lines.append("1. **Re-segment toward where the math already works** — larger systems and high-soiling sites "
                 "(low tilt, near agriculture/highway, arid/low-rain). This is risk **C2** (pick a beachhead); the "
                 "exception logic in `src/solarsoiled/recommend.py:46-76` already encodes this population. C&I / "
                 "ground-mount is the natural beachhead.")
    lines.append("2. **Sell the decision, not the percentage** — ship the `$-based clean/wait` recommendation "
                 "(risk **M2**) so value is legible; this report is the engine behind it.")
    lines.append("3. **Prove it before quantifying it** — the savings number can't be sold until validated on a "
                 "real site (risk **M1**, the highest-scored risk); keep claims hedged until then (**L4**).")
    lines.append("4. **Tune cost inputs to reality** — the per-panel rate schedule + minimum charges live in "
                 "`src/risk/economics.py`; refine with real service quotes and the feedback-loop "
                 "`actual_recovery_pct` (`POST /feedback`), then re-run this script.\n")
    lines.append("---\n_Reproduce: `python scripts/analyze/cleaning_economics.py`. "
                 "Grid + every row in `returns_grid.csv`._")

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def write_manifest(scenarios, args, out_dir: Path):
    manifest = {
        "schema_version": "1.0",
        "stage": "analyze/cleaning_economics",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "beta": True,
        "purpose": "C1 unit-economics: net-$ returns of professional vs rinse-service vs no-clean",
        "cost_model": "per-panel (2026), ~$8→$5/panel by size, min $150 pro / $50 rinse",
        "assumptions": {
            k: {"label": v["label"], "recovery_frac": v["recovery_frac"],
                "cost_by_kw": {str(kw): round(v["cost_fn"](kw), 2) for kw in (5, 20, 100)}}
            for k, v in scenarios.items()
        },
        "grid": {
            "system_kw": args.system_kw,
            "elec_rate": args.elec_rate,
            "sun_hours": args.sun_hours,
            "soiling_pct": args.soiling_pct,
        },
        "base_case": {"rate": BASE_RATE, "sun_hours": BASE_SUN, "soiling_pct": BASE_SOILING_PCT},
        "known_limitations": [
            "First-pass cost/recovery defaults — not validated against real service quotes (C1/M1).",
            "Single-clean annual model; no multi-clean optimization or seasonal timing.",
            "Soiling-loss % is an input axis, not yet calibrated per-site against ground truth (M1).",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", default="outputs/economics", type=Path)
    p.add_argument("--pro-recovery", type=float, default=DEFAULT_SCENARIOS["professional"]["recovery_frac"])
    p.add_argument("--rinse-recovery", type=float, default=DEFAULT_SCENARIOS["rinse_service"]["recovery_frac"])
    p.add_argument("--system-kw", type=float, nargs="+", default=DEFAULT_SYSTEM_KW)
    p.add_argument("--elec-rate", type=float, nargs="+", default=DEFAULT_ELEC_RATE)
    p.add_argument("--sun-hours", type=float, nargs="+", default=DEFAULT_SUN_HOURS)
    p.add_argument("--soiling-pct", type=float, nargs="+", default=DEFAULT_SOILING_PCT)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    scenarios = {k: dict(v) for k, v in DEFAULT_SCENARIOS.items()}
    scenarios["professional"]["recovery_frac"] = args.pro_recovery
    scenarios["rinse_service"]["recovery_frac"] = args.rinse_recovery

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = build_grid(scenarios, args.system_kw, args.elec_rate, args.sun_hours, args.soiling_pct)
    write_csv(rows, out_dir / "returns_grid.csv")
    charts = make_charts(scenarios, out_dir, args)
    write_report(scenarios, rows, charts, out_dir, args)
    write_manifest(scenarios, args, out_dir)

    print(f"[ok] {len(rows)} rows -> {out_dir/'returns_grid.csv'}")
    print(f"[ok] report -> {out_dir/'report.md'}")
    if charts:
        print(f"[ok] charts -> {', '.join(charts)}")
    print(f"[ok] manifest -> {out_dir/'manifest.json'}")


if __name__ == "__main__":
    main()
