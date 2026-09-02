"""Generate print-ready PDF postcards for solar array outreach.

6" x 4" postcard. 150 DPI for review, 300 DPI for print.

Layout:
  Left 62%  — brand tag, hero dollar, subtext, risk badge, CTA, panel illustration
  Right 38% — teal panel with large centered QR code
  Bottom strip — mailing address full width

Usage:
    PYTHONPATH=. python scripts/outreach/generate_mailers.py \\
        --targets outputs/outreach/santa-cruz-outreach-v1_top50.csv \\
        --out-dir outputs/outreach/mailers_v3

    PYTHONPATH=. python scripts/outreach/generate_mailers.py \\
        --targets outputs/outreach/santa-cruz-outreach-v1_top50.csv \\
        --out-dir outputs/outreach/mailers_v3 --dry-run
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pandas as pd
import qrcode
from reportlab.lib import colors
from reportlab.lib.units import inch as IN
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

try:
    from _addressing import parse_us_address
except ImportError:  # when run as a package module (python -m scripts.outreach...)
    from scripts.outreach._addressing import parse_us_address

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
try:
    from risk.economics import BASE_RATE, BASE_SUN, annual_loss_usd
except ImportError:  # pragma: no cover
    from src.risk.economics import BASE_RATE, BASE_SUN, annual_loss_usd

# ── palette ──────────────────────────────────────────────────────────────────
# Matched to the betterbehaviorfoundation.com theme (public/tools/styles.css
# :root). The site's vars are *named* navy/teal/gold but their values are all
# greens — so the card now reads as the same forest-green brand, not blue+amber.
NAVY     = colors.HexColor("#1a2e1a")  # site --navy   (forest background)
TEAL_DK  = colors.HexColor("#1e3d20")  # site --mid    (panel)
TEAL     = colors.HexColor("#2d7a2d")  # site --teal   (green)
GOLD     = colors.HexColor("#3d9e3d")  # site --gold   (bright-green accent / CTA)
GOLD_DK  = colors.HexColor("#2d5a2d")  # site --gold-dk
BRIGHT   = colors.HexColor("#7ec87e")  # site hero green — focal pop for the $ figure
WHITE    = colors.white
MUTED    = colors.HexColor("#8aa88a")  # sage, readable on dark green
LIGHT    = colors.HexColor("#dce8dc")  # site --lt-gray (body text on dark green)
RED      = colors.HexColor("#ef4444")  # risk badge — kept warm so the warning stands out
AMBER    = colors.HexColor("#f59e0b")

WEBSITE  = "betterbehaviorfoundation.com"
PHONE    = "(831) 216-8749"
EMAIL    = "betterbehaviorfoundation@gmail.com"

# ── card dimensions ───────────────────────────────────────────────────────────
W = 6 * IN          # trim width
H = 4 * IN          # trim height
BLEED = 0.125 * IN  # print bleed per edge (Lob 6x4 artwork = 6.25" x 4.25")
PAGE_W = W + 2 * BLEED
PAGE_H = H + 2 * BLEED

# ── physics ───────────────────────────────────────────────────────────────────
ELEC_RATE = 0.28
SUN_HOURS = 5.5
M2_PER_KW = 5.67


def _dollars_lost(risk: float, area: float) -> float:
    # Use realistic recovery pct per risk bucket (calibrated from UCSD 2013 study),
    # not the theoretical soiling ceiling. Midpoints: low=1.75%, medium=3%, high=5%.
    if risk >= 0.75:
        recovery_pct = 0.05
    elif risk >= 0.50:
        recovery_pct = 0.03
    else:
        recovery_pct = 0.0175
    return (area / M2_PER_KW) * SUN_HOURS * 365 * recovery_pct * ELEC_RATE


def _risk_label(s: float) -> str:
    return "HIGH SOILING RISK" if s >= 0.65 else "ELEVATED SOILING RISK"


def _risk_color(s: float) -> colors.Color:
    return RED if s >= 0.65 else AMBER


def _make_qr(url: str) -> ImageReader:
    url = url.replace("https://solarsoiled.app/dashboard?id=",
                      "https://betterbehaviorfoundation.com/tools/dashboard.html?id=")
    qr = qrcode.QRCode(box_size=8, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1a2e1a", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def _draw_glow(c: Canvas, cx: float, cy: float, max_r: float) -> None:
    """Subtle gold glow — tight radius, low alpha, won't flood the card."""
    steps = 10
    for i in range(steps, 0, -1):
        frac  = i / steps
        r     = max_r * frac
        alpha = 0.12 * (1.0 - frac) + 0.01   # 0.01 outer → 0.11 inner
        c.setFillColorRGB(GOLD.red, GOLD.green, GOLD.blue, alpha=alpha)
        c.circle(cx, cy, r, fill=1, stroke=0)


