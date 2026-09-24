# Fact table — draft claims vs. repository artifacts

> **Superseded as the guarantee, kept as the history.** Since 2026-09-22 the binding check
> is `paper/verify_numbers.py`, which reads each headline value out of the JSON that
> produced it and fails if the rounded form is absent from `paper.tex`. It runs inside
> `make paper`, so the PDF cannot be built from stale claims, and it passes 18 of 18. This
> file is a narrative record of which draft claims were wrong and why, which the automated
> check deliberately does not capture. Where the two disagree, the check is right.
>
> The Stage-2 rows below predate the validation audit and mostly describe numbers the paper
> no longer makes: 0.712, 0.710, 0.677 and 0.548 were all measured on folds that hold out a
> site or a year but never both. See `SPLIT_PLAN.md` for the current state.

Built 2026-09-22 by reading artifacts on disk, not by reconciling docs against each other.
Verdicts: **OK** = draft is right · **SUPERSEDED** = was right, no longer · **WRONG** = never
reproduced · **NUANCE** = both numbers real, they measure different things.

## 1. Stage 1 — detection

| # | Draft claim | Verdict | Verified value | Artifact | Notes |
|---|---|---|---|---|---|
| D1 | "YOLOv11 polygon segmentation" | **SUPERSEDED** | RF-DETR @728 + SAM2, both Apache-2.0 | `models/registry.yaml` | Migration off AGPL Ultralytics completed 2026-08-06/07 |
| D2 | "NAIP, ~0.6 m/px, 2023 flight" | **SUPERSEDED** | SCC 2025 ortho, true ground GSD **0.2043 m** | `outputs/aoi/santa-cruz-w2-21cm/tiles/tile_index.json` (`vintage: scc_2025`) | Stored EPSG:3857 pixel reads 0.2558 m, inflated by 1/cos(37°) |
| D3 | "val SAHI F1 0.570" | **SUPERSEDED** | test tile-level box-F1 **0.8260**, CI [0.798, 0.8528], P 0.8499, R 0.8034 | `outputs/eval/rfdetr_w2/gate.json` | Not comparable: different labels, imagery, and no SAHI in the path |
| D4 | (absent) | ADD | W1 anchor through the identical path: F1 **0.8015**, CI [0.7734, 0.8302], conf* 0.40 | `outputs/eval/rfdetr_w1_anchor/gate.json` | |
| D5 | (absent) | ADD | Paired bootstrap W2−W1: ΔF1 **+0.0246** CI [+0.0051, +0.0441], P=0.993; Δrecall +0.0479 CI [+0.0202, +0.0753] | `outputs/eval/rfdetr_w2/paired_vs_w1.txt` | Marginal CIs overlap; the paired test is the correct one |
| D6 | "conf tuned … best-tuned setting" | **WRONG PROTOCOL** | conf* **0.50** tuned on val, frozen; test's own best F1 0.8431 (penalty 0.0171) | `gate.json`, `threshold_sweep.csv` | Earlier manifest tuned on test and spent the split |
| D7 | "249 tiles, 22 km stretch" | **NUANCE** | **249 tiles**, imaged area **14.93 km²** (bbox 23.71 km²) | tile_index.json, recomputed | "22 km" conflates a linear span with an area |
| D8 | "248 tiles, ~1,100 polygons, 174/25/49" | **SUPERSEDED** | `data/yolo/scc21`: **976 chips** (680/100/196), **3,894** chip polygons | on-disk count | Rebuilt 2026-08-07 from 244 of 249 tiles |
| D9 | (absent) | ADD | Two GT counts: tile-level val 340 / test **585** (the gate); chip-level val 385 / test 636 | `gate.json:n_gt` = 585 | A tile-level F1 quoted against 636 is wrong by construction |
| D10 | "recovers only about 10% of solar parcels" | **SUPERSEDED** | APN recall **55.3%** (240/434) all years; **73.6%** (156/212) imaged-era | `outputs/economics/permit_recall_audit_w2_21cm.md` | Old stack was 5.3% / 10.4% |
| D11 | (absent) | ADD | SAM2 prompt box: box-only IoU 0.509 / area 1.97× / roof-grab 43.3%; shrink-15% **0.844 / 1.01 / 0.0%** (n=60) | `outputs/eval/sam_containment_ab.json` | Roof-grab is a prompt-box problem, not a mask problem |
| D12 | (absent) | ADD | Rejected alternatives: negative points 0.844→0.820; containment 0.824→0.673 (roof-grab 1.7%→71.7% at dilate25) | same + `sam_negpoints_ab.json` | Both measured, both worse |

