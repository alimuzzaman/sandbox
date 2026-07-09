from __future__ import annotations
import json
import os
import re
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register
from sandbox.core._plugin_check_report import render_report


# See docs/plugin-check.md and specs/013-plugin-check/ for the full design this module
# implements. Brings WordPress.org's official Plugin Check tool (`wp plugin check`)
# in as a first-class sandbox command, applying the SAME baseline-gate pattern already
# used elsewhere for noisy-but-mostly-accepted lint output: only NEW (file, rule)
# findings beyond a committed baseline fail a run. This was ported from a working,
# project-local reference implementation (a Node script in a real plugin repo) rather
# than designed from scratch — see research.md's decisions for what changed and why.

def _resolve_plugin_check_config(pconf: dict) -> dict:
    """Read + validate the `pluginCheck` section of a project's resolved config.
    `slug` has NO reasonable default (spec FR-002) — dies loud with an actionable
    message rather than guessing or silently no-op'ing."""
    pc = pconf.get("pluginCheck") or {}
    slug = (pc.get("slug") or "").strip()
    if not slug:
        die(
            "no `pluginCheck.slug` configured — add it to sandbox.config.json, e.g.:\n"
            '  "pluginCheck": { "slug": "your-plugin-slug" }\n'
            "(no default is possible — this is the plugin `wp plugin check` inspects)"
        )
    return {
        "slug": slug,
        "exclude_directories": list(pc.get("excludeDirectories") or []),
        "version_file": pc.get("versionFile") or f"{slug}.php",
        "baseline_file": pc.get("baselineFile") or "plugin-check-baseline.json",
    }


def _run_wp_plugin_check(instance: str, slug: str, exclude_dirs: list[str]) -> str:
    """Run `wp plugin check` against the instance and return its raw stdout.

    `wp plugin check`'s own exit code is NOT a pass/fail signal here — findings
    alone don't make it non-zero. What distinguishes "the command never actually
    ran" (bad flag, instance down, plugin not installed/active — an infrastructure
    failure, spec FR-010) from "it ran and reported findings" is whether ANY
    output was captured at all, not the exit code. Verified against the reference
    implementation's own `runPluginCheck`, which uses the identical distinction."""
    args = ["plugin", "check", slug, "--format=json"]
    if exclude_dirs:
        args.append(f"--exclude-directories={','.join(exclude_dirs)}")
    res = wpcli(args, instance=instance, check=False, capture=True)
    out = res.stdout or ""
    if not out.strip():
        die(
            f"`wp plugin check {slug}` produced no output — the command itself "
            f"didn't run (check the plugin is installed+active, and that the "
            f"instance is up).\nstderr: {(res.stderr or '').strip()[:500]}"
        )
    return out


def _parse_findings(output: str) -> list[dict]:
    """`wp plugin check --format=json` prints one `FILE: <path>` line followed by a
    JSON array line, repeated per file with findings — not a single JSON document.
    Parse that into a flat findings list, each tagged with its file. Ported from the
    reference implementation's `parseFindings` (identical shape, translated to
    Python)."""
    findings: list[dict] = []
    current_file: str | None = None
    for line in output.splitlines():
        m = re.match(r"^FILE:\s*(.+)$", line)
        if m:
            current_file = m.group(1).strip()
            continue
        trimmed = line.strip()
        if not trimmed.startswith("[") or current_file is None:
            continue
        try:
            items = json.loads(trimmed)
        except (json.JSONDecodeError, ValueError):
            continue
        for item in items:
            findings.append({"file": current_file, **item})
    return findings


def _count_by_key(findings: list[dict]) -> dict[str, int]:
    """Baseline key: (file, rule-code) pair, ERROR-severity findings only — line/
    column drift with refactors must never matter (spec FR-007)."""
    counts: dict[str, int] = {}
    for f in findings:
        if f.get("type") != "ERROR":
            continue
        key = f"{f.get('file')}::{f.get('code')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _load_baseline(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_baseline(path: Path, counts: dict[str, int]) -> None:
    path.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")


def _diff_against_baseline(current: dict[str, int], baseline: dict[str, int]) -> list[dict]:
    """Only (file, rule) pairs whose CURRENT count exceeds the baselined count are
    violations — a pre-existing baselined finding never fails a run by itself
    (spec FR-006)."""
    violations = []
    for key, count in current.items():
        allowed = baseline.get(key, 0)
        if count > allowed:
            violations.append({"key": key, "current": count, "baseline": allowed,
                               "delta": count - allowed})
    return sorted(violations, key=lambda v: v["key"])


def _read_version_header(path: Path) -> str:
    if not path.is_file():
        return "unknown"
    m = re.search(r"^\s*\*?\s*Version:\s*(.+)$", path.read_text(errors="replace"), re.MULTILINE)
    return m.group(1).strip() if m else "unknown"


def _checker_version(pconf: dict) -> str:
    """Best-effort version string for the `plugin-check` plugin itself, parsed from
    its pinned zip URL in the project's resolved plugin map (mirrors the reference
    implementation's own regex-on-the-configured-URL approach)."""
    entry = (pconf.get("plugins_resolved") or {}).get("plugin-check") or {}
    source = entry.get("source") or entry.get("zip") or ""
    m = re.search(r"plugin-check\.([\d.]+)\.zip", str(source))
    return m.group(1) if m else "unknown"


