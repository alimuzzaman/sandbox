# Packaging

Three install channels ship the **same** Python `sb` runtime; pick whichever
fits. All need **Python 3** and **Docker** at runtime.

| Channel | Entry | Best for |
|---|---|---|
| curl installer | `scripts/web-install.sh` → `install.sh` | quickest one-liner |
| npm | `@alimuzzaman/sandbox` (`package.json` + `bin/sandbox.js`) | Node devs / CI |
| Homebrew | `packaging/homebrew/sandbox.rb` | macOS / Linuxbrew |

The `sandbox` command is the canonical name; `sb` is kept as an alias (the
in-repo `./sb`, and a second bin in both the npm package and the brew formula).

## npm

`bin/sandbox.js` is a thin Node shim: it finds a Python 3 interpreter and execs
the bundled `sb` (a polyglot shell+Python file, so `python3 sb …` runs directly
and cross-platform). `package.json` `files` is an **allowlist** — only runtime
paths ship, so machine config / secrets (`*.local.yml`, `.env*`, `runtime/`,
the `.venv`s) can never leak even though `.npmignore` does not prune inside
`files`-listed dirs. A `prepack` step strips `__pycache__`, and
`!skills/sandbox-release/**` drops the maintainer-only release skill.

```bash
npm pack                                   # build the tarball
npm install -g ./wpdeveloper-sandbox-*.tgz # or: npm i -g @alimuzzaman/sandbox
sandbox --help
```

Verified: a global install exposes both `sandbox` and `sb`; `sandbox --help`
and `sandbox init --help` run from any directory.

## Homebrew

`packaging/homebrew/sandbox.rb` belongs in a tap repo
(`alimuzzaman/homebrew-sandbox` as `Formula/sandbox.rb`). It depends on
`python@3.12` and wraps `sb` with that python on PATH. `--HEAD` installs from
git directly; a tagged release needs `url`/`sha256` pointed at the uploaded
`sandbox-<ver>.tar.gz` (the sha is `shasum -a 256 dist/sandbox-<ver>.tar.gz`,
filled in by the release flow — see `scripts/make-release.sh`).

```bash
brew install --HEAD alimuzzaman/sandbox/sandbox   # from git
# or, after a release is published + the formula sha is filled:
brew tap alimuzzaman/sandbox && brew install sandbox
```
