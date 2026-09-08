# Sandbox lifecycle inventory

Date: 2026-09-08
Checkout: `fd7d650f8bfbb1760d931e2484cc01fa0badf546`
Method: bounded source and test inspection. The recorded test invocation supplied only `PYTHONPATH=repo`; it did not override `SANDBOX_HOME` and did not globally patch Docker preflight. No SSH, DNS, remote controller, browser, database, or production action was run. The test process did perform the read-only Docker preflight described below and wrote test-generated generic Compose overlays under the resolved Sandbox home.

## Scope and result

The lifecycle has two main provider paths:

```text
CLI sb ensure/init/up/down/status/logs
  ├─ project target resolver
  ├─ WordPress runtime service → core._instances.ensure_instance
  │   ├─ project + port locks
  │   ├─ Docker preflight / mount and install-state observations
  │   ├─ pending registry record
  │   ├─ Compose up or Herd provision
  │   ├─ WordPress install and wiring
  │   └─ ready registry record
  └─ Generic Compose runtime service → runtimes.compose.ComposeAdapter
      ├─ descriptor and service validation
      ├─ Compose up
      ├─ health probe
      └─ ready registry record

remote ensure/deploy
  ├─ target and provisioned-remote checks
  ├─ exact working-tree transfer / isolated workspace
  ├─ co-located `sb ensure --local --project-dir ...`
  ├─ WordPress `sb apply` and plugin activation when applicable
  └─ optional Caddy route, HTTPS URL, and WP option update

clean URL / cold start
  ├─ Caddy route generated from current registry
  ├─ activation gateway owns authenticated wake claims
  └─ scheduler reconciles state and suspends only safe idle routes
```

The normal WordPress path is strongly fail-closed around source-mount identity, Docker availability, and ambiguous install state. The main source-level lifecycle risks found in this pass are below. They are review candidates; the focused suite is green, and none of them has been promoted to a live defect without runtime evidence.

## WordPress entrypoints and state transitions

`cmd_ensure()` first delegates to `_remote_lifecycle()` unless `--local` is explicit. The local path invokes the typed WordPress runtime service with a project root, operation `ensure`, selected label, and `create` flag (`sandbox/commands/instances_cmd.py:492-525`). Typed operation errors are emitted as compact JSON or a redacted human error (`sandbox/commands/instances_cmd.py:526-586`). A successful local result must include an `instance` record before it is printed (`sandbox/commands/instances_cmd.py:587-601`).

`cmd_init()` has two contracts. Explicit generic `--type` writes or validates a reviewable Compose descriptor and stops; it does not start project code until `sb ensure`. The legacy/no-type flow preserves WordPress boot behavior, writes the descriptor when needed, calls `ensure_instance()`, and may provision the test harness (`sandbox/commands/instances_cmd.py:603-710`, `sandbox/commands/instances_cmd.py:710-800`).

`ensure_instance()` loads the project config, takes a project lock, and checks the Docker daemon before port allocation, local YAML, pending registry, or Compose writes. Herd is excluded from the Docker preflight because it is host-served (`sandbox/core/_instances.py:1176-1252`).

For a ready record, the path first attests the exact live source mounts. It then probes the recorded URL and classifies WordPress install state. A reachable setup screen is insufficient: `wp core is-installed` must be a clean installed result, or an empty negative result must be followed by a successful `SELECT 1`; malformed output, transport failure, timeout, or unexpected output returns a typed write-free refusal (`sandbox/core/_instances.py:1253-1279`, `sandbox/core/_instances.py:715-779`).

The ready fast path rechecks port conflicts, requires live reachability and installed state, warns on version drift, retries a clean route when appropriate, repairs the WP URL, refreshes the registry URL, and returns (`sandbox/core/_instances.py:1281-1317`). A stopped or partial record reuses its instance name and ports. A new record derives a name and allocates ports (`sandbox/core/_instances.py:1319-1344`).

