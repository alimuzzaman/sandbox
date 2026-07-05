#!/usr/bin/env node
// vrdiff.mjs — BackstopJS visual-regression diff of a REFERENCE url vs a BUILD url, producing a
// browsable HTML web report (the "web preview"). Shared by the `sb vrdiff` CLI. Replaces pixelmatch
// for the VISUAL pass of the design-fidelity workflow; `sb pxdiff` stays as the numeric locator.
//
//   node tools/backstop/vrdiff.mjs <referenceUrl> <buildUrl> \
//        [--label home] [--viewport 1280x900] [--viewport 768x1024] \
//        [--selector document] [--threshold 0.1] [--delay 1500] \
//        [--workdir tmp/vrdiff] [--no-open] [--json]
//
// BackstopJS drives its OWN full-page screenshots from the URLs (default selector `document` = the
// whole document), so — unlike the old harness that captured a single 952px viewport — the build is
// always captured full-page at the reference viewport. `backstop reference` snaps referenceUrl,
// `backstop test` snaps buildUrl + diffs + writes the HTML report. Emits ONE JSON object on stdout.

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const emit = (obj, code = 0) => { process.stdout.write(JSON.stringify(obj)); process.exit(code); };

function parseArgs(argv) {
  const pos = [], opt = {}, viewports = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--no-open') opt.open = false;
    else if (a === '--json') opt.json = true;
    else if (a === '--viewport') viewports.push(argv[++i]);
    else if (a.startsWith('--')) opt[a.slice(2)] = argv[++i];
    else pos.push(a);
  }
  if (viewports.length) opt.viewports = viewports;
  return { pos, opt };
}

const { pos, opt } = parseArgs(process.argv.slice(2));
const [referenceUrl, buildUrl] = pos;
if (!referenceUrl || !buildUrl)
  emit({ ok: false, error: 'usage: vrdiff <referenceUrl> <buildUrl> [--label L] [--viewport WxH] [--selector S] [--threshold T] [--delay MS] [--workdir DIR] [--no-open] [--json]' }, 2);

const label = opt.label || 'page';
const selector = opt.selector || 'document';
const threshold = opt.threshold !== undefined ? parseFloat(opt.threshold) : 0.1;
const delay = opt.delay !== undefined ? parseInt(opt.delay, 10) : 1500;
const open = opt.open !== false;
const workdir = path.resolve(opt.workdir || 'tmp/vrdiff');
const vpList = (opt.viewports || ['1280x900']).map((v, i) => {
  const [w, h] = v.split('x').map(n => parseInt(n, 10));
  return { label: `vp${i}_${w}x${h}`, width: w || 1280, height: h || 900 };
});

// Resolve backstopjs from this tool's own node_modules (installed via `npm --prefix tools/backstop i`).
// Bare specifier → node reads the package's `main` (core/runner.js); the classic default export is
// the `backstop(command, {config})` function. createRequire keeps resolution anchored to THIS dir.
let backstop;
try {
  const { createRequire } = await import('module');
  const require = createRequire(path.join(HERE, 'package.json'));
  backstop = require('backstopjs');
} catch (e) {
  emit({ ok: false, error: `backstopjs not installed — run: npm --prefix tools/backstop install\n(${e.message})` }, 2);
}

const paths = {
  bitmaps_reference: path.join(workdir, 'bitmaps_reference'),
  bitmaps_test: path.join(workdir, 'bitmaps_test'),
  html_report: path.join(workdir, 'html_report'),
  ci_report: path.join(workdir, 'ci_report'),
  engine_scripts: path.join(workdir, 'engine_scripts'),
};
fs.mkdirSync(workdir, { recursive: true });

