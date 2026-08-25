from __future__ import annotations
import json
import os
import re
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register
from sandbox.core._plugin_check_report import render_report


class PluginCheckOutputError(ValueError):
    """`wp plugin check --format=json` did not emit its documented format."""


# See docs/plugin-check.md and specs/013-plugin-check/ for the full design this module
# implements. Brings WordPress.org's official Plugin Check tool (`wp plugin check`)
# in as a first-class sandbox command, applying the SAME baseline-gate pattern already
# used elsewhere for noisy-but-mostly-accepted lint output: only NEW (file, rule)
# findings beyond a committed baseline fail a run. This was ported from a working,
# project-local reference implementation (a Node script in a real plugin repo) rather
# than designed from scratch — see research.md's decisions for what changed and why.

def _read_distignore_directories(root: Path) -> list[str]:
    """Auto-detect exclude-directories from a project's own `.distignore` (the
    WordPress.org SVN-deploy ignore file most real plugins already have —
    used by e.g. the 10up deploy action) when `pluginCheck.excludeDirectories`
    isn't set explicitly. Avoids requiring a project to hand-duplicate the
    same directory list in a second, sandbox-specific place — the reference
    implementation this was ported from hardcoded its own such list (a
    comment on it says it "mirrors .distignore's directory entries", i.e. it
    was already manually kept in sync with a file exactly like this one).

    `.distignore` mixes directory entries (`tests/`, `.claude`,
    `modules/developer-tools`), bare filenames (`composer.json`, `README.md`),
    and glob patterns (`*.sql`) — this does not try to distinguish them.
    Passing a non-directory entry to `wp plugin check --exclude-directories`
    is harmless (it silently matches nothing); the real risk this exists to
    avoid is UNDER-inclusion (a genuine directory Plugin Check scans that
    never actually ships), so over-inclusion from the unfiltered file is an
    acceptable, safe default."""
    path = root / ".distignore"
    if not path.is_file():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for line in path.read_text(errors="replace").splitlines():
        entry = line.strip().strip("/")
        if not entry or line.strip().startswith("#"):
            continue
        if entry not in seen:
            seen.add(entry)
            out.append(entry)
    return out


