# What PVDAQ can and cannot do for the product

**2026-08-26.** Two measurements made while scoping `PVDAQ_LABEL_PIPELINE_SPEC.md`, both
load-bearing negatives, neither previously recorded. They answer separate questions:

1. Can better per-roof soiling labels rescue the cleaning product? **No.**
2. Can PVDAQ measure the permanent/wash-only soiling layer? **No — a capability limit.**

Both are the kind of number that gets re-derived wrong later, so provenance is stated inline.

---

## 1. Per-roof labels cannot rescue the cleaning product

**Question.** The PVDAQ work buys one thing: an individual roof's soiling loss instead of its
region's. Does knowing it flip the economic verdict for any site?

**Method.** `src/risk/economics.breakeven_soiling_pct` against the measured PVDAQ loss
distribution (`outputs/soiling/pvdaq_0a_csb12.json`, n=11 after QC). Script:
`scripts/analyze/pvdaq_product_value.py`.

**Result.** At the default rate (`BASE_RATE` $0.1646/kWh, NBT no-battery) breakeven for one
professional clean **exceeds 100% of annual output** at every residential system size.

> **Quote it that way. Do not quote the raw figures** (117 to 240 percentage points at 5-20 kW).
> They are arithmetically correct but read as garbled, and a reader's first reaction is to assume
> a units error rather than a reductio. The finding is that **no physically possible soiling
> level clears the cost.**

Arithmetic check, 10 kW: 10 × 5.5 sun-hours × 365 × 0.84 derate = 16,866 kWh/yr. At 128.9%
loss and $0.1646/kWh, recovered × `recovery_frac` 0.045 = $161, against 23 panels at $7.00 =
$161. Ties.

Even at implausibly favourable rates it does not clear against a measured max of **8.00 pts**:

| rate | breakeven, 5 kW | 10 kW | 20 kW |
|---|---|---|---|
| $0.165 (NBT default) | >100 pts | >100 pts | >100 pts |
| NEM 2.0 (2.8x) | 85.8 | 46.1 | 41.9 |
| $0.70/kWh | 56.5 | 30.4 | 27.6 |

**Mechanism, and this is the point.** The loss estimate is not the binding constraint;
`recovery_frac = 0.045` is. Restore the discredited legacy 0.90 and breakeven falls to
5.9-12.1 pts, which the measured spread *does* sometimes reach. **The product only ever
penciled because of the constant already measured to be 20x wrong.** PVDAQ confirms that
independently rather than reopening it.

**Corollary for the grant.** Do not imply better labels revive the cleaning business. A
reviewer who runs this arithmetic finds the opposite in ten minutes.

**Where it does help.** Breakeven is driven by loss **times** recovery. Santa Cruz is bad on
both: modest loss plus ~27 rain resets a year. PVDAQ holds **155 residential systems in
arid/semi-arid (Köppen B*) zones, 1,295 system-years**, the low-reset regime. That is a
market-selection question, not a roof-selection one, and PVDAQ supplies only the *loss* half
— the recovery half still needs `src/risk/recovery.py` against local rain history.

---

## 2. PVDAQ cannot measure the standing (wash-only) layer

**Question.** `CLAUDE.md` records that the zero-of-1,865 verdict covers **recoverable**
soiling only; a permanent wash-only layer (moss, lichen, algae) is excluded by construction
from IWSR. Can PVDAQ settle that channel?

