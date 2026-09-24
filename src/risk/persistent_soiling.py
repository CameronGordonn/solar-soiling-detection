"""Uncalibrated per-array screen for persistent soiling investigation.

This is deliberately separate from the NREL-trained recoverable-soiling model.
NREL IWSR labels do not identify persistent contamination at individual roofs, so
the output is an inspection-priority score, not a predicted loss percentage.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.risk.tilt_response import tilt_soiling_factor

# These are a product-policy default, not fitted coefficients.  No dataset yet
# links per-array canopy and tilt to persistent-soiling outcomes.
TILT_WEIGHT = 0.70
CANOPY_WEIGHT = 0.30
CANOPY_SEARCH_RADIUS_M = 9.0
TILT_ZERO_SCORE_DEG = 35.0


def low_tilt_score(tilt_deg: float | None) -> float:
    """Return a 0-1 low-tilt exposure score from the measured Cano curve.

    Cano measured a steep decline in dust accumulation from horizontal to roughly
    20-25 degrees, followed by a much flatter response.  Normalising that curve
    between 0 and 35 degrees preserves the measured shape without pretending it
    is a persistent-loss calibration.
    """
    if tilt_deg is None or not math.isfinite(float(tilt_deg)):
        return 0.0
    flat = tilt_soiling_factor(0.0)
    high_tilt = tilt_soiling_factor(TILT_ZERO_SCORE_DEG)
    observed = tilt_soiling_factor(float(tilt_deg))
    return float(np.clip((observed - high_tilt) / (flat - high_tilt), 0.0, 1.0))


def canopy_exposure_score(
    canopy_fraction: float | None,
    nearest_canopy_m: float | None,
    *,
    radius_m: float = CANOPY_SEARCH_RADIUS_M,
) -> float:
    """Combine nearby canopy amount and proximity into a bounded exposure score."""
    fraction = 0.0
    if canopy_fraction is not None and math.isfinite(float(canopy_fraction)):
        fraction = float(np.clip(float(canopy_fraction), 0.0, 1.0))
    proximity = 0.0
    if nearest_canopy_m is not None and math.isfinite(float(nearest_canopy_m)):
        proximity = float(np.clip(1.0 - float(nearest_canopy_m) / radius_m, 0.0, 1.0))
    return max(fraction, proximity)


def persistent_soiling_score(
    tilt_deg: float | None,
    canopy_fraction: float | None,
    nearest_canopy_m: float | None,
    *,
    tilt_weight: float = TILT_WEIGHT,
    canopy_weight: float = CANOPY_WEIGHT,
    canopy_radius_m: float = CANOPY_SEARCH_RADIUS_M,
) -> float:
    """Return the 70/30 persistent-soiling inspection-priority score in [0, 1]."""
    if tilt_weight < 0 or canopy_weight < 0 or not math.isclose(
        tilt_weight + canopy_weight, 1.0, abs_tol=1e-9
    ):
        raise ValueError("tilt_weight and canopy_weight must be non-negative and sum to 1")
    return (
        tilt_weight * low_tilt_score(tilt_deg)
        + canopy_weight * canopy_exposure_score(
            canopy_fraction, nearest_canopy_m, radius_m=canopy_radius_m
        )
    )


def persistent_soiling_components(
    tilt_deg: float | None,
    canopy_fraction: float | None,
    nearest_canopy_m: float | None,
    *,
    canopy_radius_m: float = CANOPY_SEARCH_RADIUS_M,
) -> tuple[float, float]:
    """Return the independently inspectable tilt and canopy screen components."""
    return (
        low_tilt_score(tilt_deg),
        canopy_exposure_score(canopy_fraction, nearest_canopy_m, radius_m=canopy_radius_m),
    )


def flag_top_fraction(
    frame: pd.DataFrame,
    *,
    fraction: float = 0.10,
) -> pd.DataFrame:
    """Add deterministic rank and top-fraction flag to a score-sorted AOI frame."""
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    out = frame.copy()
    out["persistent_soiling_rank"] = np.arange(1, len(out) + 1)
    n_flag = int(math.ceil(len(out) * fraction))
    out["persistent_soiling_top_decile"] = out["persistent_soiling_rank"] <= n_flag
    return out
