# Hermes execution queue (critical first)

Updated: 2026-08-23. This is the handoff queue for Hermes. Feedback below is
evidence, not execution authority: reproduce it first, preserve dirty work,
and do not reset, destroy, clean up remote resources, deploy, release, or
expose secrets without fresh explicit authority.

## P0 — reliability and current operator blockers

- [x] **Restore a deterministic local CLI interpreter.** FIXED 2026-08-23.
  Launcher now validates interpreter candidates (`a818bca`, `2690b75`);
  pyenv 3.12.8 present and `.python-version` = 3.12 resolves. Acceptance
  verified from a clean shell (`env -i`): `./sb --help` exit 0, `./sb
  feedback list --project-dir . --json` returns valid envelope exit 0,
  focused `tests.test_remote` via `.cli-venv` 150/150 OK.

- [ ] **Make remote durable-job acceptance and observation reliable.** A
  job-start can exit silently without a durable request/job record, and an
  accepted job-status can emit no JSON. Define one replay-safe acceptance
  envelope, request-ID lookup, and bounded output contract. Acceptance: exact
  request lookup always distinguishes accepted, rejected, and unknown; no
  empty-success output. Feedback: `343d1a5a…`, `8b88c87e…`.

- [ ] **Re-establish trustworthy remote capacity evidence before provisioning
  or cleanup.** Historical read-only inventories found 29–31 active managed
  Docker networks, with incomplete attribution and no safe cleanup candidate.
  Re-run the supported inventory on the installed revision; retain unresolved
  records and produce a non-destructive capacity/ownership plan. Acceptance:
  complete or explicitly bounded inventory, revision receipt, and no inferred
  cleanup authority. Feedback: `78aaf583…`, `0fac3b07…`, `bf05eeb9…`,
  `a813480b…`, `600d2def…`.

- [ ] **Repair the remote evaluator flow before more benchmark runs.** The
  T120 evaluator required manual recovery because job acceptance, retained
  output retrieval, test bootstrap, and phase classification were unreliable.
  Acceptance: one reproducible remote evaluation yields durable acceptance,
  structured terminal output, phase-aware classification, and a sanitized
  receipt. Feedback: `9bb7aea1…`.

- [ ] **Make remote reachability actionable.** `remote list` can report a
  host reachable while brokered SSH times out, and there is no supported
  secret-safe host preflight/exec surface. Acceptance: status names the usable
  transport and failure reason, with a bounded supported host diagnostic path.
  Feedback: `b340f98a…`, `56bf50f…`.

- [ ] **Diagnose the Hermes dashboard session mismatch.** The selected
  “Check GitHub Repo Access” session (218 messages) rendered the old
  self-check transcript after the dashboard restart; its event feed was
  disconnected. Do not delete either session. Acceptance: after reconnect and
  a fresh browser load, each session ID renders its own persisted transcript;
  otherwise capture a minimal upstream reproduction and pin a tested fix or
  update plan. Gateway is currently off, so do not treat this as a cron fix.

## P1 — Hermes and CLI correctness

- [ ] **Expose dashboard/public-readiness in Hermes status.** `hermes status`
  can say configured while the dashboard is absent or unhealthy. Acceptance:
  status distinguishes agent configuration, dashboard health, gateway state,
  and public exposure. Feedback: `a3050df7…`.

- [ ] **Make `hermes repo sync` work for a provisioned Sandbox runtime.** The
  documented managed-repo route reports the repository missing. Acceptance:
  a supported, revision-verified sync path works without raw SSH edits.
  Feedback: `96819c8…`.

- [ ] **Make recovery and diagnostics CLI contracts match their help.** Expose
  restore confirmation flags, prevent doctor startup crashes, accept documented
  `skill show --project-dir` placement, and document focused-test selection.
  Acceptance: each command has a parser-level regression test and one
  documented invocation. Feedback: `bb1f932b…`, `3727d6d…`, `05936f99…`,
  `c7148951…`, `35ed6086…`, `6bc4c6d5…`.

- [ ] **Keep the dashboard authorization flow usable without hidden state.**
  `sandbox-authorizations` v1.0.6 is enabled and the dashboard is healthy;
  verify one manual approval request/review/reject path after the session-view
  issue is fixed. Do not change its manual-approval policy.

- [ ] **Enable long-running cron only after the gateway is intentionally
  started.** Design one workdir-pinned job per project (serialized by Hermes),
  choose an interval above p95 run time, and use a `wakeAgent:false` pre-check
  for frequent polling. Acceptance: no concurrent run of the same job, bounded
  run history, and a documented missed-run behavior. The current gateway is
  inactive; do not create an unattended production schedule yet.

## P2 — test-environment and regression cleanup

- [ ] **Fix plain-environment test behavior.** `tests/test_mcp.py` should
  skip cleanly or install its declared MCP extra; discovery guidance tests
  should skip cleanly when PHP is unavailable. Acceptance: full suite reports
  only intentional skips on a minimal supported venv.

- [ ] **Root-cause the isolated-SANDBOX_HOME reclaim probe regression.**
  `test_observe_emits_the_reclaim_inventory` returns zero deploy-source
  worktrees after remote-probe changes. Acceptance: a focused regression test
  proves correct attribution without broadening cleanup authority.

- [ ] **Close controller-only historical test seams.** Replace the cross-realm
  `structuredClone` fixture issue and closed-list schema ordering mismatch with
  deterministic tests. Feedback: `32738043…`, `65d54278…`.

