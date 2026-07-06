#!/usr/bin/env python3
"""specextract.py@1 — drive a headless browser, run extract-web.js, emit DesignSpec v1.

Phase-1 extraction, scripted. Loads a URL, prepares the page the way the design-fidelity
workflow demands before ANY measurement — force-load lazy images, dwell-scroll so they
fetch+decode+reflow, freeze animation, wait for webfonts — then runs the canonical
`extract-web.js` and writes the DesignSpec v1 JSON that `sb specdiff`/`sb specgate` consume.

This removes the manual "paste the function into browser_evaluate on the reference AND the
build" step, so the no-judgement spine is three one-liners:
    sb specextract <refUrl>   --out ref.json
    sb specextract <buildUrl> --out build.json   # after the agent BUILD
    sb specgate ref.json build.json

Runs under tools/ Playwright venv (same as `sb visit`). It does NOT replace the BUILD or the
FIX-by-cause (those stay agent work), nor the pixel/vision pass (`sb pxdiff`/`sb vrdiff`).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout

# Default extractor: the canonical one that lives next to the skill. Resolved relative to the
# repo root (…/sandbox), which is three parents up from tools/dfdiff/specextract.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTOR = REPO_ROOT / "skills" / "design-fidelity-diff" / "extract-web.js"

# Page-prep injected before extraction. Freeze motion, promote every lazy image to eager
# (native loading="lazy" AND the common data-src lazyload schemes), and dwell-scroll top→bottom
# so viewport-gated media starts fetching + the layout reflows. Image DOWNLOAD is then awaited
# on the Python side (network-idle + a bounded completeness poll) — a fixed JS decode() race is
# too short for a heavy page of remote images (measured: 49/56 unloaded on the flexigency demo).
PREP_JS = r"""
async ({ freeze, dwellMs }) => {
  // transition:none is safe to force immediately — a class-toggled reveal then applies its
  // final state INSTANTLY (no animation to interrupt). animation-play-state:paused is NOT safe
  // to force yet: a scroll-triggered @keyframes reveal (IntersectionObserver adds a class that
  // starts a slide-in animation) would freeze at frame 0 — its off-screen starting transform —
  // the instant the class is added, since play-state is already forced paused. So transitions
  // are killed now; animations are paused ONLY after the dwell-scroll below lets them complete.
  if (freeze) {
    const s = document.createElement('style');
    s.textContent = '*,*::before,*::after{transition:none!important;scroll-behavior:auto!important}';
    document.documentElement.appendChild(s);
    document.querySelectorAll('video,audio').forEach(m => { try { m.pause(); } catch (e) {} });
  }
  // promote lazy images: native loading, plus data-src/data-lazy-src/data-srcset schemes
  document.querySelectorAll('img').forEach(i => {
    i.loading = 'eager';
    const ds = i.getAttribute('data-src') || i.getAttribute('data-lazy-src');
    const dss = i.getAttribute('data-srcset') || i.getAttribute('data-lazy-srcset');
    if (ds && ds !== i.src) i.src = ds;
    else if (i.src) i.src = i.src;
    if (dss && !i.srcset) i.srcset = dss;
  });
  const H = document.body.scrollHeight, step = Math.max(1, Math.floor(innerHeight * 0.8));
  for (let y = 0; y <= H; y += step) { scrollTo(0, y); await new Promise(r => setTimeout(r, dwellMs)); }
  // let the last scroll-triggered reveal finish its animation before we pause anything
  await new Promise(r => setTimeout(r, 700));
  if (freeze) {
    const s2 = document.createElement('style');
    s2.textContent = '*,*::before,*::after{animation-play-state:paused!important}';
    document.documentElement.appendChild(s2);
    // Pin JS-driven scroll-reveal/parallax BEFORE scrolling back to top — a scroll-LINKED
    // library (not a one-time class toggle) ties inline transform/opacity directly to scroll
    // position, so scrollTo(0,0) below would snap it back to its off-screen starting value.
    // Bake the CURRENT computed transform/opacity into an inline !important override so the
    // scroll-back can't move it again (measured: flexigency's stat-card row read x=-249 while
    // visually rendered at x=52 — a mid-parallax frame captured after an unconditional scroll-up).
    document.querySelectorAll('body *').forEach(el => {
      const cs = getComputedStyle(el);
      if (cs.transform && cs.transform !== 'none') el.style.setProperty('transform', cs.transform, 'important');
      if (cs.opacity && cs.opacity !== '1') el.style.setProperty('opacity', cs.opacity, 'important');
    });
  }
  scrollTo(0, 0);
  await new Promise(r => setTimeout(r, 300));
  return { images: document.images.length };
}
"""

# Run after images have had time to download (Python-side network-idle + poll). Bounded decode +
# fonts.ready, then report stragglers so the caller can warn (a still-unloaded img = a section
# that may measure collapsed — a measurement bug, not a build defect).
SETTLE_JS = r"""
async () => {
  const decs = [...document.images].map(i => i.decode().catch(() => {}));
  await Promise.race([Promise.all(decs), new Promise(r => setTimeout(r, 5000))]);
  try { await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 4000))]); } catch (e) {}
  const imgs = [...document.images];
  return { images: imgs.length, notComplete: imgs.filter(i => !(i.complete && i.naturalWidth > 0)).length };
}
"""

# Count still-loading images — the poll predicate.
COUNT_JS = "() => [...document.images].filter(i => !(i.complete && i.naturalWidth > 0)).length"


def _wp_login_url(target_url: str) -> str:
    parts = urlsplit(target_url)
    return urlunsplit((parts.scheme, parts.netloc, "/wp-login.php", "", ""))


def _do_login(page, target_url: str, user: str, password: str):
    """Mirror of tools/visit/visit.py — submit wp-login.php; None on success, else error str."""
    try:
        page.goto(_wp_login_url(target_url), wait_until="domcontentloaded", timeout=15000)
        page.fill("#user_login", user)
        page.fill("#user_pass", password)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
            page.click("#wp-submit")
    except PWTimeout:
        return "login: timeout waiting for redirect after submit"
    except PWError as e:
        return f"login: {e}"
    if "wp-login.php" in page.url:
        return "login: still on wp-login.php (wrong credentials?)"
    return None


def load_extractor(path: Path) -> str:
    """Read extract-web.js and return the bare `() => {...}` function expression Playwright
    can call — strip the leading file-level `//` comment banner."""
    src = path.read_text()
    marker = src.find("() => {")
    if marker < 0:
        raise SystemExit(f"specextract: {path} has no `() => {{` extractor function")
    return src[marker:]


def run(args) -> int:
    extractor_path = Path(args.extractor).expanduser() if args.extractor else DEFAULT_EXTRACTOR
    if not extractor_path.is_file():
        print(f"specextract: extractor not found: {extractor_path}", file=sys.stderr)
        return 2
    extractor = load_extractor(extractor_path)

    console_errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # ignore TLS errors: local .tst instances use a local CA Chromium may not trust, and
        # external references are arbitrary https — we only read rendered geometry, not secrets.
        ctx = browser.new_context(viewport={"width": args.width, "height": args.height},
                                  ignore_https_errors=True)
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        needs_login = args.login or (args.auto_login and "/wp-admin/" in args.url)
        if needs_login:
            user = args.login_user or os.environ.get("WP_ADMIN_USER", "admin")
            pwd = args.login_password or os.environ.get("WP_ADMIN_PASSWORD", "")
            if not pwd:
                print("specextract: login requested but no password (WP_ADMIN_PASSWORD / --login-password)", file=sys.stderr)
                return 2
            err = _do_login(page, args.url, user, pwd)
            if err:
                print(f"specextract: {err}", file=sys.stderr)
                return 2

        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
        except PWTimeout:
            print(f"specextract: navigation timeout after {args.timeout}s", file=sys.stderr)
            return 1
        except PWError as e:
            print(f"specextract: navigation error: {e}", file=sys.stderr)
            return 1

        # Force viewport width every run — the skill's #1 measurement artifact is a reset
        # viewport centering a boxed layout with fat margins (every x shifts).
        page.set_viewport_size({"width": args.width, "height": args.height})

        if args.root:
            page.evaluate("(r) => { window.__DS_ROOT = r; }", args.root)

        try:
            page.evaluate(PREP_JS, {"freeze": not args.no_freeze, "dwellMs": args.dwell})
        except PWError as e:
            print(f"specextract: page-prep failed: {e}", file=sys.stderr)
            return 1

        # Wait for image downloads to settle: network-idle first, then a bounded completeness
        # poll (nudge-scroll to trip any IntersectionObserver loaders) up to --settle seconds.
        try:
            page.wait_for_load_state("networkidle", timeout=min(args.settle, 20) * 1000)
        except PWTimeout:
            pass
        deadline = time.monotonic() + args.settle
        remaining = args.width  # placeholder to enter loop
        while time.monotonic() < deadline:
            remaining = page.evaluate(COUNT_JS)
            if not remaining:
                break
            page.evaluate("() => scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            page.evaluate("() => scrollTo(0, 0)")
            page.wait_for_timeout(400)

        settle = page.evaluate(SETTLE_JS)
        if settle and settle.get("notComplete"):
            print(f"specextract: WARNING {settle['notComplete']}/{settle['images']} images still not "
                  f"loaded after {args.settle}s settle — raise --settle, or treat short sections as a "
                  f"measurement artifact (not a build defect)", file=sys.stderr)

        try:
            spec = page.evaluate(extractor)
        except PWError as e:
            print(f"specextract: extractor failed: {e}", file=sys.stderr)
            return 1
        finally:
            browser.close()

    if not isinstance(spec, dict) or "sections" not in spec:
        print("specextract: extractor returned no DesignSpec (wrong ROOT? try --root)", file=sys.stderr)
        return 1

    text = json.dumps(spec, indent=2)
    if args.out and args.out != "-":
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    else:
        print(text)

    # status → stderr so `--out -` stays clean JSON on stdout
    secs = spec.get("sections", [])
    n_el = sum(len(s.get("elements", []) or []) for s in secs)
    fonts = spec.get("fonts", []) or []
    n_font_bad = sum(1 for f in fonts if f.get("loaded") is False)
    dest = args.out if (args.out and args.out != "-") else "(stdout)"
    print(f"specextract: {len(secs)} sections · {n_el} elements · {len(fonts)} fonts"
          f"{f' ({n_font_bad} NOT loaded)' if n_font_bad else ''}"
          f" · cw={spec.get('page', {}).get('contentMaxWidth')} → {dest}", file=sys.stderr)
    if console_errors:
        print(f"specextract: {len(console_errors)} console error(s) on the page (first: {console_errors[0][:120]})", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="specextract", description="Run extract-web.js on a URL → DesignSpec v1 JSON.")
    p.add_argument("url", help="URL to extract (reference design or the build page)")
    p.add_argument("--out", default="-", help="write DesignSpec JSON here (default: stdout)")
    p.add_argument("--root", default=None, help="override window.__DS_ROOT content-root selector (e.g. .elementor, .eb-fullwidth-content-wrapper)")
    p.add_argument("--extractor", default=None, help=f"extractor JS (default: {DEFAULT_EXTRACTOR})")
    p.add_argument("--width", type=int, default=1280, help="viewport width — MUST match ref↔build (default 1280)")
    p.add_argument("--height", type=int, default=900, help="viewport height (default 900)")
    p.add_argument("--dwell", type=int, default=450, help="ms dwell per scroll step so lazy media loads (default 450)")
    p.add_argument("--settle", type=int, default=30, help="max seconds to wait for image downloads to complete (default 30)")
    p.add_argument("--no-freeze", action="store_true", help="do NOT pause CSS animation/transition/media before measuring")
    p.add_argument("--timeout", type=int, default=30, help="navigation timeout seconds (default 30)")
    p.add_argument("--login", action="store_true", help="log in via wp-login.php before navigating")
    p.add_argument("--auto-login", action="store_true", help="log in only when the URL is /wp-admin/")
    p.add_argument("--login-user", default="", help="WP username (else $WP_ADMIN_USER or 'admin')")
    p.add_argument("--login-password", default="", help="WP password (else $WP_ADMIN_PASSWORD)")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