def _draw_panel_array(c: Canvas, x: float, y: float,
                      cols: int = 3, rows: int = 2,
                      cell_w: float = 0.32 * IN, cell_h: float = 0.19 * IN,
                      gap: float = 0.03 * IN) -> None:
    """Decorative solar panel accent — dark navy panels with teal grid lines."""
    fill_r, fill_g, fill_b = (
        colors.HexColor("#24502a").red,
        colors.HexColor("#24502a").green,
        colors.HexColor("#24502a").blue,
    )
    line_r, line_g, line_b = TEAL.red, TEAL.green, TEAL.blue

    for r in range(rows):
        for col in range(cols):
            px = x + col * (cell_w + gap)
            py = y + r * (cell_h + gap)

            # panel body: dark blue with clear teal border
            c.setFillColorRGB(fill_r, fill_g, fill_b, alpha=0.90)
            c.setStrokeColorRGB(line_r, line_g, line_b, alpha=0.80)
            c.setLineWidth(0.7)
            c.roundRect(px, py, cell_w, cell_h, 2, fill=1, stroke=1)

            # cell grid: 2 cols × 3 rows of sub-cells
            c.setStrokeColorRGB(line_r, line_g, line_b, alpha=0.40)
            c.setLineWidth(0.35)
            c.line(px + cell_w / 2, py + 2, px + cell_w / 2, py + cell_h - 2)
            c.line(px + 2, py + cell_h * 0.33, px + cell_w - 2, py + cell_h * 0.33)
            c.line(px + 2, py + cell_h * 0.67, px + cell_w - 2, py + cell_h * 0.67)


