# Quickstart: Validate Remote Host Swap and Memory Monitor Commands

This guide is the implementation/release validation contract. It does not authorize a
remote update, host mutation, deployment, or reboot. Use only synthetic local fixtures until
a human separately approves the disposable Linux target and the exact consequential run.

## 1. Prerequisites and boundaries

- Work on `codex/feature-046-specification` or its later implementation branch, never
  `main`.
- Use Python 3.9+ and the repository CLI environment.
- Keep the normal inherited environment out of fixtures and output. Tests construct narrow
  synthetic mappings only; never print or serialize `os.environ`.
- Do not use raw SSH, Docker, `swapon`, `swapoff`, `systemctl`, `sysctl`, or host files from
  the controller. All remote acceptance uses the supported `./sb resources swap-*` surface.
- The concrete acceptance registration name is `approved-swap-fixture`. A human must bind
  that name to a disposable or explicitly approved Linux host before section 6.
- Install/update the remote runtime only through the supported Sandbox lifecycle after
  separate approval, then independently verify matching service marker/revision. Do not
  work around skew.
- Reboot verification is a separate authorized run and is not part of ordinary acceptance.

## 2. Artifact and static gates

```sh
git diff --check
rg -n '\[[A-Z][A-Z0-9 _-]+\]' \
  specs/046-host-swap-monitor
```

Expected: clean diff and no template/clarification markers.

Check that implementation uses the owned resource package and fixed transport, and adds no
raw-host fallback or direct registry reader:

```sh
rg -n 'host_memory|swap-status|swap-plan|swap-apply|swap-history' \
  sandbox mcp/wp-server tests docs skills/sandbox-cli
rg -n 'ssh_run|subprocess.*ssh|runtime/registry\.json|os\.environ' \
  sandbox/resources/host_memory tests/test_host_memory_*.py
```

Expected: feature symbols exist; the second review has no controller SSH/direct registry
use and no inherited-environment fixture. Fixed host subprocess calls, if any, are confined
to the reviewed provider and accept no request-supplied argv/path.

## 3. Focused local contract suite

```sh
.cli-venv/bin/python -m unittest -v \
  tests.test_host_memory_models \
  tests.test_host_memory_policy \
  tests.test_host_memory_repository \
  tests.test_host_memory_provider \
  tests.test_host_memory_service \
  tests.test_host_memory_remote \
  tests.test_host_memory_interfaces \
  tests.test_resource_interfaces \
  tests.test_resource_remote \
  tests.test_remote_service_help
```

Required coverage:

- exact 1/8 GiB, 50% RAM, 10% filesystem, and free-reserve boundaries;
- disable strict-greater-than headroom boundary;
- unknown platform/facility/revision/ownership and unmanaged/multiple swap refusals;
- missing, mismatched, expired, drifted, replay-incompatible, and unconfirmed plans;
- interruption at every phase, same-intent replay, verified rollback, and incomplete block;
- sample five-second timeout, exact 660-second freshness boundary, clock regression, and
  three-consecutive-sample warning;
- current plus eight history files, 32 MiB rotation, 1,000-sample/1 MiB read bounds;
- strict human/JSON parity and zero process/argv/environment/path/secret-like fields;
- service marker/runtime/host-memory schema mismatch with zero SSH fallback;
- Feature 047 receives read-only projections only; Spec 043 records/locks/policy are not
  imported or changed.

## 4. CLI fixture flow (no real host mutation)

Using the fake authenticated control service, run:

```sh
./sb resources swap-status --remote approved-swap-fixture --json
./sb resources swap-plan --remote approved-swap-fixture --operation enable --json
./sb resources swap-plan --remote approved-swap-fixture --operation enable --size-gib 1 --json
./sb resources swap-plan --remote approved-swap-fixture --operation enable --size-gib 8 --json
./sb resources swap-apply --remote approved-swap-fixture --plan-id fixture-plan --json
./sb resources swap-history --remote approved-swap-fixture --limit 288 --json
```

Expected:

- status/plan/history are read-only;
- valid 1-8 GiB sizes propagate through requested/effective policy and plan identity, while
  invalid integer, RAM, filesystem, and reserve boundaries refuse before mutation;
- apply without `--confirm` is `confirmation_required` before provider construction;
- every result uses the common envelope and contains only bounded allowlisted fields.

Then run the confirmed apply against the fake provider with the exact plan ID returned by
the fixture plan. Replay it and require `already_current`. Inject transport loss after each
phase and require the same operation identity to reconcile; never issue a second identity.

