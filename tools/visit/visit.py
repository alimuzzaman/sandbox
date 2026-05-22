"""Headless browser inspector for the Sandbox CLI.

Loads a URL in headless Chromium and emits a structured JSON report so a
shell / agent can verify rendered DOM without writing one-off scripts:

    $ ./sb visit http://localhost:8188/some-page/

    {
      "url": "...",
      "status": 200,
      "load_ms": 412,
      "title": "...",
      "iframes": [{"src": "...", "loaded": true, "frame_title": "...", ...}],
      "console": [{"type": "error", "text": "..."}],
      "network_failures": [{"url": "...", "status": 404}]
    }

Exit code is non-zero when *real* user-visible problems are detected:
  - main document returned 4xx/5xx
  - a console.error fired (warnings are reported but don't fail)
  - any iframe failed to load (only when --check-iframes is set)

This file is invoked by `./sb visit` from inside a sandbox-managed venv;
it isn't intended to be run directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout


def _wp_login_url(target_url: str) -> str:
    """Derive the wp-login.php URL from the target URL's host."""
    parts = urlsplit(target_url)
    return urlunsplit((parts.scheme, parts.netloc, "/wp-login.php", "", ""))


def _do_login(page, target_url: str, user: str, password: str) -> str | None:
    """Submit wp-login.php with `user` / `password`. Returns None on success,
    error string on failure. Reuses the page's context so subsequent navigations
    carry the session cookie."""
    login_url = _wp_login_url(target_url)
    try:
        page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
        page.fill("#user_login", user)
        page.fill("#user_pass", password)
        # Form submit + wait for redirect off wp-login.php.
        with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
            page.click("#wp-submit")
    except PWTimeout:
        return "login: timeout waiting for redirect after submit"
    except PWError as e:
        return f"login: {e}"
    # WordPress redirects to wp-admin on success; the .login class stays on
    # wp-login.php for a failed attempt. Either signal is reliable.
    if "wp-login.php" in page.url:
        return "login: still on wp-login.php (wrong credentials?)"
    return None


