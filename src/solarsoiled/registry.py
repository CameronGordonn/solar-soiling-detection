"""Model registry — semantic names → weight paths + manifest metadata.

The CLI's ``--weights`` flag (and the Stage 2 ``--soiling-model`` later) goes
through :func:`resolve` so partner runs can say ``--weights stage1-v0.6``
instead of pinning a checkpoint path that breaks the next time we retrain.

Resolution rules:
- Registered name or alias → return the catalog entry, with the path resolved
  relative to the repo root.
- Existing filesystem path → passthrough with ``model_version="ad-hoc:<sha>"``.
  Partners can still point at an arbitrary ``.pt`` for one-off evaluations
  without first editing the registry.
- Anything else → :class:`RegistryError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from solarsoiled.manifest import _sha256_file
from solarsoiled.paths import REPO_ROOT


DEFAULT_REGISTRY_PATH = REPO_ROOT / "models" / "registry.yaml"


class RegistryError(ValueError):
    """Raised when a name doesn't resolve to a registered model or a real file."""


@dataclass(frozen=True)
class ResolvedWeights:
    """A resolved ``--weights`` argument with the metadata needed for manifests."""

    model_version: str
    path: Path
    stage: str
    beta: bool
    metrics: Mapping[str, float] = field(default_factory=dict)
    known_limitations: tuple[str, ...] = ()
    source: str = "registered"  # "registered" | "alias" | "ad-hoc"
    # Which inference backend the checkpoint needs. "rf-detr" routes to
    # scripts/detect/rfdetr_infer.py; anything else keeps the ultralytics path.
    detector: str | None = None
    # The operating point the model was GATED at, carried so callers cannot
    # silently run it somewhere else. W2's conf was tuned on val and frozen
    # (see .claude/rules/stage1-detect.md); the CLI's own default of 0.40 is a
    # YOLO-era number and would put an RF-DETR checkpoint off its gated point.
    conf: float | None = None
    iou_nms: float | None = None
    # Ground sampling distance the model was trained at. rfdetr_infer.py needs
    # this per-tile from the tile index; here it is the training-time value, so
    # a caller can detect a GSD mismatch instead of discovering it in the areas.
    gsd_ground_m: float | None = None
    # SPDX-ish licence string from the registry entry. Carried because `production`
    # is served by default from api.py, and an AGPL checkpoint behind that alias
    # means the default path distributes the asset the permissive-stack migration
    # exists to avoid. Asserted in tests/test_registry.py. None = undeclared, which
    # for a shipping checkpoint should itself be treated as a problem.
    license: str | None = None


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load and validate ``registry.yaml``.

    Returns the raw dict with ``models`` and ``aliases`` keys. Aliases that
    don't point at a known model are rejected at load time so partners get
    a clear error early.
    """
    registry_path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    if not registry_path.is_file():
        raise RegistryError(f"registry not found at {registry_path}")

    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    models = raw.get("models") or {}
    aliases = raw.get("aliases") or {}

    if not isinstance(models, dict):
        raise RegistryError(f"registry 'models' must be a mapping, got {type(models).__name__}")
    if not isinstance(aliases, dict):
        raise RegistryError(f"registry 'aliases' must be a mapping, got {type(aliases).__name__}")

    for alias, target in aliases.items():
        if target not in models:
            raise RegistryError(f"alias {alias!r} points at unknown model {target!r}")

    return {"models": models, "aliases": aliases, "schema_version": raw.get("schema_version")}


_METRIC_KEYS = {"auc", "f1", "cv_auc", "holdout_auc", "holdout_year"}


def _entry_to_resolved(
    name: str,
    entry: Mapping[str, Any],
    *,
    source: str,
    require_file: bool = True,
) -> ResolvedWeights:
    raw_path = entry.get("path")
    if not raw_path:
        raise RegistryError(f"registry entry {name!r} missing 'path'")
    weights_path = Path(raw_path)
    if not weights_path.is_absolute():
        weights_path = (REPO_ROOT / weights_path).resolve()
    if require_file and not weights_path.is_file():
        raise RegistryError(
            f"registry entry {name!r} points at missing file {weights_path}"
        )

    metrics: dict[str, float] = {}
    for key, value in entry.items():
        if key.startswith("map50") or key in _METRIC_KEYS:
            try:
                metrics[key] = float(value)
            except (TypeError, ValueError):
                continue

    def _opt_float(key: str) -> float | None:
        try:
            return float(entry[key])
        except (KeyError, TypeError, ValueError):
            return None

    return ResolvedWeights(
        model_version=name,
        path=weights_path,
        stage=str(entry.get("stage", "stage1")),
        beta=bool(entry.get("beta", True)),
        metrics=metrics,
        known_limitations=tuple(entry.get("known_limitations") or ()),
        source=source,
        detector=(str(entry["detector"]) if entry.get("detector") else None),
        conf=_opt_float("conf"),
        iou_nms=_opt_float("iou_nms"),
        gsd_ground_m=_opt_float("gsd_ground_m"),
        license=(str(entry["license"]) if entry.get("license") else None),
    )


def resolve(
    name_or_path: str | Path,
    *,
    registry_path: Path | None = None,
) -> ResolvedWeights:
    """Resolve a CLI ``--weights`` value to a :class:`ResolvedWeights`.

    Order:
    1. Registered model name → registered metadata.
    2. Alias → metadata of the aliased model.
    3. Existing filesystem path → ad-hoc passthrough.
    4. Otherwise raise :class:`RegistryError`.
    """
    text = str(name_or_path)

    fs_candidate = Path(text)
    if fs_candidate.is_file():
        # Suffix is the only signal available off-registry: RF-DETR checkpoints are
        # .pth, ultralytics are .pt. Registered entries carry an explicit `detector`
        # and never rely on this.
        adhoc_detector = "rf-detr" if fs_candidate.suffix == ".pth" else None
        return ResolvedWeights(
            model_version=f"ad-hoc:{_sha256_file(fs_candidate)[:12]}",
            path=fs_candidate.resolve(),
            stage="ad-hoc",
            beta=True,
            metrics={},
            known_limitations=(
                "Ad-hoc weights; no registry metadata.",
                "No gated operating point — conf/iou fall back to CLI defaults.",
            ),
            source="ad-hoc",
            detector=adhoc_detector,
        )

    registry = load_registry(registry_path)
    models = registry["models"]
    aliases = registry["aliases"]

    if text in models:
        # The repo registry carries metadata for shipping checks even when the
        # checkpoint itself is not present in CI. Custom registries still enforce
        # file existence so unit tests can verify the hard-fail path.
        return _entry_to_resolved(
            text,
            models[text],
            source="registered",
            require_file=registry_path is not None,
        )

    if text in aliases:
        target = aliases[text]
        return _entry_to_resolved(
            target,
            models[target],
            source="alias",
            require_file=registry_path is not None,
        )

    raise RegistryError(
        f"{text!r} is not a registered model, alias, or existing file. "
        f"Known models: {sorted(models)}; aliases: {sorted(aliases)}"
    )


def resolve_soiling(
    name_or_path: str | Path,
    *,
    registry_path: Path | None = None,
) -> ResolvedWeights:
    """Resolve a ``--soiling-model`` value to a :class:`ResolvedWeights`.

    Resolution order:
    1. Registered soiling model name → registry metadata.
    2. Soiling alias → metadata of the aliased model.
    3. Existing filesystem path to a ``.ubj`` → ad-hoc passthrough.
    4. Otherwise raise :class:`RegistryError`.
    """
    text = str(name_or_path)

    fs_candidate = Path(text)
    if fs_candidate.is_file():
        return ResolvedWeights(
            model_version=f"ad-hoc:{_sha256_file(fs_candidate)[:12]}",
            path=fs_candidate.resolve(),
            stage="stage2",
            beta=True,
            metrics={},
            known_limitations=("Ad-hoc soiling weights; no registry metadata.",),
            source="ad-hoc",
        )

    registry_path_resolved = Path(registry_path) if registry_path is not None else DEFAULT_REGISTRY_PATH
    raw = yaml.safe_load(registry_path_resolved.read_text(encoding="utf-8")) or {}
    soiling_models = raw.get("soiling_models") or {}
    soiling_aliases = raw.get("soiling_aliases") or {}

    if not isinstance(soiling_models, dict):
        raise RegistryError(f"registry 'soiling_models' must be a mapping")

    if text in soiling_models:
        return _entry_to_resolved(text, soiling_models[text], source="registered")

    if text in soiling_aliases:
        target = soiling_aliases[text]
        if target not in soiling_models:
            raise RegistryError(f"soiling alias {text!r} points at unknown model {target!r}")
        return _entry_to_resolved(target, soiling_models[target], source="alias")

    raise RegistryError(
        f"{text!r} is not a registered soiling model, soiling alias, or existing file. "
        f"Known soiling models: {sorted(soiling_models)}; aliases: {sorted(soiling_aliases)}"
    )