// onReady engine script — CRITICAL for full-page captures of lazy-loaded pages (EB/Templately,
// Elementor). Without scrolling, everything below the fold never renders (lazy images stay blank,
// reveal-on-scroll sections stay hidden) → a "broken", mostly-empty reference/build screenshot.
// This scrolls the whole document top→bottom to trigger lazy-load + reveal animations, force-loads
// images, waits for decode, then returns to the top before BackstopJS shoots the full page.
const onReadyDir = path.join(paths.engine_scripts, 'puppet');
fs.mkdirSync(onReadyDir, { recursive: true });
// VIEWPORT capture (selector 'viewport' = above-the-fold, e.g. a hero-only diff): do NOT scroll the
// document. The full-page scroll below is only needed to force lazy-load lower down; for a viewport crop
// it races — the two pages can end at DIFFERENT scroll positions at capture time (one at top, one still
// mid-scroll), producing a garbage diff that compares the hero against a lower section. Above-fold content
// loads without scrolling, so just settle images and pin scroll to 0.
const heroMode = selector === 'viewport';
fs.writeFileSync(path.join(onReadyDir, 'onReady.js'), heroMode ? `module.exports = async (page) => {
  await page.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
    window.scrollTo(0, 0);
    const imgs = [...document.images].filter(i => i.getBoundingClientRect().top < innerHeight)
      .map(i => i.complete ? null : i.decode().catch(() => {}));
    await Promise.race([Promise.all(imgs), sleep(6000)]);
    window.scrollTo(0, 0); await sleep(300);
  });
  await new Promise(r => setTimeout(r, 600));
};
` : `module.exports = async (page) => {
  await page.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
    // Scroll the whole document to trigger lazy-load + reveal animations, then return to top.
    const step = Math.max(400, Math.round(window.innerHeight * 0.85));
    let y = 0;
    for (let guard = 0; guard < 400 && y < document.body.scrollHeight; guard++) {
      window.scrollTo(0, y); y += step; await sleep(120);
    }
    window.scrollTo(0, document.body.scrollHeight); await sleep(700);
    window.scrollTo(0, 0); await sleep(300);
    const imgs = [...document.images].map(i => i.complete ? null : i.decode().catch(() => {}));
    await Promise.race([Promise.all(imgs), sleep(8000)]);
  });
  await new Promise(r => setTimeout(r, 600));
};
`);

const config = {
  id: 'vrdiff',
  viewports: vpList,
  scenarios: [{
    label,
    referenceUrl,          // captured by `backstop reference`
    url: buildUrl,         // captured by `backstop test`
    selectors: [selector], // `document` = full page (fixes the viewport-only capture bug)
    misMatchThreshold: threshold * 100, // BackstopJS uses a 0..100 percentage
    requireSameDimensions: false,       // reference vs a different engine won't match to the pixel
    delay,
    onReadyScript: 'puppet/onReady.js', // scroll to trigger lazy-load BEFORE the full-page shot
    readyEvent: null,
    hideSelectors: [],
    removeSelectors: [],
  }],
  paths,
  report: open ? ['browser', 'CI'] : ['CI'],
  // playwright engine: its Chromium captures tall full pages correctly (capture-beyond-viewport),
  // unlike the puppeteer engine's bundled Chromium which mis-tiles and DUPLICATES the top-of-page
  // content into the bottom region on long pages. ignoreHTTPSErrors defaults true (handles .tst).
  engine: 'playwright',
  engineOptions: { browser: 'chromium', args: ['--no-sandbox'] },
  asyncCaptureLimit: 1,
  asyncCompareLimit: 5,
  debug: false,
};

async function run() {
  await backstop('reference', { config });
  // `test` REJECTS when a visual diff exists (expected for ref-vs-build) — treat that as data, not error.
  let testFailed = false;
  try { await backstop('test', { config }); }
  catch { testFailed = true; }

  // Read the compare report BackstopJS writes to html_report/config.js: `report(<json>);`
  const cfgJs = path.join(paths.html_report, 'config.js');
  let report = null;
  try {
    const raw = fs.readFileSync(cfgJs, 'utf8').replace(/^report\(/, '').replace(/\);?\s*$/, '');
    report = JSON.parse(raw);
  } catch (e) {
    return emit({ ok: false, error: `ran, but could not read report (${cfgJs}): ${e.message}`, htmlReport: path.join(paths.html_report, 'index.html') }, 1);
  }

  const results = (report.tests || []).map(t => {
    const p = t.pair || {};
    const d = p.diff || {};
    return {
      label: p.label, viewport: p.viewportLabel,
      status: t.status,
      misMatchPct: d.misMatchPercentage !== undefined ? +parseFloat(d.misMatchPercentage).toFixed(2) : null,
      sameDimensions: d.isSameDimensions !== undefined ? d.isSameDimensions : null,
      dimensionDifference: d.dimensionDifference || null,
      reference: p.fileName ? path.join(paths.bitmaps_reference, p.fileName) : null,
      diffImage: p.diffImage ? path.join(paths.bitmaps_test, p.diffImage) : null,
    };
  });
  const worst = [...results].sort((a, b) => (b.misMatchPct || 0) - (a.misMatchPct || 0))[0] || {};
  emit({
    ok: true,
    engine: 'backstopjs',
    reference: referenceUrl, build: buildUrl,
    passed: !testFailed,
    scenarios: results,
    worstMisMatchPct: worst.misMatchPct ?? null,
    htmlReport: path.join(paths.html_report, 'index.html'),
    workdir,
  });
}

run().catch(e => emit({ ok: false, error: e && e.message ? e.message : String(e) }, 1));
