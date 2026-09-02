"""Is the county cache really 21cm, or have we been under-requesting by 3.3x?

Fetch the SAME ground footprint at several requested GSDs and measure how much real high-frequency
detail comes back. If the finer requests are upsampled from a 21cm source, gradient energy per
GROUND METRE stays flat. If the source is genuinely finer, it climbs.
"""
import io, json, sys
from pathlib import Path
import numpy as np
import requests
from PIL import Image

SVC = "https://sccgis.santacruzcountyca.gov/server/rest/services/Cache/Imagery_2025/MapServer/export"
FT_PER_M = 3.28083989501312
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else '.')

# A 60 m square inside a tile known to contain arrays.
idx = json.loads(Path('/home/cameron/repos/solar-soiling-ml/data/interim/scc21_labelset/tile_index_21cm.json').read_text())
tiles = idx.get('tiles', idx)
name, meta = sorted(tiles.items())[40]
b = meta['bounds']
cx, cy = (b['minx']+b['maxx'])/2, (b['miny']+b['maxy'])/2
half3857 = 30 / 0.8  # ~30 m ground -> 3857 metres at this latitude (1/cos(lat))
box3857 = (cx-half3857, cy-half3857, cx+half3857, cy+half3857)

# reproject the 3857 box corners to 2227 for the request
from pyproj import Transformer
tr = Transformer.from_crs(3857, 2227, always_xy=True)
x0, y0 = tr.transform(box3857[0], box3857[1])
x1, y1 = tr.transform(box3857[2], box3857[3])
ground_m = (x1-x0)/FT_PER_M   # width of the footprint in real metres

print(f'tile {name}  footprint {ground_m:.1f} m across')
rows = []
for gsd_m in (0.2083, 0.1524, 0.0762, 0.0635):
    px = int(round(ground_m / gsd_m))
    if px > 4096:
        print(f'  {gsd_m:.4f} m/px -> {px} px  SKIP (over maxImageSize)'); continue
    r = requests.get(SVC, params=dict(
        bbox=f'{x0:.4f},{y0:.4f},{x1:.4f},{y1:.4f}', bboxSR=2227, imageSR=2227,
        size=f'{px},{px}', format='png32', transparent='false', f='image'), timeout=90)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert('L')
    a = np.asarray(im, dtype=np.float64)
    im.convert('RGB').save(OUT / f'gsd_{int(gsd_m*10000):04d}.png')
    # Gradient energy normalised per ground metre: upsampling adds pixels but no new edges.
    gy, gx = np.gradient(a)
    grad_per_m = float(np.sqrt(gx**2 + gy**2).sum()) / (ground_m**2)
    # Fraction of spectral energy above half-Nyquist -- flat if content was interpolated.
    F = np.abs(np.fft.fftshift(np.fft.fft2(a - a.mean())))
    n = F.shape[0]; yy, xx = np.ogrid[:n, :n]
    rad = np.hypot(yy - n/2, xx - n/2)
    hi = float(F[rad > n/4].sum() / F.sum())
    rows.append((gsd_m, px, grad_per_m, hi))
    print(f'  {gsd_m:.4f} m/px -> {px:4d} px | grad energy/m2 {grad_per_m:9.1f} | high-freq frac {hi:.4f}')

if len(rows) > 1:
    base = rows[0]
    print('\nrelative to the 0.2083 request:')
    for g, px, gp, hi in rows[1:]:
        print(f'  {g:.4f} m/px : gradient x{gp/base[2]:.2f}, high-freq frac x{hi/base[3]:.2f}')