**Answer: no, and the reason is structural.** `rdtools` builds its normalised performance
index as ([`soiling.py:152-158`](https://github.com/NatLabRockies/rdtools)):

```python
if recenter:
    oneyear = start + pd.Timedelta('364d')
    renorm = df.loc[start:oneyear, 'pi'].median()
else:
    renorm = 1
df['pi_norm'] = df['pi'] / renorm
```

`renorm` is **the median PI of the array's own first 364 days.** An array already mossy on
day one carries that moss in its own denominator. Its 1.0 means "1.0 of a mossy array", rain
restores it to its own mossy normal, and the ratio reads 1.0. **A standing layer present
across the whole monitored window is invisible by construction.** This also explains
interval-start values above 1.0: nothing exceeds expected output, it exceeds its own
first-year median.

This is the same blindness as the IWSR exclusion in `ECONOMICS_GROUNDING_20260809.md` §12,
reached by a different route.

**`recenter=False` does not fix it.** That makes `pi_norm` absolute, but the label then
inherits every absolute error in the system model: derate, module rating, packing, inverter
efficiency. That is exactly what a 2-channel feed plus *modeled* irradiance cannot supply.
The absolute level is confounded with system-model error either way.

**What the measurement DOES support.** With `recenter=True` on 5 residential Csb systems
(`scripts/analyze/pvdaq_standing_layer.py`), interval-start performance sits at or above
each array's own first-year baseline throughout 5-9 year records. That is a real result about
**recoverable-channel dynamics: rain fully reverses whatever accumulates within the monitored
window; there is no progressive buildup that resets fail to clear.** It is *not* a measurement
of the absolute standing layer, and must not be written as one.

**Three caveats that travel with it.**

1. **n = 5 systems, not 129 intervals.** Per-system medians run 0.9456 to 1.0964 and
   share-≥0.99 from 39% to 89%; system 10112 is materially unlike 11758. Intervals inside a
   system are correlated, so the system is the unit. Pooling intervals is the same error the
   Stage-1 gate rule forbids (bootstrap over tiles, not objects).
2. **Selection is severe.** These are PVoutput.org uploads. Someone who instruments and
   publishes their production is close to a perfect **anti-sample** for a hypothesis about
   neglected roofs.
3. **Coverage bias cuts in the result's favour.** `washable_share_probe.py:252` warns partial
   coverage makes a late-seen reset read as incomplete, biasing *toward* finding a standing
   layer. Our result moved the other way, so that objection does not explain it away.

**Do not use the `perfect_clean` / `half_norm_clean` gap as evidence.** It measures 7.17 pts
median here and looks like strong support for a standing layer. It is not: the `half_norm`
start prior is one-sided by construction (`washable_share_probe.py:243`), so a positive gap is
expected even with no standing layer. **This number will be rediscovered and misread.**

**Consequence.** The moss/algae hypothesis is **neither supported nor refuted** by PVDAQ; it
is untouched. It still needs the ground experiment. Keep it decoupled from the PVDAQ proposal
regardless, because the fragility is that a reviewer can run §2 and find the method silent on
the thing it appears to address.

---

## Numbers verified

- **"Recall against county permit records went from 10% to 74%" — VERIFIED 2026-08-26**, by
  re-running `permit_recall_audit.py` on both AOIs. It is the **APN-method, imaged-era** pair:
  **10.4% (22/212)** on `santa-cruz-outreach-v1` (334 arrays, 60cm) to **73.6% (156/212)** on
  `santa-cruz-w2-21cm` (3,362 arrays). Reproduce:

  ```bash
  PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/permit_recall_audit.py \
      --partner-id santa-cruz-w2-21cm --out-dir outputs/economics/recall_santa-cruz-w2-21cm
  ```

  **Always quote the method with the number.** The same run yields three defensible recalls,
  and 73.6% is the loosest:

  | metric | 60cm | W2 21cm |
  |---|---|---|
  | point match, 15 m tolerance | 3.2% | 29.0% |
  | point match, vintage-corrected (year <= 2022) | 3.3% | 33.5% |
  | **APN parcel match, imaged-era** (the quoted pair) | **10.4%** | **73.6%** |

  The point-based figure is highly tolerance-sensitive (W2: 9.0% at 10 m, 29.0% at 15 m,
  71.0% at 30 m), which is why the APN parcel join is the defensible one. A bare "74%" with no
  method attached is the failure mode this repo keeps hitting; do not create another.

---

## Reproduce

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_product_value.py
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_standing_layer.py
```