- [ ] **Add the bounded-edge-capture invariant test** and revisit non-JSON
  Compose status only when the minimum supported Compose version changes.

## Feedback ledger — all 23 records

### Critical / high

- [ ] `9bb7aea1a679db7233e18f8fbf31c841` — T120 evaluator and remote benchmark workflow gaps.
- [ ] `b340f98a0df25f2c7817d7cac2bd652b` — registered remote reachability disagrees with brokered SSH.
- [ ] `600d2def1a44dde2e811a79e01b9aa25` — 31 active managed remote networks; partial inventory.
- [ ] `343d1a5ae1c59b8f2754ee24ebf96b9d` — remote job-start silently lacks durable acceptance.
- [ ] `a813480b2b761798b839cd4682c0649b` — 31 active managed remote networks; partial inventory.
- [ ] `bf05eeb9362ba7e408b9315669698b60` — 31 active managed remote networks; partial inventory.
- [ ] `0fac3b07416044a28041c2a358cf2084` — remote network count increased to 31; partial inventory.
- [ ] `78aaf5836d63078b060336a9e306b7f5` — fast-test harness blocked by Docker network-pool exhaustion.

### Medium

- [ ] `a3050df7119b8750a6da00837951d014` — Hermes configured status hides absent dashboard.
- [ ] `56bf50f61cf5065710dc5acd608575fa` — remote host operation lacks a brokered exec path.
- [ ] `8b88c87e231e7eedc19d9410289688ca` — job-status returned no output after accepted remote job.
- [ ] `96819c8e948b59b8205afb53d5383041` — Hermes repo sync cannot refresh provisioned runtime.
- [ ] `3727d6d5ba84090a5c1a5dbbe1e408ee` — doctor crashed before remote diagnostics (verify current status).
- [ ] `bb1f932babd2e4e0c1afebe0621456ee` — restore confirmation is missing from the CLI parser.
- [x] `b1864a6bfe983b15f24a26f7c20a88b2` — false skill-registration failure from inventory truncation; fixed in `b2abd7c`.
- [x] `d47f53dccc7fce3cbf314211a0932219` — false skill-discovery failure from case-sensitive matching; fixed in `431c992`.

### Low / follow-up

- [ ] `05936f990e374823e61c0ceb5f25e5c7` — `skill show` rejects documented project-dir placement.
- [ ] `c7148951984491f103e0d106d190b7d5` — focused test-file selection is not discoverable from `sb test`.
- [ ] `aff7c116c78be838405d39bdc6f8e502` — focused Python tests selected an unsupported system interpreter.
- [ ] `3273804376c6c8d41b079a9fe6b3e15c` — T118 cross-realm `structuredClone` fixture incompatibility.
- [ ] `65d5427835e56f60a2cd0637a40cc2e3` — T118 schema closed-list ordering mismatch.
- [ ] `35ed60860c8c1d2d034c8d5489d456d8` — feedback list rejects over-limit request (document/enforce bound).
- [ ] `6bc4c6d5e0a70e0a048030d694ad76f9` — compact active-job parser failed on null data.

---

# Review findings: 6119333..HEAD (2026-08-22)

Scope: 122 commits, 210 files (+24,154/-1,056; ~10k production lines).
Method: full read of every production diff plus targeted execution.
Execution evidence: tests/test_remote.py 150/150 green on Python 3.12;
full suite 3,044 passed / 11 failed / 14 skipped (see open items).

## Verdict

No security vulnerabilities found. Secrets, redaction, fail-closed
admission, lock hardening, and output bounding are consistently correct.
Docs landed with code and tests landed with code throughout the range.

## Findings

- [x] FIXED - Python version pin. `sandbox/core/_hermes.py` uses an
  f-string backslash expression (legal only on 3.12+). With no
  `requires-python` pin, uv defaults to 3.11 and the whole suite dies at
  import with SyntaxError. Fix: add `.python-version` = 3.12. Follow-up:
  declare `requires-python = ">=3.12"` when a pyproject.toml lands.
- [ ] OPEN - `tests/test_resource_reclaim_service.py::ProbeCase::
  test_observe_emits_the_reclaim_inventory` fails on 3.12: probe returns
  zero deploy-src worktree entries for an isolated SANDBOX_HOME
  (status=partial, engine_complete=true, index_available=true). The test
  predates this range but `sandbox/resources/remote.py` (+222) changed
  the probe program. Needs root cause; do not guess-fix.
- [ ] OPEN - `tests/test_mcp.py` (7 failures): child interpreters import
  `mcp.server.fastmcp`, which is absent unless the mcp extra is installed.
  The module docstring claims tests skip cleanly when the venv is not
  built; they fail instead. Add a pytest.importorskip guard or a test
  extra so the suite stays green on plain environments.
- [ ] OPEN - `tests/test_spec003_discovery_guidance.py` (2 failures) and
  one alias SAN test require a `php` binary on PATH. Skip cleanly when
  php is unavailable instead of failing.
- [ ] NOTE - `_BoundedEdgeCapture` first-overflow fall-through (both
  branches append the chunk) was verified correct by analysis: the final
  trim always leaves the true last `tail_limit` bytes. Worth adding a
  property-style unit test to lock the invariant.
- [ ] NOTE - `compose status` keeps `ready` for non-JSON Compose output
  (older implementations). Deliberate and commented; revisit if the
  minimum supported Compose version rises.
