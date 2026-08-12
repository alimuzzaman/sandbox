# Remote Docker network-pool exhaustion — incident and feedback log

Status: investigating; no cleanup or deployment authorized

This file is the durable evidence ledger for the 2026-08-12 report that a remote
Sandbox test could not provision its declared harness because Docker reported
predefined network subnet-pool exhaustion. Append dated observations; do not replace
contradictory or failed evidence, and never include secrets, raw credentials, or
unnecessary host paths.

## Report received — 2026-08-12T08:07:35Z

Source: agent feedback supplied by the repository owner; not yet independently
verified at the time it was recorded.

- Target VPS: `vmi3430003` through the registered remote.
- Reported remote checkout: `latest`, 31 commits behind `origin/latest`.
- Attempted suite: declared `fast` suite.
- Reported boundary: harness provisioning failed before `pnpm test:fast` began.
- Reported host inventory: 33 Docker networks, including stale-looking Sandbox
  workspace networks.
- Reported Docker error class: predefined network subnet pools exhausted.
- No networks were removed and no cleanup was authorized.
- No decision was made to test the remote checkout as-is or deploy the current local
  revision.

## Safety boundary

- Diagnose through `sb resources`, durable job status/output, and registered remote
  operations; do not substitute raw Docker, SSH, or broad prune commands.
- Planning is read-only. Cleanup requires a current, target-bound reviewed plan and
  explicit confirmation from the user.
- Do not deploy or update the remote checkout merely to diagnose host network
  capacity; revision drift and network-pool exhaustion are independent facts.

## Investigation log

- 2026-08-12T08:07:35Z — Incident ledger created before live inspection. Existing
  `docs/lenzora-remote-test-job-issues-2026-07-19.md` already records earlier remote
  Docker address-pool exhaustion and calls for scoped Sandbox-owned cleanup guidance.
- 2026-08-12T08:09:40Z — Read-only `sb resources status --remote
  scaleway-sandbox --thorough --budget 60 --json` did not return a resource
  envelope. The SSH transport exceeded its 64-second outer timeout and leaked a local
  Python `subprocess.TimeoutExpired` traceback. No remote cleanup or deployment was
  attempted. This is a separate observability defect: a timed-out remote inventory
  currently prevents the intended safe diagnosis and is not normalized to structured
  partial/unavailable evidence at this call boundary.
- 2026-08-12T08:09:42Z — Fast read-only inventory completed. Sandbox observed 29
  managed user-defined networks; all 29 were classified `active` because each had at
  least one connected container. Fourteen were named as Sandbox workspace projects.
  Together with Docker's predefined networks, the live count corroborates subnet-pool
  pressure. Available disk remained about 68.2 GB, so this is address allocation, not
  disk exhaustion. The scan was otherwise partial/low-confidence because fast mode did
  not measure host/Docker filesystem roots.
- 2026-08-12T08:11:52Z — The registered remote diagnostics endpoint returned
  `remote_service_failed` / unreachable. `sb workspace list --remote
  scaleway-sandbox` also refused because its resolved remote project directory no
  longer exists. These are separate stale-control/observability signals; neither
  grants cleanup authority.
- 2026-08-12T08:14:30Z — A read-only 90-second stale-resource plan completed as plan
  `62435f4ca71f782150fe17bd4fef85e7` (expires 2026-08-12T08:29:30Z). It proposed
  seven volumes totaling about 701.5 MB and explicitly excluded all 29 networks as
  `active`. The plan therefore cannot fix subnet exhaustion and was not applied.
- 2026-08-12T08:25:04Z — The incident was appended to the machine-local feedback log
  as `78aaf5836d63078b060336a9e306b7f5`. A read-only task heartbeat named “Monitor
  Sandbox remote network capacity” was registered to recheck resource and active-job
  status every six hours. Each run records a bounded observation through `sb feedback`;
  it is explicitly prohibited from cleanup, workspace destruction, deployment,
  revision sync, raw Docker/SSH use, or daemon mutation.
- 2026-08-12T08:32:00Z — CLI/MCP feedback tests, composition inventories, and
  architecture boundary checks passed (78 focused tests). The full 2,222-test
  self-test retained three unrelated baseline failures: macOS temporary-path
  normalization and the existing `fix`/`snapshot` built-in skill mirror mismatches.
  No remote test rerun was attempted because network capacity was not remediated.

## Current diagnosis

The immediate failure is caused by network lifecycle/capacity, not the plugin test or
the remote checkout revision. Sandbox has consumed nearly all of the daemon's
allocatable user-defined network slots with connected Compose networks. Several are
persistent workspace projects, so a normal stale-resource plan correctly refuses to
remove them. The safe remediation requires a separate ownership/liveness review of
the attached workspace containers and an explicit decision to destroy selected
retained workspaces, or an independently reviewed Docker daemon address-pool change.
Updating the remote checkout would not release a subnet and must remain a separate
decision.
