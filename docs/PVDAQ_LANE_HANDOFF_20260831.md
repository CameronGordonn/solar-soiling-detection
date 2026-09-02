# PVDAQ lane — where it stands, 2026-08-31

Written on the last day of Cameron's tenure, for whoever picks this up. Everything
below is measured in this repo. Where a run is unfinished it says so and says how to
resume it.

Read `docs/GAMMA_RESOLUTION_20260827.md` first for the label pipeline's precision
story; this file is the state on top of it.

---

## 1. The finding that matters most

**The soiling model cannot generalise to a region it has not seen, and this is now
measured rather than suspected.**

`scripts/predict/regional_holdout.py`, holding out one region at a time:

```
hold out Arizona (geographic)        AUC 0.5313      <- chance
hold out random rows, same size      AUC 0.7569
hold out random stations, same size  AUC 0.7267
```

All three use the **same 349-row training set and the same holdout size**, so this is
not a sample-size effect. It is a ~0.20 AUC geographic gap.

> ⚠️ **Provenance note added 2026-08-31 by the cross-doc audit. The conclusion holds; these
> three specific numbers do not reproduce from what is committed.**
>
> `scripts/predict/regional_holdout.py` as committed reads the 1,002-row NREL
> `training_matrix.parquet` and forms regions by **k-means on (lat, lon)**. It has no
> Arizona-by-name option and no 349-row mode, so the block above cannot be regenerated from it,
> and it does not match the saved artifact `outputs/soiling/regional_holdout.json` (992 rows
> scored, 8 regions).
>
> **What the committed artifact does show, and it supports the same conclusion:**
>
> | feature set | pooled out-of-region AUC | per-region range |
> |---|---|---|
> | production (40) | **0.6774** | 0.406 – 0.912 |
> | weather+physics (27) | 0.6374 | 0.559 – 0.930 |
>
> The largest region (n=677) scores **0.548**, near chance, against ~0.73 for a random split.
> So "the model does not generalise to an unseen region" is **measured and load-bearing** and
> should keep being said. Quote 0.677 pooled / 0.548 worst region, with
> `outputs/soiling/regional_holdout.json` as the citation, until someone re-runs the
> Arizona-specific comparison and saves it. Reproduce:
> `PYTHONPATH=. conda run -n solar-soiling python scripts/predict/regional_holdout.py --regions 8 --seeds 3 --out-json outputs/soiling/regional_holdout.json`

The cause is the label set's geography, not the model: **81% of the NREL rows sit west
of -114, and 5.2% east of -100.** A model trained to separate fifteen states has, in
practice, seen the Southwest.

Two consequences for anything written outward-facing:

- Do **not** claim the model works "in any region". It works in regions resembling its
  training geography. Say that.
- Adding features will not fix it. Land cover earns its place for portability
  (+0.033); raw coordinates do not (-0.001), despite `longitude` being the
  highest-gain feature in the production model at 7.9%. That is what a coordinate
  memoriser looks like, and 50 km spatial CV is structurally unable to detect it.

## 2. The fix that was in progress

PVDAQ's residential fleet is the geographically balanced label set NREL is not:

| | NREL (trains the model today) | PVDAQ residential |
|---|---|---|
| rows / systems | 1,002 | **1,359 readable systems** (of 1,629 residential) |
| west of -114 | **81.0%** | **46.9%** |
| east of -100 | **5.2%** | **45.6%** |
| Koppen classes | Southwest-dominated | **16** |

**The denominator is 1,359, not 1,629.** The other 270 are multi-channel systems on a
sub-daily schema (`ac_power_inv_<id>`), which this extractor cannot read; it handles the
daily aggregate feed. Each one cost an S3 download to reach the same "no recognised AC
columns" failure, and a whole worker batch once produced zero results because of it. They
are excluded by default now (`--all-channels` to include them). Excluding them is also
what *revealed* the balance above: on the readable population the fleet is 46.9% west /
45.6% east, which is the balanced label set this lane exists to build. It was there all
along, hidden behind unreadable systems and a bad warm order.

Its largest class is Cfa (humid subtropical, 403 systems) -- the eastern US, which NREL
barely covers. The extraction is validated against NREL on shared ground: paired
difference **+0.16 pts, 95% CI [-0.62, +0.91]**, n=24, no detectable bias. That check
was re-run after the gamma fix rather than assumed.

