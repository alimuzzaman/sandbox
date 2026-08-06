from __future__ import annotations
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

from sandbox.core._instances import _core
from sandbox.core._ui import die, ok

from sandbox.registry import register


# `./sb zip` — a dependency-free replacement for `wp dist-archive` / `npm run
# dist-archive`, ported from a working Node reference implementation in a real
# plugin repo (`scripts/build/plugin-zip.js` + `zip-version.js` + `mime-check.js`
# + `duplicate-check.js` in the Templately tree), the same way `plugin-check`
# was. See docs/plugin-zip.md.
#
# What it adds over `wp dist-archive`, which only reads `.distignore` and writes
# `<slug>.<version>.zip`:
#
#   * a `--dev` mode that keeps the files a `.distignore` marks as
#     development-only, so a debug build ships source maps and dev tooling;
#   * a build stamp — branch-tagged filename plus a commit-count version
#     post-fix (`3.7.1` -> `3.7.1.4213`) written into the archived BYTES only,
#     never to disk, so WordPress ranks each build as an upgrade and parallel
#     worktree builds don't overwrite each other;
#   * hard guards that abort the build on stray root-level dotfiles, on
#     MIME/extension mismatches, and on executables (PE/ELF/Mach-O) that must
#     never reach a plugin zip;
#   * a non-blocking duplicate-asset report.
#
# Everything is stdlib: zipfile, hashlib, fnmatch. No adm-zip, no fast-glob.

RELEASE_BRANCHES = ("master", "main", "latest")

DEV_BLOCK_START = "# Start: Development build files"
DEV_BLOCK_END = "# End: Development build files"

# Roots whose per-file logging is collapsed to one line with a count. Listing
# every entry buries the parts anyone reads (the stamp banner, the guards) under
# hundreds of `Adding ...` lines; a per-group count still makes a module that
# unexpectedly vanished from the archive visible.
COLLAPSE_ROOTS = ("modules", "vendor", "node_modules")

PHP_EXTENSIONS = {"php", "php3", "php4", "php5", "php7", "phtml", "phar"}

# Magic-byte signatures, tested in order. `dangerous` means the format must
# never appear in a plugin zip whatever the extension says.
SIGNATURES = (
    {"mime": "image/jpeg", "exts": ("jpg", "jpeg"), "prefix": b"\xff\xd8\xff"},
    {"mime": "image/png", "exts": ("png",), "prefix": b"\x89PNG"},
    {"mime": "image/gif", "exts": ("gif",), "prefix": b"GIF"},
    {"mime": "image/webp", "exts": ("webp",), "prefix": b"RIFF", "at8": b"WEBP"},
    {"mime": "font/woff", "exts": ("woff",), "prefix": b"wOFF"},
    {"mime": "font/woff2", "exts": ("woff2",), "prefix": b"wOF2"},
    {"mime": "application/x-msdownload", "exts": (), "prefix": b"MZ", "dangerous": True},
    {"mime": "application/x-elf", "exts": (), "prefix": b"\x7fELF", "dangerous": True},
    {"mime": "application/x-mach-binary", "exts": (), "prefix": b"\xce\xfa\xed\xfe", "dangerous": True},
    {"mime": "application/x-mach-binary", "exts": (), "prefix": b"\xcf\xfa\xed\xfe", "dangerous": True},
)

# Webpack-style content-hashed filenames (`app.a1b2c3d4.js`): two builds of the
# same bytes under different hashes are not a duplicate worth reporting.
_HASHED_NAME_RE = re.compile(r"\.[a-f0-9]{8,}\.[a-z0-9]+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str) -> str:
    """A git query that never raises — every caller has a safe degraded value
    (empty branch, 0 commits, "not dirty") for a non-git export."""
    try:
        res = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=15)
    except Exception:
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def _git_info(root: Path) -> dict:
    """Branch, short sha, commit count and dirty state of `root`."""
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        # Detached HEAD (CI checkouts, bisect) — name the build after an exact
        # tag if there is one, else the sha.
        branch = (_git(root, "describe", "--tags", "--exact-match")
                  or _git(root, "rev-parse", "--short", "HEAD"))
    try:
        count = int(_git(root, "rev-list", "--count", "HEAD") or 0)
    except ValueError:
        count = 0
    return {
        "branch": branch,
        "sha": _git(root, "rev-parse", "--short=7", "HEAD"),
        "count": count,
        "dirty": _git(root, "status", "--porcelain") != "",
    }


def _slugify_branch(branch: str) -> str:
    """`feat/foo bar` -> `feat-foo-bar` — safe for a filename."""
    return re.sub(r"^-+|-+$", "", re.sub(r"[^A-Za-z0-9._-]+", "-", branch or "")).lower()


