"""Module degradation and vintage: what an array's age does to its output.

Nothing in the dollar chain knew how old an array was. ``annual_loss_usd`` multiplied
nameplate kW by sun-hours by a derate, and a 2008 system and a 2025 system priced
identically. That matters more here than it would elsewhere, because the tariff work
(``rates.AOI_TARIFF_MIX``) showed the AOI is **90.1% legacy NEM 1.0/2.0** -- and legacy
means *old*, so the highest-value homes per kWh are also the most degraded.

Fleet age, measured from CaliforniaDGStats interconnection records for the AOI zip codes
(n=7,536 residential PV, PTO dates 1998-2026): p50 **6.7 yr**, p90 **16.0 yr**, mean 7.9,
and **35% of systems are 10+ years old** (those averaging 14.2 yr).

Two distinct effects, and only one of them is well grounded
-----------------------------------------------------------
**1. Degradation (implemented, SOURCED).** Modules lose output with age. Jordan & Kurtz,
*Photovoltaic Degradation Rates -- An Analytical Review*, Prog. Photovolt. 21(1) 12-29
(2013), assembled ~2,000 published rates and report a **median 0.5 %/yr**; the follow-up
compendium (Jordan, Kurtz, VanSant & Newmiller, Prog. Photovolt. 24(7) 978-989, 2016)
extends this to >11,000 rates across ~200 studies and 40 countries. 0.5 %/yr is the default
here, with a 0.3-0.8 %/yr band for sensitivity.

Our own site measurement is *consistent but far less precise*: PVDAQ system 2107 read
**-0.162 %/yr, 95% CI [-0.520, +0.175]** by RdTools year-on-year
(``docs/CRAIG_BRIEF_2026-08-19.md`` §5). That interval contains 0.5 %/yr comfortably. One
893 kW ground mount in Arbuckle is not a basis for a residential fleet rate, so the
literature median leads and 2107 is a cross-check, not the source.

**2. Module vintage efficiency (available, default OFF, ASSUMED).** A 2010 module was
~14% efficient and a 2026 one is ~22%, so the same roof area is a much smaller system if it
was installed long ago. Because ``system_kw`` here is estimated from *detected area*, this
biases old systems UP -- and by more than degradation costs them.

**We tried to measure this from our own data and could not.** Joining permit kW to detected
area for 112 AOI sites gives a trend of **+3.86 W/m2 per year, r = 0.139, p = 0.14** -- the
right sign, not significant, and the permits only span 2016-2025 so the interesting old
vintages are absent entirely. The curve below is therefore a literature-shaped ASSUMPTION,
it is **off by default**, and turning it on should be reported as a sensitivity, not a
correction. Do not quote a number produced with it as measured.

Both effects reduce output, so both make cleaning *less* attractive. Neither rescues
anything; they sharpen who is least bad.
"""

from __future__ import annotations

import bisect
from datetime import date

#: Jordan & Kurtz (2013), ~2,000 published rates. Median of the distribution.
MEDIAN_DEGRADATION_PCT_PER_YR = 0.5

#: Sensitivity band spanning the bulk of the published distribution.
DEGRADATION_BAND_PCT_PER_YR = (0.3, 0.8)

#: Our own single-site read, kept for provenance. NOT the default -- see module docstring.
PVDAQ_2107_DEGRADATION_PCT_PER_YR = 0.162

#: ASSUMED, off by default. Mainstream residential module power per m2 of module area by
#: install year. Shape follows the well-known efficiency trend; the levels are not sourced
#: to a specific table and our own data could not resolve them (p = 0.14).
_VINTAGE_YEARS = (2008, 2012, 2016, 2020, 2024, 2026)
_VINTAGE_W_PER_M2 = (135.0, 150.0, 170.0, 195.0, 215.0, 220.0)


def production_factor(
    age_years: float | None,
    rate_pct_per_yr: float = MEDIAN_DEGRADATION_PCT_PER_YR,
) -> float:
    """Fraction of nameplate output remaining after ``age_years``.

    ``None`` returns 1.0 -- an unknown age must never be silently aged. Compounding, not
    linear, which is how the published rates are defined.

    >>> round(production_factor(0), 4)
    1.0
    >>> round(production_factor(10), 4)
    0.9511
    >>> round(production_factor(20), 4)
    0.9046
    """
    if age_years is None:
        return 1.0
    a = max(0.0, float(age_years))
    return (1.0 - float(rate_pct_per_yr) / 100.0) ** a


def age_years(install_date, asof: date | None = None) -> float | None:
    """Years between an install/PTO date and ``asof``. ``None`` in, ``None`` out."""
    if install_date is None:
        return None
    import pandas as pd

    d = pd.to_datetime(install_date, errors="coerce")
    if d is None or pd.isna(d):
        return None
    ref = pd.Timestamp(asof or date.today())
    return max(0.0, (ref - d).days / 365.25)


def module_w_per_m2(install_year: int | None) -> float | None:
    """ASSUMED module power density for a vintage. ``None`` in, ``None`` out.

    Linear interpolation between the anchor years, clamped at both ends. See the module
    docstring before using this for anything you intend to publish.
    """
    if install_year is None:
        return None
    y = int(install_year)
    if y <= _VINTAGE_YEARS[0]:
        return _VINTAGE_W_PER_M2[0]
    if y >= _VINTAGE_YEARS[-1]:
        return _VINTAGE_W_PER_M2[-1]
    i = bisect.bisect_right(_VINTAGE_YEARS, y) - 1
    y0, y1 = _VINTAGE_YEARS[i], _VINTAGE_YEARS[i + 1]
    w0, w1 = _VINTAGE_W_PER_M2[i], _VINTAGE_W_PER_M2[i + 1]
    return w0 + (w1 - w0) * (y - y0) / (y1 - y0)
