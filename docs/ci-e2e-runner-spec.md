# CI & E2E Runner — parallel matrix execution on multi-instance sandboxes

Author: drafted 2026-07-08, rewritten 2026-07-09 after adopting `act` as the CI execution
engine (design-fidelity-diff session). Status: implemented, live-verified. **Depends on**
`docs/multi-instance-spec.md` (multi-instance-per-root; the labelled-instance primitive
this whole document is built on).

## 1. Where this lives

This is a companion doc to `docs/multi-instance-spec.md`, not a section appended to it.
The multi-instance doc describes a *primitive* (a root may own N labelled, isolated
stacks) with a hard, narrow release gate — "zero behavior change for single-instance
projects." Bolting two large *consumer features* (a Playwright shard-runner and a
GitHub-Actions interpreter) onto it would blur that gate and balloon a tight spec to
triple length. These two features are siblings that share two pieces of plumbing — the
fan-out helper (§4.1) and per-label `sandbox.config` (§4.2, owned by the multi-instance
doc but consumed here) — and belong together, in their own doc that *references* the
primitive.

## 2. E2E multi-worker design

### 2.1 The problem it fixes

The default Playwright model is N workers hammering **one** `baseURL`. For WordPress that
means N browsers mutating one shared DB — option state, transients, created posts,
logged-in cookies — so tests pollute each other and flake. Multi-instance-per-root lets us
give **each worker its own fresh WordPress install**: same plugin code (bind-mounted from
the same worktree, per the shared-`SANDBOX_PLUGINS_HOST` note in the multi-instance spec
§Risks), independent DB/uploads/WP state.

### 2.2 Worker → labelled-instance mapping

One labelled instance per Playwright worker/shard, reserved prefix `e2e-w<i>` (ephemeral,
torn down after the run unless `--keep-on-fail` and that shard failed; hidden from
`./sb instances` by convention so a stray leftover is always visible for cleanup).

### 2.3 Config discovery + baseURL wiring

