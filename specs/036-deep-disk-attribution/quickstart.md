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
  payload, and total transport loss;
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
- every discovered writable local filesystem has a coverage status;
- the response names the selected directory, deleted-open, mount, and container
  capabilities;
- overlapping container values do not increase accounted bytes;
- residual unexplained bytes are non-negative;
- incomplete categories are explicit.

## 4. Live named-remote deep status

```sh
./sb resources status \
  --remote scaleway-sandbox \
  --deep \
  --budget 600 \
  --json
```

Expected:

- root, Sandbox-home, and container-data filesystems are selected when distinct;
- installed `gdu` is used when present, otherwise the standard fallback is
  reported (large inode-dense fallback scans may need the larger budget);
- deleted-open bytes are measured or the precise availability/privilege limit
  is reported;
- detailed Docker values remain logical diagnostics;
- the previous capacity gap is reduced by new readable evidence or every
  remaining boundary is named.

## 5. Human output and runtime smoke

```sh
./sb resources status --deep --budget 60
./sb status
```

The human report must expose the same target, capacity, reconciliation,
coverage, and largest findings as JSON. `./sb status` must continue to report
the current instance normally.

## Done gate

- Focused resource tests pass.
- Full repository tests pass.
- Local and named-remote read-only deep status return within budget plus five
  seconds.
- Existing status/plan/cleanup behavior remains compatible.
- No packages, files, processes, mounts, privileges, or cleanup state changed.
