"""Shared US address parsing for outreach (mailer rendering + Lob sending).

One correct parser so the printed creative and the actual mail piece agree, and
so neither hard-codes a city/state. Parses from the END (ZIP, then state, then
city) so an extra comma from an apartment/unit line doesn't shift fields.
"""

from __future__ import annotations

import re

# Full state-name → USPS abbreviation. Taking the first two letters is wrong for
# several states (Nevada→NE, Arizona→AR, …), so map explicitly.
STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_ZIP_ONLY = re.compile(r"^\d{5}(?:-\d{4})?$")


def normalize_state(token: str) -> str:
    """Return a 2-letter USPS state code, or '' if the token isn't a state."""
    t = token.strip()
    if not t:
        return ""
    if t.lower() in STATE_ABBREV:
        return STATE_ABBREV[t.lower()]
    return t.upper() if len(t) == 2 and t.isalpha() else ""


def parse_us_address(address: str | None) -> dict | None:
    """Parse a free-form US address into parts, or None if it can't be trusted.

    Returns {line1, line2, city, state, zip}. line2 holds an apartment/unit when
    present. Returns None (skip — don't mail blind) when there's no street or no
    valid 5-digit ZIP.
    """
    address = (address or "").strip()
    if not address or address.lower() == "nan":
        return None

    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) < 2:
        return None

    m = _ZIP.search(address)
    if not m:
        return None
    zipcode = m.group(1)

    # Drop a trailing pure-ZIP part so the part before it is the state.
    if _ZIP_ONLY.match(parts[-1]):
        parts = parts[:-1]

    # State: the (now) last part, e.g. "CA", "California", or "CA 95062".
    state = ""
    if parts:
        tail = _ZIP.sub("", parts[-1]).strip()
        state = normalize_state(tail)
        if state:
            parts = parts[:-1]

    if not parts or not parts[0]:
        return None

    line1 = parts[0]
    city = parts[-1] if len(parts) >= 2 else ""
    line2 = ", ".join(parts[1:-1]) if len(parts) > 2 else ""

    return {
        "line1": line1,
        "line2": line2,
        "city": city,
        "state": state or "CA",
        "zip": zipcode,
    }
