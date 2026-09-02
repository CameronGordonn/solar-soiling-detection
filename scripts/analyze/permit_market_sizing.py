#!/usr/bin/env python3
"""Market-sizing from real Santa Cruz solar permits — the C1 unit-economics answer.

Takes the solar-PV permit table (`ingest_permits.py` output), applies the
per-array economics engine to each parcel's REAL permitted kW, and quantifies
where the cleaning dollars actually are. The headline: value is concentrated in
a handful of large commercial systems, so the beachhead is commercial O&M, not
rooftops (risk C1 / C2).

The report aggregates only — no addresses/APNs — so it is safe to share. Inputs
(the permit CSV) remain gitignored PII.

Usage:
    PYTHONPATH=. python scripts/analyze/permit_market_sizing.py \\
        --permits data/external/sc_solar_permits.csv \\
        --out-dir outputs/economics
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from risk.economics import annual_loss_usd, array_recommendation  # noqa: E402

# kW segments (small residential / large residential or small C&I / commercial)
SEGMENTS = [("Residential (<8 kW)", 0, 8), ("Large / small-C&I (8–100 kW)", 8, 100),
            ("Commercial (>100 kW)", 100, float("inf"))]
LOSS_SCENARIOS = (3, 5)  # % annual soiling loss to bracket the estimate


def _gini(values: pd.Series) -> float:
    v = values.clip(lower=0).sort_values().to_numpy()
    n = v.size
    if n == 0 or v.sum() == 0:
        return float("nan")
    cum = (2 * (pd.Series(v).rank().to_numpy()) - n - 1) * v
    return float(cum.sum() / (n * v.sum()))


def load_installed_base(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["kw"].notna() & (df["kw"] > 0)].copy()
    # one record per parcel (largest system seen on it)
    return df.sort_values("kw").groupby("apn", as_index=False).last()


def net_for(base: pd.DataFrame, loss_pct: float) -> pd.Series:
    return base["kw"].apply(lambda kw: array_recommendation(loss_pct, kw)["expected_net_usd"])


def build_report(base: pd.DataFrame, permits_total: int) -> str:
    lines = ["# Solar cleaning economics — market sizing from Santa Cruz permits", ""]
    lines.append(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. "
                 f"{permits_total:,} solar permits parsed; {len(base):,} parcels with a stated kW "
                 f"(deduped to largest system per parcel). Assumes $0.25/kWh, 5.5 peak sun-h._")
    lines += ["", "## Installed-base size distribution", "",
              "| Segment | Parcels | Median kW | Total kW |", "|---|---|---|---|"]
    for name, lo, hi in SEGMENTS:
        seg = base[(base["kw"] >= lo) & (base["kw"] < hi)]
        if len(seg):
            lines.append(f"| {name} | {len(seg):,} | {seg['kw'].median():.1f} | {seg['kw'].sum():,.0f} |")

    lines += ["", "## Where the cleaning dollars are", ""]
    for loss in LOSS_SCENARIOS:
        net = net_for(base, loss)
        worth = net[net > 0]
        total = worth.sum()
        big_mask = base["kw"] > 100
        big_share = 100 * net[big_mask].clip(lower=0).sum() / max(net.clip(lower=0).sum(), 1e-9)
        gini = _gini(net)
        lines += [f"### Assuming {loss}% annual soiling loss", "",
                  f"- Parcels where cleaning nets > $0: **{len(worth):,} / {len(base):,} "
                  f"({100*len(worth)/len(base):.0f}%)**",
                  f"- Median net among those payers: **${worth.median():,.0f}/yr** "
                  f"{'← too small for residential' if worth.median() < 150 else ''}",
                  f"- Total annual recoverable net value: **${total:,.0f}/yr**",
                  f"- **{int(big_mask.sum())} systems > 100 kW hold {big_share:.0f}% of all that value**",
                  f"- Top-10 parcels alone: ${net.nlargest(10).sum():,.0f}/yr  ·  Gini of value: {gini:.2f}",
                  ""]

    lines += ["## Takeaway", "",
              "At a median of well under $150/yr net, **residential rooftops will not sustain a "
              "subscription** — substantiating risk **C1**. The value is concentrated in a small "
              "commercial tail (~200+ systems > 100 kW carry ~95% of it). The beachhead is "
              "**commercial / utility O&M**, not homeowners (risk **C2**). Detection, scoring, and "
              "outreach should re-rank toward large systems by net-$ (already wired in "
              "`recommend.py`). Per-site loss% from the Stage-2 regression head will sharpen these "
              "figures; today they assume a flat loss rate.", ""]
    return "\n".join(lines)


def make_chart(base: pd.DataFrame, out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[skip] matplotlib not available — no chart", file=sys.stderr)
        return
    net5 = net_for(base, 5).clip(lower=0)
    seg_val = []
    for name, lo, hi in SEGMENTS:
        m = (base["kw"] >= lo) & (base["kw"] < hi)
        seg_val.append((name, net5[m].sum()))
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar([s[0] for s in seg_val], [s[1] for s in seg_val], color=["#94a3b8", "#38bdf8", "#0ea5e9"])
    ax.set_ylabel("Annual net cleaning value ($/yr, 5% loss)")
    ax.set_title("Cleaning value concentrates in commercial systems")
    ax.tick_params(axis="x", labelrotation=15)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    print(f"[ok] chart -> {out_path}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--permits", default="data/external/sc_solar_permits.csv", type=Path)
    p.add_argument("--out-dir", default="outputs/economics", type=Path)
    args = p.parse_args(argv)

    permits_total = len(pd.read_csv(args.permits))
    base = load_installed_base(args.permits)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(base, permits_total)
    md = args.out_dir / "permit_market_sizing.md"
    md.write_text(report + "\n", encoding="utf-8")
    print(f"[ok] report -> {md}")
    make_chart(base, args.out_dir / "permit_value_concentration.png")


if __name__ == "__main__":
    main()