Use this authenticated transport/provider harness for the complete refusal matrix:
unregistered target, unsupported platform/facility, service ownership or protocol/revision
mismatch, transport failure, invalid size/range, insufficient disk or RAM, unmanaged or
multiple swap, unsafe/foreign path, missing/mismatched/expired/drifted/replay-incompatible
plan, missing confirmation, concurrent operation, incomplete rollback, malformed/empty/
duplicate/late response, and unknown required evidence. Record these as synthetic acceptance,
not live-host proof. Expected outcomes use only the contract statuses; for example,
conflicting work is `refused` with `operation_in_progress`, and unknown delivery is `partial`
with `response_invalid`.

## 5. Adjacent regression gates

```sh
.cli-venv/bin/python -m unittest -v \
  tests.test_storage_monitor_policy \
  tests.test_storage_monitor_schedule \
  tests.test_storage_monitor_runner \
  tests.test_mcp_resource_tier \
  tests.test_resource_service \
  tests.test_workspace_contracts \
  tests.test_remote
```

Expected: Spec 043 disk monitoring/scheduling, cleanup, workspace, resource, and remote
service lifecycle behavior is unchanged. Run the repository's full supported Python gate
after focused suites pass and record any documented unrelated exclusions truthfully.

## 6. Separately authorized disposable Linux acceptance

Stop unless a human has approved `approved-swap-fixture`, its current runtime revision, and
this mutation matrix. Capture only sanitized aggregate evidence and opaque IDs.

Read-only start:

```sh
./sb remote service status approved-swap-fixture --json
./sb resources swap-status --remote approved-swap-fixture --json
./sb resources swap-plan --remote approved-swap-fixture --operation enable --json
```

Verify marker/revision match, no unmanaged swap, all eligibility numbers, zero mutation from
status/plan, and no raw paths/process/argv/environment values.

After separate confirmation, set `SWAP_PLAN_ID` to the exact `plan_id` copied from the
reviewed output, then apply it:

```sh
./sb resources swap-apply --remote approved-swap-fixture \
  --plan-id "$SWAP_PLAN_ID" --confirm --json
```

Required evidence: verified 4 GiB active and persistent swap, swappiness 15, active
five-minute timer, fresh sample, compliant retention, next sample, and owner-safe receipt.
Immediately replay the same command and require `already_current` with no duplicate state.

Read history:

```sh
./sb resources swap-history --remote approved-swap-fixture --limit 288 --json
```

Prove allowed aggregate fields, requested/observed range, freshness, completeness,
malformed/missing counts, and truncation. Run controlled synthetic host-provider fixtures
for sustained-use and partial samples; do not create real memory pressure on a shared host.

Plan disable and review the strict headroom calculation:

```sh
./sb resources swap-plan --remote approved-swap-fixture --operation disable --json
```

After separate confirmation, apply that exact disable plan. Verify only receipt-owned swap,
persistence, swappiness policy, monitor, retention, and receipt state are reconciled. Future
sampling must stop. Previously retained bounded aggregate history must remain readable under
the minimal disabled-state receipt, with no new samples and no unmanaged artifact change.

## 7. Failure and rollback acceptance

Use disposable provider fault injection, not arbitrary host commands, to cover every
protected phase. For each injected interruption:

1. Read status; require exact partial phase and the same operation ID.
2. Resubmit only the same plan/operation identity.
3. Require verified completion, `rollback_complete` with every prior element proven, or
   `rollback_incomplete` with unrelated mutation blocked.
4. Confirm status/history remain available during the block.
5. Confirm empty, malformed, duplicate, late, and contradictory output never renders as
   success or authorizes a new request identity.

## 8. Reboot evidence (separate authorization)

Without an approved reboot, status must say persistent configuration is present but reboot
verification is `unverified`. Only a separately approved reboot acceptance may record
`verified`; observe the exact post-reboot swap, swappiness, monitor/timer, fresh sample,
receipt, and revision state before making that claim.

## 9. Release gate

Release remains blocked until:

- focused and adjacent/full supported suites pass;
- independent human review covers privileged host paths, authentication, authorization,
  ownership, rollback, privacy, cryptographic identities, and dependency trust;
- the approved disposable Linux matrix proves status, refusals, enable, replay, history,
  warning behavior, partial reconciliation, disable, cleanup, privacy, and rollback;
- the fixed authenticated transport/provider matrix proves every refusal class that would be
  unsafe, impossible, or unauthorized to create on the approved live host, with its synthetic
  evidence kept distinct from live-Linux evidence;
- operator docs, command help, skill routing, runtime digest/revision evidence, and the
  installed remote revision match the accepted source.

Local planning or fake-provider results alone are not live proof or release readiness.