For a new or resumed boot, extension runtime preparation completes before local YAML or registry mutation. The controller then writes the instance block, records `status=pending`, renders Compose, boots Docker or provisions Herd, and runs WordPress install (`sandbox/core/_instances.py:1345-1401`). After installation, it calls `_wait_http()` and retries the owned clean route. The `_wait_http()` result is currently ignored (`sandbox/core/_instances.py:608-617`, `sandbox/core/_instances.py:1402-1413`). Plugin/theme wiring, extension verification, multisite recreation/readiness, and install snapshots follow (`sandbox/core/_instances.py:1414-1449`). Only after these steps does the function compute URLs and replace the pending record with `status=ready` (`sandbox/core/_instances.py:1451-1479`).

`_wait_reachable()` uses the canonical URL without following redirects. A 2xx–4xx response is treated as a web-tier response; 5xx and transport failures retry until the bounded timeout. Its `backend_only` mode probes `localhost:<port>` so activation wake does not re-enter its own pending forward-auth request (`sandbox/core/_instances.py:620-680`). `_instance_reachable()` has the same 4xx-is-up interpretation for the ready-path probe (`sandbox/core/_instances.py:683-707`).

## Generic Compose lifecycle

`ComposeAdapter.invoke()` validates and normalizes the descriptor, derives a runtime identity, finds or allocates an HTTP port, writes an overlay, and builds a Compose command with explicit project name, directory, source file, and overlay (`sandbox/runtimes/compose.py:277-320`).

For `ensure`, it verifies the declared service through `docker compose config --services`, starts only that service, polls the declared health URL until the startup deadline, and writes a ready registry record only after a successful probe. A timeout collects a bounded tail of service logs and raises (`sandbox/runtimes/compose.py:321-351`).

For status, it parses JSON `compose ps` rows and treats an explicit matching service row as authoritative. A failed Compose command is `error`; a running matching row is `ready`; a stopped matching row is `stopped` (`sandbox/runtimes/compose.py:353-385`). Start/stop/resume/suspend/apply/exec/destroy are separate operations. Resume has a health deadline and returns `resume_readiness_failed` on timeout. Failed generic exec returns bounded stdout/stderr and the child exit code without writing a ready/stopped record (`sandbox/runtimes/compose.py:387-498`).

`cmd_up()` dispatches generic Compose `start` through the runtime service and preserves typed stale-network recovery. WordPress `up` handles Herd as host-served, otherwise starts the declared service set, verifies PHP extensions, rewrites required mu-plugins, and returns the current site URL (`sandbox/commands/lifecycle.py:219-302`, `sandbox/commands/lifecycle.py:352-482`). `cmd_down()` stops generic Compose through the service. WordPress down takes the owner lock, verifies the current registry identity, runs Compose down, and records `status=stopped` so the next ensure resumes the retained instance (`sandbox/commands/lifecycle.py:503-528`).

## Remote lifecycle and deployment

The remote lifecycle resolver supports direct remote status/logs and project-scoped remote operations. For remote ensure it checks reachability, transfers an exact working tree, and prepares an isolated workspace (`sandbox/commands/lifecycle.py:904-955`). The nested command is explicitly `sb ensure --local --project-dir <target>`, with `--label <workspace> --create` for ensure and JSON output for machine parsing (`sandbox/commands/lifecycle.py:956-987`). Remote ensure has bounded SSH execution, bounded output, typed empty/invalid output handling, and a compatibility retry for older runtimes that lack `--reveal-login` (`sandbox/commands/lifecycle.py:988-1040`).

The lower-level remote ensure follows the same explicit local selector and supervisor timeout (`sandbox/core/_remote.py:1223-1260`). Remote apply uses an explicit project path and label but intentionally does not create another instance (`sandbox/core/_remote.py:1263-1299`). Plugin activation uses the exact returned instance name for probing, symlink creation, and `wp plugin activate` (`sandbox/core/_remote.py:1302-1344`).

Remote deploy establishes a read-only inventory baseline before ensure, cleans up only a uniquely new instance after a failed ensure, and refuses mutation if the baseline cannot be established (`sandbox/commands/deploy.py:108-125`). With ensure/expose it reconciles and activates WordPress, checks that apply returned the same instance identity, then configures the public route and aliases (`sandbox/commands/deploy.py:280-345`).

