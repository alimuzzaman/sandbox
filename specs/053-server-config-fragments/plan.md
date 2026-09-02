# Implementation Plan: Instance-Scoped Server Configuration Fragments

**Branch**: `codex/server-config-fragments` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/053-server-config-fragments/spec.md`

**Planning checkpoint**: refreshed against `origin/latest` at `c6c06e5`, which contains
the accepted source for Features 048-051. The persisted active feature pointer remains
`specs/051-immutable-activation-recovery` by owner request; Feature 053 analysis uses the
read-only `--feature-dir specs/053-server-config-fragments` selector and does not rewrite
`.specify/feature.json` or the managed AGENTS/CLAUDE pointers. Accepted source is not
live, deployment, production, or human-security proof; the open Feature 051 gates remain
open.

## Summary

Add a command-owned `sb server` surface that preserves the existing server-switch
forms and adds `config apply|list|show|revert`. A new `sandbox.server_config`
application service will accept one bounded data source, enforce the deny-by-default
`wordpress-cache-v1` authority, construct the complete ordered fragment set, validate
that set with an adapter bound to the target's exact running image, and activate only
an instance-specific generation. A durable per-instance journal and known-good
generation make replace, revert, interruption recovery, and one bounded rollback
deterministic. Read-only inspection bypasses legacy pre-dispatch writers.

nginx receives an instance-specific included generation while retaining Sandbox's
existing base vhost. OpenLiteSpeed receives an adapter-rendered, instance-specific
vhost generation; its candidate is booted and behavior-probed with the exact active
image in a network-none, data-free validation container before the live vhost can
change. Apache and Herd remain explicitly unsupported in v1. Docker/Caddy clean URLs,
TLS, DNS, host ingress, and other instances are outside the fragment authority.

## Technical Context

**Language/Version**: Python 3.9+ compatible standard-library CLI and service code;
native nginx and OpenLiteSpeed configuration data rendered by server adapters

**Primary Dependencies**: `sandbox.registry.CommandSpec` and the built-in command
manifest; existing canonical instance resolver and WordPress Compose lifecycle;
`sandbox.core` Compose/runtime observation helpers; explicit server-adapter manifest;
the existing project/instance lifecycle mutation lock exposed through one typed
fragment-lifecycle guard;
POSIX `openat`/`O_NOFOLLOW`, `flock`, `fsync`, and atomic rename; active nginx and
`litespeedtech/openlitespeed` images; existing redaction and structured-result rules.
The accepted Feature 048 recovery and Feature 049-051 OCI packages are compatibility
boundaries only, not dependencies or authority sources for local server fragments

**Storage**: owner-only durable state beneath
`$SANDBOX_HOME/runtime/server-config/<instance-incarnation-id>/`, containing a lock,
fragment bytes, immutable rendered generations, an active-state receipt, and at most
one in-progress transaction. Compose mounts only that exact instance root read-only;
no fragment lives in the repository or host-global server configuration

**Testing**: Python `unittest`; pure model/policy/parser tests; repository race,
permission, corruption, and crash-phase tests; fake-runner nginx/OpenLiteSpeed adapter
tests; command/JSON/redaction/module-boundary tests using
`tests.subprocess_support.synthetic_environment`; lifecycle/server-switch/deletion and
Compose isolation regressions; separately authorized live two-instance nginx and
OpenLiteSpeed acceptance using only supported `sb`/Sandbox operations

**Target Platform**: local Docker Compose WordPress instances on macOS or Linux;
minimum active servers `nginx` and `litespeed` (OpenLiteSpeed). Remote-host service
configuration, native Herd/Valet, host nginx/OLS, and MCP parity are out of v1

**Project Type**: modular Python CLI/application service with explicit policy and
runtime-adapter registries; no new general command runner or file-editing API

**Performance Goals**: healthy `list` and metadata `show` finish within 5 seconds with
zero writes; each validation, activation/readiness, and rollback phase is at most 60
seconds; a complete mutation reaches a truthful terminal state within 180 seconds;
identical reapply and healthy missing-name revert perform zero validation boots and
zero reloads

**Constraints**: one immutable instance incarnation and active server per operation;
262,144-byte input maximum; strict normalized names; raw bytes never enter routine
output, JSON, logs, exceptions, argv, or environment; no shell text accepted; no raw
Docker/SSH user journey; exact-image validation is isolated from live networks, data,
secrets, and mutable configuration; one writer per instance; no host-global ingress or
cross-instance mount; stopped/unknown is never ready

**Scale/Scope**: one target and one named fragment per mutation; complete candidate
set ordered by normalized name; bounded local generation history needed for current
known-good plus one transaction; two required adapters; one control instance retained
through every live mutation

## Constitution Check

*GATE: PASS before Phase 0 research. Re-checked after Phase 1 design: PASS.*

| Principle or boundary | Assessment |
|---|---|
| I. Per-project is the only instance model | PASS. Normal instance resolution selects exactly one registered project-owned instance. No global/default instance or display-name adoption is introduced. |
| II. Registry is the source of truth | PASS. The composition root consumes the existing resolver and adds an opaque incarnation ID to the authoritative instance record. Fragment code never reads registry JSON directly. |
| III. Single entry file, modular package | PASS. `sb` remains unchanged. The feature owns `sandbox.commands.server`, `sandbox.server_config`, and explicit command/adapter manifests. The legacy switch handler moves behind the new command registration instead of adding another parser. |
| IV. Live-stack verification | PASS BY PLAN, NOT YET PROVEN. Unit/contract checks cannot close the feature. Release requires live nginx and exact-image OpenLiteSpeed behavior, rollback, and two-instance isolation evidence from disposable instances. |
| V. Idempotency and docs-with-code | PASS. Content and set digests, same-name replacement, known-good generations, a phase journal, exact replay/reconciliation, and one recovery activation define re-runs. CLI/reference/skill docs land with code. |
| VI. Feature parity before removal | PASS. Existing `sb server <type>` and `sb server <instance> <type>` behavior remain through command-owned parsing. Apache, Herd, Caddy, domains, TLS, and current lifecycle paths are not removed or stubbed. |
| Clean-URL default | PASS. Fragment authority cannot address Caddy, TLS, DNS, autologin, health, or protected routes. `_ensure_url_proxy` and `tools/proxy-helper.sh` are untouched. |
| Module and manifest boundaries | PASS. A `CommandSpec` owns parsing and pre-dispatch policy; an adapter manifest owns server policy. No new consumer of `sandbox_core.py`, `sandbox.registry.COMMANDS`, Hermes facades, MCP app helpers, or raw registry/state JSON is added. |
| Test subprocess environment | PASS. Every captured subprocess test uses `run_test_process` or an explicit synthetic environment. Parent `os.environ` is never copied, enumerated, or forwarded. |
| Secrets and consequential change | PASS BY DESIGN. The authority rejects credential-like names and secret transport; content-free evidence crosses routine output. This privileged configuration path requires human security review and live acceptance before release. |
| Features 048-051 authority separation | PASS BY PLAN. Local fragment code does not import host recovery, OCI trust, credential staging/proof custody, immutable activation, or remote activation transports. Its exact-image observation is a local runtime precondition only. |
| Dev-tool packaging | PASS. Spec Kit artifacts and the managed AGENTS pointer remain outside shipped product artifacts. |

Post-design re-check: the state, policy, adapter, CLI, and lifecycle contracts preserve
all gates. No constitution exception is required. Planning does not claim runtime proof,
deployment, release readiness, or Apache support.

## Project Structure

### Documentation (this feature)

```text
specs/053-server-config-fragments/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   ├── authority-policy.md
│   ├── state-transaction.md
│   └── server-adapters.md
└── checklists/
    └── requirements.md