`_find_playwright_config` searches the project root AND common subdirs (`tests/`, `test/`,
`e2e/`, `tests/e2e/`) — real projects (Templately's e2e suite) keep the config under
`tests/`, not the root; `--playwright-config` overrides for anything else. Each shard's
`npx playwright test --shard=i/N --config=<discovered>` gets four conventional env vars
(`SANDBOX_E2E_BASE_URL`, `WP_BASE_URL`, `BASE_URL`, `PLAYWRIGHT_TEST_BASE_URL`) — there's no
single universal Playwright convention, so all four are set rather than guessing wrong.

**`.wp-env-port` convention, detected automatically.** A real observed pattern
(`scripts/lib/sandbox.js` + `tests/e2e/utils/wp-env-url.js` in Templately's own repo): a
project's OWN test harness may already boot a sandbox instance itself (via `sb ensure
--json`) and write a `{baseUrl, loginUrl, runtime, instance}` descriptor to a repo-root
`.wp-env-port` file that its Playwright config reads once at startup. `_detect_wp_env_port_convention`
greps the discovered config for the literal markers (`wp-env-port`/`wp-env-url`); if found,
`_write_wp_env_port` writes that exact file shape too — so `./sb e2e` works against such a
project with zero changes to the project's own harness. Because that file is a SINGLE
shared descriptor (not worker-indexed), `workers > 1` against a detected project is
clamped to 1 with an explicit message — never silently raced.

### 2.4 Provisioning, concurrency cap, failure isolation

Handled entirely by the shared fan-out helper (§4.1): concurrency capped (default ~4
simultaneous stacks), provisioning failure of one worker recorded as `provision_failed`
without aborting the others (`--strict-provision` to flip to fail-fast), ephemeral
instances torn down unless `--keep-on-fail`.

### 2.5 CLI + MCP surface

```
./sb e2e --project-dir DIR \
         [--workers N] [--concurrency N] [--playwright-config PATH] \
         [--grep PATTERN] [--keep-on-fail] [--strict-provision] \
         [--async] [--json] [-- <extra playwright args>]
```

`--async`: runs detached via the generic host-level job runner (§4.3), printing
`{"job_id": ...}` immediately; poll with `./sb async-job <job_id> [--follow|--kill]`.

MCP tool: `run_e2e(project_dir, workers=2, concurrency=None, grep=None,
keep_on_fail=False, strict_provision=False, timeout=900) -> dict` — blocking; returns the
aggregated `{ok, workers, concurrency, passed, failed, by_worker:[...]}` report.
(Async MCP wrapping for `run_e2e` is not yet exposed — the CLI's `--async` + `async-job`
path works standalone; wiring an `async_=` MCP param is a small follow-up, see §7.)

**Verified**: config discovery (including a `tests/`-subdir layout matching Templately's
real repo), `.wp-env-port` shape/writing, and the missing-config error path — via unit
tests (`tests/test_e2e.py`). **Not verified**: an actual live Playwright test suite run —
no real Playwright project was available to drive end-to-end; the underlying fan-out
mechanism this shares with the CI runner (§3) IS proven live (see §3.8).

## 3. CI-workflow-runner design

### 3.1 Execution engine: real `act`, not a bespoke interpreter

**This is the single biggest change from the original design.** The first implementation
(2026-07-08) hand-interpreted a narrow YAML subset (checkout/setup-php/setup-node/cache/
artifacts as "known" steps, everything else "unknown, skipped"). That was too narrow for
"full coverage, close to GitHub Actions" — an unfamiliar third-party action (even a benign
one like `anthropics/claude-code-action`) was silently skipped rather than run, and
semantics we didn't hand-implement (`if:` expression evaluation, `needs:` ordering,
`services:`, composite actions, reusable workflow calls) simply didn't work.

`act` (nektos/act — https://github.com/nektos/act) already solves exactly this problem: it
runs real GitHub Actions workflows locally in Docker, with full fidelity for matrix,
`if:`, `needs:`, `services:`, composite and reusable actions, and arbitrary real
third-party actions (it fetches and runs them exactly as GitHub would). It was already
installed on the dev machine (`brew install act`; upgraded mid-session from v0.2.83 to
v0.2.89 after `act` self-reported a CVE against 0.2.83, see §3.8 item 10) with its default runner image
(`catthehacker/ubuntu:act-22.04`) already pulled — validated live before adopting it
(§3.8).

**Why not just shell out to `act` with zero changes?** `act` has no concept of "don't
actually deploy" — given real secrets, it will happily execute a step that pushes a real
WordPress.org SVN release. This module's job shrank to exactly three things `act` doesn't
do for us:

1. **`ci plan`** classification/listing — parse the workflow, show every job's matrix
   cells and step kinds, list referenced secrets. Always safe, no execution, no docker.
2. **Matrix-cell → sandbox-instance mapping** — `act` has no concept of our sandbox
   instances; we still expand the matrix ourselves and boot one labelled instance per
   cell (php/wp version override, per-label config — §3.5).
3. **The safety deny-list** — a patched COPY of the workflow, with deploy/publish-class
   steps neutralized into no-op stubs, is what actually gets handed to `act` (§3.6).

Everything else — matrix semantics, `if:`, `needs:`, `services:`, composite/reusable
actions, arbitrary real actions — is `act`'s job now, not ours.

### 3.2 Supported surface (via `act`, not hand-implemented)

Because `act` runs the real workflow, the "supported YAML subset" table from the original
design is now moot for anything act itself handles — which is nearly everything. What our
own code still touches (for planning/mapping/safety) is:

| Concern | Who handles it | Notes |
|---|---|---|
| `on:` triggers | Our code, informational only | `--if-event` filters; never auto-fires (§3.7) |
| `jobs.<id>.strategy.matrix` | Our code | Expanded to cells → one sandbox instance each (§3.5) |
| `jobs.<id>.if:`, `needs:`, `services:`, composite/reusable actions | **`act`**, for real | Not hand-implemented; verify per-workflow if in doubt |
| Most `uses:` actions (checkout, setup-*, cache, third-party) | **`act`**, for real | Full fidelity — this is the whole point of adopting it |
| `actions/upload-artifact` | Sandbox job runtime | Replaced with a local marker; `act --bind` keeps declared output in the isolated workspace and the durable supervisor retains it as a Sandbox job artifact because self-hosted `act` has no GitHub runtime token |
| Deploy/publish-class `uses:` or `run:` | Our safety deny-list | Neutralized BEFORE act sees them, unless `--allow-deploy` (§3.6) |
| `${{ secrets.* }}` | Resolved from `sandbox.local.yml` `ci_secrets:` / `$SANDBOX_CI_SECRET_*`, fed to act via `--secret-file` | Never GitHub's; unresolved → fail loud before anything runs |

### 3.3 The real-world shape this targets (survey, unchanged from original research)

Query Monitor's real CI (`johnbillion/query-monitor`) uses an 8-cell `strategy.matrix.include`
of explicit `{wp, php}` pairs, split across `integration-tests.yml`/`acceptance-tests.yml`,
plus `coding-standards.yml`/`static-analysis.yml`/deploy files — and **delegates to a
reusable workflow** (`johnbillion/plugin-infrastructure/.github/workflows/reusable-*-tests.yml`),
which `act` supports natively (not yet live-verified in this environment — see §7). 10up
`wp-scaffold`'s `php.yml` is the simpler checkout→setup-php→composer→lint→static shape.
WooCommerce shards Playwright with `--shard`/`--last-failed`. The Templately examples
(`deploy.yml`/`assets.yml`/`claude-code-review.yml`) are the concrete local ground truth
this was built and tested against.

### 3.4 The safety deny-list (replaces the old "known-actions allow-list")

`_is_deploy_class(uses_ref)`: explicit prefixes (`10up/action-wordpress-plugin-deploy`,
`10up/action-wordpress-plugin-asset-update`) OR a keyword heuristic on the action
reference (`deploy`, `release`, `publish`, `gh-pages`, `svn`, `push` — deliberately
excludes `upload`, since `actions/upload-artifact` is common and safe). This is a
**deny-list, not an allow-list** — the previous design's "only run actions we recognize"
model is inverted: now everything runs (via act) EXCEPT what's explicitly flagged
dangerous. `_neutralize_workflow_for_safety` deep-copies the parsed workflow and, unless
`--allow-deploy`:
- Replaces any deny-listed step with a no-op `run: echo "[sandbox-ci] skipped deploy-class
  step '<name>' (<uses>) — pass --allow-deploy to run for real"`.
- Scans every `run:` step for raw `git push`/`gh pr merge`/`gh release create`/
  `svn commit` and comments out just the offending line (`_guard_dangerous_commands`,
  unchanged from the original design).

The patched workflow is written to a temp file (`_write_patched_workflow`) and is the ONLY
copy `act` ever sees — the original on disk is never touched, never even read by act.
Deploy-only secrets from neutralized steps are likewise excluded from the safe-run
preflight; they are required when `--allow-deploy` is enabled.

### 3.5 Matrix → concurrent labelled instances, with real version override

Each matrix cell gets a labelled instance via the shared fan-out helper (§4.1), with:

- **`php`/`wp` override — "CI takes priority over sandbox.config", now real.**
  `_resolve_cell_versions(cell, steps)` prefers a `shivammathur/setup-php` step's
  interpolated `with.php-version` over a same-named matrix key (the more explicit "what
  will actually run" signal), falls back to matching matrix keys
  (`php`/`php-version`/`phpVersion`/`php_version`, `wp`/`wp-version`/`wpVersion`/`wp_version`)
  otherwise. **Verified live**: a scratch project configured for PHP 8.1 correctly booted
  its CI cell's sandbox instance on PHP 8.3 when the workflow's matrix requested it — the
  registry entry now actually stores `php_version` (a real pre-existing gap this surfaced
  and fixed: it was computed but never persisted, so `ci run`'s own status report
  previously said "(project default)" even when the override had worked).
- **`config_label` — per-label `sandbox.config` (owned by `docs/multi-instance-spec.md`),
  consumed here via a STABLE per-cell slug.** A CI cell's actual instance `label` is
  randomized per run (`ci-<runid>-<slug>`, for concurrency-safety — two concurrent `ci
  run`s of the same workflow must never collide). A user could never pre-author a config
  file matching a random label, so `_cell_slug(cell)` (the slug WITHOUT the run id — e.g.
  `68-84` for `{wp:'6.8', php:'8.4'}`, stable across every run of that cell) is passed as
  `ensure_instance`'s separate `config_label` param. A project can then author
  `sandbox.config.68-84.json` once and have it apply to every run of that specific matrix
  cell — different plugin sets per cell, not just a different PHP/WP version.

### 3.6 Networking: reaching a live WordPress site from the act job container

For workflows that test against a real WP site (as opposed to classic self-contained
phpunit-with-`services:mysql` CI, which `act` runs entirely on its own with no need for our
instance at all), the cell's instance URL is exposed as `WP_BASE_URL=http://host.docker.internal:<port>`
— **not** the instance's human-facing secured `https://<name>.tst` URL, which is a
host-side proxy/DNS convenience that doesn't resolve inside an arbitrary container. This
was a real bug caught by live verification (see §3.8) before the fix.

`--container-options "--add-host=host.docker.internal:host-gateway"` is passed to every
`act` invocation so the job container can resolve `host.docker.internal` (Docker Desktop
provides this automatically on macOS; the explicit `--add-host` makes it work on native
Linux Docker too, where it isn't automatic).

**A "readiness race" was suspected, investigated at length, and disproven — worth recording
so it isn't re-litigated.** The very first `act`-backed run failed with `curl` `HTTP 000`
(§3.8 item 4), which was initially attributed to Docker Desktop's port-publish routing
needing time to propagate to OTHER containers after a fresh boot. A throwaway-container
"readiness probe" (`_wait_container_reachable`) was built to wait this out before invoking
`act`. It was subsequently REMOVED after live verification disproved the theory:
- Once the actual bug was fixed (the code was passing the instance's secured `.tst` URL,
  unreachable from inside any container, instead of `host.docker.internal:<port>` —
  §3.8 item 5), **every** subsequent run succeeded immediately (hundreds of ms) — no
  readiness delay was ever observed again, across many live runs.
- The probe itself turned out to be unreliable independent of the theory: under Docker's
  default bridge network it produced false-negative "unreachable" warnings even while
  `act`'s own curl succeeded moments later in the SAME run; under `--network host`
  (matching `act`'s own default network mode, on the theory that it needed to test the
  identical path) it **hung indefinitely** (30s+, no response at all) on this Docker
  Desktop / Apple Silicon setup.
- Conclusion: there was no real race. The one observed failure was fully explained by the
  wrong URL. The probe added real wall-clock cost (up to a minute in the worst case) and a
  misleading diagnostic for a condition that, on the evidence, never recurs once the URL is
  correct. Removed rather than keep patching a mechanism with no evidence it ever helped —
  see §3.8 for the full investigation trail.

### 3.7 `--if-event` and other CLI/MCP surface

```
./sb ci plan <workflow.yml> [--json]
./sb ci run <workflow.yml> --project-dir DIR \
       [--job ID ...] [--matrix-filter k=v ...] [--if-event NAME] \
       [--label-prefix P] [--concurrency N] \
       [--allow-deploy] [--list-secrets] [--keep-on-fail] [--strict-provision] \
       [--local | --remote NAME] [--workspace LABEL] [--timeout N] \
       [--accept-difference ID ...] [--async] [--dry-run] [--json]
./sb async-job <job_id> [--follow] [--kill] [--offset N] [--json]
```

`--if-event NAME`: loose containment check against the workflow's `on:` (string, list, or
dict all handled) — no trigger simulation, just "does this workflow mention this event at
all." A non-matching event returns `{"ok": true, "skipped": true, "reason": "..."}` and
runs nothing (not an error — matches how a real trigger mismatch behaves on GitHub).

`--async`: same detached-job model as `./sb e2e --async` (§4.3) for local runs.
Remote runs are already accepted durably and return a `parent_job_id` immediately;
each selected job/matrix cell is a child with its own retained output and deadline.
The exact local working tree is deployed once before remote acceptance. Remote
workflow processes are never attached to the submitting SSH/MCP stdio stream;
poll `job-status` and read retained pages with `job-output` instead.

MCP tools:
```python
def ci_plan(workflow: str) -> dict
def ci_run(project_dir: str, workflow: str, jobs: list[str] | None = None,
           matrix_filter: dict | None = None, if_event: str | None = None,
           label_prefix: str | None = None, concurrency: int | None = None,
           allow_deploy: bool = False, keep_on_fail: bool = False,
           strict_provision: bool = False, timeout: int = 900,
           local: bool = False, remote: str | None = None,
           workspace: str = "ci",
           accepted_differences: list[str] | None = None) -> dict
```
(Blocking; async MCP wrapping not yet exposed for `ci_run` either — see §7.)

### 3.8 Live verification log (what was actually proven, not just designed)

In order, on real Docker with real `act` invocations against disposable scratch projects
(never the user's real repos), each cleaned up after:

1. **Networking baseline**: a bare `act` job (no sandbox involvement) reached a long-running
   sandbox instance via `host.docker.internal:<port>` + `--add-host` — `HTTP 200`.
2. **`ci plan`** against the REAL Templately `deploy.yml`/`assets.yml`/`claude-code-review.yml`:
   correctly classified checkout=known, npm/composer=run, the two 10up actions=deploy,
   `claude-code-action`=unknown (pre-act-adoption; now unknown actions just run via act);
   listed `SVN_USERNAME`/`SVN_PASSWORD`/`MUKUL_PAT`/`ANTHROPIC_API_KEY` as needed secrets.
   Caught and fixed a classic PyYAML gotcha (`on:` parses to boolean `True`).
3. **Fail-loud secrets**: `ci run` with no secrets configured aborted immediately, before
   any docker call, with a clear message naming the missing secret.
4. **First full `act`-backed run** (fake 10up deploy step + a `git push` + a curl-the-
   instance step): the deploy step was correctly neutralized (stub ran, not the real
   action), the `git push` was blocked, `act` completed ("Job succeeded", exit 0) — but the
   curl got `HTTP 000` (instant connection failure).
5. **Root-caused #4**: the code was passing the instance's secured `.tst` URL (unreachable
   from inside any container) as `WP_BASE_URL`, not `host.docker.internal:<port>`. Fixed
   (§3.6). A manual reproduction against a long-running instance (using the CORRECT URL)
   confirmed `HTTP 200` — the connection itself was always fine; the URL was wrong. (A
   throwaway-container "readiness probe" was ALSO added at this point, on a theory later
   disproven — see items 6-8.)
6. **Re-ran #4 with the URL fix**: `HTTP 301` (a real response — WP's own http→https
   redirect), connected in 668ms — immediate success, no delay. Deploy-neutralization and
   dangerous-command-blocking both still correct. The readiness PROBE added in step 5,
   however, reported a false-negative "unreachable" warning on this SAME run, despite the
   actual curl succeeding — inconsistent with the probe's own premise.
7. **Investigated the probe's false negative**: hypothesized the per-attempt subprocess
   timeout (10s) was too short (killing a slow-but-fine attempt), bumped to 20s/attempt,
   60s total. Re-ran: warning STILL present. Hypothesized a network-mode mismatch instead
   (`act`'s job containers default to `--network host`, confirmed via its own debug output
   and `act --help`; the probe used Docker's default bridge network) and added
   `--network host` to the probe. Re-ran: warning STILL present. Tested the probe's exact
   `docker run --network host ...` command BY HAND against a long-running, known-good
   instance — it **hung for 30+ seconds with no response**, while the identical command
   WITHOUT `--network host` (bridge mode) had succeeded in under 1 second in an earlier
   manual test. Conclusion: `--network host` on this Docker Desktop / Apple Silicon setup
   doesn't behave like `act`'s own internal host-network container lifecycle (which uses
   `docker create` + a long-lived `tail -f /dev/null` entrypoint + `docker exec`, not a
   plain `docker run`) — matching network MODE didn't reproduce the network PATH, so the
   probe was comparing apples to oranges from the start. Given the probe had never once
   caught a real failure in dozens of successful runs since the URL fix, it was **removed
   entirely** rather than continue chasing its internals — see §3.6.
8. **Async wrapping**: `./sb ci run --async` returned `{"job_id":...}` immediately;
   `./sb async-job <id> --follow` initially showed NOTHING for the async job's full
   duration despite it actively progressing (docker containers visibly running) —
   root-caused to Python's stdout switching from line- to full block-buffering when
   redirected to a file (not a tty); fixed with `PYTHONUNBUFFERED=1` in the generated
   wrapper script, confirmed streaming real incremental output within 3 seconds of launch
   on the next run.
9. **Final clean run** (probe removed, URL fix only): `ci run` against the same scratch
   workflow passed end to end with no warnings, no misleading diagnostics, and no readiness
   delay — confirming steps 5-7's conclusion.
10. **`act` self-reported a CVE**: `act version 0.2.83` (the version installed all session)
    printed `🚨 This version of 'act' is vulnerable to CVE-2026-34041 and CVE-2026-34042 -
    please upgrade to 0.2.86 or later` on a routine run. Upgraded via `brew upgrade act` to
    0.2.89; re-ran the same workflow, warning gone, otherwise identical behavior. Worth
    periodically re-checking `act --version` against upstream, since this module trusts
    `act` to safely execute untrusted-ish third-party actions.
11. **Reusable workflow support, live-verified** (closes the §7 gap): built a synthetic
    `called.yml` (`on: workflow_call`, one input) + `caller.yml` (one job
    `uses: ./.github/workflows/called.yml` with `with:`, a second plain job `needs:` the
    first). `act -l` correctly staged both jobs; `act -j call-it` correctly resolved into
    `[call-it/Called/greet]` and printed the interpolated input — confirming `act` handles
    local reusable workflow calls with zero extra work from this module, as expected.
12. **Found and fixed a real label-collision bug via this same test**: running the
    caller/called pair through `./sb ci run` (not raw `act`) failed. Root cause: `_cell_label`
    keyed the ephemeral instance label on `(run_id, cell)` only — NOT `job_id`. Both jobs in
    the synthetic workflow have an empty `{}` matrix (`_cell_slug({})` is `None` for both),
    so both got the byte-for-byte identical label. `run_across_instances` then launched TWO
    concurrent `ensure_instance(..., create=True)` calls for the same instance name — one
    thread's fresh `wp core install` raced the other's, and the results dict (keyed by
    label) silently dropped one unit. This had been latent all session: every earlier test
    workflow had exactly one job, so it never collided until the first multi-job workflow
    was tried. Fixed by hashing `(job_id, cell)` together into the label (§4.1's
    `_cell_label` docstring has the full explanation); also fixed a pre-existing cosmetic bug
    in the same function where the caller already passed a `"ci-"`-prefixed run_id into a
    function that prepended its own `"ci-"`, producing labels like `ci-ci-79c6` in every
    earlier log entry above. Re-ran after the fix: both cells got distinct labels
    (`ci-0404-b6c15f`, `ci-0404-afdf79`), both passed.

Each of these is a REAL bug this iteration's live verification caught that unit tests alone
would have missed (wrong URL, wrong timeout, wrong module-attribute patched in a test, a
label collision that only a multi-job workflow could expose). That's the point of not
skipping the live step.

## 4. Shared plumbing

### 4.1 Fan-out helper (`sandbox/core/_fanout.py`)

Unchanged in shape from the original design: `run_across_instances(cfg, root, specs,
worker_fn, concurrency=None, keep_on_fail=False, strict_provision=False, on_progress=None)`
boots one labelled instance per spec (concurrently, capped default ~4), runs `worker_fn`
against each, tears down ephemerals. Both the e2e runner (`_run_shard`) and the CI runner
(`_run_cell_with_act`) plug into this as their `worker_fn`. A `spec` dict may carry
`php`/`wp` (version override) and `config_label` (per-label config key) — both threaded
through to `ensure_instance` by the helper; e2e specs simply omit them.

### 4.2 Per-label `sandbox.config` (owned by `docs/multi-instance-spec.md`)

`load_project_config(project_dir, label=None)` layers an optional
`sandbox.config.<label>.json/.yml/.yaml` at the HIGHEST precedence (above
`sandbox.config.override.json`) when `label` is given and not `"default"`. `ensure_instance`
gained a separate `config_label` param (distinct from the ephemeral instance `label`) for
exactly the CI case above (§3.5) where the two must differ. Malformed labels
(path-traversal shapes, wrong characters) are validated against the same
`^[a-z0-9][a-z0-9_-]{0,20}$` pattern used for instance labels and silently ignored (fall
back to no per-label layer) rather than raising — config loading must never fail because
of an untrusted label string.

### 4.3 Generic async job runner (`sandbox/core/_asyncjobs.py`)

**Not** the same model as spec 004's `wp_cli_async`/`sandbox/commands/jobs.py` (`launch_job`/
`job_status`/`kill_job`), which runs exactly one `wp <args>` command inside ONE instance's
container, state kept under that instance's own `wp_dir`. Neither `run_e2e` nor `ci_run` is
instance-scoped — they mint MULTIPLE instances via the fan-out helper — so they need a job
directory independent of any single instance: `$SANDBOX_HOME/runtime/async-jobs/<jid>/`.

`launch_background_job(cmd, cwd) -> jid`: writes a tiny `run.sh` wrapper (pid file, then
`cmd > output.log 2>&1`, then exit-status file) and launches it via
`subprocess.Popen(["sh", script], start_new_session=True)` — `start_new_session=True` makes
Python call `setsid()` itself before exec, so the job survives the launching process's own
exit with **no dependency on the external `setsid` binary**, which macOS doesn't ship
(util-linux is Linux-only; `commands/jobs.py`'s `launch_job` uses the external binary and
would not work standalone on macOS, though it happens to run inside a Linux docker
container there so it's unaffected). `background_job_status(jid, offset=0)` /
`kill_background_job(jid)` mirror `commands/jobs.py`'s shape exactly (same field names) so
polling code doesn't need two mental models.

New CLI: `./sb async-job <job_id> [--follow] [--kill] [--offset N] [--json]` — NOT
instance-scoped (no `--instance`/cwd resolution gate), a plain registry-wide global command
like `./sb instances`.

## 5. Spec / doc impact

- **A future numbered speckit spec** should own this feature's FRs once it stabilizes
  further (async MCP wrapping, reusable-workflow live verification — see §7). Check
  `specs/` for the next free number before running `speckit-specify` — `_instances.py`
  already name-drops "spec 013" internally for an unlanded licensing feature.
- **Amends `004-async-wp-cli-jobs`**: no FR change to that spec itself, but note the NEW,
  separate async model (§4.3) exists alongside it for non-instance-scoped operations —
  don't conflate the two when reading spec 004.
- **Amends `docs/multi-instance-spec.md`**: the per-label config layer (§4.2) is
  implemented THERE (`sandbox_core.py`, `sandbox/core/_instances.py`) and consumed here;
  cross-link both directions.
- **New external dependency**: `act` (nektos/act, https://github.com/nektos/act) is now
  required for `ci run` (not `ci plan`, which stays a pure parse). `cmd_ci` checks
  `shutil.which("act")` and dies with an install hint if missing, rather than failing
  confusingly mid-run. `remote provision` installs it on supported remote hosts.

## 6. Risks (updated)

- **`act` itself has no deploy gate** — the single most important thing to keep correct.
  Mitigated by the deny-list neutralization happening on a PATCHED TEMP FILE before `act`
  ever sees the workflow, not by any cooperation from `act`. Two independent nets: the
  `uses:` deny-list AND the raw-command scanner on every `run:` step.
  - **Deny-list is a keyword heuristic, not exhaustive** — a truly novel deploy action whose
    name contains none of `deploy/release/publish/gh-pages/svn/push` could slip through.
    Residual risk, same shape as any deny-list; the explicit 10up prefixes cover the two
    concrete real cases this was built against.
- **Resource exhaustion** — unchanged from the original design: default concurrency cap
  ~4 concurrent stacks, `--concurrency` to override, queuing not "all cells at once."
- **Concurrent `act` cleanup** — `act` does not safely namespace all Docker resources
  across host processes. Sandbox keeps each matrix cell as an independent durable job and
  serializes only the short `act` invocation with a host-wide lock.
- **`act` + Apple Silicon** — act warns about container architecture on M-series chips
  (`--container-architecture linux/amd64` available if issues arise); not needed in any
  live verification run so far.
- **Reusable workflows** — `act` supports them natively, but this was not live-verified in
  this environment (no reusable-workflow-calling test fixture was built). Flagged as a gap,
  not silently assumed working (§7).
- **Herd driver** — unchanged: fan-out assumes docker isolation; verify per-linked-dir PHP
  isolation before advertising CI/e2e parallelism on Herd.
- **Bind-mount coupling** — unchanged: all of a root's instances share the same plugin
  source; a build-class `run:` step writing into the shared worktree across concurrent
  cells is a real race if a workflow's build isn't idempotent/cell-independent.

## 7. Known gaps (explicit, not silently skipped)

- **Async MCP wrapping** — DONE. `run_e2e`/`ci_run` MCP tools take `async_: bool = false`;
  when true they shell to `--async` and return `{ok, job_id}` immediately instead of
  blocking. New MCP tools `async_job_status(job_id, offset=0)` / `async_job_kill(job_id)`
  (`mcp/wp-server/tools/asyncjobs.py`) poll/cancel it — same shape as `wp_cli_job`/
  `wp_cli_job_kill` so callers don't need two mental models.
- **Reusable workflow calls** (`jobs.<id>.uses: ./.github/workflows/x.yml`) — DONE,
  live-verified (§3.8 items 11-12). Confirmed working via `act` natively, both at the raw
  `act` level and through the full `./sb ci run` wrapper. Only LOCAL reusable workflow
  refs were tested (`./.github/workflows/x.yml`); a call to a reusable workflow in ANOTHER
  repo (`org/repo/.github/workflows/x.yml@ref`) would need that repo to be resolvable by
  `act` (network/auth-dependent) — not tested, but orthogonal to anything this module does.
- **Composite actions** — still not specifically live-verified beyond the reusable-workflow
  and third-party actions already exercised. Expected to work via `act` the same way; lower
  priority since nothing in this session's real-world survey (Templately, Query Monitor,
  wp-scaffold) uses one.
