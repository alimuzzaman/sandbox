# Quickstart: Agent-Aware Remote Development Sync

This guide is the acceptance gate for feature 033. It uses only a disposable
remote development workspace and finite commands. Do not use a permanent or
production instance, and do not substitute raw Docker or SSH for Sandbox
transport evidence.

## Prerequisites

- Checkout on the active non-`main` branch with the focused test dependencies.
- A registered, provisioned remote and a disposable workspace selected by
  explicit name/ID.
- `SANDBOX_HOME` pointing at the current machine state directory.
- A disposable fixture project with a small source tree, a tracked test file, an
  untracked non-secret file, an ignored file, and a credential-like negative
  fixture that is never transferred.

## 1. Static and contract checks

```bash
python3 -m unittest -q \
  tests.test_sync_manifest \
  tests.test_sync_state \
  tests.test_sync_transport \
  tests.test_sync_cli \
  tests.test_sync_mcp
git diff --check
```

Expected: all focused tests pass; envelopes contain only bounded redacted fields.

## 2. One-time acceptance

Use the feature-owned command with an explicit remote and disposable workspace:

```bash
SANDBOX_HOME=/Users/alim/sandbox ./sb sync once \
  --project-dir /absolute/path/to/fixture \
  --remote scaleway-sandbox --workspace-id <disposable-workspace-id> \
  --request-id <replay-safe-request-id> --json
```

Expected: one accepted generation ID, a matching remote workspace status, and no
service recreation. Repeat the same request ID and verify no second generation.

## 3. Credential and stable-capture negatives

Run the same bounded request with a tracked credential-like fixture and with a
file changed during capture. Expected outcomes are respectively
`credential_detected` before remote mutation and either one coherent accepted
generation or `unstable_capture` without a mixed accepted tree.

## 4. Live/checkpoint/off behavior

```bash
SANDBOX_HOME=/Users/alim/sandbox ./sb sync start --mode live --project-dir /absolute/path/to/fixture --remote scaleway-sandbox --workspace-id <disposable-workspace-id> --json
SANDBOX_HOME=/Users/alim/sandbox ./sb sync status --project-dir /absolute/path/to/fixture --remote scaleway-sandbox --workspace-id <disposable-workspace-id> --json
SANDBOX_HOME=/Users/alim/sandbox ./sb sync stop --project-dir /absolute/path/to/fixture --remote scaleway-sandbox --workspace-id <disposable-workspace-id> --json
```

Edit a supported file and create a local commit while live mode is active.
Under the healthy profile, trigger-to-acceptance is at most 10 seconds. Stop
prevents new transfers and leaves pending state visible. In checkpoint and off
mode, edits and commits do not transfer automatically.

## 5. Job-generation and write-isolation acceptance

Start a durable remote job through the existing job command against generation A,
create pending generation B, then request a second job. Expected: the second job
waits for B; parallel-safe jobs share only an accepted generation. A synchronized
job cannot write managed source. An explicitly isolated job copy can write only
its artifact/output boundary and cannot alter A or a parallel-safe peer.

## 6. Recovery, divergence, parity, and cleanup

- Interrupt a transfer or discard its client acknowledgment; rerun status with
  the same request identity and verify one accepted generation.
- Introduce an out-of-band managed-source edit and verify `divergence` with no
  automatic overwrite or adoption.
- Compare CLI and MCP status for the same relationship; all contract fields must
  agree and protected values must be absent.
- Stop/release the disposable workspace through the existing confirmation-gated
  lifecycle command and retain bounded job/status evidence.

Record the remote name, workspace ID, generation IDs, request IDs, command
results, timings, and cleanup result. Do not record credentials, source content,
raw paths, or process arguments.

## 2026-08-29 US1 remote acceptance evidence (blocked)

This attempt used `scaleway-sandbox` only after its active, authenticated,
owned service reported matching local and installed runtime revision
`483914586a6e3d5ce3d9a278`. The focused static gate passed 27 tests and
`git diff --check`.

- The supported lifecycle created disposable workspace
  `ws_31b212f517b741c0bf22fe8266e0d496` with complete workspace index
  generation 101 and a two-hour lease.
- Request `spec033-t026-once-20260829-a` created pending generation
  `gen_bf178536621342867087737d410d5e0ca7ed931101c0529b61a1b7ef372951e4`.
  Its first bounded transfer returned `remote_unavailable`. Replaying the exact
  request identity returned `transport_unknown`, `retryable:false`, for the
  same generation. No accepted generation was reported, so T026 remains open.
- Request `spec033-t026-credential-negative-20260829-a` returned
  `credential_detected` before remote mutation. No protected value or fixture
  content was retained in this evidence.
- The workspace lease was released successfully at
  `2026-08-29T11:40:03.100493Z`. Feedback record
  `282cd7f7afba66f30a1c6ddd3bfe5cd3` retains the sanitized transport gap.
- A no-production fixture for T026b validated locally, but read-only host status
  reported no state record, no deployed revision, and no configured or running
  service. Request `spec033-t026b-host-sync-20260829-a` therefore returned
  `remote_unavailable` with pending generation
  `gen_73afc1fac49afa60836dc877f9d54781d8ed91e6f53cfcbe7e5dd1b9bb867f4b`.
  `host apply` was not run because the documented creation path also changes
  public routing/DNS, which was outside this acceptance lane. No edit,
  no-restart, or committed-revision restoration proof exists; T026b remains
  open.
