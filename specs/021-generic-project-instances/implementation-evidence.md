# Implementation Evidence: Generic Project Instances

## Generic Compose runtime increment — 2026-07-16

One framework-neutral local Compose adapter now covers explicit PHP,
JavaScript/Node, Docker-native, Laravel/Sail, Astro, and similar projects.
Framework aliases normalize to `kind=compose`; recognition is read-only and
only proposes reviewable configuration. No discovered package command runs
during detection.

The design follows Docker Compose's explicit project/service model, Laravel
Sail's Compose-first development model, and npm's declared `scripts` contract.
The adapter uses argument-list-only subprocess execution, loopback-only host
publication, Sandbox-owned overlays under `$SANDBOX_HOME/runtime/projects/`,
bounded health probing, and generic destroy without Compose volume removal.

## 2026-08-21 — explicit generic init is review-only

`sb init --type compose|generic|astro|laravel|php|node|javascript` now stops
after descriptor validation/proposal. It writes only reviewable project files,
prints `./sb ensure --project-dir ...` as the next action, and does not invoke a
runtime adapter, start a service, execute a package command, or provision the
WordPress test harness. Existing explicit kind/framework conflicts fail closed
before any runtime call. The no-type WordPress init path retains its historical
config scaffold, instance ensure, and optional test-harness behavior.

Focused regression evidence:

```text
.cli-venv/bin/python -m unittest -v tests.test_generic_init
Ran 4 tests — OK
```

Focused evidence:

```text
python3 -m unittest -v tests.test_generic_compose tests.test_project_config tests.test_runtime_adapters tests.test_runtime_service
Ran 17 tests — OK
```

Disposable `tests/fixtures/generic-compose/` evidence: ensure twice reused one
registry identity and URL; `status → down → up → apply` completed successfully;
generic instance deletion completed without a volume-removal flag. A copied
Astro fixture generated reviewable `sandbox.config.json` and
`sandbox.compose.yml`, selected npm from package metadata, and booted after
dependency installation.

The first Astro run exposed and fixed a shared HTTP transport defect:
`RemoteDisconnected` is now treated as a transient failed probe instead of
escaping the bounded health loop. Generic MCP lifecycle parity, every
WordPress-only capability preflight, HTTPS route parity, and final full-suite
acceptance remain open tasks and are not claimed complete here.

Modularity follow-up: lifecycle parser registration now lives beside the
lifecycle handlers in `sandbox/commands/lifecycle.py`; `sandbox/cli.py` remains
large because it is still the compatibility composition root for the older
parser definitions and the central instance-resolution/capability gate. This
is an intentional incremental boundary, not a claim that the whole CLI has
been rewritten.

Astro live acceptance: a fresh copy under `$HOME` was initialized with
`sb init --type astro --no-test-harness`, generated explicit config/Compose
files, reached HTTP 200 on its allocated loopback port, then reflected a
source edit (`Sandbox Astro Fixture Updated`) without rebuilding the image.
The disposable Compose project was stopped and removed afterward.

## T001 — Pre-change WordPress baseline

**Recorded:** 2026-07-15

This evidence establishes the WordPress behavior and composed surface inventory
before generic-project implementation. It intentionally excludes generated
credentials and autologin URLs.

### Live project instance

| Check | Command | Result |
| --- | --- | --- |
| Ensure | `./sb ensure` | Exit 0. Created/resolved `sandbox-remaining-spec-t`; WordPress URL `http://localhost:8192`; server `nginx`; ports WP `8192`, DB `3322`, Mailpit `8129`. |
| Status | `./sb status` | Exit 0. `wp`, `nginx`, `db`, and `mailpit` services were running; database health check passed. |
| WP-CLI | `./sb wp core version` | Exit 0; reported WordPress `7.0`. |
| REST | `./sb visit http://localhost:8192/wp-json/` | Exit 0; HTTP status `200`, no browser console errors or network failures; load time `268 ms`. |

### Composed command and tool inventory

The owned-manifest architecture guard reports the following pre-change counts:

- CLI command specs: **68** (`sandbox.commands.manifest` / `sandbox.registry.COMMANDS`).
- MCP tool groups: **18** (`mcp/wp-server/tools/manifest.py`).
- MCP tools: **75** unique declared names.

These counts were independently asserted by
`tests/test_architecture_boundaries.py::TestArchitectureBoundaries::test_exact_owned_cli_and_mcp_inventories_are_enforced`.

### Repository test baseline

Command required by the scheduled-execution contract:

```text
python3 -m unittest discover -s tests -v
```

Result: exit **1** after **663 tests** in **24.472 seconds**: **2 failures**,
**1 error**, **3 skipped**. The failures are pre-change MCP composition
baseline failures in this environment:

1. `test_mcp_composition.TestMcpComposition.test_instance_and_hermes_groups_register_against_an_isolated_fake_context` errored because importing `mcp/wp-server/app.py` raised `ModuleNotFoundError: No module named 'mcp.server'`.
2. `test_mcp_composition.TestMcpComposition.test_instance_and_hermes_groups_have_no_app_import_or_import_registration_side_effect` failed because `mcp/wp-server/tools/hermes.py` contains an `app` import.
3. The suite also skipped `test_server_transport` because the MCP server dependencies are not importable from the selected interpreter.

The feature work has not changed product behavior yet; later task verification
must distinguish this pre-existing environment/dependency baseline from new
regressions.
### 2026-07-16 — generic proxy and MCP capability boundary increment

- `sandbox/core/_domains.py` now renders declared `kind=compose` registry routes
  alongside WordPress routes. Generic clean domains are persisted in the
  registry and do not trigger WP-CLI, REST, database, or application URL
  mutations. Generic secure dispatch uses the same Caddy/mkcert plumbing through
  `secure_generic_instance`.
- `sandbox/commands/net.py` and MCP `secure_instance` dispatch generic instances
  through that runtime-neutral path. WordPress secure behavior is unchanged.
- Capability preflight now covers the remaining WordPress-only MCP groups:
  debug, context plugin actions, plugin check, e2e, CI execution, and remote
  deploy, in addition to the previously covered WP/data/files/mail/abilities
  groups.
- Verification: `python3 -m py_compile` on all changed command/MCP modules;
  `.cli-venv/bin/python -m unittest -q tests.test_sandbox
  tests.test_generic_compose tests.test_cli tests.test_mcp
  tests.test_mcp_composition tests.test_architecture_boundaries
  tests.test_modularity`; `git diff --check` — all passed.

### 2026-07-16 — disposable generic lifecycle acceptance

- Copied `tests/fixtures/generic-compose` to a disposable project under
  `tmp/`, ensured it, and ran three complete `down → up → apply → status`
  cycles. The instance identity, allocated port `62822`, URL, and health path
  remained stable; every cycle reported ready.
- The Compose project declared a named volume. After destroy, the volume
  `generic-compose-fixture-marker` remained present, confirming the generic
  destroy path does not pass `down -v` or delete project-owned volumes.
- A CLI capability probe against the generic instance returned exit 1 with
  `project kind 'compose' does not support 'wordpress.cli'` before any WP-CLI
  subprocess. MCP schema/registration and preflight behavior remain covered by
  the direct MCP venv probe and focused MCP suites.
- A first generic HTTPS replay exposed stale bind-mounted Caddyfile state on
  Docker Desktop: the generated host file was current but the running proxy
  retained an older duplicate route. `regen_caddyfile` now replaces the file
  atomically (inode replacement) and generic destroy removes its route through
  the proxy facade. The proxy was restored to the WordPress HTTPS baseline
  (`curl -ksS https://sandbox.tst/wp-json/` returned 200), but the generic HTTPS
  replay itself still needs one clean rerun after the proxy has stabilized; T036
  remains intentionally open until that evidence is captured.

### 2026-07-16 — generic HTTPS replay after route-isolation fix

- Root cause of duplicate Caddy sites was confirmed: generic registry entries
  were included by the WordPress `resolve_instances()` path and by the generic
  proxy renderer. Generic records are now excluded from WordPress resolution;
  the proxy renderer owns them exactly once.
- Fresh disposable replay then ran `ensure → secure`, waited for the proxy to
  stabilize, and fetched `https://generic-https-86861.tst/`; the fixture health
  content was returned successfully. The same run destroyed the instance and
  removed its route without deleting the project volume.
- The focused post-fix command (compile, generic, sandbox, remote, MCP, and
  architecture suites) passed; `git diff --check` passed.

### 2026-07-16 — correctness/security and scope review

- Registry identity remains canonical-root + label based; generic runtime IDs
  are deterministic and collision-safe. Compose descriptors require the
  declared file/service/port and stay inside the project root.
- Process execution remains argv-based. Generic `exec` accepts only a non-empty
  string list; no discovered package script or shell text is executed. The
  overlay is written under the machine runtime directory, not the repository.