def _draw_postcard(c: Canvas, row: pd.Series) -> None:
    array_id   = int(row["array_id"])
    risk       = float(row["risk_score"])
    area       = float(row["area_m2"])
    address    = str(row.get("mailing_address", ""))
    qr_url     = str(row.get("qr_url",
                    f"https://betterbehaviorfoundation.com/tools/dashboard.html?id={array_id}"))

    # Dollar headline = gross annual soiling loss, from the SAME economics the
    # targeting ranks on (consistent inputs); fall back to the risk estimate when
    # the targets CSV predates the net-$ columns.
    sk = row.get("system_kw")
    lp = row.get("loss_pct")
    if sk is not None and not pd.isna(sk) and lp is not None and not pd.isna(lp):
        dollars = annual_loss_usd(float(sk), BASE_SUN, float(lp) / 100.0, BASE_RATE)
    else:
        dollars = _dollars_lost(risk, area)
    risk_lbl   = _risk_label(risk)
    risk_col   = _risk_color(risk)

    # ── layout constants ──────────────────────────────────────────────────────
    ADDR_H  = 0.72 * IN     # bottom address strip
    SPLIT_X = 3.72 * IN     # left | right column split
    L_PAD   = 0.28 * IN
    L_W     = SPLIT_X - L_PAD - 0.12 * IN
    R_CTR   = SPLIT_X + (W - SPLIT_X) / 2

    # Vertical anchors — all baselines, top-to-bottom (decreasing Y).
    # 52pt Helvetica cap-height ≈ 0.505". Each line must clear the element above it.
    BRAND_Y  = H - 0.26 * IN    # 9pt brand tag.  top ≈ H - 0.17" — clear of card edge ✓
    LOSING_Y = H - 0.52 * IN    # 10.5pt preamble. baseline-gap from brand = 0.26" ✓
    # Dollar top = DOLLAR_Y + 0.505". Must be < LOSING_Y so no overlap.
    # Choose DOLLAR_Y = H - 1.18" → dollar top = H - 0.675", well below LOSING_Y (H - 0.52") ✓
    DOLLAR_Y = H - 1.18 * IN    # 52pt "$XXX" baseline
    # "per year" sits on the SAME baseline as DOLLAR, to the right — no extra row needed
    DUST_Y   = H - 1.53 * IN    # 9pt "to dust…" — 0.35" below dollar baseline ✓
    BADGE_Y  = H - 1.88 * IN    # risk badge bottom edge (badge is 0.185" tall)
    DIV_Y    = H - 2.18 * IN    # thin divider
    CTA_Y    = H - 2.46 * IN    # 8.5pt CTA bold
    SUB_Y    = H - 2.66 * IN    # 7.5pt CTA sub
    PANEL_Y  = ADDR_H + 0.08 * IN  # bottom of panel illustration (sits in gap below SUB_Y)

    # ── bleed: move origin to the trim corner so all layout below stays in
    #    0..W / 0..H, and extend full-bleed fills past the trim edges so trimming
    #    leaves no white slivers. ───────────────────────────────────────────────
    c.translate(BLEED, BLEED)

    # ── full-card navy background (covers the full bleed) ──────────────────────
    c.setFillColor(NAVY)
    c.rect(-BLEED, -BLEED, PAGE_W, PAGE_H, fill=1, stroke=0)

    # ── right teal panel (bleeds off the top + right edges) ────────────────────
    c.setFillColor(TEAL_DK)
    c.rect(SPLIT_X, ADDR_H, W - SPLIT_X + BLEED, H - ADDR_H + BLEED, fill=1, stroke=0)

    # ── address strip (bleeds off the left/right/bottom edges) ─────────────────
    c.setFillColor(colors.HexColor("#0f1f0f"))
    c.rect(-BLEED, -BLEED, PAGE_W, ADDR_H + BLEED, fill=1, stroke=0)

    # thin gold top edge on address strip (full bleed width)
    c.setFillColorRGB(GOLD.red, GOLD.green, GOLD.blue, alpha=0.4)
    c.rect(-BLEED, ADDR_H - 1, PAGE_W, 1, fill=1, stroke=0)

    # ── brand tag ─────────────────────────────────────────────────────────────
    # The foundation name appears once here (the trust anchor on the $ side). Location
    # + URL are dropped from the front — the back panel + Lob's return address carry them.
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(L_PAD, BRAND_Y, "BETTER BEHAVIOR FOUNDATION")

    # ── glow behind dollar (tight, subtle) ───────────────────────────────────
    glow_cx = L_PAD + 0.65 * IN
    glow_cy = DOLLAR_Y + 0.25 * IN
    _draw_glow(c, glow_cx, glow_cy, max_r=0.55 * IN)

    # ── "your panels may be losing" ───────────────────────────────────────────
    c.setFillColor(LIGHT)
    c.setFont("Helvetica", 10.5)
    c.drawString(L_PAD, LOSING_Y, "Your solar panels may be losing")

    # ── dollar number ─────────────────────────────────────────────────────────
    dollar_str = f"${dollars:,.0f}"
    c.setFillColor(BRIGHT)
    c.setFont("Helvetica-Bold", 52)
    c.drawString(L_PAD, DOLLAR_Y, dollar_str)

    # "per year" inline to the right, vertically centered on the dollar
    num_w = c.stringWidth(dollar_str, "Helvetica-Bold", 52)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(L_PAD + num_w + 0.10 * IN, DOLLAR_Y + 0.18 * IN, "per year")

    # ── "to dust and pollen" ──────────────────────────────────────────────────
    c.setFillColor(LIGHT)
    c.setFont("Helvetica", 9)
    c.drawString(L_PAD, DUST_Y, "to dust and pollen building up on them.")

    # ── risk badge ────────────────────────────────────────────────────────────
    bpad_x = 0.09 * IN
    bpad_y = 0.045 * IN
    bh     = 0.185 * IN
    bw     = c.stringWidth(risk_lbl, "Helvetica-Bold", 7.5) + bpad_x * 2
    c.setFillColor(risk_col)
    c.roundRect(L_PAD, BADGE_Y, bw, bh, 3, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(L_PAD + bpad_x, BADGE_Y + bpad_y + 0.01 * IN, risk_lbl)
    # (No raw "Score 0.56/1.00" — meaningless to a homeowner, and a sub-1.0 number
    #  can read as low-risk. The badge label carries the message.)

    # ── divider ───────────────────────────────────────────────────────────────
    c.setStrokeColorRGB(TEAL.red, TEAL.green, TEAL.blue, alpha=0.4)
    c.setLineWidth(0.6)
    c.line(L_PAD, DIV_Y, SPLIT_X - 0.18 * IN, DIV_Y)

    # ── CTA ───────────────────────────────────────────────────────────────────
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(L_PAD, CTA_Y, "Scan for your free personalized energy report.")

    # Phone lives once, in the footer — keep this line to the no-signup reassurance only.
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawString(L_PAD, SUB_Y, "No signup required — it only takes a moment.")

    # ── solar panel accent (bottom-left, below CTA — 2 rows × 3 cols) ───────
    # Panels span 2*(0.19+0.03)-0.03 = 0.41" tall.  Bottom at PANEL_Y=0.62",
    # top at 1.03" — well below SUB_Y (1.34").  No overlap.
    _draw_panel_array(c, x=L_PAD, y=PANEL_Y)

    # ── front footer strip: phone CTA (left) + mission line (right) ────────────
    # No address/URL here (the back carries the URL; Lob stamps the addresses + barcode
    # onto the back). The mission line fills the band under the QR and adds trust; the
    # internal array ref is no longer printed (it rides in the QR for tracking).
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 6)
    c.drawString(L_PAD, ADDR_H / 2 + 0.07 * IN, "QUESTIONS?")
    c.setFillColor(LIGHT)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(L_PAD, ADDR_H / 2 - 0.12 * IN, f"Call or text Craig at {PHONE}")

    # 501(c)(3) status comes via fiscal sponsorship under Ecologistics; the short
    # "A 501(c)(3) nonprofit project" form was chosen for the badge.
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 6)
    c.drawRightString(W - L_PAD, ADDR_H / 2 + 0.07 * IN, "A 501(C)(3) NONPROFIT PROJECT")
    c.setFillColor(LIGHT)
    c.setFont("Helvetica", 7.5)
    c.drawRightString(W - L_PAD, ADDR_H / 2 - 0.12 * IN,
                      "Helping homeowners get the most from their solar")

    # ── right panel: QR code ──────────────────────────────────────────────────
    qr_img  = _make_qr(qr_url)
    qr_side = 1.60 * IN
    qr_x    = R_CTR - qr_side / 2
    # center vertically in the right panel (between ADDR_H and H)
    right_h = H - ADDR_H
    qr_y    = ADDR_H + (right_h - qr_side) / 2 + 0.06 * IN

    # white card
    pad = 0.07 * IN
    c.setFillColor(WHITE)
    c.roundRect(qr_x - pad, qr_y - pad,
                qr_side + 2 * pad, qr_side + 2 * pad, 6, fill=1, stroke=0)
    c.drawImage(qr_img, qr_x, qr_y, width=qr_side, height=qr_side,
                preserveAspectRatio=True)

    # "SCAN" above
    c.setFillColor(BRIGHT)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(R_CTR, qr_y + qr_side + pad + 0.13 * IN, "SCAN")

    # "Free report" below
    c.setFillColor(LIGHT)
    c.setFont("Helvetica", 7)
    c.drawCentredString(R_CTR, qr_y - pad - 0.15 * IN, "Free report")


