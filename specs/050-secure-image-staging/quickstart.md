# Quickstart: Secure Private Image Staging

Local implementation validation uses only fake remotes/daemons and synthetic credential
canaries. It does not authorize a live secret, GHCR pull, host mutation, deployment, or production.

## RED-first acceptance

Implementation-order waiver (2026-09-01): the task owner explicitly required all
production code and documentation (T015-T037 authored work) before focused tests
(T001-T013). Therefore no implementation-free RED run was observed and no observed RED
is claimed. Expected RED causes before production would have been missing staging models,
repository/service/worker/helper modules, fixed transport, broker adapter, CLI registration,
and installer provisioning. T014 records this explicit waiver rather than inventing RED evidence.

The authored acceptance set covers plan/policy authority, fixed broker/helper,
all credential surfaces/cleanup paths, descendant ownership, exact pull, coherent local proof,
ledger replay/conflict/uncertainty, proof mutation, and zero activation reachability.

The process gate requires a transient systemd service on cgroup v2 and proves whole-unit
termination from unit state plus cgroup empty/removal. The proof gate covers unchanged
Feature 049 projection/topology, anonymous denial, authenticated pull, full-proof retention,
pinning, compaction, and `proof_expired` non-authority.
It also covers exact cross-store lock order, prepared proof lease/pin before validation,
holder/deadline behavior without auto-unpin, host-acceptance crash recovery, idempotent
promote/cancel/release ownership, compaction exclusion, 4096-tombstone saturation, bounded
ledger bytes, the strict tombstone-full new-unique-request refusal predicate, durable-holder deadline replay,
and `retention_full` before effects.
The helper workspace parent must be a root-owned 0700 directory on the `/run` tmpfs,
proved before READY. READY itself has a finite timeout and timeout kills the whole unit.
The retained unit is stopped and its exact cgroup checked before explicit collection.

## Focused checks

```sh
python3 -m unittest \
  tests.test_hosting_image_staging_policy \
  tests.test_hosting_image_staging_repository \
  tests.test_hosting_image_staging_service \
  tests.test_hosting_image_staging_secrets \
  tests.test_hosting_image_staging_process \
  tests.test_remote_hosting_images
python3 -m compileall -q sandbox/hosting/images sandbox/transports/remote_hosting_images.py
git diff --check
```

Observed final local evidence (2026-09-01):

- The combined selector for the six Feature 050 test modules above plus
  `tests.test_credential_resolver` ran 59 tests: `OK`.
- `tests.test_secret_service` plus `tests.test_job_service` ran 47 tests: `OK`.
- `tests.test_hosting` plus `tests.test_host_recovery_service` ran 189 tests: `OK`.
- `tests.test_command_composition` plus `tests.test_architecture_boundaries` ran 25 tests:
  `OK`.
- `compileall` passed for `sandbox/hosting/images`,
  `sandbox/transports/remote_hosting_images.py`, `sandbox/isolation/credential_resolver.py`,
  `sandbox/secrets/service.py`, and `sandbox/commands/hosting.py`.
- `git diff --check` passed.

Earlier focused iterations exposed boundary and test-fixture defects. Those defects were
repaired before the final combined result above; the final counts supersede the earlier
iterations.

All subprocess fixtures use `tests.subprocess_support.synthetic_environment`; no test
copies/enumerates the parent environment.

## Later authorized acceptance

Use a disposable non-production host, synthetic read-only test package, exact installed
revision/helper measurement, and no production domain/data. Prove cleanup and process trees
before any Feature 051 activation work.

## Implementation evidence and source provenance

| Implemented surface | Authoritative source | Adaptation / exclusion |
|---|---|---|
| Plan admission and projection | Current Feature 049 `sandbox/hosting/images/models.py` public `validate_verified_image_plan` and `DeliveryIdentityProjection` APIs | No trust reinterpretation or machine-policy import |
| Stage ledger durability | Current Feature 048 repository durability concepts | Independently implemented in a separate per-target ledger; component-wise no-follow creation, owner/mode/type validation and creation fsync; never reads/writes/parses `hosts.json` and does not reuse the Feature 048 target lock |
| Stage ownership/capacity | Feature 050 spec and data model | Durable per-target owner/phase/effect/process identity; 1-MiB proof plus envelope reserved before effects; exact 16-MiB serialization checked; uncertainty remains fenced |
| Credential delivery | Current `sandbox/isolation/credential_resolver.py` `BrokerLease.consume` callback and registered source revision APIs | One atomic source snapshot derives both opaque revision and one-use lease bytes before helper launch; consume atomically detaches or loses to invalidation, never reopens/falls back, and wipes detached material after every callback path; generic leases remain separate |
| Remote execution | Current registered `sandbox.core._remote` lookup/SSH argument seams | Root-owned regular artifact/manifest and directory chain are opened no-follow; the verified helper descriptor is hashed then executed through `/proc/self/fd`; finite READY timeout and retained-unit verification; no generic transport or activation path |
| Process ownership | Feature 050 specification and current systemd/cgroup-v2 platform contract | Exact transient unit, `KillMode=control-group`, no delegation/escape, inactive plus empty/removed cgroup; no PID/process-group authority |
| Local image evidence | Docker immutable image ID/config digest, RepoDigests, platform fields, and digest-bound `org.sandbox.application-topology.v1` config label | Machine and daemon start/end epochs, registry observation, topology, local observation, and final proof digests are recomputed at model boundaries |
| Proof custody | Feature 050 proof lease contract | Reachable only under target mutation -> atomic host state -> stage ledger lock; transaction-authenticated evidence drives promote/cancel/release; retained proof generation and commit ledger revision remain stable across later stages |
| Older Feature 047 worktree | Read-only concept comparison only | No cherry-pick, merge, wholesale copy, old schema, Compose/runtime/PID/process-group/inherited-env/Docker-config design was reused |

Source and documentation were authored locally. The local focused, regression, architecture,
compile, and whitespace checks recorded above passed. The complete focused matrices for
T007-T013 drive
the public repository against durable replay/capacity/custody state, the real helper workspace
and revision-bound lease lifecycle, injected transport/process streams and cgroup evidence,
real proof compaction and downstream replay, and real service success/refusal/reconcile flows
with poisoned Compose/init/runtime/edge/adoption/rollback/prune/activation capabilities.
Legacy 047/048 evidence remains unchanged and non-authorizing.
Two deterministic authored concurrency cases schedule each revision-bound lease lock winner:
invalidate-before-consume refuses without a source read, while consume-before-invalidate
delivers only the detached snapshot. They are included in the passing combined selector.

The user-required independent Sol High source/security review was automated, not human. After
the repair rounds its final verdict reported no critical, high, or medium findings. Live secret
use, live GHCR access, live remote mutation, deployment, and production evidence were explicitly
unattempted and remain separately authorized gates.
