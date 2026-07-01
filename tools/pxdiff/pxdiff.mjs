#!/usr/bin/env node
// pxdiff.mjs — pixelmatch diff with per-band locator hints. Emits ONE JSON object on stdout.
// Shared by the `pixelmatch_diff` MCP tool and the `sb pxdiff` CLI. Run from the repo root so
// `import 'pixelmatch'`/`'pngjs'` resolve the vendored node_modules.
//
//   node tools/pxdiff/pxdiff.mjs <ref.png> <build.png> [--out diff.png] [--threshold 0.1] [--bands 12]
//
// Returns: dimensions of both inputs, the compared region (cropped to the smaller), total
// mismatch pixels + %, and a per-horizontal-band breakdown + the worst bands — so you can
// LOCATE which part of the page drifted, not just get a single number. Never throws on a
// dimension mismatch (it crops); exits 0 whenever it produced a result.

import fs from 'fs';
import path from 'path';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

const emit = (obj, code = 0) => { process.stdout.write(JSON.stringify(obj)); process.exit(code); };

function parseArgs(argv) {
  const pos = [], opt = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) { opt[a.slice(2)] = argv[i + 1]; i++; }
    else pos.push(a);
  }
  return { pos, opt };
}

const { pos, opt } = parseArgs(process.argv.slice(2));
const [refPath, buildPath] = pos;
if (!refPath || !buildPath)
  emit({ ok: false, error: 'usage: pxdiff <ref.png> <build.png> [--out diff.png] [--threshold T] [--bands N]' }, 2);

const threshold = opt.threshold !== undefined ? parseFloat(opt.threshold) : 0.1;
const nBands = opt.bands !== undefined ? Math.max(1, parseInt(opt.bands, 10)) : 12;
const outPath = opt.out || null;

let a, b;
try { a = PNG.sync.read(fs.readFileSync(refPath)); }
catch (e) { emit({ ok: false, error: `cannot read reference (${refPath}): ${e.message}` }, 2); }
try { b = PNG.sync.read(fs.readFileSync(buildPath)); }
catch (e) { emit({ ok: false, error: `cannot read build (${buildPath}): ${e.message}` }, 2); }

const w = Math.min(a.width, b.width), h = Math.min(a.height, b.height);
const crop = (src) => {
  if (src.width === w && src.height === h) return src;
  const o = new PNG({ width: w, height: h });
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const si = (src.width * y + x) << 2, di = (w * y + x) << 2;
    o.data[di] = src.data[si]; o.data[di + 1] = src.data[si + 1];
    o.data[di + 2] = src.data[si + 2]; o.data[di + 3] = src.data[si + 3];
  }
  return o;
};
const ca = crop(a), cb = crop(b);
const diff = new PNG({ width: w, height: h });
const mismatch = pixelmatch(ca.data, cb.data, diff.data, w, h, { threshold });

// per-horizontal-band diff density (pixelmatch paints differing pixels solid red) — locates drift
const bandH = Math.max(1, Math.ceil(h / nBands));
const miss = new Array(nBands).fill(0), tot = new Array(nBands).fill(0);
for (let y = 0; y < h; y++) {
  const bi = Math.min(nBands - 1, Math.floor(y / bandH));
  for (let x = 0; x < w; x++) {
    const di = (w * y + x) << 2;
    const isDiff = diff.data[di] > 200 && diff.data[di + 1] < 100 && diff.data[di + 2] < 100 && diff.data[di + 3] > 200;
    tot[bi]++; if (isDiff) miss[bi]++;
  }
}
const bands = [];
for (let i = 0; i < nBands; i++) {
  const top = i * bandH, hh = Math.min(bandH, h - top);
  if (hh <= 0) break;
  bands.push({ i, top, h: hh, pct: +(100 * miss[i] / (tot[i] || 1)).toFixed(2) });
}
const worstBands = [...bands].sort((x, y) => y.pct - x.pct).slice(0, 3);

let wrote = null, writeErr = null;
if (outPath) {
  try {
    const abs = path.resolve(outPath);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, PNG.sync.write(diff));
    wrote = abs;
  } catch (e) { writeErr = e.message; }
}

const pct = +(100 * mismatch / (w * h)).toFixed(2);
emit({
  ok: true,
  reference: { w: a.width, h: a.height },
  build: { w: b.width, h: b.height },
  compared: { w, h },
  dimensionsMatch: a.width === b.width && a.height === b.height,
  mismatch, pct, threshold,
  verdict: pct < 1 ? 'near-identical' : pct < 8 ? 'close' : pct < 25 ? 'diverged' : 'very-different',
  bands, worstBands,
  diff: wrote, diffWriteError: writeErr,
});