def run(args: argparse.Namespace) -> int:
    report: dict = {
        "url": args.url,
        "status": None,
        "load_ms": None,
        "title": None,
        "iframes": [],
        "console": [],
        "network_failures": [],
        "errors": [],
    }

    failed = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            user_agent=args.user_agent or None,
        )
        page = ctx.new_page()

        main_status: dict = {"value": None}

        def on_response(resp):
            # Capture the status of the top-level document.
            if main_status["value"] is None and resp.url.rstrip("/") == args.url.rstrip("/"):
                main_status["value"] = resp.status
            # Track any 4xx/5xx for sub-resources.
            if resp.status >= 400:
                report["network_failures"].append({
                    "url": resp.url,
                    "status": resp.status,
                    "resource_type": resp.request.resource_type,
                })

        def on_console(msg):
            # Browser console output — useful for surfacing JS errors.
            if msg.type in ("error", "warning"):
                report["console"].append({"type": msg.type, "text": msg.text})

        def on_pageerror(exc):
            report["console"].append({"type": "error", "text": str(exc)})

        page.on("response", on_response)
        page.on("console", on_console)
        page.on("pageerror", on_pageerror)

        # Auto-login when --login is set OR when the target URL is wp-admin
        # and we have credentials available. The session cookie set by
        # wp-login.php sticks to the page context for the actual navigation.
        needs_login = args.login or (
            args.auto_login and "/wp-admin/" in args.url
        )
        if needs_login:
            user = args.login_user or os.environ.get("WP_ADMIN_USER", "admin")
            pw = args.login_password or os.environ.get("WP_ADMIN_PASSWORD", "")
            if not pw:
                report["errors"].append(
                    "login requested but WP_ADMIN_PASSWORD is empty "
                    "and --login-password not provided"
                )
                failed = True
            else:
                err = _do_login(page, args.url, user, pw)
                if err:
                    report["errors"].append(err)
                    failed = True

        try:
            if failed:
                raise PWError("skipping navigation — pre-navigation step failed")
            resp = page.goto(args.url, wait_until=args.wait_until, timeout=args.timeout * 1000)
            if resp is not None and main_status["value"] is None:
                main_status["value"] = resp.status
        except PWTimeout:
            report["errors"].append(f"navigation timeout after {args.timeout}s")
            failed = True
        except PWError as e:
            report["errors"].append(f"navigation error: {e}")
            failed = True

        # Give frames a moment to settle if we asked for iframe checks.
        if args.check_iframes and not failed:
            page.wait_for_timeout(args.iframe_settle * 1000)

        # Pull metadata.
        try:
            report["title"] = page.title()
        except PWError:
            pass

        report["status"] = main_status["value"]

        # Inventory iframes from the DOM.
        try:
            iframes_raw = page.eval_on_selector_all(
                "iframe",
                """els => els.map(e => {
                    const r = e.getBoundingClientRect();
                    return {
                        src: e.getAttribute('src'),
                        cls: e.getAttribute('class'),
                        title: e.getAttribute('title'),
                        width: r.width,
                        height: r.height,
                        visible: r.width > 0 && r.height > 0,
                    };
                })""",
            )
        except PWError as e:
            iframes_raw = []
            report["errors"].append(f"iframe scan failed: {e}")

        for raw in iframes_raw:
            entry = dict(raw)
            entry["loaded"] = None
            entry["frame_title"] = None
            if args.check_iframes and raw.get("src"):
                frame = next(
                    (f for f in page.frames if f.url and f.url == raw["src"]),
                    None,
                )
                if frame is None:
                    # Fallback: match by hostname (some embeds redirect / mutate src).
                    src_host = raw["src"].split("/")[2] if "://" in raw["src"] else None
                    if src_host:
                        frame = next(
                            (f for f in page.frames if src_host in (f.url or "")),
                            None,
                        )
                if frame is not None:
                    try:
                        entry["frame_title"] = frame.title()
                        entry["loaded"] = True
                    except PWError:
                        entry["loaded"] = False
                else:
                    entry["loaded"] = False
            report["iframes"].append(entry)

        # Optional screenshot for visual verification.
        if args.screenshot:
            out = Path(args.screenshot).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), full_page=args.full_page)
            report["screenshot"] = str(out)

        # Measure how long the page took (rough). loadEventEnd is 0 until the
        # load event fires, which never happens for some error pages — in that
        # case report null rather than a garbage negative number.
        try:
            ms = page.evaluate(
                "() => { const t = performance.timing;"
                " return t.loadEventEnd > 0"
                " ? Math.round(t.loadEventEnd - t.navigationStart) : null; }"
            )
            report["load_ms"] = ms
        except PWError:
            pass

        ctx.close()
        browser.close()

    # Decide exit code.
    status = report["status"]
    if status is None or status >= 400:
        failed = True
    if any(c["type"] == "error" for c in report["console"]):
        failed = True
    if args.check_iframes and any(
        f.get("loaded") is False for f in report["iframes"]
    ):
        failed = True

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(prog="sb visit")
    p.add_argument("url", help="URL to load")
    p.add_argument("--check-iframes", action="store_true",
                   help="probe each iframe and confirm it loaded successfully")
    p.add_argument("--screenshot", help="save a PNG to this path")
    p.add_argument("--full-page", action="store_true",
                   help="screenshot the full scrollable page (default: viewport only)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--timeout", type=int, default=20,
                   help="navigation timeout in seconds (default: 20)")
    p.add_argument("--iframe-settle", type=int, default=2,
                   help="seconds to wait for iframes to load before probing (default: 2)")
    p.add_argument("--wait-until",
                   choices=["load", "domcontentloaded", "networkidle", "commit"],
                   default="domcontentloaded",
                   help="navigation wait condition (default: domcontentloaded)")
    p.add_argument("--login", action="store_true",
        help="Force a wp-login.php form submission before navigation.")
    p.add_argument("--auto-login", action="store_true",
        help="Auto-submit wp-login.php when the target URL is /wp-admin/.")
    p.add_argument("--login-user", default="",
        help="Override WP_ADMIN_USER for the login step.")
    p.add_argument("--login-password", default="",
        help="Override WP_ADMIN_PASSWORD for the login step.")
    p.add_argument("--user-agent", default="",
                   help="override the browser User-Agent")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