### Remote URL selector risk (review candidate)

`preview` receives a branch-specific label and passes it to both remote ensure and apply (`sandbox/commands/preview.py:169-189`). It then calls `set_remote_instance_url()` without passing the selected instance or label (`sandbox/commands/preview.py:190-200`). Deploy follows the same pattern after selecting an ensured instance (`sandbox/commands/deploy.py:286-301`, `sandbox/commands/deploy.py:302-348`).

`set_remote_instance_url()` changes `home` and `siteurl` by running `cd <target> && <sb> wp option update ...` with no `--local`, `--instance`, `--project-dir`, or `--label` (`sandbox/core/_remote.py:1585-1598`). This is weaker than the exact selectors used by ensure, apply, and plugin activation. On a remote project root with more than one registered label, ordinary `sb wp` project resolution can be ambiguous and refuse before either option is changed. A stale `SANDBOX_INSTANCE` or a different implicit selector could also update the wrong instance if present in the remote process environment. No test currently asserts the constructed URL-update command; existing deploy/preview tests mock `set_remote_instance_url()` (`tests/test_remote.py:3589-3595`, `tests/test_hosting.py:3766`).

Recommended bounded follow-up: pass the exact returned instance identity into this helper and invoke the co-located CLI with an explicit local/instance selector, or pass the exact target label through `--local --project-dir ... --label ...`; add a test with two labels proving only the ensured preview receives both option updates. This is a source-level candidate until a remote multi-instance reproduction confirms impact.

## Clean URL and activation wake

The documented default is Sandbox Docker/Caddy plus Sandbox-owned DNS on every platform. Localhost is a fallback only when the default proxy is unavailable (`docs/clean-url-default.md:1-20`). Ingress status probes the exact generated route. Ensure retries the route for an existing ready instance whose URL is still localhost and retries after fresh WordPress installation, then repairs WordPress's canonical URL (`docs/clean-url-default.md:69-80`).

`_ensure_activation_gateway()` builds the catalog from current registry/config, accepts an empty catalog, accepts a healthy authority, or attempts bounded supervision enablement (`sandbox/core/_domains.py:889-905`). `regen_caddyfile()` strips stale `forward_auth` middleware when the authority is unhealthy, leaving running backends reachable and making stopped backends fail at their normal port (`sandbox/core/_domains.py:907-985`).

The activation HTTP application keeps `/healthz` independent of registry I/O, refreshes the catalog for `/v1/activate`, requires a known route ID and Bearer token, and returns 204 only when the single-flight activation service succeeds (`sandbox/activation/http.py:75-107`). `ActivationService.activate()` fast-paths ready/pinned routes, claims one owner, bounds waiters, marks ready/error from the owner result, and ends the request claim (`sandbox/activation/service.py:20-71`).

The scheduler refreshes changed routes, reconciles observed runtime state, checks activity evidence before suspension, pins uncertain or active routes, and marks errors on uncertain runtime or failed suspend. Its loop refreshes failures conservatively and does not suspend through a stale catalog (`sandbox/activation/scheduler.py:59-143`).

## Verification run

The focused suite was run from this checkout:

```text
PYTHONPATH=repo python -m unittest \
  tests.test_instance_lifecycle \
  tests.test_instance_ready_install_state \
  tests.test_generic_compose \
  tests.test_docker_preflight \
  tests.test_setup_idempotency \
  tests.test_clean_url_default_policy \
  tests.test_activation_gateway

Ran 88 tests in 2.205s
OK
```

Coverage is mixed rather than fully hermetic. In `tests/test_instance_ready_install_state.py`, the ready-path patches replace registry/config/write helpers and `docker_daemon_preflight` is not patched (`tests/test_instance_ready_install_state.py:160-170`, `tests/test_instance_ready_install_state.py:172-198`). Because those fixtures report a ready Apache instance, `ensure_instance()` reached the real preflight implementation, which calls `shutil.which("docker")` and `docker info` with `check=False`, captured output, and a five-second timeout; that probe is read-only (`sandbox/core/_docker.py:662-703`). The exact inherited `SANDBOX_HOME` was not captured in the prior output, so the base used by that probe and by generated artifacts cannot be stated from the transcript alone.