## 2. Roof geometry (new material, absent from the draft)

| # | Claim | Verdict | Verified value | Artifact |
|---|---|---|---|---|
| G1 | Per-array tilt from public lidar | ADD | **3,068 of 3,362** fits (91.3%), median RMS residual 0.040 m | `outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv` |
| G2 | Independent tilt cross-check | **RESOLVED** | lidar p50 **19.03°** (n=3,068) vs installer-reported p50 **19.00°** (n=**8,573**) | `data/external/dgstats/provenance.json` (cached 2026-08-30) |
| G3 | Sub-5° share | ADD | lidar **8.38%**, DGStats 0.78% (0° dropped as "not reported") | same; verified by `figure_tilt_validation.py` |
| G4 | Mercator trap | ADD | Uncorrected fits understate tilt by **18.9%** silently | `tests/test_roof_geometry.py` |
| G5 | Standoff natural experiment | ADD | pre-flight +0.145 m vs post-flight +0.013 m, Δ **+0.132 m**, CI [+0.084, +0.208], p<0.0001, n=37/54 | `scripts/analyze/panel_standoff_probe.py` |

## 3. Stage 2 — soiling risk

| # | Draft claim | Verdict | Verified value | Artifact | Notes |
|---|---|---|---|---|---|
| R1 | "891 rows, 146 stations, **six states**, 2008–2022" | **OK** | 891 panel rows, 146 stations, **6 states** (AZ CA GA MD NC OR), 2008–2022 | `data/external/nrel_soiling_map_annual.csv` | The repo's own "15 states" is the *full station set*, not the panel rows — see R2 |
| R2 | "109 summary stations → 1,000 rows" | **WRONG** | **111** summary rows → **1,002** total, **257** stations; the 15-state figure belongs to the 255-station source CSV | `outputs/soiling/training_matrix.parquet`, `nrel_soiling_map.csv` | "109" is a different quantity (109 of 255 stations with IWSR>0.99) |
| R3 | "spatial-CV AUC 0.728" | **SUPERSEDED** | **0.712** (`abl_full40`, 0.721±0.006 over 7 seeds) under the current `model.yaml` | `runs/soiling/abl_full40/metrics.json` | 0.728 is `run_optionb`'s stored figure and does not reproduce; margin over the 0.70 bar is ~1 pt |
| R4 | "pooled out-of-year AUC 0.710 … clears our target" | **OK, needs its CI** | 0.7095, n=891 (440/451), SE 0.01725, CI **[0.676, 0.742]**, P(≥0.70)=0.70 | `runs/soiling/run_optionb/holdout_ci.json` | Verdict recorded in the artifact is "STRADDLES" |
| R5 | "single-year holdout too small to trust" | **OK** | 0.6789, n=97, SE 0.061, CI [0.557, 0.797] | `run_optionb/metrics.json` | 0.680 belongs to `run_regularized2022`, a different run |
| R6 | "calibration holds up out-of-year" | **OK** | Brier **0.2177** vs base-rate 0.2500, ECE **0.0457**, MCE 0.1259 | `holdout_ci.json` | |
| R7 | "40 features in four groups" | **OK** | 40, exactly as described | `runs/soiling/run_optionb/feature_names.json` | |
| R8 | "MERRA-2 made it slightly worse" | **OK** | 0.716 CV / 0.666 holdout, 1.3 GB cache, abandoned | `runs/soiling/run_merged_merra2/` | |
| R9 | "two houses on the same street get nearly the same score" | **OK, and quantified** | **58.1%** of importance on AOI-constant features; within-AOI p10–p90 spread **0.377 pts** | `econ_summary.json`; recomputed from `site_economics.csv` | Supersedes the earlier 21.8% reading |
| R10 | (absent) | ADD | Out-of-region pooled AUC **0.6774**; largest region (n=677) **0.548** vs ~0.73 random split | `outputs/soiling/regional_holdout.json` | No feature set recovers it |
| R11 | (absent) | ADD | Level bias: raw head 5.53% vs PVDAQ ≤120 km median 2.76% (n=118); ×0.500 calibration applied | `econ_summary.json:level_calibration` | Corrects the symptom, not the cause |
| R12 | "risk score … passed through calibration" | **OK** | isotonic; XGBoost 200 est / depth 4 / lr 0.05 / λ=5.0 | `configs/soiling/model.yaml` | |
| R13 | (absent) | ADD | Loss head: 3 quantile heads on (1−IWSR)×100, OOF MAE **1.746** pts, PI coverage 0.558→**0.802** after CQR | `models/registry.yaml:run_lossreg` | Replaces `RISK_TO_LOSS_PCT = 8.0` |

