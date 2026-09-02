# `published_qr_ids.csv` — a compatibility contract, not an output

Every row is a QR code that has been **physically printed and mailed to a real address**.
The card is in someone's kitchen drawer. `dashboard.html?id=<qr_id>` must keep resolving
for as long as those cards exist, which is indefinitely.

This file is checked in — unlike everything under `outputs/` and `/data/`, which are
gitignored — precisely because it is the only durable record of what was published.
`tests/test_qr_compat.py` fails if any id in here stops resolving.

## The hazard

`arrays_data.js` on the dashboard currently carries **334 features: the 60cm
`santa-cruz-outreach-v1` AOI**, and the 50 `detected-v1` QR ids are indices into it.
The 21cm rebuild (`santa-cruz-w2-21cm`) has **3,362 arrays with entirely different
`array_id` values** — the 21cm relabel changed the label convention, so the same roof is
now several polygons with new numbers.

**Regenerating `arrays_data.js` from the 21cm AOI without an alias step silently
repoints every mailed QR code at a different house.** Not a 404 — a wrong answer, which
is worse. That is the failure this file exists to prevent.

## Why APN is the durable key

Every published id resolved to a parcel APN at print time, and all 97 rows carry one.
APNs survive re-detection, re-numbering, imagery changes and label-convention changes;
`array_id` survives none of them. So the migration path for any dashboard rebuild is:

    printed qr_id  ->  apn (frozen here)  ->  whatever the current data calls that site

Keep serving the old id as an alias. Never reuse a published id for a different parcel.

## Batches

| batch | n | id form | resolved by |
|---|---:|---|---|
| `detected-v1` | 50 | integer, e.g. `89` | `arrays_data.js` (60cm AOI array_id) |
| `permit-deeplink-v1` | 47 | `permit-N` | `mailer_homes.js` |

Cards in `permit_targets.csv` (n=8) point at `/tools/calculator` with no `id`, so there is
nothing to preserve for them and they are deliberately absent.

## Adding a batch

Append rows when a new mailing goes out — *before* it goes out, ideally in the same
commit as the send. Regenerate with the snippet in `scripts/outreach/select_targets.py`
or by re-running the extraction over the batch's targets CSV.