**Status changes as the extraction runs — read it, don't trust this line:**

```bash
PYTHONPATH=. python scripts/analyze/pvdaq_fleet_extract.py --status
```

It stood at 483 fitted / 429 non-degenerate when this file was written on 2026-08-31,
with ~876 of 1,359 remaining at roughly 2/min across three workers. See §3.

**The supervisor has died twice and the cause is not known.** It now logs a heartbeat:
check `outputs/soiling/fleet/_supervisor.log` for `SUPERVISOR START` lines. More than one
means it restarted. This is the first thing to check if the fit count stops moving.

## 3. How to resume the extraction

Everything is in `handoff-pvdaq.tar` on the `handoff-v1` release. Restore it before
doing anything, or you will spend days re-earning API quota:

```bash
gh release download handoff-v1 --repo Better-Behavior-Foundation/solar-soiling-ml -D /tmp/handoff
tar xf /tmp/handoff/handoff-pvdaq.tar -C <your clone>
```

Then:

```bash
# what is done
PYTHONPATH=. python scripts/analyze/pvdaq_fleet_extract.py --status

# warm more cells (225 of 343 cached). PACE THIS -- see the warning below
PYTHONPATH=. python scripts/analyze/pvdaq_fleet_extract.py --warm-only --max-cells 12 --sleep 25

# fit whatever is cached; resumable, skips everything already done
PYTHONPATH=. python scripts/analyze/pvdaq_fleet_extract.py --no-warm --launch --workers 3
bash outputs/soiling/fleet/run_shards.sh

# combine
PYTHONPATH=. python scripts/analyze/pvdaq_fleet_extract.py --merge
```

`outputs/soiling/fleet/supervisor.sh` relaunches the shards whenever they finish, so
fitting keeps pace with warming. It does not survive a reboot; restart it by hand.

### `--status` over-reports failures, and shard3 is the reason

**Do not investigate shard3's failure count. It was investigated on 2026-08-31 and it is
benign.** Writing that down because working it out took an hour and the number is alarming
on its face.

`--status` sums `failures` across every `shard*.json` on disk, including shards from
**previous runs with a different worker count**. `shard3.json` is a leftover from the
4-worker run that died in an S3 outage at 2026-08-30 21:35. The current run has three
workers, so shard3 is not live, but its 129 stale failures are still counted and always
will be.

Those 129 break down as:

| count | cause | disposition |
|---|---|---|
| 46 | multi-channel, hit the "no recognised AC columns" check | correctly excluded now |
| 63 | network (SSL / ConnectionError / ReadTimeout) on two-channel systems | **queued in the live run, will retry** |
| 20 | network, but on multi-channel systems | correctly excluded — verified all 20 have `available_sensor_channels` 11–26, none in the readable 1,359 |

**Nothing is lost.** Every two-channel system that failed to the outage is back in the queue;
everything not re-queued is a system this reader cannot parse anyway. The two-channel filter
did exactly its job.

**Do not delete `shard3.json`** even though it is dead: `--merge` reads every `shard*.json`,
and its 151 successful fits are real and needed.

The honest progress number is `results` summed across shards, not `done` minus `failed`. If
you want `--status` to stop lying, make it ignore shards whose `.sh` is older than the newest
`supervisor.sh`, or move retired shards to `outputs/soiling/fleet/retired/`.

### ⚠ Open-Meteo's free tier is the binding constraint, and it bites twice

The quota is priced on **data volume**, not call count, and the ceiling is **daily**.
A first attempt asked each cell for a 17-year 5-variable window (~745,000 values) and
took HTTP 429 on 316 of 345 cells in ten minutes. Cells now request only the span
their own systems need (47% less data) with backoff and pacing, and that works -- but
**a day's warming is roughly 100 cells and then the daily ceiling stops you.** 118
cells remain, so budget two more days.

It also bites where you will not expect it: `build_risk_features.py` calls Open-Meteo
live, so **a fleet warm running in the background will fail two Stage-2 contract
tests** with an empty feature matrix. That is not a regression. Check the quota before
debugging a test failure.

If fleet extraction becomes routine rather than one-off, price the commercial tier.
This is the second time the free tier has been the limiting factor and the first time
it broke the test suite as collateral.

