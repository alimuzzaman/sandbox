#!/usr/bin/env python3
"""extract-png.py@1 — raster-hint extractor -> DesignSpec v1 (fidelity: low).

What a raster CAN give (this script): page dimensions, horizontal BAND boundaries
(full-width background changes) and each band's dominant background color. What it
CANNOT: exact padding/margin/line-height, text, fonts, inset-panel geometry. Those
are filled by a VISION pass (read the PNG, populate each section's `elements`), every
value flagged fidelity:low and prefixed "≈". See DESIGNSPEC.md "Source adapters".

Deps: none required. Uses Pillow if present, else macOS `sips` (PNG->BMP).
Usage:  python3 extract-png.py <image.png> [--out spec.json] [--rows N] [--cols N] [--thresh T]
"""
import json, os, struct, subprocess, sys, tempfile
from collections import Counter

def load_pixels(path):
    """Return (width, height, get(x,y)->(r,g,b)). Pillow fast-path, else sips->BMP."""
    try:
        from PIL import Image
        im = Image.open(path).convert('RGB'); w, h = im.size; px = im.load()
        return w, h, (lambda x, y: px[x, y])
    except Exception:
        pass
    if not (sys.platform == 'darwin' and _has('sips')):
        raise SystemExit("need Pillow or macOS `sips` to read pixels")
    bmp = tempfile.mktemp(suffix='.bmp')
    subprocess.run(['sips', '-s', 'format', 'bmp', path, '--out', bmp],
                   check=True, capture_output=True)
    try:
        data = open(bmp, 'rb').read()
    finally:
        os.path.exists(bmp) and os.remove(bmp)
    if data[:2] != b'BM':
        raise SystemExit("sips did not produce a BMP")
    off = struct.unpack('<I', data[10:14])[0]
    w   = struct.unpack('<i', data[18:22])[0]
    h   = struct.unpack('<i', data[22:26])[0]
    bpp = struct.unpack('<H', data[28:30])[0]
    topdown = h < 0
    h = abs(h)
    nb = bpp // 8
    rowsize = ((bpp * w + 31) // 32) * 4  # padded to 4 bytes
    def get(x, y):
        row = y if topdown else (h - 1 - y)
        i = off + row * rowsize + x * nb
        b, g, r = data[i], data[i + 1], data[i + 2]  # BMP is BGR
        return (r, g, b)
    return w, h, get

def _has(cmd):
    return subprocess.run(['which', cmd], capture_output=True).returncode == 0

def _hex(c):
    return '#%02x%02x%02x' % c

def _q(c):
    return (c[0] // 16 * 16 + 8, c[1] // 16 * 16 + 8, c[2] // 16 * 16 + 8)

def row_hist(get, y, w, col_step):
    """Quantized color histogram of a row over sampled columns; returns (Counter, total)."""
    hist = Counter(); n = 0
    for x in range(0, w, col_step):
        hist[_q(get(x, y))] += 1; n += 1
    return hist, n

def row_mode(get, y, w, col_step):
    hist, _ = row_hist(get, y, w, col_step)
    return hist.most_common(1)[0][0]

def row_band_color(get, y, w, col_step, page_bg, cover=0.25):
    """Band color of a row: the most common NON-page-bg color if it covers >`cover` of
    the row width (catches inset colored panels); else page_bg."""
    hist, n = row_hist(get, y, w, col_step)
    for col, cnt in hist.most_common():
        if dist(col, page_bg) <= 20:
            continue                     # skip near-background
        return col if cnt / n >= cover else page_bg
    return page_bg

def dist(a, b):
    return abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2])

def extract(path, row_step, col_step, thresh, min_band):
    w, h, get = load_pixels(path)
    ys = list(range(0, h, row_step))
    # page background = the most common row-mode color (usually white)
    page_bg = Counter(row_mode(get, y, w, col_step) for y in ys).most_common(1)[0][0]
    # per-row band color: page_bg, or an inset panel color that covers >25% of the row
    rows = [(y, row_band_color(get, y, w, col_step, page_bg)) for y in ys]
    # segment into bands where the row color departs from the current band color
    def band_col(run):  # a band's color = its dominant NON-background color, else page_bg
        nb = [c for c in run if dist(c, page_bg) > 20]
        return Counter(nb).most_common(1)[0][0] if nb else page_bg
    # classify each row as background vs panel, segment on that transition
    bands = []
    cur_top, run = rows[0][0], [rows[0][1]]
    cur_is_bg = dist(rows[0][1], page_bg) <= thresh
    for y, c in rows[1:]:
        is_bg = dist(c, page_bg) <= thresh
        if is_bg != cur_is_bg:
            bands.append((cur_top, y, band_col(run)))
            cur_top, run, cur_is_bg = y, [c], is_bg
        else:
            run.append(c)
    bands.append((cur_top, h, band_col(run)))
    # merge bands shorter than min_band into the previous one
    merged = []
    for top, bot, col in bands:
        if merged and (bot - top) < min_band:
            merged[-1] = (merged[-1][0], bot, merged[-1][2])
        else:
            merged.append((top, bot, col))
    sections = [{
        'id': 's%d' % (i + 1),
        'label': '≈band %d' % (i + 1),
        'top': top, 'height': bot - top,
        'bgOwner': {'background': _hex(col), 'padding': None, 'radius': None},
        'contentWidth': None, 'align': None, 'columns': [],
        'elements': [],            # <- VISION fills these (text/kind/font/box), fidelity low
        'fidelity': 'low',
    } for i, (top, bot, col) in enumerate(merged)]
    return {
        'designspec': '1.0',
        'meta': {'source': 'png', 'ref': os.path.abspath(path), 'viewport': {'w': w, 'h': h},
                 'colorFormat': 'hex', 'fidelity': 'low', 'tool': 'extract-png.py@1'},
        'page': {'width': w, 'height': h, 'background': _hex(page_bg), 'contentMaxWidth': None},
        'sections': sections,
        'note': 'RASTER hints only: band boundaries + bg colors are approximate (~%dpx rows); '
                'inset panels/text/fonts are NOT captured. Fill elements[] via a vision pass, '
                'flag every value fidelity:low.' % row_step,
    }


def main():
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    path = a[0]
    opt = {a[i]: a[i + 1] for i in range(1, len(a) - 1, 2) if a[i].startswith('--')}
    out = opt.get('--out')
    spec = extract(path,
                   int(opt.get('--rows', 6)), int(opt.get('--cols', 24)),
                   int(opt.get('--thresh', 24)), int(opt.get('--min', 120)))
    s = json.dumps(spec, indent=2)
    if out:
        open(out, 'w').write(s); print('wrote', out, '(%d sections)' % len(spec['sections']))
    else:
        print(s)

if __name__ == '__main__':
    main()