def _output_dir(root: Path, override: str | None) -> Path:
    """Where the zip lands. Always the same directory for every worktree of one
    repo, so branch builds pile up side by side instead of scattering into
    throwaway agent worktrees:

      1. `--out` / `SANDBOX_ZIP_DIR` (explicit escape hatch).
      2. Parent of git's MAIN worktree — `git worktree list --porcelain` lists
         it first, wherever the linked worktrees live.
      3. Relocated git store (`git init --separate-git-dir`): git's "main
         worktree" is the store itself and no checkout is main, so prefer a
         worktree sitting beside the store, else the parent most worktrees
         share.
      4. Parent of the current worktree.
    """
    if override:
        return Path(os.path.expanduser(override)).resolve()

    common = _git(root, "rev-parse", "--git-common-dir")
    if common:
        common_dir = (root / common).resolve() if not os.path.isabs(common) else Path(common).resolve()
        listed = [Path(line[len("worktree "):].strip()).resolve()
                  for line in _git(root, "worktree", "list", "--porcelain").splitlines()
                  if line.startswith("worktree ")]
        if listed and listed[0] != common_dir:
            return listed[0].parent
        worktrees = [p for p in listed if p != common_dir]
        counts: dict[Path, int] = {}
        best, best_count = None, 0
        for wt in worktrees:
            parent = wt.parent
            if parent == common_dir.parent:
                return parent
            counts[parent] = counts.get(parent, 0) + 1
            if counts[parent] > best_count:
                best, best_count = parent, counts[parent]
        if best is not None:
            return best
    return root.parent


# ---------------------------------------------------------------------------
# .distignore
# ---------------------------------------------------------------------------

class DistIgnore:
    """gitignore-like matcher over a `.distignore`.

    An entry containing a slash anywhere but the very end is ANCHORED to the
    project root; anything else matches at any depth. A trailing slash marks a
    directory but is otherwise treated the same (matching the entry excludes
    everything under it either way)."""

    def __init__(self, entries: list[str]) -> None:
        self.anchored: list[str] = []
        self.floating: list[str] = []
        for raw in entries:
            pattern = raw.strip()
            if not pattern or pattern.startswith("#"):
                continue
            if pattern.endswith("/"):
                pattern = pattern[:-1]
            first_slash = pattern.find("/")
            is_anchored = first_slash != -1 and (first_slash != len(pattern) - 1 or first_slash == 0)
            pattern = pattern.lstrip("/")
            if not pattern:
                continue
            (self.anchored if is_anchored else self.floating).append(pattern)

    def match(self, rel: str) -> bool:
        """True when `rel` (a POSIX project-relative path) is excluded."""
        for pattern in self.anchored:
            if fnmatch.fnmatch(rel, pattern) or rel.startswith(pattern + "/"):
                return True
        if self.floating:
            for segment in rel.split("/"):
                for pattern in self.floating:
                    if fnmatch.fnmatch(segment, pattern):
                        return True
        return False


def _read_distignore(root: Path, dev: bool) -> tuple[DistIgnore, bool]:
    """Parse `.distignore`, honouring the development-block markers.

    Entries between `# Start: Development build files` and `# End: …` are the
    files a debug build WANTS — under `--dev` they stop being exclusions, as do
    `*.map` source maps. Returns the matcher and whether a file was found."""
    path = root / ".distignore"
    if not path.is_file():
        return DistIgnore([]), False

    entries: list[str] = []
    in_dev_block = False
    for line in path.read_text(errors="replace").splitlines():
        trimmed = line.strip()
        if trimmed == DEV_BLOCK_START:
            in_dev_block = True
            continue
        if trimmed == DEV_BLOCK_END:
            in_dev_block = False
            continue
        if dev and in_dev_block:
            continue
        if dev and trimmed.endswith(".map"):
            continue
        entries.append(trimmed)
    return DistIgnore(entries), True


