"""solarsoiled — single CLI for the Stage 1 / Stage 2 / recommend pipeline.

Wraps the research scripts in ``scripts/`` as library calls. Scripts keep
working standalone for research; this CLI is the canonical entrypoint for
partner-facing AOI runs.

Run ``solarsoiled --help`` for usage.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import typer

from solarsoiled.aoi import Aoi, parse_aoi, write_aoi_geojson
from solarsoiled.manifest import write_manifest
from solarsoiled.paths import AoiPaths, REPO_ROOT
from solarsoiled.recommend import (
    recommend_cleaning,
    recommend_per_array,
    write_array_recommendations,
    write_recommendation,
)
from solarsoiled.registry import RegistryError, ResolvedWeights, resolve as resolve_weights, resolve_soiling

# Ensure repo root is on sys.path so scripts.* and src.* imports resolve.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.tile_naip_image import main as _tile_main  # noqa: E402
from scripts.data.fetch_scc_imagery import main as _fetch_scc_raw  # noqa: E402
from scripts.detect.infer import main as _infer_main  # noqa: E402
from scripts.detect.rfdetr_infer import main as _rfdetr_infer_main  # noqa: E402
from scripts.detect.evaluate import main as _eval_main  # noqa: E402
from scripts.detect.eval_threshold_sweep import main as _eval_sweep_main  # noqa: E402
from scripts.detect.per_detection_rca import main as _rca_main  # noqa: E402
from scripts.detect.sahi_threshold_sweep import main as _sahi_sweep_main  # noqa: E402
from scripts.detect.export_polygons_geojson import export_polygons as _export_polygons  # noqa: E402
from scripts.analyze.extract_array_features import extract_features as _extract_features  # noqa: E402
from scripts.analyze.build_risk_features import main as _build_features_main  # noqa: E402
from scripts.predict.predict_risk import main as _predict_risk_main  # noqa: E402
from scripts.labeling.bucket_overlays import main as _bucket_overlays_main  # noqa: E402


app = typer.Typer(
    add_completion=False,
    help="SolarSoiled — detect arrays, score soiling risk, recommend cleaning.",
    no_args_is_help=True,
)


def _aoi_centroid_wgs84(aoi: Aoi) -> tuple[float, float]:
    """Return ``(lat, lon)`` WGS84 centroid for an AOI."""
    c = aoi.polygon.centroid
    return (float(c.y), float(c.x))


def _resolve_aoi(spec: str, partner_id: str | None) -> tuple[Aoi, AoiPaths]:
    aoi = parse_aoi(spec, partner_id=partner_id)
    paths = AoiPaths(aoi.aoi_id)
    paths.ensure_root()
    write_aoi_geojson(aoi, paths.aoi_geojson)
    return aoi, paths


def _resolve_weights(spec: str) -> ResolvedWeights:
    try:
        return resolve_weights(spec)
    except RegistryError as exc:
        raise typer.BadParameter(str(exc), param_hint="--weights") from exc


def _resolve_soiling_model(spec: str) -> ResolvedWeights:
    try:
        return resolve_soiling(spec)
    except RegistryError as exc:
        raise typer.BadParameter(str(exc), param_hint="--soiling-model") from exc


#: The county's 2025 orthoimagery service. Santa Cruz only -- this is the layer that makes
#: 21cm possible, and the reason 21cm is not a nationwide capability.
SCC_IMAGERY_SERVICE = (
    "https://sccgis.santacruzcountyca.gov/server/rest/services/Cache/Imagery_2025/MapServer/export"
)


def _fetch_scc_main(argv: list[str]) -> int:
    """Call the SCC fetcher, translating its 'incomplete' SystemExit into a return code.

    The fetcher exits 1 when tiles are still missing so a shell loop can re-run it. Inside the
    CLI that must not kill the process -- a partial AOI still has a consistent tile_index and
    the user needs the message, not a traceback.
    """
    try:
        _fetch_scc_raw(argv)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)


#: How far a tile's ground GSD may drift from the model's training GSD before we refuse.
#: 0.25 admits the 0.2043 vs 0.2044 rounding between the two 21cm indices while still
#: rejecting the case this exists for: 60cm NAIP tiles (0.48 m) fed to a 21cm model, a
#: 2.3x mismatch that produces a quiet accuracy regression rather than an error.
GSD_TOLERANCE = 0.25


def _assert_gsd_compatible(resolved: ResolvedWeights, tile_index_path: Path) -> None:
    """Refuse to run a model on imagery at a resolution it was not trained for.

    W5 audit item 1: `tile` fetches 60cm NAIP and W2 has never seen 60cm, so pointing the
    two at each other is the most likely way to ship a silent regression. Routing alone does
    not prevent it -- someone can always tile NAIP and then detect with a 21cm checkpoint.
    This makes that combination an error wherever it comes from.
    """
    if resolved.gsd_ground_m is None:
        return  # ad-hoc weights or an unannotated entry: nothing to compare against
    try:
        raw = json.loads(Path(tile_index_path).read_text())
    except (OSError, ValueError):
        return
    tiles = raw.get("tiles", raw)
    gsds = [t["gsd_ground_m"] for t in tiles.values()
            if isinstance(t, dict) and t.get("gsd_ground_m") is not None]
    if not gsds:
        return  # rfdetr_infer's own pre-flight raises on this; not this guard's job
    median_gsd = sorted(gsds)[len(gsds) // 2]
    drift = abs(median_gsd - resolved.gsd_ground_m) / resolved.gsd_ground_m
    if drift > GSD_TOLERANCE:
        raise typer.BadParameter(
            f"{resolved.model_version} was trained at {resolved.gsd_ground_m:.4f} m/px ground but "
            f"these tiles are {median_gsd:.4f} m/px ({drift:.0%} off). Detection quality is not "
            f"defined at this resolution and the failure would be a quiet accuracy loss, not a "
            f"crash. Re-tile at the model's resolution "
            f"(`solarsoiled tile --imagery scc21` for the 21cm county service), or pick a model "
            f"trained for this imagery.",
            param_hint="--weights",
        )


# ---------- subcommands ----------


@app.command()
def tile(
    aoi: str = typer.Option(..., "--aoi", help="bbox 'minx,miny,maxx,maxy' OR path to GeoJSON polygon"),
    partner_id: str | None = typer.Option(None, "--partner-id", help="Override AOI directory name"),
    download: bool = typer.Option(False, "--download", help="Pass AOI to scripts/data/tile_naip_image.py for GeoAI download"),
    imagery: str = typer.Option("naip", "--imagery", help="'naip' (~48cm, nationwide) or 'scc21' (Santa Cruz County 2025 service, 21cm)"),
    gsd: float | None = typer.Option(None, "--gsd", help="Ground m/px for --imagery scc21. Default 0.208 (what scc21 models were trained at)."),
    tile_px: int = typer.Option(1200, "--tile-px", help="Tile size in px for --imagery scc21 (matches scc21 training framing)"),
) -> None:
    """Tile imagery for an AOI into PNGs + tile_index.json.

    `naip` is nationwide but ~48cm; `scc21` is the 21cm county service and is Santa Cruz only.
    A 21cm-trained model needs `scc21` — `detect` refuses the mismatch rather than quietly
    regressing (W5 audit item 1).
    """
    aoi_obj, paths = _resolve_aoi(aoi, partner_id)
    if imagery == "scc21":
        rc = _fetch_scc_main([
            f"--aoi={aoi}",
            "--service-url", SCC_IMAGERY_SERVICE,
            "--out-dir", str(paths.tiles_dir),
            "--out-index", str(paths.tile_index),
            "--native-gsd", str(gsd if gsd is not None else 0.208),
            "--tile-px", str(tile_px),
        ])
        if rc:
            raise typer.Exit(rc)
    elif imagery == "naip":
        download_arg = aoi if download else None
        _tile_main(
            download_aoi=download_arg,
            out_tiles_dir=paths.tiles_dir,
            out_tile_index=paths.tile_index,
        )
    else:
        raise typer.BadParameter(f"unknown imagery source {imagery!r}", param_hint="--imagery")
    typer.echo(f"tile → {paths.tiles_dir} ({imagery})")


@app.command()
def detect(
    aoi: str = typer.Option(..., "--aoi"),
    weights: str = typer.Option(
        ...,
        "--weights",
        help="Registered model name/alias (e.g. 'production', 'stage1-v0.5-baseline') or filesystem path to a .pt",
    ),
    partner_id: str | None = typer.Option(None, "--partner-id"),
    conf: float | None = typer.Option(None, "--conf", help="Override the model's gated conf. Default: the registry's frozen value, else 0.40."),
    iou: float | None = typer.Option(None, "--iou", help="Override the model's NMS IoU. Default: the registry's frozen value, else 0.50."),
    sahi: bool = typer.Option(False, "--sahi/--no-sahi", help="Add a SAHI supplementary pass after whole-tile inference to recover small panels (ultralytics path only)"),
) -> None:
    """Run detection over an AOI's tiles → arrays.geojson.

    Routes to RF-DETR (+SAM2) or ultralytics based on the resolved model's `detector`.
    """
    resolved = _resolve_weights(weights)
    aoi_obj, paths = _resolve_aoi(aoi, partner_id)
    if not paths.tile_index.is_file():
        raise typer.BadParameter(
            f"missing {paths.tile_index} — run `solarsoiled tile --aoi …` first"
        )

    # A registered checkpoint's conf/iou are its GATED operating point -- conf tuned on
    # val and frozen, test never consulted (.claude/rules/stage1-detect.md). Defaulting to
    # the CLI's own 0.40/0.50 would run W2 at a threshold it was never gated at, which is
    # how a shipped model quietly stops matching its published F1. Explicit flags still win.
    eff_conf = conf if conf is not None else (resolved.conf if resolved.conf is not None else 0.40)
    eff_iou = iou if iou is not None else (resolved.iou_nms if resolved.iou_nms is not None else 0.50)
    if conf is None and resolved.conf is not None:
        typer.echo(f"conf={eff_conf} iou={eff_iou} (gated operating point from registry)")

    _assert_gsd_compatible(resolved, paths.tile_index)

    is_rfdetr = resolved.detector == "rf-detr"
    if is_rfdetr:
        if sahi:
            raise typer.BadParameter(
                "--sahi is an ultralytics-path flag; the RF-DETR path already tiles each "
                "tile into a 2x2 640px chip grid and merges across seams by NMS.",
                param_hint="--sahi",
            )
        infer_argv = [
            "--weights", str(resolved.path),
            "--source", str(paths.tiles_dir),
            "--tile-index", str(paths.tile_index),
            "--project", str(paths.root),
            "--name", "detect",
            "--conf", str(eff_conf),
            "--iou", str(eff_iou),
            # A full AOI is many hours of CPU with SAM2; always resume rather than restart.
            # Labels are written atomically, so this cannot pick up a partial tile.
            "--resume",
        ]
        rc = _rfdetr_infer_main(infer_argv)
        if rc:
            raise typer.Exit(rc)
    else:
        infer_argv = [
            "--weights", str(resolved.path),
            "--source", str(paths.tiles_dir),
            "--project", str(paths.root),
            "--name", "detect",
            "--conf", str(eff_conf),
            "--iou", str(eff_iou),
        ]
        if sahi:
            infer_argv.append("--sahi")
        _infer_main(infer_argv)

    _export_polygons(
        labels_dir=paths.detect_labels_dir,
        tile_index_path=paths.tile_index,
        output_geojson=paths.arrays_geojson,
    )

    # Overlay the detect manifest with full registry metadata so `model_version`
    # is the semantic name (e.g. 'stage1-v0.5-baseline') rather than the
    # script-derived run tag, and the catalog metrics + limitations propagate.
    tile_inputs = sorted(str(p) for p in paths.tiles_dir.glob("*.png"))
    write_manifest(
        paths.detect_dir,
        stage="stage1_detect",
        model_version=resolved.model_version,
        model_weights=resolved.path,
        inputs=tile_inputs,
        beta=resolved.beta,
        metrics={
            **dict(resolved.metrics),
            "conf": eff_conf,
            "iou": eff_iou,
            "sahi_supplementary": int(sahi),
            "n_tiles": len(tile_inputs),
        },
        known_limitations=list(resolved.known_limitations) or None,
        # This manifest overwrites the one rfdetr_infer.py just wrote, so carry the
        # backend forward -- otherwise the artifact dir loses all record of whether
        # SAM2 produced the polygons or they are raw detector boxes.
        extra={
            "weights_source": resolved.source,
            "detector": resolved.detector or "ultralytics",
            "mask_stage": "sam2-hiera-large" if is_rfdetr else None,
            "operating_point": "registry-frozen" if (conf is None and resolved.conf is not None) else "cli-override-or-default",
        },
    )

    typer.echo(f"detect → {paths.arrays_geojson} ({resolved.model_version})")


@app.command()
def score(
    aoi: str = typer.Option(..., "--aoi"),
    soiling_model: str = typer.Option(..., "--soiling-model", help="Registered name/alias (e.g. 'soiling_production') or path to model.ubj"),
    partner_id: str | None = typer.Option(None, "--partner-id"),
    region_config: Path = typer.Option(REPO_ROOT / "configs" / "soiling" / "california.yaml", "--region-config"),
    features_config: Path = typer.Option(REPO_ROOT / "configs" / "soiling" / "features.yaml", "--features-config"),
    as_of: str | None = typer.Option(None, "--as-of", help="YYYY-MM-DD; default = today UTC"),
) -> None:
    """Extract array features → soiling features → risk scores. Writes risk.geojson."""
    resolved_soiling = _resolve_soiling_model(soiling_model)
    aoi_obj, paths = _resolve_aoi(aoi, partner_id)
    if not paths.arrays_geojson.is_file():
        raise typer.BadParameter(
            f"missing {paths.arrays_geojson} — run `solarsoiled detect --aoi …` first"
        )
    paths.features_dir.mkdir(parents=True, exist_ok=True)

    _extract_features(
        input_geojson=paths.arrays_geojson,
        out_table=paths.array_features_parquet,
        out_geo=paths.array_features_geo_parquet,
    )

    build_argv = [
        "--config", str(region_config),
        "--features-config", str(features_config),
        "--arrays", str(paths.array_features_geo_parquet),
        "--out", str(paths.inference_matrix),
    ]
    if as_of:
        build_argv += ["--as-of", as_of]
    _build_features_main(build_argv)

    _predict_risk_main([
        "--model", str(resolved_soiling.path),
        "--features", str(paths.inference_matrix),
        "--arrays", str(paths.array_features_geo_parquet),
        "--out", str(paths.risk_geojson),
    ])
    typer.echo(f"score → {paths.risk_geojson}")


@app.command()
def recommend(
    aoi: str = typer.Option(..., "--aoi"),
    last_cleaned: str = typer.Option(..., "--last-cleaned", help="YYYY-MM-DD"),
    partner_id: str | None = typer.Option(None, "--partner-id"),
    risk_threshold: float = typer.Option(0.6, "--risk-threshold"),
    rain_mm_threshold: float = typer.Option(5.0, "--rain-mm-threshold"),
    min_days_since_clean: int = typer.Option(30, "--min-days-since-clean"),
) -> None:
    """Apply v1 cleaning rule → recommendations.json."""
    aoi_obj, paths = _resolve_aoi(aoi, partner_id)
    if not paths.risk_geojson.is_file():
        raise typer.BadParameter(
            f"missing {paths.risk_geojson} — run `solarsoiled score --aoi …` first"
        )
    payload = recommend_cleaning(
        risk_geojson=paths.risk_geojson,
        last_cleaned=date.fromisoformat(last_cleaned),
        aoi_centroid=_aoi_centroid_wgs84(aoi_obj),
        risk_threshold=risk_threshold,
        rain_mm_threshold=rain_mm_threshold,
        min_days_since_clean=min_days_since_clean,
    )
    write_recommendation(paths.recommendations_json, payload, upstream_manifest=paths.root / "manifest.json")
    typer.echo(f"recommend → {paths.recommendations_json}")

    array_rows = recommend_per_array(
        paths.risk_geojson,
        payload,
        risk_threshold=risk_threshold,
    )
    write_array_recommendations(paths.array_recommendations_json, array_rows)
    n_actionable = sum(1 for r in array_rows if r["action"] == "clean")
    typer.echo(f"  array_recommendations → {paths.array_recommendations_json} ({n_actionable}/{len(array_rows)} arrays to clean)")

    typer.echo(json.dumps({"rule_fired": payload["rule_fired"], "confidence": payload["confidence"]}))


@app.command()
def run(
    aoi: str = typer.Option(..., "--aoi"),
    weights: str = typer.Option(
        ...,
        "--weights",
        help="Registered model name/alias or filesystem path to a .pt",
    ),
    soiling_model: str = typer.Option(..., "--soiling-model", help="Registered name/alias or path to model.ubj"),
    last_cleaned: str = typer.Option(..., "--last-cleaned"),
    partner_id: str | None = typer.Option(None, "--partner-id"),
    skip_tile: bool = typer.Option(False, "--skip-tile"),
    skip_detect: bool = typer.Option(False, "--skip-detect"),
    skip_score: bool = typer.Option(False, "--skip-score"),
    skip_recommend: bool = typer.Option(False, "--skip-recommend"),
    download: bool = typer.Option(False, "--download"),
    as_of: str | None = typer.Option(
        None, "--as-of",
        help="Date for weather/air-quality lookback, YYYY-MM-DD. Default: today."
             " Use a past date if MERRA-2 data for the current date isn't yet available.",
    ),
) -> None:
    """Chain tile → detect → score → recommend, fail-fast."""
    aoi_obj, paths = _resolve_aoi(aoi, partner_id)
    # Resolve once up-front so we fail fast on a bad name and so the rollup
    # manifest at the bottom can record the resolved version even when
    # --skip-detect was set.
    resolved_weights = _resolve_weights(weights)
    started = datetime.now(timezone.utc).isoformat()

    if not skip_tile:
        # Pick the imagery the MODEL needs, rather than always NAIP. A 21cm-annotated
        # checkpoint (gsd_ground_m well below NAIP's ~0.48) gets county tiles; everything else
        # keeps the nationwide NAIP path. `detect`'s GSD guard is the backstop if this is wrong.
        model_gsd = resolved_weights.gsd_ground_m
        imagery = "scc21" if (model_gsd is not None and model_gsd < 0.35) else "naip"
        tile(aoi=aoi, partner_id=paths.aoi_id, download=download,
             imagery=imagery, gsd=model_gsd if imagery == "scc21" else None, tile_px=1200)
    elif not paths.tile_index.is_file():
        raise typer.BadParameter(f"--skip-tile but {paths.tile_index} is missing")

    if not skip_detect:
        # conf/iou stay None so the model's own gated operating point applies. Hardcoding
        # 0.40/0.50 here silently pinned every end-to-end run to the YOLO-era threshold,
        # which for an RF-DETR checkpoint is not the point its F1 was measured at.
        detect(aoi=aoi, weights=weights, partner_id=paths.aoi_id, sahi=False, conf=None, iou=None)
    elif not paths.arrays_geojson.is_file():
        raise typer.BadParameter(f"--skip-detect but {paths.arrays_geojson} is missing")

    if not skip_score:
        score(
            aoi=aoi, soiling_model=soiling_model, partner_id=paths.aoi_id,
            region_config=REPO_ROOT / "configs" / "soiling" / "california.yaml",
            features_config=REPO_ROOT / "configs" / "soiling" / "features.yaml",
            as_of=as_of,
        )
    elif not paths.risk_geojson.is_file():
        raise typer.BadParameter(f"--skip-score but {paths.risk_geojson} is missing")

    if not skip_recommend:
        recommend(
            aoi=aoi,
            last_cleaned=last_cleaned,
            partner_id=paths.aoi_id,
            risk_threshold=0.6,
            rain_mm_threshold=5.0,
            min_days_since_clean=30,
        )

    # AOI-level rollup manifest indexes each stage's manifest.
    stage_manifests = [
        paths.tiles_dir / "manifest.json",
        paths.detect_dir / "manifest.json",
        paths.root / "manifest.json",
        paths.features_dir / "manifest.json",
        paths.recommendations_json.parent / "manifest.recommend.json",
    ]
    write_manifest(
        paths.root,
        stage="recommend",
        model_version="solarsoiled-run-v1",
        model_weights=None,
        inputs=[str(p) for p in stage_manifests if p.exists()],
        beta=True,
        metrics={
            "skipped_tile": int(skip_tile),
            "skipped_detect": int(skip_detect),
            "skipped_score": int(skip_score),
            "skipped_recommend": int(skip_recommend),
        },
        known_limitations=[
            "Detection below 0.70 mAP50 GA bar",
            "Soiling AUC below 0.70 GA bar",
            "Recommend engine v1 is rule-based; expected_recovery_pct is a placeholder",
        ],
        extra={
            "started_at": started,
            "aoi_root": str(paths.root),
            "stage1_model_version": resolved_weights.model_version,
            "stage1_weights_source": resolved_weights.source,
        },
    )
    typer.echo(f"run → {paths.root}")


def _run_full_eval_pipeline(
    resolved,
    *,
    data: Path | None,
    run_name: str | None,
    report_out: Path | None,
) -> None:
    """Chain per_detection_rca → summarize → sahi_threshold_sweep → bucket_overlays → build_report."""
    weights_str = str(resolved.path)
    data_args = ["--data", str(data)] if data else []

    # Derive run_name from weights path the same way per_detection_rca does when
    # --run-name is omitted, so all artifacts land in the same directory.
    if run_name is None:
        wp = resolved.path
        run_name = wp.parent.parent.name if wp.parent.name == "weights" else wp.stem

    typer.echo(f"[1/5] per_detection_rca (SAHI, conf=0.05, splits=val test) → outputs/eval/{run_name}/")
    rc = _rca_main(
        ["--weights", weights_str, "--sahi", "--conf", "0.05", "--iou", "0.5",
         "--splits", "val", "test", "--run-name", run_name] + data_args
    ) or 0
    if rc:
        raise typer.Exit(code=rc)

    csv_path = REPO_ROOT / "outputs" / "eval" / run_name / "per_detection.csv"

    typer.echo("[2/5] per_detection_rca --summarize → failure_modes.json")
    rc = _rca_main(["--summarize", "--csv", str(csv_path)]) or 0
    if rc:
        raise typer.Exit(code=rc)

    typer.echo("[3/5] sahi_threshold_sweep → sahi_threshold_sweep.csv")
    rc = _sahi_sweep_main(
        ["--weights", weights_str, "--run-name", run_name] + data_args
    ) or 0
    if rc:
        raise typer.Exit(code=rc)

    typer.echo("[4/5] bucket_overlays (confident_fp + worst_small_fn)")
    for bucket in ("confident_fp", "worst_small_fn"):
        rc = _bucket_overlays_main(
            ["--csv", str(csv_path), "--bucket", bucket, "--top", "20"] + data_args
        ) or 0
        if rc:
            raise typer.Exit(code=rc)

    typer.echo("[5/5] Building HTML report")
    from solarsoiled.eval_report import build_report
    eval_dir = REPO_ROOT / "outputs" / "eval" / run_name
    out = build_report(eval_dir, report_out, weights_resolved=resolved)
    typer.echo(f"report → {out}")


@app.command()
def eval(
    weights: str = typer.Option(
        ...,
        "--weights",
        help="Registered model name/alias or filesystem path to a .pt",
    ),
    data: Path | None = typer.Option(None, "--data", help="data.yaml path; defaults handled by script"),
    split: str = typer.Option("val", "--split"),
    threshold_sweep: bool = typer.Option(False, "--threshold-sweep", help="Run eval_threshold_sweep instead of evaluate"),
    metrics_json: Path | None = typer.Option(None, "--metrics-json"),
    report: bool = typer.Option(False, "--report", help="Build single-file HTML quality report from existing eval artifacts in --report-dir."),
    report_dir: Path | None = typer.Option(None, "--report-dir", help="outputs/eval/<run-name>/ to ingest. Defaults to most recent under outputs/eval/."),
    report_out: Path | None = typer.Option(None, "--report-out", help="HTML output path. Defaults to <report-dir>/report.html."),
    full: bool = typer.Option(False, "--full", help="Run the full eval pipeline (RCA → SAHI sweep → overlays → HTML report). Slow — runs inference."),
    run_name: str | None = typer.Option(None, "--run-name", help="Artifact directory name under outputs/eval/. Derived from weights if omitted."),
) -> None:
    """Evaluate Stage 1 weights or run a threshold sweep."""
    resolved = _resolve_weights(weights)
    typer.echo(f"eval → resolved weights: {resolved.model_version} ({resolved.path})")

    if full:
        _run_full_eval_pipeline(resolved, data=data, run_name=run_name, report_out=report_out)
        return

    if report:
        from solarsoiled.eval_report import build_report
        out = build_report(report_dir, report_out, weights_resolved=resolved)
        typer.echo(f"report → {out}")
        return

    if threshold_sweep:
        argv = ["--weights", str(resolved.path)]
        if data:
            argv += ["--data", str(data)]
        rc = _eval_sweep_main(argv) or 0
        if rc:
            raise typer.Exit(code=rc)
    else:
        argv = ["--weights", str(resolved.path), "--split", split]
        if data:
            argv += ["--data", str(data)]
        if metrics_json:
            argv += ["--metrics-json", str(metrics_json)]
        _eval_main(argv)


@app.command()
def viz(
    aoi: str = typer.Option(..., "--aoi", help="bbox 'minx,miny,maxx,maxy' OR path to GeoJSON polygon"),
    partner_id: str | None = typer.Option(None, "--partner-id", help="Override AOI directory name"),
    basemap: str = typer.Option("satellite", "--basemap", help="Basemap style: satellite | osm | topo"),
    out: Path | None = typer.Option(None, "--out", help="Override output HTML path"),
) -> None:
    """Render an interactive soiling-risk map from risk.geojson → risk_map.html."""
    from solarsoiled.viz import build_risk_map

    _, paths = _resolve_aoi(aoi, partner_id)

    if not paths.risk_geojson.exists():
        typer.echo(
            f"risk.geojson not found at {paths.risk_geojson}. "
            "Run `solarsoiled score` first.",
            err=True,
        )
        raise typer.Exit(code=1)

    out_path = out or (paths.root / "risk_map.html")
    rec_json = paths.recommendations_json if paths.recommendations_json.exists() else None
    arr_rec_json = paths.array_recommendations_json if paths.array_recommendations_json.exists() else None

    build_risk_map(
        paths.risk_geojson,
        out_path,
        recommendations_json=rec_json,
        array_recommendations_json=arr_rec_json,
        basemap=basemap,
    )

    write_manifest(
        paths.root,
        stage="eval",
        model_version="viz",
        inputs=[str(paths.risk_geojson)],
        metrics={},
        known_limitations=[],
        filename="manifest.viz.json",
    )
    typer.echo(f"viz → {out_path}")


if __name__ == "__main__":
    app()