Generic Compose tests use temporary project roots and fake process/HTTP/registry dependencies, but `ComposeAdapter._artifact_dir()` resolves its artifact base from inherited `SANDBOX_HOME` or `Path.home()/sandbox`, creates `runtime/projects/<runtime_id>`, and `_overlay()` writes `sandbox.override.yaml` there (`sandbox/runtimes/compose.py:212-254`). Since those tests passed and do not patch `_artifact_dir()` or `_overlay()`, the 88-test invocation wrote generated overlay files under that resolved Sandbox home; the exact base was not captured, and the fixture source files themselves were temporary and cleaned by their context managers. WordPress ensure write paths are mocked in the ready-install tests (`tests/test_instance_ready_install_state.py:287-305`, `tests/test_instance_ready_install_state.py:388-395`, `tests/test_instance_ready_install_state.py:456-472`), and the direct Docker preflight gate test patches the preflight and local write helper (`tests/test_docker_preflight.py:42-75`). Activation tests use temporary directories only for lease/Caddy fixtures. Generic Compose tests cover idempotent ensure/status, stale-network handling, stop/start resume, failed status, force recreate, and bounded health-timeout logs (`tests/test_generic_compose.py:14-31`, `tests/test_generic_compose.py:269-363`). Activation tests cover auth/no-replay, header normalization, catalog refresh/failure, unhealthy Caddy fallback, scheduler safety, and route refresh (`tests/test_activation_gateway.py:193-385`). Remote transport tests assert explicit local nested ensure, JSON parsing, typed empty output, and target routing, but use mocked SSH/process results (`tests/test_runtime_transport.py:1079-1145`).

## Prioritized acceptance gaps and bounded follow-ups

1. **P1 candidate — ignored WordPress HTTP wait.** `_wait_http()` returns `False` on timeout, but the fresh ensure path continues toward plugin/theme wiring, snapshots, and final `status=ready` (`sandbox/core/_instances.py:608-617`, `sandbox/core/_instances.py:1401-1479`). Add a focused regression with a simulated HTTP timeout and assert no ready registry record. Then run a real local lifecycle smoke to determine whether `cmd_install` or later wiring independently prevents the state.

2. **P1/P2 candidate — remote URL update lacks exact instance identity.** The preview path can intentionally create multiple labels, while `set_remote_instance_url()` relies on implicit `sb wp` selection (`sandbox/commands/preview.py:182-200`, `sandbox/core/_remote.py:1585-1598`). Add the two-label command-construction test described above, then validate on a provisioned remote with exact instance and option evidence before changing the helper.

3. **P2 candidate — generic Compose status compatibility fallback.** If `docker compose ps --format json` exits zero but produces no parseable rows, `states=[]` and `service_state=None` map to `status=ready` for historical compatibility (`sandbox/runtimes/compose.py:353-377`). Existing tests cover failed status but do not cover a successful empty/malformed response. Add an explicit test and decide whether compatibility needs a stronger version/row evidence contract.

4. **Runtime proof gap.** The focused 88-test run proves that the local Docker preflight returned successfully in the ready fixtures, but it does not prove real container lifecycle behavior, real bind mounts, Caddy routing, activation wake, remote SSH/control service behavior, public HTTPS, or browser output. The next acceptance pass should use the supported Sandbox CLI, retain exact revision and bounded job output, and distinguish source/test success from live route and remote evidence.

5. **Generic daemon preflight comparison.** WordPress ensure has an explicit Docker daemon preflight before writes (`sandbox/core/_instances.py:1232-1252`). Generic Compose ensure begins with `docker compose config --services` (`sandbox/runtimes/compose.py:326-335`). Verify the bounded ProcessRunner behavior when the daemon is absent; do not classify this as a defect without observing whether it leaves state or exceeds the documented bound.

6. **Remote deployment evidence.** Remote deployment tests prove command composition and typed result handling through mocks. They do not prove the terminal remote job, exact installed Sandbox revision, isolated workspace, route, or public URL. Those are required for any release or production claim.