## 4. The gate this was all for — and the step that is missing

> **Read this before running the gate. Corrected 2026-08-31 by the cross-doc audit.**
> The three-command version of this section that circulated earlier **cannot work**, and
> fails silently rather than erroring. Details below.

### 4a. What is missing

**There is no step that turns fleet labels into a feature matrix.** `--merge` writes
`outputs/soiling/pvdaq_fleet_labels.json`, which is labels only: no engineered
weather/AQ/static columns. `regional_holdout.py` scores a *matrix*. The only thing that
writes a matrix is `train_risk_model.py`, and it builds from the NREL label sources in
`configs/soiling/california.yaml`.

So the honest state of this lane is: **the balanced label set is being extracted; the gate
is specified but not yet runnable.** Finishing it means adding a `pvdaq_fleet` label source
to `src/risk/labels.py` and running feature engineering over the fleet systems, the same
path NREL rows already take. That is real work, not a 20-minute step.

### 4b. The trap, so nobody falls in it again

Until 2026-08-31 `regional_holdout.py` had its matrix and feature list hardcoded. This:

```bash
# WRONG. Looks like a fleet run. Is not one.
PYTHONPATH=. python scripts/predict/regional_holdout.py --regions 8 --seeds 3 \
    --out-json outputs/soiling/regional_holdout_fleet.json
```

re-scored the **NREL** matrix, ignored every fleet label, exited 0, and wrote the result to
a file named `_fleet`. Because NREL was unchanged it returned ~0.677, the number already on
disk. Read as a fleet result that says "a balanced label set did not fix regional
blindness" — a false negative with a filename asserting otherwise.

The script now takes `--matrix` and `--feats`, records both in its output JSON, and refuses
to run when features are missing rather than silently median-filling them. Pass them
explicitly for anything that is not the NREL default.

### 4c. The experiment, once 4a is done

1. Build the fleet feature matrix (see 4a).
2. Retrain on the **35-feature set** — weather, physics proxies and land cover, with no
   coordinates, no `tilt_deg`, no `month_of_year`, no dead AQ columns. That set is
   statistically tied with the production 40 out-of-region while dropping every positional
   feature.
3. Run the gate against the fleet matrix explicitly:

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/predict/regional_holdout.py \
    --matrix outputs/soiling/fleet_matrix.parquet \
    --feats  runs/soiling/<fleet_run>/feature_names.json \
    --regions 8 --seeds 3 --out-json outputs/soiling/regional_holdout_fleet.json
```

4. **The number to beat is 0.6774 pooled out-of-region, with the worst region at 0.548**
   (`outputs/soiling/regional_holdout.json`, production 40 features).

> ⚠️ **Not 0.5313.** The "hold out Arizona = 0.5313 / random rows = 0.7569 / random
> stations = 0.7267" block in §1 does not reproduce from anything committed:
> `regional_holdout.py` clusters regions by k-means on (lat, lon) and has no
> Arizona-by-name mode or 349-row mode, and the saved artifact scores 992 rows across 8
> regions. Benchmark against 0.6774/0.548, which is reproducible, or re-run the Arizona
> comparison and save its JSON before quoting it.

**Either outcome is a real result.** If a 45.6%-eastern label set across 16 Koppen classes
moves the out-of-region AUC, the model's regional blindness was a data problem and this
lane fixed it. If it does not move, the problem was never geography — which is the more
interesting finding and needs to be known before anything else is built on this.

## 5. Things not to redo

- **Tilt does not belong in the ML head.** Supplying real per-array tilt *reduces* AOI
  spread by 0.0145. It already does real work in the economics chain (the 11.1% of
  per-home dollar variance) and in the moss channel's drying term.
- **58.1% of the model's gain sits on features constant across the AOI** (measured
  2026-08-12; supersedes the ~20%/21.8% figure, which counted only the absent ones and
  missed the all-NaN and low-variance ones). Filling them was tried for WorldCover and did
  nothing. The limit is structural — station-level labels have no within-AOI variation to
  learn from — so no feature supplied to the AOI matrix can create one.
- The cleaning product is measured dead on all three channels (dust, mineral band,
  biological). Do not reopen it without a new mechanism.
