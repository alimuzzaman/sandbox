# Quickstart: Deep Disk Attribution

## Prerequisites

- Work on `latest` or a feature branch, never `main`.
- Use the repository `./sb` CLI; do not substitute raw Docker or SSH.
- A named remote must already be provisioned for remote validation.
- Deep status is read-only and does not require installing optional tools.

## 1. Focused automated verification

```sh
python3 -m unittest \
  tests.test_resource_attribution \
  tests.test_resource_adapters \
  tests.test_resource_remote \
  tests.test_resource_service \
  tests.test_resource_interfaces
```

Verify deterministic fixtures for:

- directory ranking and hard-link deduplication;
- preferred-scanner and standard-fallback parsing;
- deleted-open aggregation and redaction;
- structured container unique/shared accounting;
- mount selection, nested boundaries, timeout, permission, delivered partial
  payload, total transport loss, safe `capacity_scope_id` reconciliation, and
  pre-cancelled CLI/MCP seams;
- non-negative reconciliation and overlap exclusion;
- CLI/MCP parity and zero mutation.

## 2. Existing behavior regression

```sh
./sb resources status --json
./sb resources status --thorough --budget 60 --json
./sb resources plan --scope cache --thorough --budget 60 --json
./sb resources plan --scope stale --thorough --budget 90 --json
```

The existing response and cleanup surfaces remain compatible.

## 3. Live local deep status

```sh
./sb resources status --deep --budget 60 --json
```

Expected:

- the command performs no mutation;
- every discovered filesystem has a coverage status; only safe root,
  Sandbox-home, Docker-data, and typed managed-root scopes are selected;
- mount/source and managed-root paths are not disclosed; opaque mount and
  capacity-scope identities demonstrate topology without locators;
- the response names the selected directory, deleted-open, mount, and container
  capabilities;
- Docker unique/shared/activity/reclaimable values remain logical diagnostics
  and do not increase accounted bytes;
- residual unexplained bytes are non-negative;
- incomplete, cancelled, excluded, unavailable, and timed-out categories are
  explicit; completed or parseable partial evidence remains present.

## 4. Live named-remote deep status

```sh
./sb resources status \
  --remote scaleway-sandbox \
  --deep \
  --budget 600 \
  --json
```

Expected:

- root, Sandbox-home, Docker-data, and typed managed-root filesystems are
  selected when distinct and each capacity scope is counted once;
- installed `gdu` is used when present, otherwise the standard fallback is
  reported (large inode-dense fallback scans may need the larger budget);
- deleted-open allocated blocks are mapped to selected filesystems or the
  precise availability/privilege/metadata limit is reported;
- detailed Docker unique/shared/activity/reclaimable values remain logical
  diagnostics;
- the previous capacity gap is reduced by new readable evidence or every
  remaining boundary is named.

## 5. Human output and runtime smoke

```sh
./sb resources status --deep --budget 60
./sb resources status --deep --cancelled --json
./sb status
```

The human report must expose the same target, capacity, both capacity and
attributed drift, reconciliation, safe topology, coverage, limitations, and
largest findings as JSON. The cancellation command is a non-mutating test seam;
the MCP equivalent is `resource_status(deep=true, cancelled=true)`. `./sb
status` must continue to report the current instance normally.

That MCP boolean is only a deterministic pre-cancellation seam. Do not treat it
as proof that client cancellation or transport loss reaches an in-flight tool;
T040 remains open until a reviewed MCP request-lifecycle authority is wired.

For an in-flight CLI request, `SIGINT` sets the request-owned signal instead of
discarding already completed evidence. A remote transport that loses delivery
uses `disconnected`; a delivered valid partial payload remains usable, while
total transport loss stays explicit and unavailable for capacity decisions.

## Done gate

- Focused resource tests pass.
- Full repository tests pass.
- Local and named-remote read-only deep status are contract-bounded to budget
  plus five seconds; record actual timing separately before claiming live proof.
- Deterministic cancellation coverage does not replace the named-remote live
  acceptance run required by T045.
- Existing status/plan/cleanup behavior remains compatible.
- No packages, files, processes, mounts, privileges, or cleanup state changed.
