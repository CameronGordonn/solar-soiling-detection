# DATA.md — data artifacts, custody, and provenance

A fresh clone has **code only**. Nothing below is in git: it is large, license-restricted, or
PII-adjacent. This file is the whole story of how you get it.

> **Custody, as of 2026-08-26.** The data lives in a **GitHub Release on this repo**, so custody is
> the org itself rather than a person: anyone who can read this file can already fetch it. Org
> owners **Craig** and **Akshitha** are the custodians of record. Cameron is not in this path and
> does not need to be. The remaining single point of failure is the GitHub org, so keep more than
> one owner on it, and check the restore works before you need it.

## Get the data

**The store is a GitHub Release on this repo: [`handoff-v1`](https://github.com/Better-Behavior-Foundation/solar-soiling-ml/releases/tag/handoff-v1).**
Decided 2026-08-26. Not a bucket, and deliberately so: release assets ride on the org membership
you already have to read this file, so there is **no separate credential to hold, hand over, or
lose**. That was the actual failure mode this hand-off exists to prevent, and R2 or Drive would
each have reintroduced it as a token or a share list.

```bash
gh release download handoff-v1 --repo Better-Behavior-Foundation/solar-soiling-ml -D /tmp/handoff
cd <your clone>
for t in /tmp/handoff/*.tar; do tar xf "$t" -C .; done   # or just your lane's group
make check-data          # zero MISSING in the blocks your lane needs
make verify-handoff      # sha256 every file against setup/handoff/MANIFEST.json
```

> ### ⚠ Known state as of 2026-08-31 evening — `make verify-handoff` fails, and it is not corruption
>
> Two gaps, both closed by one command once the PVDAQ fleet extraction finishes. Recorded here so
> whoever runs the check on day one does not have to work out whether the data is broken.
>
> **1. Three `aoi` files show a checksum mismatch.** `econ_summary.json`, `risk_econ.geojson` and
> `site_economics.csv` were regenerated on 2026-08-30 (they match the live dashboard, built the
> same day) while `MANIFEST.json` was hashed on 2026-08-26. **The disk is correct and the manifest
> is stale, not the other way round.** The released `handoff-aoi.tar` was rebuilt and re-uploaded
> on 2026-08-31, so the *asset* is now right; only the manifest still needs rehashing.
>
> **2. `pvdaq` is not in the manifest at all.** The tracked manifest covers
> `aoi, stage1-gate, stage1-train, stage2` — four of five groups. `handoff-pvdaq.tar` is 335 MB and
> **completely unverified**: `make verify-handoff` skips it silently rather than reporting it, so a
> green run does not mean the PVDAQ restore is sound. That group holds the fleet labels and the
> Open-Meteo quota that bought them, which is the most expensive thing here to re-earn.
>
> **The fix, after the fleet extraction finishes** (`pvdaq_fleet_extract.py --status` stops moving):
>
> ```bash
> # full rehash across all five groups -- a partial run refuses to write the tracked manifest,
> # deliberately, so this must be the full set
> PYTHONPATH=. python scripts/data/build_handoff_bundle.py --hash \
>     --groups stage1-gate,stage1-train,stage2,aoi,pvdaq --tar /tmp/handoff-build
>
> gh release upload handoff-v1 /tmp/handoff-build/handoff-pvdaq.tar --clobber \
>     --repo Better-Behavior-Foundation/solar-soiling-ml
>
> make verify-handoff        # expect: OK on all five groups
> git add setup/handoff/MANIFEST.json && git commit -m "data: rehash handoff manifest incl. pvdaq"
> ```
>
> Do not rehash while the extraction is running: `outputs/soiling/fleet` and the irradiance cache
> are in the `pvdaq` group and are being written, so the hashes would be obsolete within minutes.

Paths inside each tar are repo-relative, so extracting at the clone root puts every file where the
code already expects it. Pull only what your lane needs:

| asset | size | lane |
|---|---|---|
| `handoff-stage2.tar` | 2.4 MB | reproduce holdout AUC 0.7095; the Stage-2, paper and product lanes |
| `handoff-stage1-gate.tar` | 725 MB | reproduce test F1 0.826 |
| `handoff-stage1-train.tar` | 809 MB | retrain the detector |
| `handoff-aoi.tar` | 19 MB | the live 1,865-site Santa Cruz run and its economics |
| `handoff-pvdaq.tar` | 336 MB | the PVDAQ fleet labels **and the API quota that bought them** |

Start with `handoff-stage2.tar`. It is 2.4 MB and unlocks a reproducible gate number.

**`handoff-pvdaq.tar` is the one that cannot be re-earned by waiting.** Everything else in this
release is slow to rebuild; this one is *rate-limited*. The 225 cached irradiance cells inside it
are about three days of Open-Meteo free-tier quota, and that quota is a hard **daily** ceiling, so a
fresh clone does not re-fetch them in an afternoon -- it waits two to three days before it can fit a
single system. It also carries the 483 systems already fitted (429 non-degenerate labels), and the
fit is resumable, so a rerun skips every system in it. Added 2026-08-31; restore verified, not just
uploaded.

The bundle is defined **in code**, not in this table: `scripts/data/build_handoff_bundle.py` names
every file with the reason it is in or out, `--hash` regenerates the tracked manifest, `--tar`
rebuilds these assets, and `--verify` checks a restore. Verify a restore, not an upload.

**If this ever has to move to a bucket** (Drive, R2, S3) nothing about the bundle changes: it is
repo-relative paths plus a git-tracked sha256 manifest. Stage it with `--stage <dir>`, `rclone
copy` it up, and swap the download command above. R2 was the first choice and was dropped because
Cloudflare requires a payment method on file even inside the free tier.

---

## MUST BE PROVIDED — irreplaceable, 1.5 GB total

Losing any of these means losing a number this repo claims.

| Artifact | Size | Why it cannot be regenerated |
|---|---|---|
| `models/rfdetr_w2_20260807.pth` | 122 MB | **The single highest-value file in the project.** The 0.826 GA gate is unreproducible without it, and it exists only from a Colab training run. |
| `data/interim/scc21_labelset/images` | 603 MB | The 249 full 21cm tiles. **This is what `eval_tile_f1.py` reads** (`--tiles` default), not the chips. Re-fetchable from the county MapServer in principle; nobody has done it. |
| `data/interim/scc21_labelset/tile_index_21cm.json` | 136 KB | CRS + affine for the 21cm tiles. A **different file** from `data/interim/tile_index.json`, which is the 60cm index. |
| `data/interim/tile_index.json` | 104 KB | Sacred. Regenerable only by re-tiling the imagery. Never overwrite without reading. |
| `data/yolo/scc21/{images,labels,annotations,tile_labels,…}` | 689 MB | The relabel sprint output. Recoverable only from Roboflow, which needs account access that also expires with people. `tile_labels/` carries the tile-level GT (val 340 / test 585) the gate scores against. |
| `models/rfdetr_w1_20260806.pth` | 122 MB | Superseded, but it is the anchor in the paired W2-vs-W1 bootstrap. Without it that comparison cannot be re-run. |
| `outputs/soiling/training_matrix.parquet` | 136 KB | Nominally regenerable. Practically not: a rebuild hits the **Open-Meteo quota wall** (~600 homes/day). Treat as irreplaceable. |
| `runs/soiling/run_optionb/` | 373 KB | The reference Stage-2 run. `holdout_ci.py` reads its `feature_names.json`. |
| `runs/soiling/run_lossreg/` | 1.8 MB | The CQR-conformalised loss regressor that replaced the removed `RISK_TO_LOSS_PCT = 8.0`. |

## REGENERABLE — do not put these in the bucket

Each row is a command, not a request to a person.

| Artifact | Size | Regenerate with |
|---|---|---|
| `data/rfdetr/scc21` | 685 MB | `python scripts/data/build_rfdetr_dataset.py` (hardlinks from `data/yolo/scc21`; the COCO relayout RF-DETR wants) |
| `data/external/osm/` | 1.3 GB | Documented in `7edb710`. Cuts 3,362 Overpass calls to ~24. |
| `data/external/nrel_soiling_map_annual.csv` | 98 KB | `python scripts/predict/ingest_nrel_soiling_map.py` — but it is 98 KB, so it is in the bucket anyway |
| `data/external/static_features.csv` | 22 KB | `python scripts/predict/build_static_features.py` (~15 min, hits external services) — also small enough to ship |
| `data/external/pvdaq_2107/` | 38 MB | curl loop in the `scripts/analyze/washable_share_probe.py` docstring |
| `data/interim/scc_2025_6cm/` | 11 GB | `scripts/data/fetch_scc_imagery.py`. Not on any gate path. |
| `outputs/aoi/<partner>/` | ~1 GB | Re-run the pipeline from the bundle above |

## Never distributed

| Artifact | Why |
|---|---|
| `models/*.pt` (yolo11\*, R2, the SAHI baseline) | **AGPL-lineage.** Eval-only history. The permissive-stack migration exists to stop shipping these. One of them, `models/yolov8s_solar_array_v1.pt`, was tracked in git from before the ignore rule and was removed from `HEAD` on 2026-08-26; it remains in history. |
| `data/yolo/naip/` (60cm) | Retired label set. Its numbers are not comparable to anything current. |
| `.cache/soiling/merra2.sqlite` | 1.3 GB, and MERRA-2 was measured to **hurt** performance. Do not rebuild, do not ship. |
| Anything with addresses, owner names, or APNs | An address file leaked once and had to be history-scrubbed. See `SYNC.md` in the public repo. |

## Secrets

`.env` and `keys.yaml` — copy from `.env.example` / `keys.example.yaml`. Real values come from the
shared password-manager vault (Craig), **never Slack/email/git**. Keys: `SOLARSOILED_API_KEY`,
`NASA_EARTHDATA_TOKEN`, `LOB_API_KEY` (⚠️ use the **test** key — `mail_via_lob --send` mails real
postcards), Roboflow, and the R2 credential above. Deployed services (Render, Cloudflare Pages)
hold their own copies in platform env settings; the vault is only for the human handoff.
