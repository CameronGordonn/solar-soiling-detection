# SolarSoiled — Meeting Brief (June 2026)

Two questions this brief answers, both from the risk register:
1. **C1 (unit economics):** does soiling-cleaning save enough money to sell? For whom?
2. **How do we go forward** given the model isn't yet at the GA bar?

---

## 1. Headline — where the money is

We rebuilt the product's output from a **soiling risk score** (a 0–1 number a customer can't act on) into a **dollar decision**:

```
annual loss $ = system_kW × sun_hours × 365 × soiling_loss% × electricity_rate
net benefit $ = annual loss $ × recovery% − cleaning cost
```

Three cleaning options, with a **2026 per-panel cost model** (cost scales with panel count, plus a minimum service charge). The middle tier is **"light-pro"** — purified water + telescoping poles, no roof access, ~$60–120 — the underserved gap between an ineffective hose rinse and a $120–420 full scrub:

| Option | Cost (6 kW / 200 kW) | Recovers | 6 kW @ 3% | 6 kW @ 6% | 200 kW @ 5% |
|---|---|---|---|---|---|
| No clean | $0 | 0% | $0 | $0 | $0 |
| **Light-pro** | ~$75 / ~$1,560 | ~70% | −$12 | **+$51** | **+$1,951** |
| Professional | ~$150 / ~$3,125 | ~90% | −$69 | +$13 | +$1,392 |

**The verdict — two viable wedges, both gated by the model:**

- **Typical homes (≈3% soiling) don't pay** at any tier — confirming risk **C1** and defusing the false-advertising risk **L4**.
- **High-soiling homes (6%+) DO pay — but only via light-pro** (+$51–102/yr), not full professional. The model's job is to find the ~25% of homes that are high-soilers; light-pro is the price point that makes them economic.
- **Commercial still wins big** — the 224 systems >100 kW hold the bulk of absolute value; light-pro beats full-pro there too.

> **Two beachheads to weigh at the meeting (decision deferred — both paths open):**
> **(A) Commercial / utility O&M** — few sites, large $/site, easiest validation.
> **(B) Residential light-pro subscription** — many high-soiler homes, thin $/home but recurring + route density + the data funnel.
> Both depend on the model identifying *who needs cleaning and when*. The real moat is owning that data/funnel, not the cleaning labor.

---

## 2. The returns analysis (the deliverable requested)

Full sensitivity grid across the four axes — **system size × electricity rate × peak sun hours × soiling-loss rate** — with breakeven thresholds:

- `outputs/economics/report.md` (+ charts: net-benefit vs size, best-strategy heatmap)
- `outputs/economics/returns_grid.csv` (every combination, machine-readable)

Key breakevens (at $0.25/kWh, 5.5 sun-h): professional cleaning only nets positive above ~8 kW at 5% loss; rinse service breaks even lower. Below those, **no-clean wins** — which is most homes.

Cost/recovery numbers are first-pass defaults; **Cameron to refine with real cleaning quotes** — they're config, easy to update.

---

## 3. How the metrics work (for the team)

- **Soiling risk → loss% → dollars.** Today the model predicts a 0–1 risk; we map it to an annual energy-loss %, then to dollars. (A regression head that predicts loss% *directly* from NREL ground truth is in progress — it removes the guesswork in that middle step.)
- **Two physics baselines** for comparison on the dashboard: SOMOSclean (ENEL accumulation) and Kimber 2007, alongside the XGBoost ML score.
- **Recovery %** is calibrated from the UCSD 2013 soiling study (1–7% per clean); **cost** is the new piece this work added.

---

## 4. What makes it credible — real permit data

We parsed 11 years of County "Building Permits Issued" PDFs into a solar-install registry (`scripts/analyze/ingest_permits.py`):

- 8,780 solar permits, **7,399 unique parcels**; annual installs **tripled** 2016→2025.
- Real system sizes: median 6.9 kW, but a fat tail to **9.6 MW**; **1,025 systems >8 kW**, **224 >100 kW**.
- Joined to detected arrays by **APN/parcel** → each array can carry its *real* permitted kW (not a pixel-area estimate).
- Bonus: a permits-vs-detections cross-check gives a **relabeling queue** to improve the detector (risk M1).

---

## 5. Risk register — how we go forward

The three "can-kill-it" risks and status:

| Risk | Status after this work |
|---|---|
| **C1 — unit economics** | **Addressed.** The $ math is done; answer = commercial, not residential. |
| **M1 — validation gap** | **Started.** Permit cross-check surfaces detection misses; still need a real-site soiling validation (reference panel or inverter PR vs. rain-reset) before selling a number. |
| **L2 — employment IP** | **Open + time-sensitive (Cameron).** Before signing any offer: dated prior-inventions/exclusion exhibit; review moonlighting/non-compete. *Not a code task — highest urgency.* |

Plus: M2 (ship $-based recommendation, not a %) is now built into `recommend.py`.

