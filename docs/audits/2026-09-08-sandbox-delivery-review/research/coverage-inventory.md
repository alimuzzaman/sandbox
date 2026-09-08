# Sandbox coverage inventory

Date: 2026-09-08
Revision reviewed: `fd7d650f8bfbb1760d931e2484cc01fa0badf546`
Review scope: source, tests, CI, release/install scripts, and documented acceptance gates. This is an inventory, not a claim that the stack was booted or deployed during this review.

## What the core outcomes require

The important outcomes are multi-stage lifecycles. A green unit suite proves the command contract and selected branch logic; it does not prove that the stages compose on a real host.

| Outcome | Production path | Evidence that exists | Coverage boundary / likely miss |
| --- | --- | --- | --- |
| Create a local instance | `sb init`/config resolution → compose or Herd setup → instance boot → WordPress readiness → clean URL → persistence across restart | `commands/instances_cmd.py` has dispatch and init logic; `commands/lifecycle.py` has a smoke path that captures CLI/REST, clean URL, restart, retained data, and owner cleanup | `tests/test_generic_init.py` uses `_FakeCore` and patched commands; its generic init is initialization-only. The normal Python selftest does not boot WP. Real smoke is `workflow_dispatch` only in `.github/workflows/smoke.yml`. |
| Provision and activate an immutable image | image/package build → remote provision → exact artifact/revision check → activate → health/edge/public-route checks → rollback | Hosting code and many unit contracts exist; `docs/release-readiness.md` names the required gates; dedicated canaries exist | `tests/test_hosting.py`, `test_hosting_image_activation_execution.py`, `test_hosting_image_activation_private_graph.py`, and topology tests patch remote, runtime, compose, Caddy, health, and edge collaborators or use fakes. No default PR/push workflow runs this lifecycle. |
| Prove remote/production deployment | supported remote job submission → durable job output → exact SHA/image identity → runtime and public-route proof | `commands/hosting.py` and remote job helpers encode parts of the flow; plan/spec documents tiered acceptance | Local unit evidence, staging health, and an image plan do not establish deployment. `docs/release-readiness.md` leaves protected Drive/Hermes/Lenzora gates unchecked; no observed terminal Sandbox job or public-route proof is in this review. |
| Reopen and recover | capture state → destroy/reopen or restore → verify data, ownership, secrets, and routing | `tests/integration/recovery_reopen_canary.py` describes explicit Docker + pinned PostgreSQL capture/restore; containment/secret/settlement canaries cover related controls | These canaries are outside default discovery and require explicit infrastructure. They are not represented by the ordinary selftest workflow. |
| Exercise MCP/CLI boundaries | fresh subprocess with controlled environment → protocol/registry dispatch → real command result | `tests/test_cli.py` runs a real `sb` subprocess for resolution; `tests/test_mcp.py` has fresh-MCP-venv subprocess checks when the venv is present; architecture checks enforce subprocess environment discipline | MCP cases are `skipUnless` gated on the venv. CLI resolution is not a live lifecycle. No normal gate proves MCP against a running WP instance or remote deployment. |
| Produce a usable desktop release | web typecheck/build → Electron tests → universal package → bundle/artifact manifest → codesign/launch/smoke | `.github/workflows/desktop-macos.yml` runs web build and desktop tests and uploads an unsigned universal package; `src/desktop/scripts/smoke-mac.mjs` checks bundle, codesign, and architecture when invoked | Workflow does not prove a signed/notarized release or full launch path. Desktop tests are mainly static source/packaging contracts. `src/web` has no dedicated test files. |
| Install from release scripts | archive committed HEAD → install on supported OS → dependencies/service/proxy → start → verify CLI and runtime | `scripts/make-release.sh` archives and performs bundle/owned-storage sanity checks; installer scripts are present; static installer tests exist | No routine end-to-end macOS/Linux/Arch/remote installer run was found. Static substring checks cannot prove permissions, service startup, upgrade, or clean URL behavior. |

## Repository and test population

Counts below are tracked-file inventories made with `rg --files`/`wc`; they are approximate LOC and exclude `package-lock`, test-result artifacts, and `vendor` from the family table. The repository is test-heavy, but most volume is Python unit tests and fixtures.

| Area | Files | Approx. LOC | Notes |
| --- | ---: | ---: | --- |
| `sandbox/` core | 441 | 158,906 | 429 Python / 157,639 LOC; includes production commands, compatibility layers, and tests under the tree |
| `mcp/wp-server/` | 34 | 6,584 | 32 Python / 6,477 LOC |
| `tools/` | 31 | 20,219 | 19 Python, 5 shell, 2 mjs; controllers and helper tooling |
| `src/desktop/` | 25 | 2,673 | Runtime, packaging scripts, and desktop tests |
| `src/web/` | 25 | 2,127 | 22 TypeScript / 1,964 LOC; no web test files found |
| `scripts/` | 9 | 1,260 | Release and installer shell/Python scripts |
| `config/` | 11 | 608 | Services and runtime configuration/assets |
| Tests overall | 613 | 137,914 | 481 top-level `test_*.py`, 3 acceptance files, 5 integration files, 8 `live_*.py` files after the review exclusions |

