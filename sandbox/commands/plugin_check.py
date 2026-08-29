from __future__ import annotations
import json
import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register
from sandbox.core._plugin_check_report import render_report
from sandbox.plugin_check import (
    ArchivePreflightError,
    ArchiveResultError,
    ArchiveReviewJournal,
    ArchiveTargetError,
    archive_error_counts,
    build_archive_review_target,
    cleanup_receipt_complete,
    load_archive_baseline,
    open_archive,
    persist_archive_artifact,
    update_caller_baseline_atomic,
)
from sandbox.plugin_check.runner import (
    ArchiveRunnerError,
    launch_archive_runner,
    resolve_archive_provenance,
)


_SUCCESS_SUMMARY_RE = re.compile(
    r"^Success:\s*Checks complete\.\s*No errors found\.?$",
    re.IGNORECASE,
)


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

    lines = []
    success_summary = False
    for number, line in enumerate(output.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if _SUCCESS_SUMMARY_RE.fullmatch(stripped):
            success_summary = True
            continue
        lines.append((number, stripped))
    if not lines:
        if success_summary:
            return []
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


def _archive_failure(
    args,
    code: str,
    message: str,
    *,
    preflight=None,
) -> None:
    """Emit one typed archive error without falling through to source mode."""

    result = {
        "ok": False,
        "action": "update" if bool(getattr(args, "update", False)) else "check",
        "plugin_slug": getattr(preflight, "archive_slug", None),
        "errors": 0,
        "warnings": 0,
        "baseline_total": 0,
        "new_count": 0,
        "violations": [],
        "baseline_exists": None,
        "message": None,
        "report_path": None,
        "error": code,
        "input_mode": "archive",
    }
    if preflight is not None:
        result.update({
            "archive_sha256": preflight.archive_sha256,
            "archive_slug": preflight.archive_slug,
            "main_file": preflight.main_file,
            "member_count": preflight.member_count,
            "member_manifest_sha256": preflight.member_manifest_sha256,
        })
    # Keep paths and archive-controlled text out of the machine result. Human
    # output gets the stable error code plus a short reason for recovery.
    if not getattr(args, "json", False):
        die(f"{code}: {message[:500]}")
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(1)


def _archive_run_id() -> str:
    """Mint a short, lowercase run identity for the isolated target."""

    # Keep ``plugin-check-`` + this identity within Sandbox's 24-character
    # instance-name budget.  That makes the derived Compose/registry name
    # deterministic while retaining 40 bits of uniqueness for concurrent runs.
    # Journal run IDs must start with a letter; keep nine random hex
    # characters after the prefix so the total still fits the instance-name
    # budget.
    return f"r{uuid.uuid4().hex[:9]}"


def _archive_report_meta(result: Mapping[str, object], *, baseline_total: int,
                         new_count: int, baseline_file: str) -> dict[str, object]:
    provenance = result.get("checker_provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    checker = str(provenance.get("plugin_check", "unknown"))
    return {
        "plugin_slug": result.get("archive_slug") or result.get("plugin_slug") or "unknown",
        "plugin_version": result.get("plugin_version") or "unknown",
        "checker_version": checker.split("@", 1)[0],
        "wp_version": provenance.get("wordpress", "unknown"),
        "php_version": provenance.get("php", "unknown"),
        "exclude_directories": [],
        "baseline_total": baseline_total,
        "new_count": new_count,
        "baseline_file": baseline_file,
    }


def _cmd_plugin_check_archive(cfg, args, pconf: dict, root: Path, pc: dict) -> None:
    """Run the exact-release archive path through the disposable child runner."""

    archive_input = Path(str(getattr(args, "archive", ""))).expanduser()
    if not archive_input.is_absolute():
        archive_input = root / archive_input
    archive_path = archive_input.resolve(strict=False)
    preflight = None
    target = None
    journal_path = None
    try:
        # Resolve and pin provenance before any runtime or caller-state write.
        pin, provenance = resolve_archive_provenance(
            pconf,
            sandbox_root=Path(__file__).resolve().parents[2],
        )
        sc = _core()
        sandbox_home = Path(getattr(sc, "BASE", os.environ.get("SANDBOX_HOME", "~/sandbox")))
        with open_archive(archive_path) as session:
            preflight = session.inspect()
            target = build_archive_review_target(
                root,
                preflight,
                run_id=_archive_run_id(),
                sandbox_home=sandbox_home,
                plugin_check=pin,
                baseline_path=root / pc["baseline_file"],
                wordpress_version=provenance["wordpress"],
                php_version=provenance["php"],
                sandbox_revision=provenance["sandbox"],
            )
            journal_path = target.sandbox_home.parent / "archive-journal.json"
            journal = ArchiveReviewJournal.create(
                journal_path,
                run_id=target.review_instance.removeprefix("plugin-check-"),
                target=target.contract_dict(),
            )
            # Extraction is streamed from the same open descriptor used by
            # inspection and hashing; the caller checkout is never a target.
            session.extract_to(target.extraction_root, preflight)

        result = launch_archive_runner(
            target,
            journal.path,
            timeout=900,
            root=Path(__file__).resolve().parents[2],
        )
    except ArchiveRunnerError as exc:
        _archive_failure(args, exc.code, exc.code, preflight=preflight)
    except (ArchivePreflightError, ArchiveTargetError, ArchiveResultError) as exc:
        # A target may have been allocated before journal creation failed. It
        # has no recoverable ledger, so remove only that exact run directory and
        # report root; never touch the caller project or global siblings.
        if target is not None and journal_path is None:
            for path in (target.sandbox_home.parent, target.artifact_dir):
                try:
                    if path.is_dir() and not path.is_symlink():
                        import shutil
                        shutil.rmtree(path)
                except OSError:
                    pass
        error_code = (
            "archive_preflight_failed" if isinstance(exc, ArchivePreflightError)
            else getattr(exc, "code", "archive_target_failed")
        )
        _archive_failure(args, error_code, getattr(exc, "code", type(exc).__name__), preflight=preflight)
    except Exception:
        _archive_failure(args, "archive_target_failed", "archive target could not be prepared", preflight=preflight)

    if not isinstance(result, dict):
        _archive_failure(args, "archive_check_failed", "archive runner returned no result", preflight=preflight)
    # Bind stable identity from host preflight, not from child-controlled text.
    result = {
        **result,
        "input_mode": "archive",
        "archive_sha256": preflight.archive_sha256,
        "archive_slug": preflight.archive_slug,
        "plugin_slug": preflight.archive_slug,
        "main_file": preflight.main_file,
        "member_count": preflight.member_count,
        "member_manifest_sha256": preflight.member_manifest_sha256,
        "action": "update" if bool(getattr(args, "update", False)) else "check",
    }
    findings = result.get("findings")
    if not isinstance(findings, list):
        findings = []
    cleanup = result.get("cleanup")
    try:
        counts = archive_error_counts(findings)
    except ArchiveResultError:
        result["ok"] = False
        result["error"] = "archive_artifact_failed"
        counts = {}

    baseline_path = target.baseline_path
    try:
        baseline = load_archive_baseline(
            baseline_path,
            caller_project_root=root,
        )
    except ArchiveResultError:
        result["ok"] = False
        result["error"] = "archive_artifact_failed"
        baseline = {}
    had_baseline = baseline_path.is_file()
    baseline_total = sum(baseline.values())
    violations = _diff_against_baseline(counts, baseline) if had_baseline else []
    new_count = sum(item["delta"] for item in violations)
    if result.get("error") is None:
        if getattr(args, "update", False):
            if not cleanup_receipt_complete(cleanup):
                result["ok"] = False
                result["error"] = "archive_cleanup_unknown"
            else:
                try:
                    update_caller_baseline_atomic(
                        baseline_path,
                        counts,
                        cleanup,
                        caller_project_root=root,
                    )
                    baseline_total = sum(counts.values())
                    had_baseline = True
                    new_count = 0
                    violations = []
                except ArchiveResultError:
                    result["ok"] = False
                    result["error"] = "archive_artifact_failed"
        else:
            result["ok"] = new_count == 0
    result["errors"] = sum(1 for item in findings if item.get("type") == "ERROR")
    result["warnings"] = sum(1 for item in findings if item.get("type") == "WARNING")
    result["baseline_total"] = baseline_total
    result["new_count"] = new_count
    result["violations"] = violations
    result["baseline_exists"] = had_baseline
    if not had_baseline and result.get("error") is None and not getattr(args, "update", False):
        result["message"] = "no baseline exists yet — run with --update to establish one from current findings"
        result["ok"] = True
    report_meta = _archive_report_meta(
        result,
        baseline_total=baseline_total,
        new_count=new_count,
        baseline_file=pc["baseline_file"],
    )
    report_html = render_report(findings, report_meta)
    try:
        artifacts = persist_archive_artifact(
            target.artifact_dir,
            result,
            report_html,
            reports_root=target.artifact_dir.parent,
        )
        result["report_path"] = str(artifacts["report"])
    except ArchiveResultError:
        result["ok"] = False
        result["error"] = "archive_artifact_failed"
        result["report_path"] = None
    # Findings are retained in result.json but not duplicated in the CLI's
    # response envelope. This keeps the public shape compact and path-safe.
    result.pop("findings", None)
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
        if not result.get("ok"):
            raise SystemExit(1)
        return
    if result.get("error"):
        die(f"{result['error']}: archive review did not complete")
    if not had_baseline and not getattr(args, "update", False):
        info("no baseline found — archive run is NOT gated; run `./sb plugin-check --archive FILE --update` to establish one")
    elif violations:
        die("Plugin Check archive gate FAILED — NEW ERROR-level finding(s) not in the baseline:\n" +
            "\n".join(f"  {v['key']}: {v['current']} (baseline {v['baseline']}, +{v['delta']})" for v in violations))
    else:
        ok("Plugin Check archive gate passed — no new errors")
    print(f"HTML report: {result.get('report_path')}")


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
    if getattr(args, "archive", None):
        return _cmd_plugin_check_archive(cfg, args, pconf, root, pc)
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
