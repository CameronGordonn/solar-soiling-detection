# Meeting runbook — Cloudflare cutover + Lob send

> **ARCHIVED — completed 2026-06-30.** The Cloudflare DNS cutover and the Lob test-50 send (`mailers_v13`)
> are done. Kept for history. Reusable Lob procedure lives in [`../MAILER_PIPELINE.md`](../MAILER_PIPELINE.md).

Live, step-by-step for the meeting. Goal: get the tools live on `betterbehaviorfoundation.com`
and mail the test batch. Everything code-side is committed + pushed; the steps below are
infrastructure (accounts/keys/DNS) only.

## Status going in
- **Code: not blocking.** Both repos clean and pushed; `npm run build` passes.
- **Send batch: ready.** 50 **all-detected** rooftops with real owner names + county addresses.
  - Targets: `outputs/outreach/detected_targets.csv`
  - PDFs: `outputs/outreach/mailers_v13/` (50 + `all_mailers.pdf`)
  - Each QR → `dashboard.html?id=<array_id>&addr=…` → rich detected-array view (dim map +
    spotlight + address). All 50 ids verified present on the dashboard.
- Addresses come from the Santa Cruz County parcel service (`build_detected_targets.py`); the
  public dashboard layer still carries no addresses (address rides only in each card's QR).

## Have ready
GitHub login (repo `CameronGordonn/BBF-Website`), the domain registrar login for
`betterbehaviorfoundation.com`, a card for Lob, the return mailing address.

---

## Part A — Cloudflare account + deploy (~10 min)
1. **dash.cloudflare.com → Sign Up**, verify email.
2. **Workers & Pages → Create → Pages → Connect to Git** → authorize GitHub → repo
   **`CameronGordonn/BBF-Website`**, branch `main`.
3. Build settings:
   - Framework preset: **Next.js (Static HTML Export)** (or "None")
   - Build command: **`npm run build`**
   - Build output directory: **`out`**
4. **Save and Deploy.** Note the `https://<project>.pages.dev` URL.
5. **Test the real build (before DNS):**
   `https://<project>.pages.dev/tools/dashboard.html?id=89&addr=510%20Arroyo%20Seco`
   → dimmed map, spotlighted rooftop, address shown. Also `/tools/calculator` and
   `/research/airline-donation-flow-redesign` (stat reads 42, not NaN).

## Part B — Point the domain (~10 min + propagation)
6. Pages project → **Custom domains → Set up a custom domain** → `betterbehaviorfoundation.com`
   (repeat for `www`).
7. Cloudflare prompts to **add the domain to Cloudflare** (needed for the apex). It gives **two
   nameservers**.
8. At the **registrar**, replace the nameservers with Cloudflare's two. (Moves DNS off Vercel.)
9. Custom domain flips to **Active** after propagation (minutes–hours).
10. **Verify live:** `https://betterbehaviorfoundation.com/tools/dashboard.html?id=89` loads.
    - Propagation may lag the meeting. That's OK for the send — postcards take days to arrive, so
      the domain just needs to be live **before recipients receive them**. Verify if you can.

## Part C — (Optional) Render redeploy
11. Only needed for detected-array *backend* "Recalculate"; the QR/permit flow runs client-side.
    Render dashboard → `solarsoiled-api` → **Manual Deploy → Deploy latest commit** (CORS fix).

## Part D — Lob setup + mail (~15 min)
12. **dashboard.lob.com → Sign Up**, verify, add a card.
13. **Settings → API Keys** → copy **Test** (`test_…`) and **Live** (`live_…`).
14. Create `~/repos/solar-soiling-ml/.env`:
    ```
    LOB_API_KEY=test_xxxxxxxx
    SOLARSOILED_RETURN_ADDRESS=Better Behavior Foundation|<street>|<city>|CA|<zip>
    ```
15. **Dry-run = test key + `--send`** (free, mails nothing, but actually creates each postcard,
    runs Lob's deliverability check, and leaves a previewable proof in the dashboard). NOTE: running
    *without* `--send` does **not** contact Lob — it only parses addresses locally, so always use
    `--send` with the test key for the real pre-flight:
    ```
    cd ~/repos/solar-soiling-ml
    PYTHONPATH=. conda run -n solar-soiling python scripts/outreach/mail_via_lob.py \
      --targets outputs/outreach/detected_targets.csv \
      --mailers-dir outputs/outreach/mailers_v13 --send
    ```
    Current `mailers_v13` already passes **50/50** in test mode. Preview the proofs at
    **Lob dashboard → Postcards** (two-sided green card; back's lower-right left blank for Lob's
    address + barcode).
16. Any `FAIL` row = Lob rejected the address; drop it and backfill from a buffer to keep a full 50
    (`build_detected_targets.py --top 80` → test-send → drop FAILs → top 50). See MAILER_PIPELINE §9.
17. **Send:** set `LOB_API_KEY=live_…` in `.env`, make sure `SOLARSOILED_RETURN_ADDRESS` is the
    **real** return address, then re-run the **same command** (already has `--send`). The script
    prompts `y/N` before charging. Cost ≈ $0.50–$1.50/card. Confirm mailpieces in the Lob dashboard.

**Critical ordering: A → B (verify live) → D.** Every card's QR points at
`betterbehaviorfoundation.com/tools/dashboard.html?id=…`, so the domain must serve the Cloudflare
build before the cards land.

## Notes / decisions
- Owner names are real (assessor-derived, cleaned to "First Last"). Revert to generic
  "Solar Panel Owner" in `build_detected_targets.py` if you'd rather not name recipients.
- 226 detected residential homes have addresses; the batch is the top 50 by net-$. Re-run
  `build_detected_targets.py --top N` to change the count.