Tracked extension totals before exclusions are 1,011 Python, 276 PHP, 74 shell, 28 TypeScript, 19 JavaScript, and 11 mjs files. These totals include tests, generated/package-adjacent material, and vendor content, so they are context rather than executable production size.

## How tests enter normal gates

| Gate | What it actually runs | What it does not establish |
| --- | --- | --- |
| `.github/workflows/python.yml` | Ubuntu, Python 3.12, PyYAML, `./sb selftest` on PR/push to `latest` | Docker/WordPress startup, clean URL, installer, remote job, edge, signed artifact, or production deployment |
| `./sb selftest` | `commands/debug.py` runs unittest discovery with explicit environment and timeout handling | A live instance or external service; `tests/README.md` explicitly says passing source tests are not runtime proof |
| `.github/workflows/smoke.yml` | Manual `workflow_dispatch`; Docker prerequisites followed by `./sb smoke` | Any PR/push regression signal; it is opt-in and was not run here |
| `.github/workflows/desktop-macos.yml` | macOS 14 web `npm ci`/typecheck/build, desktop `npm audit`/tests, unsigned universal package artifact | Signed/notarized release, real supported-OS install, or production distribution |
| Acceptance tests | `tests/acceptance/test_remote_job_runtime.py`; WP portion requires `SANDBOX_RUN_WP_ACCEPTANCE` and project setup | Default selftest coverage; many paths skip when node/php/WP prerequisites are absent |
| Integration/live canaries | Explicit Docker, registered host, resolver, sudo, or supplied immutable-image setups, depending on file | Normal regression protection; headers in the canaries state they are outside default discovery or require explicit infrastructure |

`tests/README.md` separates source tests from runtime acceptance and labels smoke as a manual workflow. `docs/ci-e2e-runner-spec.md` says its current tests cover E2E configuration/runner contracts and that actual live Playwright execution is not verified; its known-gaps section calls out the external reusable workflow and composite action as not specifically live-verified.

## Representative unit/mock families

The following families are useful contract coverage, but their green result should not be read as lifecycle proof.

- `tests/test_hosting.py`: broad hosting contracts. Representative cases patch save, edge, Caddy, health, runtime, compose, remote, and Cloudflare collaborators.
- `tests/test_hosting_image_activation_execution.py`: execution graph behavior through `Mock`/`Fake` collaborators and patched subprocess behavior.
- `tests/test_hosting_image_activation_private_graph.py` and `tests/test_hosting_image_activation_lenzora_topology.py`: graph/topology checks using fake host/runtime/edge objects.
- `tests/test_remote.py`: the module header explicitly says it does not use Docker, real SSH, or a real VPS; it therefore cannot prove remote execution.
- `tests/test_generic_init.py`: `_FakeCore` and direct command patches verify dispatch and error contracts, not compose boot or WordPress readiness.
- `tests/test_e2e.py`: module header explicitly describes pure configuration discovery without Docker.
- `tests/test_install_macos_script.py` and `tests/test_owned_storage_packaging.py`: read scripts as text and assert expected strings or packaging fragments.
- `tests/test_hermes_dashboard_authorizations.py`: the web/dashboard check is a syntax/source check for the bundle, not a browser authorization flow.
- Desktop packaging-boundary and Electron-boundary tests use source/regex assertions. They catch accidental boundary changes but do not execute a signed application or a full user flow.

These tests are not redundant: they catch regressions cheaply. The gap is the missing bridge from their mocked collaborators to a real instance, host, proxy, remote job, artifact, and public route.

## Subprocess and environment boundaries

`tests/subprocess_support.py` provides the approved explicit synthetic environment and timeout helpers with `shell=False`; the architecture checker in `sandbox/testing_boundaries.py` detects parent-environment copying/iteration and captured-child leakage, and `tests/test_architecture_boundaries.py` asserts no violations plus adversarial fixtures. This is strong source-level safety coverage.

Static search also finds direct `os.environ` setup or mutation in test fixtures such as `tests/test_compose.py`, `tests/test_sandbox.py`, `tests/test_owned_storage_cli.py`, `tests/test_server_transport.py`, and `tests/test_php_extension_integration.py`. Those occurrences are test setup and are not by themselves a confirmed captured-child violation. The one candidate worth keeping under review is `tests/test_generic_compose.py` reading `PATH` for a patched runner; production child execution goes through the bounded runner. No violation is claimed without the architecture checker identifying one.

