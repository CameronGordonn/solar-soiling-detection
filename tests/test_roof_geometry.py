"""Guards on the roof-plane fit, especially the mercator scale trap.

The failure this file exists to prevent: EPT X/Y are EPSG:3857 metres while Z is true
metres, so a plane fitted on raw coordinates reports tilt ~19% too shallow at Santa
Cruz's latitude -- silently, on every roof, with entirely plausible-looking output.
"""

import math

import numpy as np
import pytest

from src.risk.roof_geometry import MAX_RMS_M, fit_plane

LAT = 36.9637
K = math.cos(math.radians(LAT))


def _synthetic_plane(tilt_deg: float, azimuth_deg: float, n: int = 400,
                     noise_m: float = 0.01, seed: int = 0):
    """Points on a plane of known tilt/downslope-azimuth, returned in EPT (3857) units."""
    rng = np.random.default_rng(seed)
    xt = rng.uniform(-4, 4, n)          # true metres
    yt = rng.uniform(-4, 4, n)
    m = math.tan(math.radians(tilt_deg))
    de, dn = math.sin(math.radians(azimuth_deg)), math.cos(math.radians(azimuth_deg))
    z = -m * (xt * de + yt * dn) + rng.normal(0, noise_m, n)
    return xt / K, yt / K, z           # inflate x,y to mercator


@pytest.mark.parametrize("tilt,az", [(20, 180), (25, 270), (15, 0), (30, 90), (5, 135)])
def test_recovers_known_plane(tilt, az):
    x, y, z = _synthetic_plane(tilt, az)
    f = fit_plane(x, y, z, LAT)
    assert f.ok
    assert f.tilt_deg == pytest.approx(tilt, abs=0.2)
    d = abs(f.azimuth_deg - az) % 360.0
    assert min(d, 360.0 - d) < 1.0


def test_mercator_correction_is_load_bearing():
    """Without the cos(lat) de-inflation the tilt is understated by ~19%."""
    x, y, z = _synthetic_plane(20.0, 180.0, noise_m=0.0)
    good = fit_plane(x, y, z, LAT)
    bad = fit_plane(x, y, z, 0.0)          # lat=0 -> k=1 -> correction disabled
    assert good.tilt_deg == pytest.approx(20.0, abs=0.1)
    assert bad.tilt_deg < 17.0
    assert bad.tilt_deg / good.tilt_deg == pytest.approx(1 / 1.2515, rel=0.05)


def test_azimuth_points_downhill():
    """Sanity on the sign: the reported bearing must lead to lower z, not higher."""
    x, y, z = _synthetic_plane(25.0, 210.0, noise_m=0.0)
    f = fit_plane(x, y, z, LAT)
    de = math.sin(math.radians(f.azimuth_deg))
    dn = math.cos(math.radians(f.azimuth_deg))
    proj = (x - x.mean()) * K * de + (y - y.mean()) * K * dn   # metres downslope
    assert np.corrcoef(proj, z)[0, 1] < -0.99


def test_ridge_is_rejected_not_guessed():
    """An array straddling a gable ridge must fail the fit, not return a mean plane."""
    rng = np.random.default_rng(1)
    n = 400
    xt = rng.uniform(-4, 4, n)
    yt = rng.uniform(-4, 4, n)
    m = math.tan(math.radians(25.0))
    z = -m * np.abs(yt)                       # two faces meeting at y=0
    f = fit_plane(xt / K, yt / K, z, LAT)
    assert not f.ok
    assert f.rms_residual_m > MAX_RMS_M


def test_too_few_points_fails_cleanly():
    x, y, z = _synthetic_plane(20.0, 180.0, n=5)
    f = fit_plane(x, y, z, LAT)
    assert not f.ok
    assert math.isnan(f.tilt_deg)
    assert "points" in f.reason