## 4. Stage 3 — economics and deployment

| # | Draft claim | Verdict | Verified value | Artifact | Notes |
|---|---|---|---|---|---|
| E1 | "mapped 334 rooftop arrays" / "nearly 350" | **SUPERSEDED** | **3,362 polygons → 1,865 sites** | `arrays.geojson`, `econ_summary.json` | 1.80 polygons/site at 21 cm vs 1.30 at 60 cm; sites are the comparable unit |
| E2 | "recommends cleaning when recovery beats cost" | **TRUE BUT EMPTY** | **0 of 1,865** sites clear; max `prob_net_positive` = **0.0** | `econ_summary.json`, `rate_sensitivity/` | 0% at every regime and at every rate in the swept range |
| E3 | "persistent loss roughly doubles by year three" | **WRONG / unsupported** | PVDAQ 2107, 8.08 yr: standing layer **0.0 pts**, CODS annual max soiling ratio pinned at 1.0000 in **8 of 8** years | `scripts/analyze/washable_share_probe.py` | Localised channel survives; it *saturates*, it does not compound |
| E4 | (implicit 0.90) | **WRONG** | `recovery_frac` measured **0.045** (was assumed 0.90, a 20× overstatement) | `src/risk/recovery.py` | With 0.90 restored, 44.8–95.4% of the AOI "should clean" |
| E5 | "$0.25/kWh flat" (implied) | **SUPERSEDED** | retail offset **$0.4573**, NBT export **$0.0392**, blended AOI **$0.4284**; 5.5% of output in the 4–9pm peak | `src/risk/rates.py`, `econ_summary.json` | 90.1% of the AOI is legacy NEM (n=7,536) |
| E6 | "7.2% loss / $291–541 a year" | **WRONG — no source** | Not in any artifact. AOI median modelled recoverable loss **2.77%**, worth roughly $4–10/yr recovered | `site_economics.csv` | Searched 2026-07, 2026-08 and again today |
| E7 | Shortfall by size | ADD (recomputed) | 0–5 kW **45.9×** → 150+ kW **5.5×**; best site **3.60×** at 1,442 kW; 0 sites clear | `site_economics.csv` + `economics.professional_cost()` | **Supersedes the 13.81×→3.02×/2.76× table in the 2026-08-23 brief**, which predates the ×0.500 level calibration |
| E8 | "600 homes/day free-tier cap" | **OK** | quota gate in the scoring path | `src/risk/weather_client.py` | |
| E9 | "sent mailers to a subset" | **OK, needs date + status** | 50 postcards live via Lob **2026-06-30**, 50/50 accepted, **$47.52**, delivered 13–14 July | `docs/MAILER_PIPELINE.md` | Deliverability test, not a behavioural result |
| E10 | "boosted-tree … single risk score per array" | **NUANCE** | Two models: a calibrated classifier (risk) and a separate quantile regressor (magnitude) | registry | The draft describes only the classifier |

## 5. Introduction claims (external sources — not repo-verifiable)

| # | Claim | Verdict | Note |
|---|---|---|---|
| X1 | 2,383 GW global capacity, 510 GW added 2025 (IRENA 2026) | **UNVERIFIED IN REPO** | External citation; verify against the source before submission |
| X2 | Solar >8% of world electricity (IEA 2026) | **UNVERIFIED IN REPO** | same |
| X3 | ~83 bn kWh US small-scale 2024 (EIA 2023) | **UNVERIFIED IN REPO** | same |
| X4 | 6 M US installations early 2026 (SEIA 2026) | **UNVERIFIED IN REPO** | same |
| X5 | Mejía & Kleissl: 0.051%/day, 7.4% over 145 days, quarter of sites >2× average, <5° tilt soils ~5× faster | **OK as cited** | Consistent with the published paper; the <5° line now has a payoff — 8.38% of our roofs are below 5° (G3) |
| X6 | "NREL 2023" cited for persistent soiling | **WEAK** | Our own measurement (E3) does not support a uniform persistent layer; re-check what that citation actually claims |