## Coverage gaps that explain “green but cannot create/deploy”

1. **Default CI stops at source contracts.** The PR/push Python workflow never starts Docker/WordPress, exercises Caddy/DNS, or submits a remote job.
2. **The local-create path is split across mocks and a manual smoke.** Unit tests can prove dispatch while `ensure_instance`/compose/readiness/clean URL/persistence can still fail when combined.
3. **Hosting activation is collaborator-shaped.** Patching runtime, compose, remote, edge, and health calls makes graph decisions testable, but leaves network/API credentials, exact artifact identity, controller refusal behavior, and public routing unobserved.
4. **E2E configuration is not E2E execution.** The E2E spec and tests cover configuration and safe-mode contracts; they do not run the external reusable/composite workflow against a live stack.
5. **Recovery evidence is opt-in.** The strongest reopen/capture/restore checks live in integration canaries with explicit infrastructure and do not enter ordinary discovery.
6. **Install and release checks are mostly static.** Archive sanity, substring checks, and unsigned package creation cannot catch a fresh-machine install, service lifecycle, upgrade, codesign/notarization, or proxy failure.
7. **Web/runtime/UI proof is thin.** `src/web` has no dedicated tests; dashboard bundle syntax and desktop package checks do not prove browser behavior, authorization, or WP admin flows.

The strongest missing acceptance bundle is one replay-safe, durable job that records: exact source SHA and image digest, fresh local/remote instance creation, WordPress and clean-URL response, a persisted marker across restart/reopen, activation/rollback outcome, and the final public-route/edge headers. The existing unit tests should remain as fast preflight gates; this bundle is the evidence needed for the lifecycle outcomes.

## Evidence anchors

These are the main source locations behind the inventory. Line numbers refer to revision `fd7d650f8bfbb1760d931e2484cc01fa0badf546`.

- `tests/README.md:18-20,29-37,66-69,81-83,97-107` separates source tests from runtime acceptance, states that green source tests do not prove WP start/clean URL/signed deploy, documents plugin/test modes, and describes manual smoke behavior.
- `.github/workflows/python.yml:1-35`, `.github/workflows/smoke.yml:1-26`, and `.github/workflows/desktop-macos.yml:1-44` define the default Python, manual smoke, and macOS gates.
- `README.md:30-57,61-125,202-245` documents runtime/test claims, bootstrap/install, generic Compose, and harness behavior.
- `docs/release-readiness.md:1-55` records that a green suite is not live proof and lists the still-required doctor, Herd, dashboard, full, protected deployment, and MCP wrapper gates.
- `docs/ci-e2e-runner-spec.md:88-92,94-145,461-477` describes configuration/unit coverage, safe-mode architecture, and known gaps in live Playwright/reusable/composite workflow verification.
- `specs/051-immutable-activation-recovery/plan.md:30-33,54,200,247-259` marks fake adapters/unit tests as planned coverage and leaves remote, edge, and production acceptance tiered/open.
- `sandbox/commands/instances_cmd.py:492-610`, `sandbox/commands/lifecycle.py:1660-1774`, `sandbox/commands/debug.py:193-531`, and `sandbox/commands/hosting.py:4092-4197,4891+` contain the local dispatch, smoke, selftest, image provision, activation, and rollback paths.
- `tests/test_hosting.py:3282-3300`, `tests/test_remote.py:1-10`, `tests/test_generic_init.py:1-10`, and `tests/test_e2e.py:1-7` show the representative mock/fake/no-Docker boundaries. `tests/test_install_macos_script.py:10-25` and `tests/test_owned_storage_packaging.py:38-57` show static script/package checks.
- `tests/acceptance/test_remote_job_runtime.py:25-45,103-108` and the headers of `tests/integration/recovery_reopen_canary.py` and the related canaries show explicit prerequisites and opt-in status.
- `tests/subprocess_support.py:1-37`, `sandbox/testing_boundaries.py:138-172`, and `tests/test_architecture_boundaries.py:82-121` define and enforce the explicit synthetic subprocess environment rule.
- `src/desktop/package.json:8-22`, `src/desktop/scripts/smoke-mac.mjs:13-62`, and `scripts/make-release.sh:1-101` anchor the desktop packaging/smoke and release archive claims.

## Commands and limits used for this inventory

- Read repository instructions first, then ran `./sb guide --project-dir .` and `./sb skill show sandbox-cli`.
- Read `specs/051-immutable-activation-recovery/plan.md`, `tests/README.md`, `README.md`, `docs/release-readiness.md`, and `docs/ci-e2e-runner-spec.md`.
- Inspected the current revision and relevant command/test/workflow files with `rg`, `sed`, and `wc`.
- No Docker, WordPress, browser, remote Sandbox job, deployment, release, or production probe was run.
- No source, test, workflow, or instruction file was changed. This inventory is the only requested output artifact.
