#!/usr/bin/env python3
"""Light-pro cleaning opportunity model — is it feasible, and how big? (meeting metrics)

Combines (a) the per-array economics engine, (b) the Santa Cruz permit installed
base, and (c) 2026 market research on the solar-cleaning trade into a defensible
opportunity sizing for the "light-pro + own-the-data-funnel" thesis.

Research anchors (cited in the report):
  - Solar-cleaning gross margins ~40-50% for lean operators; ~$6/panel; a 200-panel
    job ≈ $1,200 rev / ~$600 net.  (financialmodelslab, smallbusinesskings)
  - Water-fed pole (purified water, ground-based) is the INDUSTRY-STANDARD
    residential method — i.e. "light-pro" is how the trade already works, not a
    novel risk.  (equipmaxx 2026 guide)
  - 70-80% of first-time cleaning clients become recurring.  (operator surveys)
  - Market ~$1.2-1.27B (2025-26) -> $2.57-4.6B by 2034-35 (CAGR 7.9-14.4%); the
    highest-margin layer is the data/optimization software, not the labor.
    (gminsights, mordor, snsinsider)

Everything below an "ASSUMPTION" label is editable via CLI — bring your own
numbers to the meeting.

Usage:
    PYTHONPATH=. python scripts/analyze/lightpro_opportunity.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from risk.economics import array_recommendation, professional_cost, rinse_cost  # noqa: E402

# ── ASSUMPTIONS (editable) ────────────────────────────────────────────────────
CA_RESIDENTIAL_SOLAR = 1_500_000   # CA rooftop solar homes (est.; CA leads US)
HIGH_SOILER_FRAC = 0.25            # share that are high-soiling enough to pay (inland-skewed)
VISITS_PER_YEAR = 1.5              # light-pro visits/yr for a high-soiler
LIGHTPRO_PRICE = 90.0             # $/visit, residential light-pro ($60-120 range)
CLEANER_MARGIN = 0.45              # cleaner gross margin (research: 40-50% lean)
RECURRING_RATE = 0.75              # first-time -> recurring (research: 70-80%)
OUR_TAKE = 0.15                    # SolarSoiled data/lead-gen take of cleaning revenue
HOME_KW = 6.0                      # representative residential system
HIGH_SOILER_LOSS_PCT = 6.0         # the loss level at which a home becomes a target


def _f(x): return f"${x:,.0f}"


def build(args) -> str:
    # CLI-overridable assumptions (shadow module defaults locally)
    CA_RESIDENTIAL_SOLAR = args.ca_homes
    HIGH_SOILER_FRAC = args.high_soiler_frac
    VISITS_PER_YEAR = args.visits
    LIGHTPRO_PRICE = args.price
    OUR_TAKE = args.take
    L = []
    L.append("# Light-pro cleaning — feasibility & opportunity (meeting metrics)\n")
    L.append("_Per-panel economics + Santa Cruz permits + 2026 market research. "
             "Assumptions are labeled and editable; treat as a sizing, not a forecast._\n")

    # 1. Is it feasible for a cleaner?
    L.append("## 1. Is light-pro feasible — and would a cleaner pursue it?\n")
    L.append("**Yes — it's already the industry-standard residential method.** Water-fed pole "
             "(purified water, soft brush, operator on the ground) is the default for residential "
             "solar cleaning; no roof climbing, one-person crew, high route density. The trade runs "
             "**~40-50% gross margins** for lean operators (~$6/panel; a 200-panel job ≈ $1,200 "
             "revenue / ~$600 net), and **70-80% of first-time clients become recurring** — the exact "
             "profile that supports a subscription.\n")
    job_net = LIGHTPRO_PRICE * CLEANER_MARGIN
    L.append(f"At our light-pro price ({_f(LIGHTPRO_PRICE)}/visit) and a {CLEANER_MARGIN:.0%} margin, a "
             f"cleaner nets **~{_f(job_net)}/job**. A modest **8 jobs/day** route → "
             f"~{_f(job_net*8)}/day, ~{_f(job_net*8*220)}/yr for a solo operator — consistent with the "
             f"$50-100k owner-income range the trade reports. The cleaner's constraint is **lead flow "
             f"and route density**, which is precisely what our targeting provides.\n")

    # 2. Homeowner side — the model says who it's worth for
    L.append("## 2. Who is it worth for? (the model is the qualifier)\n")
    rec_low = array_recommendation(3, HOME_KW)
    rec_high = array_recommendation(HIGH_SOILER_LOSS_PCT, HOME_KW)
    L.append(f"For a {HOME_KW:.0f} kW home (light-pro {_f(rinse_cost(HOME_KW))}, "
             f"full-pro {_f(professional_cost(HOME_KW))}):\n")
    L.append("| Home soiling | Best action | Homeowner net/yr |")
    L.append("|---|---|---|")
    L.append(f"| Typical 3% (coastal) | {rec_low['recommended_action']} | {_f(rec_low['expected_net_usd'])} |")
    L.append(f"| High {HIGH_SOILER_LOSS_PCT:.0f}% (inland/dusty) | {rec_high['recommended_action']} "
             f"| +{_f(rec_high['expected_net_usd'])} |")
    L.append("")
    L.append("**Only high-soilers pay — and Santa Cruz (coastal, rain-reset) is the wrong place to "
             "look.** The high-soilers are inland CA (Central Valley, Inland Empire, high-dust). This is "
             "the residential case *for* statewide scoring: go where the soiling is. _(High-soiler share "
             "is an ASSUMPTION until the regression head + statewide scoring pin it down.)_\n")

    # 3. Market sizing
    L.append("## 3. Opportunity sizing (California residential)\n")
    targets = CA_RESIDENTIAL_SOLAR * HIGH_SOILER_FRAC
    spend_home = LIGHTPRO_PRICE * VISITS_PER_YEAR
    serviceable = targets * spend_home
    our_rev = serviceable * OUR_TAKE
    L.append(f"- CA residential solar homes (ASSUMPTION): **{CA_RESIDENTIAL_SOLAR:,}**")
    L.append(f"- High-soiler target share (ASSUMPTION {HIGH_SOILER_FRAC:.0%}): **{targets:,.0f} homes**")
    L.append(f"- Cleaning spend/home/yr ({VISITS_PER_YEAR}× {_f(LIGHTPRO_PRICE)}): **{_f(spend_home)}**")
    L.append(f"- **Serviceable residential cleaning revenue: ~{_f(serviceable)}/yr**")
    L.append(f"- **SolarSoiled data/lead-gen take ({OUR_TAKE:.0%}): ~{_f(our_rev)}/yr** "
             f"(recurring, {RECURRING_RATE:.0%} retention)")
    L.append("")
    L.append("Context: the **whole** US/global solar-cleaning market is ~$1.2-1.27B (2025-26) growing to "
             "$2.57-4.6B by ~2034 (7.9-14.4% CAGR). The highest-margin layer, per analysts, is the "
             "**data/optimization software** that decides what to clean and when — **our position**, not "
             "the labor.\n")

    # 4. The wedge
    L.append("## 4. The wedge: own the funnel, not the bucket\n")
    L.append("The cleaning margin is a commodity; the defensible asset is **knowing which homes need "
             "cleaning and when** + routing + recurring billing. Asset-light first move: **lead-gen to "
             "existing cleaners** (we qualify high-soilers statewide, they do the labor), then integrate "
             "if unit economics prove out. This is the same 'data layer beats hardware' conclusion the "
             "market analysts reach independently.\n")
    L.append("**Honest gaps to close:** light-pro recovery (~70%) is unvalidated (risk M1); a pole rinse "
             "won't touch bonded grime/bird droppings (two soiling regimes — dust vs. bonded); and the "
             "high-soiler share needs the regression head + statewide scoring to confirm.\n")

    L.append("---\n_Edit assumptions via CLI flags and re-run. Sources: financialmodelslab, "
             "smallbusinesskings, equipmaxx 2026, gminsights, mordorintelligence, snsinsider._")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ca-homes", type=int, default=CA_RESIDENTIAL_SOLAR)
    p.add_argument("--high-soiler-frac", type=float, default=HIGH_SOILER_FRAC)
    p.add_argument("--visits", type=float, default=VISITS_PER_YEAR)
    p.add_argument("--price", type=float, default=LIGHTPRO_PRICE)
    p.add_argument("--take", type=float, default=OUR_TAKE)
    p.add_argument("--out-dir", default="outputs/economics", type=Path)
    args = p.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = build(args)
    out = args.out_dir / "lightpro_opportunity.md"
    out.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\n[ok] -> {out}")


if __name__ == "__main__":
    main()