# ── back (address side) ────────────────────────────────────────────────────────

def _wrap(c: Canvas, text: str, font: str, size: float, max_w: float) -> list[str]:
    """Greedy word-wrap to max_w (points) at the given font/size."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if c.stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _draw_back(c: Canvas, row: pd.Series) -> None:
    """Address side of the postcard.

    Lob owns the centre/right band of this side — verified against a real Lob proof,
    it stamps the return address, IMB barcode, postage indicia, and recipient address
    there. So OUR design is confined to a dark-green brand panel down the LEFT edge
    (right edge at ~2.4"; Lob's content starts ~2.7"), matching the front and the
    betterbehaviorfoundation.com theme. The rest stays white for Lob. We deliberately
    do NOT print our own return address — Lob already prints it, and duplicating it is
    what made the previous all-white back look cluttered.
    """
    c.translate(BLEED, BLEED)

    # white full-bleed background — keeps Lob's address area clean + legible
    c.setFillColor(WHITE)
    c.rect(-BLEED, -BLEED, PAGE_W, PAGE_H, fill=1, stroke=0)

    # ── left brand panel: dark green, full-bleed on the left/top/bottom ────────
    PANEL_R = 2.40 * IN          # right edge of our zone; clears Lob's content (~2.7")
    c.setFillColor(NAVY)
    c.rect(-BLEED, -BLEED, PANEL_R + BLEED, PAGE_H, fill=1, stroke=0)
    # thin gold seam where the panel meets the white address field
    c.setFillColorRGB(GOLD.red, GOLD.green, GOLD.blue, alpha=0.9)
    c.rect(PANEL_R - 1.5, -BLEED, 1.5, PAGE_H, fill=1, stroke=0)

    LP = 0.28 * IN                       # panel padding
    TW = PANEL_R - LP - 0.16 * IN        # text wrap width inside the panel

    # ── wordmark ──────────────────────────────────────────────────────────────
    y = H - 0.44 * IN
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 9.5)
    for ln in ("BETTER BEHAVIOR", "FOUNDATION"):
        c.drawString(LP, y, ln)
        y -= 0.205 * IN
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.5)
    c.drawString(LP, y, "Santa Cruz, California")
    y -= 0.20 * IN

    # gold rule
    c.setFillColorRGB(GOLD.red, GOLD.green, GOLD.blue, alpha=0.9)
    c.rect(LP, y, 0.78 * IN, 1.4, fill=1, stroke=0)
    y -= 0.34 * IN

    # ── message ───────────────────────────────────────────────────────────────
    msg = ("We spotted your rooftop solar array in recent aerial imagery of the "
           "Santa Cruz area and estimated what it may be losing to soiling each year.")
    c.setFillColor(LIGHT)
    for ln in _wrap(c, msg, "Helvetica", 8, TW):
        c.setFont("Helvetica", 8)
        c.drawString(LP, y, ln)
        y -= 0.158 * IN

    # ── CTA ───────────────────────────────────────────────────────────────────
    y -= 0.14 * IN
    cta = "Turn over and scan the code for your free, personalized report."
    c.setFillColor(BRIGHT)
    for ln in _wrap(c, cta, "Helvetica-Bold", 8.5, TW):
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(LP, y, ln)
        y -= 0.165 * IN

    # ── contact, pinned near the panel bottom ─────────────────────────────────
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(LP, 0.54 * IN, WEBSITE)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.5)
    c.drawString(LP, 0.39 * IN, EMAIL)
    c.drawString(LP, 0.24 * IN, f"Call or text {PHONE}")


# ── generation driver ─────────────────────────────────────────────────────────

def generate_mailers(targets_csv: Path, out_dir: Path, *, dry_run: bool = False) -> list[Path]:
    if not targets_csv.exists():
        sys.exit(f"ERROR: {targets_csv} not found.")
    df = pd.read_csv(str(targets_csv))
    if dry_run:
        df = df.head(3)
        print(f"Dry-run: {len(df)} sample PDFs...")
    else:
        print(f"Generating {len(df)} PDFs...")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []

    for _, row in df.iterrows():
        array_id = int(row["array_id"])
        out_path = out_dir / f"{array_id}.pdf"
        c = Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H))
        c.setTitle(f"Better Behavior Foundation - Array #{array_id}")
        _draw_postcard(c, row)   # page 1: front (marketing)
        c.showPage()
        _draw_back(c, row)       # page 2: back — left brand panel; right kept clear for Lob
        c.save()
        out_paths.append(out_path)
        print(f"  -> {out_path}")

    if out_paths and not dry_run:
        _merge_pdfs(out_paths, out_dir / "all_mailers.pdf")
    return out_paths


def _merge_pdfs(paths: list[Path], out: Path) -> None:
    try:
        from pypdf import PdfWriter
        writer = PdfWriter()
        for p in paths:
            writer.append(str(p))
        with open(str(out), "wb") as f:
            writer.write(f)
        print(f"Merged -> {out}")
    except ImportError:
        print("(pip install pypdf for merged PDF)")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--targets", required=True)
    p.add_argument("--out-dir", default="outputs/outreach/mailers")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    generate_mailers(Path(args.targets), REPO_ROOT / args.out_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