```

`tasks.md` is created later by `speckit-tasks`; it is not a planning artifact.

### Source Code (repository root)

```text
sandbox/
├── application/
│   └── context.py                       # compose resolved instance and service dependencies
├── commands/
│   ├── manifest.py                      # register the feature-owned server command module
│   ├── net.py                           # relinquish legacy server registration only
│   └── server.py                        # owned parser, legacy switch compatibility, rendering
├── core/
│   ├── _docker.py                       # instance-specific read-only adapter mount and image/runtime facts
│   ├── _instances.py                    # incarnation identity creation/preservation and lifecycle gates
│   └── _provision.py                    # ready/start reconciliation hook; no direct state parsing
└── server_config/
    ├── __init__.py                      # typed public exports
    ├── context.py                       # dependency composition; no policy duplication
    ├── input.py                         # stable bounded regular-file/stdin reads and safe export
    ├── models.py                        # fragments, sets, transactions, observations, results
    ├── policy.py                        # common wordpress-cache-v1 authority and name/content bounds
    ├── repository.py                    # owner-only generations, journal, lock, read-only observation
    ├── lifecycle.py                     # lock-ordered switch/delete/reconcile guard
    ├── service.py                       # apply/list/show/revert/reconcile orchestration
    └── adapters/
        ├── base.py                      # adapter protocol and typed evidence
        ├── manifest.py                  # explicit nginx/litespeed adapter catalog
        ├── nginx.py                     # subset parser, candidate renderer, exact-image validate/reload
        └── openlitespeed.py             # subset parser, vhost renderer, isolated boot/probe/restart