- Destroy invokes Compose `down` without `-v`, removes only the Sandbox registry
  record/artifact directory, and now removes the corresponding proxy route via
  the injected proxy contract. Named-volume survival was verified live.
- Review found and resolved two proxy defects: duplicate generic/WordPress route
  ownership and Docker Desktop stale bind-mounted Caddyfile state. No secrets
  were added to output or files; no commit, push, release, deployment, or
  external mutation was performed. Remaining deferred work is limited to the
  live MCP-server restart/full-suite gate and explicitly protected acceptance
  tasks listed in the Spec-Kit artifacts.

### 2026-07-16 — fresh MCP runtime-tool acceptance

- A fresh `mcp/wp-server/.venv/bin/python` process imported the current server
  composition against a disposable generic fixture and invoked
  `instance_status`, `instance_logs`, and
  `instance_exec(["printf", "mcp-runtime-ok"])`. All returned `ok=true`;
  exec returned `mcp-runtime-ok`, with no WordPress subprocess involved. The
  fixture was then destroyed through the CLI.
- Combined with the three lifecycle cycles, named-volume survival, capability
  rejection, and successful `https://generic-https-86861.tst/` probe above,
  this closes T036. The long-lived application MCP process still needs a
  restart before external clients see the new tool group, as required by the
  project runtime contract.

### 2026-07-16 — modularity inventory after touched-path cleanup

- Inventory: 68 CLI commands, 50 statically decorated MCP tools, 20 remaining
  wildcard-import sites, and 41 runtime-kind branches. The three modified
  command modules now declare their Sandbox-core imports explicitly;
  `sandbox/cli.py` remains a documented compatibility composition root rather
  than being treated as fully decomposed.

### 2026-07-16 — quickstart scenario audit

- Scenario 1 (WordPress baseline): ensure/status/WP-CLI/REST/lifecycle parity
  was captured against the existing `sandbox` instance; this tooling repository
  has no plugin PHP harness, so `./sb test` is recorded as expected
  non-applicability rather than a product failure.
- Scenario 2 (minimal Compose): fresh fixture ensure/idempotency plus three
  lifecycle cycles passed with stable identity/port/URL.
- Scenario 3 (capability rejection): CLI WP command rejected with
  `unsupported_capability`; a fresh MCP runtime-tool process verified generic
  status/logs/exec without WP subprocesses.
- Scenario 4 (Astro): a fresh copied fixture generated explicit config/Compose,
  booted, passed HTTP health, and reflected a source edit live.
- Scenario 5 (safe destroy): the named-volume marker survived generic destroy
  and the proxy route was removed.
- Scenario 6 (WordPress parity): local HTTPS REST and lifecycle replay passed;
  exact command/output references are recorded above.

## T060 live-state/session-refresh evidence — 2026-08-13

- `python3 -m unittest -v tests.test_state_freshness tests.test_generic_compose tests.test_runtime_service`
  — 21 tests passed in 0.125s.
- A mutable Compose `ps --format json` observation changed from a running to an
  exited service between two status sessions. The second response reflected the
  new service state and carried a different `observation_generation` digest;
  no process-local cache was used.
- A live WordPress/plugin-shaped adapter fixture changed a plugin from active to
  inactive between observations. The next response reflected the mutation and
  changed generation. A registry snapshot with the same plugin data was retained
  as evidence but returned `state_current=false`, `observation.stale=true`, and
  could not satisfy an active/inactive truth claim; existing WordPress parity
  evidence remains the live-stack boundary.
- Compose status now derives its lifecycle state from the declared service row
  when Docker returns structured output and marks the observation as live. The
  shared runtime service adds UTC observation time, generation, and an explicit
  stale/current marker to every status response.
- `./sb status --help` exposes `--refresh`; MCP `instance_status` accepts the
  matching `refresh` flag and both pass it through the shared runtime request.
- `git diff --check` passed; no remote, Docker, SSH, deployment, or destructive
  action was used for this evidence.

## Final local verification — 2026-07-16

- `./.cli-venv/bin/python -m unittest discover -s tests -v` — 686 tests
  passed in 30.889s; 1 MCP transport test skipped because the root test
  environment does not provide `httpx` (the MCP venv was verified separately).
- `./sb selftest` — passed; it reran the same 686-test suite and completed the
  registry, CLI, and runtime checks.
- `git diff --check` — passed.
- No retry was required after the compatibility-import fixes; no commit, push,
  release, deployment, or external mutation was performed.
