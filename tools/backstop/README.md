# vrdiff — BackstopJS visual-regression diff (web preview)

Diffs a **reference URL** against a **build URL** and produces a browsable **HTML web report**
(reference | test | diff, per viewport). This is the *visual / web-preview* pass of the
design-fidelity workflow. It **replaces pixelmatch for the visual comparison**; the numeric
locator (`sb pxdiff` → per-band `worstBands`/`dimensionsMatch`) stays available as a fallback.

Why BackstopJS over the old pixelmatch harness: BackstopJS drives its **own full-page**
screenshots from the URLs (default selector `document` = the whole document), so the build is
always captured full-page at the reference viewport — the old harness compared a full-page
reference against a single ~952px build viewport, which silently hid the real drift.

Uses BackstopJS's **playwright engine** (not puppeteer): the puppeteer engine's bundled Chromium
mis-tiles long full-page captures and DUPLICATES the top-of-page content into the bottom region
(a header/hero appearing mid-page in the report). Playwright's Chromium captures tall pages
correctly. `ignoreHTTPSErrors` defaults on, so self-signed `.tst` build URLs work.

## Install (one-time)

```bash
npm --prefix tools/backstop install    # pulls backstopjs + a headless Chromium
```

## Use

```bash
# reference URL first, build URL second
sb vrdiff https://reference.example.test/ https://sandbox.tst/build/ \
   --label home --viewport 1280x900

# multiple viewports, no auto-open, raw JSON
sb vrdiff <refUrl> <buildUrl> --viewport 1280x900 --viewport 768x1024 --no-open --json
```

Or call the runner directly:

```bash
node tools/backstop/vrdiff.mjs <refUrl> <buildUrl> --label home --viewport 1280x900
```

By default it opens the HTML report (`tmp/vrdiff/html_report/index.html`) in your browser.

## Flags

| flag | default | meaning |
|---|---|---|
| `--label` | `page` | scenario label in the report |
| `--viewport WxH` | `1280x900` | viewport (repeatable for responsive checks) |
| `--selector` | `document` | capture selector; `document` = full page |
| `--threshold` | `0.1` | mismatch tolerance 0..1 |
| `--delay` | `1500` | ms after load before capture (lets fonts/images settle) |
| `--workdir` | `tmp/vrdiff` | where bitmaps + the HTML report are written |
| `--no-open` | off | don't auto-open the report |
| `--json` | off | emit the raw JSON summary |

## Output

Prints per-viewport mismatch % and the `web report:` path. `tmp/vrdiff/` (bitmaps + report) and
`tools/backstop/node_modules/` are gitignored — the report is a local artifact, not a committed one.

Notes: self-signed `.tst` certs are accepted (`--ignore-certificate-errors`);
`requireSameDimensions:false`, since a cross-engine rebuild never matches to the exact pixel —
read the mismatch % + the visual diff, and use `sb pxdiff` for the per-band numeric locator.