def _resolve_plugin_check_config(pconf: dict) -> dict:
    """Read + validate the `pluginCheck` section of a project's resolved config.

    There is no `pluginCheck.slug` override key. When the canonical plugin map
    declares a path entry for the project root (the normal `"my-plugin": "."`
    form), that map key is the authoritative install slug. This matters for
    disposable review directories whose root name/top-level `slug` is unique
    only for isolation. If no self-path entry exists, retain the historical
    top-level `slug`/directory-name fallback.

    `excludeDirectories` falls back to `.distignore` (see
    `_read_distignore_directories`) when the project hasn't set its own list
    explicitly — an empty/unset `excludeDirectories` is treated as "not
    customized," not as "explicitly zero exclusions" (a project that
    genuinely wants zero exclusions despite having a `.distignore` is an
    edge case rare enough not to need a dedicated escape hatch yet)."""
    sc = _core()
    pc = pconf.get("pluginCheck") or {}
    root = Path(pconf["root"]).expanduser().resolve()

    # Prefer the canonical slug-keyed map's self-path entry. The normalized
    # form is available on every current descriptor, while the raw map fallback
    # keeps this helper useful for older/direct callers in unit tests.
    candidates: list[str] = []

    def is_self_path(value: object) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            source = Path(value).expanduser()
            if not source.is_absolute():
                source = root / source
            return source.resolve() == root
        except (OSError, RuntimeError, ValueError):
            return False

    resolved = pconf.get("plugins_resolved")
    if isinstance(resolved, dict):
        for candidate, entry in resolved.items():
            source = entry.get("source") if isinstance(entry, dict) else None
            if (isinstance(candidate, str) and isinstance(source, dict)
                    and source.get("kind") == "path"
                    and is_self_path(source.get("value"))):
                candidates.append(candidate)
    if not candidates and isinstance(pconf.get("plugins"), dict):
        for candidate, value in pconf["plugins"].items():
            if isinstance(candidate, str) and is_self_path(value):
                candidates.append(candidate)

    try:
        configured_slug = pconf.get("slug")
        if isinstance(configured_slug, str) and configured_slug in candidates:
            slug = sc._project_slug(configured_slug, configured_slug)
        elif len(candidates) == 1:
            slug = sc._project_slug(candidates[0], candidates[0])
        elif len(candidates) > 1:
            names = ", ".join(sorted(candidates))
            die("could not resolve one Plugin Check target: multiple project "
                f"self-path plugin keys ({names}); keep one map entry for `.`")
        else:
            slug = sc._project_slug(configured_slug, root.name)
    except sc.ConfigError as e:
        die(f"could not resolve a plugin slug for Plugin Check: {e}")
    exclude_directories = list(pc.get("excludeDirectories") or [])
    if not exclude_directories:
        exclude_directories = _read_distignore_directories(root)
    return {
        "slug": slug,
        "exclude_directories": exclude_directories,
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


def _parse_findings(output: str, root: str | Path | None = None) -> list[dict]:
    """`wp plugin check --format=json` prints one `FILE: <path>` line followed by a
    JSON array line, repeated per file with findings — not a single JSON document.
    Parse that into a flat findings list, each tagged with its file. A single `[]`
    is also accepted for a successful zero-finding run. Any other malformed or
    unrecognised output raises `PluginCheckOutputError` so a tool failure cannot
    become a false passing gate. Ported from the reference implementation's
    `parseFindings` (identical shape, translated to Python).

    `root`, when given, converts each reported path to PROJECT-RELATIVE via
    `os.path.relpath` (matching the reference's own `path.relative(REPO_ROOT,
    ...)` — a step this port initially dropped, a real bug: sandbox bind-mounts
    plugin source at the SAME absolute host path inside the container, so `wp
    plugin check` reports real host-absolute paths like
    `/Users/you/project/includes/Foo.php`. Without stripping `root`, those
    never match a baseline keyed on relative paths like `includes/Foo.php` —
    every genuine finding would look "new" on every run. `os.path.relpath`
    (not `Path.relative_to`) matches JS's `path.relative` exactly: it never
    raises even when the reported path isn't actually under `root`, unlike
    `Path.relative_to`, which would."""
    def normalise_file(raw: str) -> str:
        if root and os.path.isabs(raw):
            return os.path.relpath(raw, str(root))
        # Plugin Check versions differ: some report host-absolute paths, while
        # current releases report project-relative paths. A relative path is
        # already in baseline identity space; do not resolve it against the
        # Sandbox checkout's cwd.
        return os.path.normpath(raw)

    lines = [(number, line.strip()) for number, line in
             enumerate(output.splitlines(), start=1) if line.strip()]
    if not lines:
        raise PluginCheckOutputError("empty output")
    # A successful zero-finding run may be emitted as a plain JSON empty array.
    # It is the only FILE-less form accepted; accepting arbitrary prose here
    # would turn tool failures into false passing gates.
    if len(lines) == 1 and lines[0][1] == "[]":
        return []

    findings: list[dict] = []
    index = 0
    while index < len(lines):
        line_number, file_line = lines[index]
        match = re.fullmatch(r"FILE:\s*(.+)", file_line)
        if not match:
            raise PluginCheckOutputError(
                f"unexpected output on line {line_number}: {file_line[:160]!r}")
        raw_file = match.group(1).strip()
        if not raw_file:
            raise PluginCheckOutputError(f"empty FILE path on line {line_number}")
        if index + 1 >= len(lines):
            raise PluginCheckOutputError(
                f"missing JSON findings array after FILE line {line_number}")
        json_line_number, json_line = lines[index + 1]
        try:
            items = json.loads(json_line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise PluginCheckOutputError(
                f"malformed JSON findings array on line {json_line_number}") from exc
        if not isinstance(items, list):
            raise PluginCheckOutputError(
                f"expected a JSON findings array on line {json_line_number}")
        current_file = normalise_file(raw_file)
        for item in items:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("type"), str)
                    or item["type"] not in {"ERROR", "WARNING"}
                    or not isinstance(item.get("code"), str)
                    or not item["code"]):
                raise PluginCheckOutputError(
                    f"unrecognised finding in JSON array on line {json_line_number}")
            findings.append({"file": current_file, **item})
        index += 2
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
    try:
        findings = _parse_findings(raw, root=root)
    except PluginCheckOutputError as exc:
        die(f"`wp plugin check {pc['slug']}` returned unrecognised output: {exc}")

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
                 "baseline_exists": True, "message": None,
                 "report_path": str(report_path.relative_to(root)), "error": None}
        if as_json:
            print(json.dumps(result))
        else:
            ok(f"plugin-check baseline written: {len(current)} finding-type(s) / "
              f"{total} ERROR(s) -> {pc['baseline_file']}")
            print(f"HTML report: {report_path}")
        return

    baseline_total = sum(baseline.values())
    # A first run is deliberately informational, not a gate. Do not expose its
    # current findings as "new" in JSON: clients use `ok`/`new_count` to decide
    # whether to stop, and must be able to distinguish this state explicitly.
    violations = _diff_against_baseline(current, baseline) if had_baseline else []
    new_count = sum(v["delta"] for v in violations)
    meta["baseline_total"] = baseline_total
    meta["new_count"] = new_count
    report_path.write_text(render_report(findings, meta))

    result = {"ok": new_count == 0, "action": "check", "plugin_slug": pc["slug"],
             "errors": len(errors), "warnings": len(warnings),
             "baseline_total": baseline_total, "new_count": new_count,
             "violations": violations, "baseline_exists": had_baseline,
             "message": None,
             "report_path": str(report_path.relative_to(root)), "error": None}

    if not had_baseline:
        result["message"] = ("no baseline exists yet — run with --update to establish "
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
