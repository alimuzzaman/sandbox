# `./sb zip` — building a distributable plugin zip

A dependency-free replacement for `wp dist-archive` / `npm run dist-archive`.
Python stdlib only (`zipfile`, `hashlib`, `fnmatch`) — no `adm-zip`, no
`fast-glob`, no node in the loop, and no need for the WP-CLI dist-archive
package to be installed in the container.

Ported from a working Node reference implementation in a real plugin repo
(`scripts/build/plugin-zip.js` + `zip-version.js` + `mime-check.js` +
`duplicate-check.js`), the same way `plugin-check` was — see
`sandbox/commands/zip.py`.

```bash
./sb zip                          # package the project in the current directory
./sb zip --project-dir ~/dev/foo  # package another project
./sb zip --dev                    # keep the dev-only files (source maps, dev tooling)
./sb zip --clean                  # ship the declared version verbatim
./sb zip --hash                   # append the short sha to the stamped version
./sb zip --out ~/builds           # write elsewhere
./sb zip --json                   # machine-readable result
```

## What it does beyond `wp dist-archive`

`wp dist-archive` reads `.distignore` and writes `<slug>.<version>.zip`. That is
the floor, not the ceiling:

| | `wp dist-archive` | `./sb zip` |
|---|---|---|
| `.distignore` | yes | yes, plus a **dev block** (`--dev`) |
| Build stamp | no | branch-tagged name + commit-count version, in-archive only |
| Guards | no | root dotfiles, MIME/extension mismatch, executables |
| Duplicate assets | no | reported (non-blocking) |
| Dependencies | WP-CLI package | none |

## The build stamp

On a non-release branch the archive is stamped:

```
Build stamp: 3.7.1 -> 3.7.1.4213 (branch `feat/nav`, 4213 commits, a1b2c3d).
Version rewritten in-archive only — the repo is untouched.
```

* **Filename** — `<slug>[-dev][-<branch>].<version>.zip`. The branch segment
  keeps parallel worktree builds from overwriting each other in the shared
  output directory.
* **Version** — `<declared>.<commit-count>` (`--hash` appends the short sha).
  `version_compare()` ranks `3.7.1.4213` above `3.7.1`, so re-uploading a build
  over an existing install **updates** it instead of reporting "already
  installed", and the number climbs with every commit.

The stamped version is written into the file bytes as they enter the archive —
never to disk. `git status` is clean after a build.

Stamping is skipped, and the declared version shipped verbatim, when:

* the branch is a release branch (`master`, `main`, `latest` by default), or
* `--clean` / `SANDBOX_ZIP_CLEAN=1` is set, or
* there is no git history or no declared version.

**Where the version is read and rewritten.** The declared version comes from the
main plugin file's `Version:` header (falling back to `package.json`). It is
rewritten in:

* the main file — both the `Version:` header and any quoted occurrence of the
  same version, which covers the near-universal
  `define( '<SLUG>_VERSION', '3.7.1' )` constant;
* `readme.txt` — `Stable tag:`;
* every path in `zip.versionSites` — quoted occurrences, e.g.
  `public $version = '3.7.1';` in `includes/Plugin.php`.

A file that declares something *other* than the expected version is left
untouched rather than mangled.

## `.distignore` and the dev block

Entries follow gitignore-like rules: an entry containing a slash anywhere but
the end is anchored to the project root, anything else matches at any depth, and
globs (`*.sql`) work in both positions. `.git/` is never shipped, listed or not.

Entries between the two markers are development-only. A normal build excludes
them; `--dev` ships them (and keeps `*.map` source maps wherever they live):

```
# Start: Development build files
webpack.config.js
*.map
# End: Development build files
```

## Guards

Two abort the build (exit 1), one only reports:

1. **Root-level dotfiles** that slipped past `.distignore`. Only the first path
   segment is checked — dotfiles inside third-party vendor packages are not ours
   to control.
2. **MIME/extension mismatches**, by magic bytes: a PNG named `.jpg`, a PHP
   opening tag in a `.txt`, and any PE/ELF/Mach-O executable (which must never
   reach a plugin zip, whatever its extension says).
3. **Duplicate assets** — byte-identical files under `assets/`, reported and not
   fatal. Webpack content-hashed names (`app.a1b2c3d4.js`) are skipped.

## Where the zip lands

Always the same directory for every worktree of one repo, so branch builds pile
up side by side instead of scattering into throwaway agent worktrees:

1. `--out` / `zip.outputDir` / `SANDBOX_ZIP_DIR`;
2. the parent of git's **main** worktree;
3. for a relocated git store (`git init --separate-git-dir`), a worktree beside
   the store, else the parent most worktrees share;
4. the parent of the current worktree.

## Configuration (`sandbox.config.json`)

Every key is optional — a project with a `.distignore` and a standard main file
needs none of it.

```jsonc
"zip": {
  "mainFile": "templately.php",              // default: <slug>.php
  "versionSites": ["includes/Plugin.php"],   // extra files whose quoted version is stamped
  "outputDir": "~/builds",                   // default: the shared worktree parent
  "releaseBranches": ["master", "main", "latest"],
  "collapseRoots": ["modules", "vendor"],    // logged as one counted line each
  "duplicateScan": "assets/"                 // "" scans every file
}
```

The plugin slug is not configurable here: `./sb zip` packages the project's own
plugin, resolved exactly like legacy `plugins: ["."]` self-entries
(`sandbox.config.json`'s root-level `slug`, else the project directory name).

## `--json` output

```json
{"ok": true, "plugin_slug": "myplugin", "declared_version": "2.3.1",
 "version": "2.3.1.42", "stamped": true,
 "stamped_files": ["myplugin.php", "readme.txt"], "branch": "feat/nav",
 "dev": false, "files": 812, "zip_path": "/Users/me/Sites/git/myplugin-feat-nav.2.3.1.42.zip",
 "size": 1839221, "duplicates": [], "error": null}
```

Tests: `tests/test_zip.py` (stdlib unittest, no docker).
