# Recovery fraction — reconciliation plan

**Status: open. Proposed 2026-09-24, awaiting Cameron's decision on Step 3.**

`recovery_frac` is the share of a year's soiling loss that one wash gets back. It is the
constant that has twice carried this product, and it is wrong again. This document is the
diagnosis, the evidence, and a sequence for fixing it.

> **One-line version.** Commit `187161f` (2026-09-04, on `origin/main`) replaced the
> **measured** `DEFAULT_RECOVERY_PRO = 0.045` with a **modelled** `0.4944 × 0.90 = 0.445`,
> a 9.9× increase, in a commit whose subject is "soiling: model seasonal and persistent
> cleaning value" and whose body is **empty**. Re-running the AOI on today's code would
> report **80 of 1,865 sites worth cleaning** instead of zero, reversing the project's
> headline finding.

---

## 1. What the number is, in each place it lives

| Value | Where | Kind | Agrees with |
|---|---|---|---|
| **0.045** pro / **0.032** rinse | `BBF-Website/public/tools/breakeven.html` (`CLEAN`); [`ECONOMICS_GROUNDING_20260809.md`](ECONOMICS_GROUNDING_20260809.md); the published AOI run's `econ_summary.json` | **measured**, real-weather SOMOSclean trajectory | the paper, the live site, the published dashboard |
| **0.0634** (median, half-norm basis) | `paper/paper.tex`, from `outputs/soiling/audit/value_per_clean.json` | **measured**, 505 observed cleaning events on 149 metered systems | the above |
| **0.445** pro / **0.346** rinse | `src/risk/economics.py` `DEFAULT_SCENARIOS` — **current code** | **modelled** planning scenario | nothing else |
| **~1.0** | `src/risk/band_soiling.py` `bio_recovery_fraction` | **a different channel**, and correct | n/a — see §5 |
| 0.90 / 0.70 | `economics.py` `LEGACY_*`, A/B only | retired 2026-08-09 | n/a |

**Three independent measurements agree at 0.03–0.065. One model sits at 0.445. The model
is the outlier, and it is what the code currently ships.**

## 2. How it happened

```
2026-08-09  63fcbfc  "ground the dollar chain in measured data — the product does not clear"
                     DEFAULT_RECOVERY_PRO = 0.045   # measured best-date clean, coastal SCC
                     comment: the old 0.90 "would overstate every recovery figure by ~20x"

2026-08-30           AOI rebuilt. econ_summary.json records recovery_frac_professional: 0.045
                     -> "zero of 1,865 sites worth cleaning". This is the published figure.

2026-09-04  187161f  "soiling: model seasonal and persistent cleaning value"   (no body)
                     DEFAULT_RECOVERY_PRO = REGULAR_SOILING_FULL_RESET_RECOVERY * 0.90
                                          = 0.4944 * 0.90 = 0.445
                     -> the grounding fix is reverted. No re-run, no note, now on main.
```

The AOI has not been rebuilt since, which is the only reason the published result is still
correct. **The dashboard is right and the code is wrong**, not the other way round.

## 3. Why the modelled number is not simply "a mistake"

`0.4944` is derived, not invented, and `tests/test_economics.py` reproduces it from
`clearsky_daily_weight` to four decimals. It is a **correct computation of a different
quantity**: the share of *dry-season-accumulated* soiling cost avoided by a perfect
early-July reset, assuming

1. dust accumulates linearly across a 180-day April–September dry season,
2. no rain resets it during those 180 days, and
3. winter rain fully resets it.

The defect is not the arithmetic, it is that this quantity was installed as the factor
`scenario_net` multiplies **annual** loss by. Two of the three assumptions are also
measurably wrong for the AOI:

**Assumption 2 fails at the edges.** Santa Cruz daily precipitation, 2015–2024
(`.cache/soiling/band_rain/36.974_-122.031_*.parquet`), heavy-rain resets at ≥10 mm/day:

| | resets/yr |
|---|---|
| whole year | 25.5 |
| **April–September** | **2.5** |
| April | 1.6 |
| May | 0.7 |
| June / July / August | 0.0 |
| September | 0.2 |

So June–August is a genuine dry season, but **April and May are not** — 2.3 of the 2.5
in-season resets land there. The effective rain-free window is ~120 days from June, not
180 days from April, which is the assumption the 0.4944 rests on.

## 4. What it would cost to ship the regression

