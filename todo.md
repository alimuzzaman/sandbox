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