def _discover_files(root: Path, ignore: DistIgnore) -> list[str]:
    """Every shippable file as a POSIX project-relative path, sorted.

    Directories are pruned as soon as they match, so a `node_modules/` entry
    costs one check rather than a walk of 40,000 files. `.git` is pruned
    unconditionally — it is never shippable and is not always in `.distignore`."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(
            d for d in dirnames
            if not (prefix == "" and d == ".git") and not ignore.match(prefix + d))
        for name in sorted(filenames):
            rel = prefix + name
            if not ignore.match(rel):
                found.append(rel)
    return found


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------

def _check_mime_mismatches(files: list[str], root: Path) -> list[dict]:
    """Magic-byte vs extension mismatches, plus PHP hiding in a non-PHP file.

    Reads the first 16 bytes of each file — enough for every signature here."""
    violations: list[dict] = []
    for rel in files:
        try:
            with open(root / rel, "rb") as fh:
                head = fh.read(16)
        except OSError:
            continue
        if not head:
            continue
        ext = Path(rel).suffix[1:].lower()

        for sig in SIGNATURES:
            if not head.startswith(sig["prefix"]):
                continue
            at8 = sig.get("at8")
            if at8 and head[8:8 + len(at8)] != at8:
                continue
            if sig.get("dangerous"):
                violations.append({"file": rel, "reason":
                    f"dangerous binary format ({sig['mime']}) — executables must "
                    f"not appear in a plugin zip"})
            elif ext not in sig["exts"]:
                violations.append({"file": rel, "reason":
                    f"content detected as {sig['mime']} but extension is "
                    f".{ext or '(none)'}"})
            break  # first match wins

        if ext not in PHP_EXTENSIONS:
            snippet = head.decode("latin-1")
            if snippet.startswith("<?php") or snippet.startswith("<?="):
                violations.append({"file": rel, "reason":
                    f"contains PHP opening tag but extension is .{ext or '(none)'}"})
    return violations


def _find_duplicates(files: list[str], root: Path) -> list[dict]:
    """Groups of byte-identical files. Identical copies waste space and usually
    mean a directory reorganisation was never cleaned up. Reported, never fatal."""
    by_hash: dict[str, list[str]] = {}
    for rel in files:
        if _HASHED_NAME_RE.search(rel):
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size == 0:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        by_hash.setdefault(digest, []).append(rel)
    return [{"hash": h, "files": group} for h, group in by_hash.items() if len(group) > 1]


# ---------------------------------------------------------------------------
# version stamping
# ---------------------------------------------------------------------------

def _read_plugin_version(root: Path, main_file: str) -> str:
    """The plugin's declared version, from its main file's `Version:` header —
    the WordPress-canonical source, not package.json (which many plugin repos
    leave at 1.0.0 or don't have at all). Falls back to package.json only when
    the header is missing."""
    path = root / main_file
    if path.is_file():
        m = re.search(r"^\s*\*?\s*Version:\s*(.+)$", path.read_text(errors="replace"),
                      re.MULTILINE)
        if m:
            return m.group(1).strip()
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            return str(json.loads(pkg.read_text()).get("version") or "")
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def _resolve_build_stamp(root: Path, version: str, clean: bool, with_hash: bool,
                         release_branches: tuple[str, ...]) -> dict:
    """How this build is labelled.

    `3.7.1` + 4213 commits -> `3.7.1.4213`, which `version_compare()` ranks
    ABOVE `3.7.1`, so re-uploading a build over an existing install updates it
    instead of reporting "already installed". Off on a release branch (those
    builds must ship the exact declared version) and under `--clean`."""
    info_git = _git_info(root)
    branch_slug = _slugify_branch(info_git["branch"])

    def unstamped(reason, slug=""):
        return {"version": version, "stamped": False, "branch_slug": slug,
                "git": info_git, "reason": reason}

    if clean:
        return unstamped("clean build requested")
    if branch_slug in release_branches:
        return unstamped(f"release branch ({branch_slug})")
    if not version or not info_git["count"]:
        return unstamped("no git history / no version", branch_slug)

    stamped_version = f"{version}.{info_git['count']}"
    if with_hash and info_git["sha"]:
        stamped_version += f".{info_git['sha']}"
    return {"version": stamped_version, "stamped": True, "branch_slug": branch_slug,
            "git": info_git, "reason": ""}


def _version_sites(main_file: str, extra: list[str]) -> list[tuple[str, str]]:
    """(path, mode) pairs whose declared version gets rewritten in-archive.

    `header` = a WordPress file header (`Version:` / `Stable tag:`);
    `literal` = any quoted occurrence of the exact declared version, which
    covers `public $version = '3.7.1';` and
    `define( 'PLUGIN_VERSION', '3.7.1' )` alike without needing a per-project
    regex. Extra sites come from `zip.versionSites` in sandbox.config.json.

    The main plugin file gets BOTH: nearly every plugin repeats its header
    version in a `define( '<SLUG>_VERSION', '…' )` constant a few lines down,
    and a build whose header says 3.7.1.4213 while its constant still says
    3.7.1 breaks asset cache-busting and any version gate reading the
    constant."""
    sites = [(main_file, "header+literal"), ("readme.txt", "header")]
    sites += [(path, "literal") for path in extra]
    return sites


def _stamp_file(rel: str, contents: bytes, base_version: str, new_version: str,
                sites: list[tuple[str, str]]) -> bytes | None:
    """Rewrite the version inside one file's bytes, in memory. Returns None when
    the file carries no version, or declares something other than the expected
    one — a file that says something unexpected is left alone rather than
    mangled. Paths compare case-insensitively (the readme ships as both
    `readme.txt` and `README.txt`)."""
    key = rel.lower()
    mode = next((m for path, m in sites if path.lower() == key), None)
    if not mode or base_version == new_version:
        return None

    text = contents.decode("utf-8", errors="strict")
    escaped = re.escape(base_version)
    hits = 0
    if "header" in mode:
        text, n = re.subn(
            rf"^(\s*\*?\s*(?:Version|Stable tag):\s*){escaped}[ \t]*$",
            rf"\g<1>{new_version}", text, flags=re.MULTILINE)
        hits += n
    if "literal" in mode:
        text, n = re.subn(rf"(['\"]){escaped}\1", rf"\g<1>{new_version}\g<1>", text)
        hits += n
    return text.encode("utf-8") if hits else None


# ---------------------------------------------------------------------------
# config + command
# ---------------------------------------------------------------------------

def _resolve_zip_config(pconf: dict) -> dict:
    """The `zip` section of a project's resolved config, all keys optional.

    The slug comes from the SAME `_project_slug` resolution legacy
    `plugins: ["."]` self-entries use (sandbox.config.json's root-level `slug`,
    else the project directory name) — this always packages the project's own
    plugin, so there is no slug override here either."""
    sc = _core()
    zc = pconf.get("zip") or {}
    try:
        slug = sc._project_slug(pconf.get("slug"), Path(pconf["root"]).name)
    except sc.ConfigError as e:
        die(f"could not resolve a plugin slug to package: {e}")
    return {
        "slug": slug,
        "main_file": zc.get("mainFile") or f"{slug}.php",
        "version_sites": list(zc.get("versionSites") or []),
        "output_dir": zc.get("outputDir") or os.environ.get("SANDBOX_ZIP_DIR") or None,
        "release_branches": tuple(zc.get("releaseBranches") or RELEASE_BRANCHES),
        "collapse_roots": tuple(zc.get("collapseRoots") or COLLAPSE_ROOTS),
        "duplicate_scan": zc.get("duplicateScan", "assets/"),
    }


def _log_files(files: list[str], collapse_roots: tuple[str, ...]) -> None:
    """Per-file lines for the parts where a surprise entry matters (plugin root,
    includes/, assets/, languages/), one counted line for the bulk trees."""
    collapsed: dict[str, int] = {}
    for rel in files:
        segments = rel.split("/")
        if segments[0] in collapse_roots and len(segments) > 1:
            # vendor/<org>/<pkg>/… groups per package; modules/<name>/… per module.
            depth = 3 if segments[0] == "vendor" else 2
            group = "/".join(segments[:min(depth, len(segments) - 1)])
            collapsed[group] = collapsed.get(group, 0) + 1
            continue
        print(f"  Adding `{rel}`.")
    if collapsed:
        print()
        for group in sorted(collapsed):
            count = collapsed[group]
            print(f"  Adding `{group}/` ({count} file{'' if count == 1 else 's'}).")
        total = sum(collapsed.values())
        print(f"  {len(collapsed)} grouped path(s), {total} files.")


def cmd_zip(cfg, args) -> None:
    """`./sb zip [--project-dir DIR] [--dev] [--clean] [--hash] [--out DIR] [--json]`
    — build a distributable plugin zip from `.distignore`, with guards, a git
    build stamp, and no `wp dist-archive`/node dependency (docs/plugin-zip.md).

    Nothing on disk is rewritten: the stamped version exists only in the bytes
    that go into the archive, so `git status` stays clean after a build."""
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    root = Path(pconf["root"])
    as_json = bool(getattr(args, "json", False))
    is_dev = bool(getattr(args, "dev", False))
    zc = _resolve_zip_config(pconf)
    name = zc["slug"]

    version = _read_plugin_version(root, zc["main_file"])
    stamp = _resolve_build_stamp(
        root, version,
        clean=bool(getattr(args, "clean", False)) or os.environ.get("SANDBOX_ZIP_CLEAN") == "1",
        with_hash=bool(getattr(args, "hash", False)),
        release_branches=zc["release_branches"])

    if not as_json:
        print(f"Creating archive for `{name}` plugin...\n")
        if is_dev:
            print("Development mode enabled. Including development files.\n")
        if stamp["stamped"]:
            git_info = stamp["git"]
            print(f"Build stamp: {version} -> {stamp['version']} "
                  f"(branch `{git_info['branch']}`, {git_info['count']} commits, "
                  f"{git_info['sha']}"
                  f"{', working tree dirty' if git_info['dirty'] else ''}).\n"
                  f"Version rewritten in-archive only — the repo is untouched.\n")
        elif stamp["reason"]:
            print(f"Shipping the declared version {version or '(none)'} — "
                  f"{stamp['reason']}.\n")

    ignore, had_distignore = _read_distignore(root, is_dev)
    if not as_json and had_distignore:
        print("Using .distignore to exclude files.\n")
    files = _discover_files(root, ignore)
    if not files:
        die(f"nothing to archive in {root} — every file is excluded by .distignore.")

    # Guard: a root-level dotfile that slipped past .distignore. Only the first
    # path segment is checked; hidden files inside third-party vendor packages
    # are not ours to control and are excluded with their dev dependencies.
    hidden = [f for f in files if f.split("/")[0].startswith(".")]
    if hidden:
        die("hidden files detected in zip output — aborting.\n"
            "These must be added to .distignore:\n\n"
            + "\n".join(f"   {f}" for f in hidden))

    mime_violations = _check_mime_mismatches(files, root)
    if mime_violations:
        die("MIME/extension mismatches detected in zip output — aborting.\n\n"
            + "\n".join(f"   {v['file']}\n     -> {v['reason']}" for v in mime_violations)
            + "\n\nFix the files above or update .distignore to exclude them.")

    # Only the asset tree is scanned for duplicates by default — vendor packages
    # may carry coincidentally identical config files.
    scan_prefix = zc["duplicate_scan"]
    scanned = [f for f in files if f.startswith(scan_prefix)] if scan_prefix else files
    duplicates = _find_duplicates(scanned, root)

    sites = _version_sites(zc["main_file"], zc["version_sites"])
    stamped_files: list[str] = []

    out_dir = _output_dir(root, getattr(args, "out", None) or zc["output_dir"])
    # `<slug>[-dev][-<branch>].<version>.zip` — the branch segment keeps parallel
    # worktree builds from overwriting each other in the shared output dir.
    base_name = "-".join(p for p in (name, "dev" if is_dev else None,
                                     stamp["branch_slug"] or None) if p)
    zip_name = f"{base_name}.{stamp['version']}.zip" if stamp["version"] else f"{base_name}.zip"
    zip_path = out_dir / zip_name

    if not as_json:
        print("Using Plugin Handbook best practices to discover files:\n")
        _log_files(files, zc["collapse_roots"])

    out_dir.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        if not as_json:
            print(f"Deleting existing archive: {zip_path}")
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            src = root / rel
            # Everything sits under a `<slug>/` folder, as WordPress expects of
            # an installable plugin zip.
            arcname = f"{name}/{rel}"
            restamped = None
            if stamp["stamped"]:
                try:
                    restamped = _stamp_file(rel, src.read_bytes(), version,
                                            stamp["version"], sites)
                except (OSError, UnicodeDecodeError):
                    restamped = None
            if restamped is not None:
                stamped_files.append(rel)
                zf.writestr(arcname, restamped)
            else:
                zf.write(src, arcname)

    result = {
        "ok": True,
        "plugin_slug": name,
        "declared_version": version,
        "version": stamp["version"],
        "stamped": stamp["stamped"],
        "stamped_files": stamped_files,
        "branch": stamp["git"]["branch"],
        "dev": is_dev,
        "files": len(files),
        "zip_path": str(zip_path),
        "size": zip_path.stat().st_size,
        "duplicates": duplicates,
        "error": None,
    }
    if as_json:
        print(json.dumps(result))
        return

    if stamp["stamped"]:
        print(f"\nStamped version {stamp['version']} into: {', '.join(stamped_files)}."
              if stamped_files else
              f"\nWarning: build stamp requested but no version declaration matched "
              f"{version} — the archive ships {version}. "
              f"Check {zc['main_file']} / readme.txt / zip.versionSites.")

    ok(f"`{zip_name}` is ready ({len(files)} files, "
       f"{zip_path.stat().st_size // 1024} KB).")
    print(f"Archive created at: {zip_path}")

    if duplicates:
        print("\nDuplicate asset files detected in zip output:\n")
        for group in duplicates:
            print("   " + "\n   ".join(group["files"]) + "\n")
        print("Consider removing redundant copies or adding them to .distignore.\n")


register({'zip': cmd_zip})