docs/
├── sandbox-config-reference.md          # public commands, states, bounds, recovery, server support
└── clean-url-default.md                 # unchanged contract; only link/reference if clarification is needed

skills/sandbox-cli/
└── SKILL.md                             # CLI-first safe fragment workflow and refusal guidance

tests/                                      # representative; tasks.md is exhaustive
├── test_server_config_models.py
├── test_server_config_policy.py
├── test_server_config_repository.py
├── test_server_config_service.py
├── test_server_config_nginx.py
├── test_server_config_nginx_runtime.py
├── test_server_config_openlitespeed.py
├── test_server_config_openlitespeed_runtime.py
├── test_server_config_cli.py
├── test_server_config_recovery.py
├── test_server_config_inspection.py
├── test_server_config_lifecycle.py
├── test_server_config_isolation.py
├── test_architecture_boundaries.py
├── test_cli.py
├── test_modularity.py
├── test_lifecycle.py
└── subprocess_support.py
```

The feature directory also owns `implementation-evidence.md`, a bounded,
content-free record of RED/GREEN, compatibility, live acceptance, and review gates.
The exact test-module inventory and ownership live in `tasks.md`; the tree above is
representative and must not be used to infer that an omitted focused suite is optional.

**Structure Decision**: keep the CLI thin and place the security-sensitive state
machine in a dedicated feature package. Common policy decides authority before an
adapter is called; adapters own native grammar, exact-image validation, generation
rendering, target-only activation, and readiness observation. Repository code owns bytes
and durable transitions but cannot execute a server. Service code composes them and is
the only mutation coordinator. Core lifecycle receives typed projections/hooks instead
of parsing feature JSON.

## Research

See [research.md](research.md). All technical choices and integration unknowns are
resolved; no product `NEEDS CLARIFICATION` remains. Current-image OpenLiteSpeed support is
still a bounded implementation feasibility gate: Phase 1 must prove the planned stable
vhost inclusion, isolated boot/canary, and reload path on a disposable instance after
explicit authorization. Failure requires plan/design revision, not a fallback to
`.htaccess`, host-global config, raw runtime edits, or assumed image behavior.

## Design Artifacts

- [data-model.md](data-model.md)
- [contracts/cli.md](contracts/cli.md)
- [contracts/authority-policy.md](contracts/authority-policy.md)
- [contracts/state-transaction.md](contracts/state-transaction.md)
- [contracts/server-adapters.md](contracts/server-adapters.md)
- [quickstart.md](quickstart.md)

## Composition and Lifecycle Boundaries

- The `server` command becomes feature-owned. Its parser recognizes `config` first;
  every other valid token shape is delegated to the preserved server-switch operation.
  The current optional-name parser accepts both `sb server <type>` and
  `sb server <instance> <type>`; both remain compatibility behavior even though the
  current README documents only the named form. The refresh must correct supporting
  research/docs and add parser tests rather than infer syntax from documentation alone.
  `list` and default `show` declare a pre-dispatch skip so auto-migration, Compose
  regeneration, and legacy environment writes cannot violate read-only behavior.
- The authoritative instance record gains a random opaque incarnation ID when a new
  instance is created. Apply/reconcile preserves it; confirmed deletion disassociates
  its fragment root. A reused display name receives a new ID and cannot adopt old state.
  Incarnation minting, legacy-record adoption rules, typed projection, and rollback-safe
  preservation are foundational work completed before any adapter mount or candidate.
- Compose mounts only the selected incarnation's adapter root. nginx keeps its checked-in
  base vhost and an absent-safe fixed guest include glob; each nginx container mounts only
  its own incarnation directory at that fixed guest path, so a legacy container without
  the mount still boots but cannot apply fragments. OpenLiteSpeed may use a complete
  adapter-rendered instance vhost rooted in the mounted generation only after the Phase 1
  exact-image capability probe proves a stable instance-local inclusion point, isolated
  candidate boot/canary, and fixed target-only reload path. Probe failure stops the feature
  for design revision before production source work. Caddy never consumes either mount.
- Existing instances without the mount fail `config apply` before state mutation with an
  actionable supported `sb apply --instance NAME` reconciliation. The fragment operation
  never silently recreates a web tier merely to attach authority.
- `up`, `ensure`, apply/reconcile, relocation, and restart prove the current generation,
  server type, runtime image, mount identity, and readiness before reporting fragment
  state healthy. They never pick a generation by timestamp. Read-only inspection reports
  drift without repairing it.
- Server switching and deletion run through one lifecycle mutation owner. It acquires the
  existing project/instance lifecycle lock, then the fragment lock, re-reads both states,
  and holds both across the gate, YAML/state write, runtime action, fragment commit or
  disassociation, and rollback/terminal receipt. Any active, unresolved, degraded, or
  recovery-needed fragment state refuses before the first lifecycle write. Confirmed
  deletion must include exact fragment-state removal; ordinary stop/start keeps the same
  incarnation and known-good state. No preflight-only gate may release locks before effect.
- Exact fragment content never enters the registry or instance block. Those stores retain
  only opaque incarnation/mount identity. Fragment bytes remain owner-only in the feature
  repository and are exposed only by explicit content output.

## Current `latest` and Feature 048-051 Integration Boundaries

- `origin/latest` already contains Feature 048 observation-only host recovery, Feature
  049 pure OCI trust verification, Feature 050 credential-brokered staging/proof custody,
  and Feature 051 immutable activation/adoption/rollback/recovery. Feature 053 starts from
  that integrated source; it does not recreate, replace, or backport any former Feature
  047 path.
- Feature 048 owns host-scoped observation/recovery under `sandbox.hosting.recovery`.
  Features 049-051 own host-scoped image trust, staging/custody, activation state, the
  shared outer hosting writer/target mutation port, and remote activation transport.
  Feature 053 remains local instance-incarnation state under `sandbox.server_config`.
  It imports none of those packages and shares no repository, lock, transaction, receipt,
  proof, generation, recovery meaning, credential path, or mutation ownership.
- Feature 053's "exact image" means an independently observed content-addressed image for
  the selected local running web service, used only to validate the candidate and recheck
  an activation precondition. It is not a Feature 049 trust plan, Feature 050 staged proof,
  Feature 051 activation grant, registry credential, pull/build authority, remote-host
  identity, or deployment receipt.
- CLI work preserves the accepted `CommandSpec.predispatch_policy` composition and every
  existing `host image` parser/handler. The `server` migration removes only its own legacy
  parser/registration bridge and adds one narrow read-only predicate; it does not edit
  `sandbox.commands.hosting`, `sandbox.core._hosting`, `sandbox.hosting`, or remote image
  transports.
- Re-run Feature 048 recovery suites, Feature 049 trust/contracts/boundary suites, Feature
  050 staging/process/repository/secret/service suites, Feature 051 activation suites,
  command-manifest/CLI/modularity/architecture checks, and Feature 053 focused suites after
  integration. A clean textual merge is not evidence that command ownership, pre-dispatch
  ordering, sole-writer rules, proof custody, or activation/recovery separation survived.
- Feature 051 T060 human security review and live registered-host/edge/rollback/deployment/
  production validation remain independent open gates. Feature 053 planning, source tests,
  or local disposable-instance acceptance cannot close or inherit those gates.

## Verification Strategy

1. Pure and fake-runner tests prove name/input bounds, deny-by-default policy, exact set
   identity, no-op/replace semantics, phase deadlines, lock conflicts, interrupted-phase
   recovery, corrupt/drifted read-only inspection, one rollback attempt, and content-free
   envelopes.
2. Adapter tests prove argv-only exact-image selection, network-none/data-free
   OpenLiteSpeed validation, nginx complete-candidate inclusion, runtime identity recheck,
   target-only reload/restart, and unknown readiness refusal.
3. CLI and lifecycle tests prove command-owned registration, all legacy server-switch
   forms, read-only pre-dispatch skip, server-switch/deletion gates, instance-name reuse,
   safe content output, synthetic subprocess environments, and unchanged Feature 048-051
   host/OCI command and authority boundaries.
4. Live acceptance uses disposable target and control instances. It records both
   identities/readiness/markers before and after every operation; completes nginx static
   hit then PHP fallback; completes the required OpenLiteSpeed origin/warm/hit/purge/
   miss/rewarm/hit/revert sequence; proves invalid input causes no reload; and proves a
   controlled post-validation activation/readiness failure restores the exact prior set
   or truthfully enters recovery-needed. Every operation uses `sb` or a Sandbox tool,
   never raw Docker, SSH, or direct runtime edits.
5. Live evidence records exact Git SHA, installed Sandbox revision, server image IDs,
   fragment-set digests, target/control observations, phase results, and terminal state.
   No content bytes, caller paths, secrets, raw container metadata, or unredacted logs
   enter the evidence bundle. This local acceptance does not exercise a registry, staged
   image, remote host, Feature 051 activation, edge rollout, deployment, or production.

## Complexity Tracking

No constitution violations require justification.