---

## 5b. Model honesty — what to trust (and what not to)

The soiling model is useful but **not yet validated on a real site** (risk **M1**, our #1 risk). State this plainly:

- **Trust the *relative* ranking.** Inland/dusty ≫ coastal is reliable (live: Fresno 7.6% vs. Santa Cruz 4.6%). Use it to *target* who needs cleaning.
- **Don't yet sell the *absolute* %/$.** The SOMOSclean physics likely **overstates coastal soiling** — it has no fog/dew term (which naturally rinses marine-climate panels) and its saturation ceiling is tuned toward dustier sites. So Santa Cruz's +$22/yr is optimistic; real-world coastal recovery is ~1–3%.
- **The fix path & where it stands:** the Stage-2 **regression head** learns annual loss% directly from NREL ground truth. First run: it **ranks well (0.74 spatial-CV AUC)** but does **not yet beat a naive mean on absolute %** (holdout MAE 2.68 vs 2.25). Two diagnosed reasons: (1) historical **air-quality coverage is ~21%** — Open-Meteo has *no* PM/dust data before ~2022, and dust drives magnitude; (2) the NREL target has low variance. So today: **use the model to rank, not to quote absolute %.**
- **Bugs fixed along the way:** the $-math now uses **annual-average** soiling (panels reset on rain), not the dry-season peak; and a weighting bug that flagged *every* training row as "feedback" (3×) was corrected.
- **The honest unlock is M1** — one validated site (reference panel or inverter performance-ratio vs. rain-reset) does more for trust than any model tweak.

## 5c. Running the pipeline reliably

The "runs that hang" are MERRA-2 (NASA) network failures. MERRA-2 is **opt-in** and per CLAUDE.md it *hurt* performance — so don't use it:

- **`unset NASA_EARTHDATA_TOKEN`** before training/scoring → falls back to Open-Meteo (reliable). This is also the production AQ path.
- **Activate the env first:** `conda activate solar-soiling`.
- **Pre-warm once, then it's cached** (`.cache/soiling/openmeteo.sqlite`); repeat runs are fast and offline.
- Training run (no MERRA-2): `PYTHONPATH=. python scripts/predict/train_risk_model.py --target-mode regression --holdout-year 2022 --run-name run_regression_loss2022`

## 6. Artifacts to open/screenshot for the meeting

| What | Path |
|---|---|
| Returns grid + charts (per-panel + light-pro) | `outputs/economics/report.md`, `*.png` |
| Commercial-concentration finding | `outputs/economics/permit_market_sizing.md` (+ `permit_value_concentration.png`) |
| **Light-pro feasibility + opportunity sizing** | `outputs/economics/lightpro_opportunity.md` |
| Detector recall cross-check | `outputs/economics/permit_detection_recall.md` |
| Live homeowner dashboard (demo) | BBF site `/tools/dashboard` (was GitHub Pages; now `../BBF-Website/public/tools`) |

### Light-pro opportunity — meeting numbers (assumptions editable)
- **Feasible & proven:** water-fed pole is the *industry-standard* residential method; trade runs **40–50% margins**, **70–80% of first-time clients become recurring**.
- **Cleaner unit economics:** ~$40 net/job at our $90 price → ~$71k/yr solo at 8 jobs/day. Constraint is *lead flow* — which our targeting supplies.
- **CA residential SAM:** ~375k high-soiler homes × $135/yr ≈ **$50.6M/yr serviceable**; **~$7.6M/yr recurring** at a 15% data/lead-gen take.
- **Market context:** solar-cleaning is $1.2B→$4.6B by ~2034; analysts say the **data/optimization layer is the highest-margin position** — exactly ours.
- **Honest caveats:** light-pro ~70% recovery unvalidated (M1); high-soiler share needs the regression head + statewide scoring; SC itself is *low*-soiling (look inland).

---

## Next steps

**This week / immediate**
1. **Refine cost inputs** with real professional + rinse quotes (Cameron). One config edit re-runs everything.
2. **Finish the regression head** — predict per-site loss% directly (training now), then wire `pred_loss_pct` into scoring so the dashboard/recommender net-$ is model-driven per site.
3. **Surface net-$ in the dashboard** — replace the placeholder energy calc with the real 3-scenario net benefit; rank arrays by net-$.
4. **Push the private repo** (4 commits waiting; sandbox has no GitHub access).

**Strategic (the pivot)**
5. **Aim at commercial.** Pull the 224 >100 kW systems into a target list; design detection/outreach for ground-mount + large rooftops.
6. **Land one commercial design-partner site** to run the M1 validation protocol — proof before pitch.
7. **L2 IP action** before any employment offer (time-sensitive, non-code).

**Sharpening (lower priority)**
8. Improve permit kW capture in 2021–23 (county changed the description format — only 29% of recent permits parsed a kW).
9. Tighten detector recall using the tiled-footprint denominator + the 728-parcel relabel queue.