def cmd_plugin_check(cfg, args) -> None:
    """`./sb plugin-check --project-dir DIR [--update] [--json]` — run WordPress.org's
    Plugin Check against a project's configured plugin, gated by a committed baseline
    (docs/plugin-check.md; specs/013-plugin-check/). Only NEW (file, rule) findings
    beyond the baseline fail the run; WARNING-level findings are reported/rendered but
    never gate. `--update` rewrites the baseline to match current findings exactly.
    Always writes a self-contained HTML report to
    tests/test-results/plugin-check-report.html, whether the gate passes or fails.
    """
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    root = Path(pconf["root"])
    as_json = bool(getattr(args, "json", False))
    do_update = bool(getattr(args, "update", False))

    pc = _resolve_plugin_check_config(pconf)
    entry = ensure_instance(cfg, str(root), create=True)
    instance = entry["instance"]

    raw = _run_wp_plugin_check(instance, pc["slug"], pc["exclude_directories"])
    findings = _parse_findings(raw)

    results_dir = root / "tests" / "test-results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "plugin-check.json").write_text(json.dumps(findings, indent=2) + "\n")
    log_lines = [f"{f.get('type')}\t{f.get('file')}:{f.get('line')}:{f.get('column')}\t"
                f"{f.get('code')}\t{f.get('message')}" for f in findings]
    (results_dir / "plugin-check.log").write_text("\n".join(log_lines) + "\n")

    current = _count_by_key(findings)
    errors = [f for f in findings if f.get("type") == "ERROR"]
    warnings = [f for f in findings if f.get("type") == "WARNING"]
    baseline_path = root / pc["baseline_file"]
    had_baseline = baseline_path.is_file()
    baseline = _load_baseline(baseline_path)

    report_path = results_dir / "plugin-check-report.html"
    meta = {
        "plugin_slug": pc["slug"],
        "plugin_version": _read_version_header(root / pc["version_file"]),
        "checker_version": _checker_version(pconf),
        "wp_version": pconf.get("wpVersion") or "unknown",
        "php_version": pconf.get("phpVersion") or "unknown",
        "exclude_directories": pc["exclude_directories"],
        "baseline_file": pc["baseline_file"],
    }

    if do_update:
        _write_baseline(baseline_path, current)
        total = sum(current.values())
        meta["baseline_total"] = total
        meta["new_count"] = 0
        report_path.write_text(render_report(findings, meta))
        result = {"ok": True, "action": "update", "plugin_slug": pc["slug"],
                 "errors": len(errors), "warnings": len(warnings),
                 "baseline_total": total, "new_count": 0, "violations": [],
                 "report_path": str(report_path.relative_to(root)), "error": None}
        if as_json:
            print(json.dumps(result))
        else:
            ok(f"plugin-check baseline written: {len(current)} finding-type(s) / "
              f"{total} ERROR(s) -> {pc['baseline_file']}")
            print(f"HTML report: {report_path}")
        return

    baseline_total = sum(baseline.values())
    violations = _diff_against_baseline(current, baseline)
    new_count = sum(v["delta"] for v in violations)
    meta["baseline_total"] = baseline_total
    meta["new_count"] = new_count
    report_path.write_text(render_report(findings, meta))

    result = {"ok": new_count == 0, "action": "check", "plugin_slug": pc["slug"],
             "errors": len(errors), "warnings": len(warnings),
             "baseline_total": baseline_total, "new_count": new_count,
             "violations": violations,
             "report_path": str(report_path.relative_to(root)), "error": None}

    if not had_baseline and as_json:
        result["error"] = ("no baseline exists yet — run with --update to establish "
                           "one from current findings")
    if as_json:
        print(json.dumps(result))
        if not result["ok"]:
            import sys as _sys
            _sys.exit(1)
        return

    if not had_baseline:
        info(f"no baseline found at {pc['baseline_file']} — this run is NOT gated. "
            f"Run `./sb plugin-check --update` to establish one from the "
            f"{len(current)} current finding-type(s).")
        print(f"HTML report: {report_path}")
        return

    print(f"Plugin Check: {len(errors)} error(s) [{baseline_total} baselined], "
         f"{len(warnings)} warning(s) [not gated] across "
         f"{len({f['file'] for f in findings})} file(s).")
    print(f"HTML report: {report_path}")
    if violations:
        die_lines = [f"  {v['key']}: {v['current']} (baseline {v['baseline']}, "
                     f"+{v['delta']})" for v in violations]
        die("Plugin Check gate FAILED — NEW ERROR-level finding(s) not in the "
           "baseline:\n" + "\n".join(die_lines) +
           "\n\nFix them, or if deliberate, run: ./sb plugin-check --update")
    total = sum(current.values())
    if total < baseline_total:
        ok(f"Plugin Check gate passed — and error debt DROPPED "
          f"({baseline_total} -> {total}). Run --update to tighten the baseline.")
    else:
        ok(f"Plugin Check gate passed — no new errors ({total} baselined, frozen).")


register({'plugin-check': cmd_plugin_check})