Recomputed over all 1,865 sites in `outputs/aoi/santa-cruz-w2-21cm/site_economics.csv`,
using each site's own kW, sun hours, tariff and loss percentile:

| recovery | sites clearing (p50 loss) | sites clearing (p90 loss) | median best net |
|---|---|---|---|
| **measured 0.045 / 0.032** | **0 of 1,865** | **0 of 1,865** | −$86.93 |
| modelled 0.445 / 0.346 | **80 of 1,865** | **398 of 1,865** | −$54.13 |

The modelled value does not merely shift the dollars, **it reverses the finding** — and
the p90 column shows 21% of the AOI flipping under a pessimistic-loss assumption. The
measured value never clears at any percentile, max net −$75.

## 5. What is *not* in scope

`band_soiling.bio_recovery_fraction ≈ 1.0` is **correct and must not be touched**. Moss and
lichen are the wash-only channel: rain does not remove them, so a wash removes all of it.
Its recovery being ~20× the dust channel's is the physics, not a bug. The code says so at
`band_soiling.py:238`. Any fix must not collapse these two channels into one constant.

## 6. Plan

### Step 1 — stop the bleeding (no decision needed)

Add a regression test asserting `DEFAULT_RECOVERY_PRO ≈ 0.045`, with the git archaeology
above in its docstring, so the constant cannot be changed again without a commit that
explains itself. Today `tests/test_economics.py` asserts the *modelled* derivation, which
means the test suite is currently **defending the regression**.

### Step 2 — separate the two quantities in code (no decision needed)

Keep the 0.4944 derivation, but stop it being the default. Name it for what it is:

```python
DRY_SEASON_RESET_RECOVERY = 0.4944       # share of DRY-SEASON cost avoided, modelled
DEFAULT_RECOVERY_PRO      = 0.045        # share of ANNUAL cost recovered, MEASURED
```

Both remain available; only the default changes back. `risk/decision.py` already carries
this split as `recovery_basis`, so the API needs no change.

### Step 3 — the decision I need from you

**Which measured value becomes the default?**

| option | value | argument |
|---|---|---|
| **A (recommended)** | **0.045** | Restores the pre-regression state exactly. Matches the live site and the published dashboard, so nothing downstream moves and no figure needs re-issuing. |
| B | 0.0634 | The paper's number, from 505 observed cleans — a larger and more direct evidence base than the single-trajectory 0.045. But it moves the site, the dashboard and the published median break-even ($3.85 → $2.73/kWh). |
| C | per-roof | Use measured recovery where a roof has it, falling back to a scalar. Correct in principle, but only 149 systems have it and none are in the AOI. Worth building toward, not switching to now. |

**I recommend A**, and treating B as a follow-up once someone re-runs the AOI deliberately.
A is a revert; B is a new claim that needs its own re-issue of the dashboard. Do the revert
first so the code, the site and the paper agree, then argue about the second decimal.

### Step 4 — re-run and diff (after Step 3)

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/rebuild_aoi_economics.py \
    --partner-id santa-cruz-w2-21cm
```

Expect `n_sites_worth_cleaning: 0` and `recovery_frac_professional: 0.045` — i.e. byte-level
agreement with the 2026-08-30 artifact. **If anything else moves, stop**: something other
than this constant has drifted too.

### Step 5 — close the drift channel

The site's JS and `economics.py` are two implementations of one model. I verified their
cost functions agree to the cent across 24 system sizes, but **nothing enforces it** and
the recovery constant is exactly where they silently diverged. Add a test that parses the
`CLEAN` block out of `breakeven.html` and asserts it against the Python constants, skipping
when the site repo is not checked out beside this one.

## 7. What is already done

- `src/solarsoiled/decision.py` defaults to the measured pair and names the basis in every
  response, so **the API is already correct** regardless of Step 3.
- A real bug in `array_recommendation_mc` was found and fixed on the way here: it scaled
  each scenario's recovery by `scen["recovery_frac"] / DEFAULT_RECOVERY_PRO`, the *module*
  constant rather than the active table, applying the scaling twice and understating
  sampled recovery ~10× for any non-default table. Invisible while every caller used
  `DEFAULT_SCENARIOS`. Regression test: `test_monte_carlo_agrees_with_the_point_estimate`.
- The API reproduces the paper exactly when given per-system recovery: median break-even
  **$2.4410/kWh** against the paper's **$2.4410**, and **0 of 149** clearing at the
  marginal export rate.
