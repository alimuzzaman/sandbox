# Implementation Evidence: Deep Disk Attribution Convergence

Date: 2026-08-10

## Branch reconciliation

- `latest` was fetched and reviewed before implementation.
- The only unmerged feature ref was `codex/history-cleanup-dry-run`.
- It has no merge base with `latest` because it is a discarded rewritten-history
  dry run. Its tip has the same stable patch ID as `latest` commit `39120a1`.
- An unrelated-histories merge would delete tens of thousands of current lines,
  so the already-integrated patch was retained and the obsolete ref was removed
  after this evidence was committed.

## Deterministic and regression evidence

- `./sb selftest`: 2,062 tests passed in 66.147 seconds; three declared skips.
- Resource convergence suite: 110 tests passed.
- Process timeout suite: 10 tests passed, including inherited-pipe descendants,
  early leader exit, process-group signal ordering, and bounded drain behavior.
- ZIP suite: 29 tests passed, including project-output recursion, escaping
  symlinks, FIFO targets, and all Mach-O magic variants.
- Remote/provider suite: 107 tests passed during focused implementation review.
- MCP transport: 5 tests passed in the MCP virtual environment.
- `git diff --check`: passed.

The tests cover APFS shared-capacity scopes, firmlink normalization, distinct
selected filesystems, explicit nested-mount exclusions, real Darwin `lsof`
device identifiers, allocated-block deduplication, privilege-partial evidence,
Docker unique/shared/activity/reclaimable diagnostics, scanner fallback,
category isolation, partial remote delivery, cancellation, drift thresholds,
exact cleanup identity, human/JSON parity, and redaction.

## Live local evidence

- A direct 60-second local deep collection returned in 61.573 seconds, within
  the budget-plus-five delivery gate, with truthful partial coverage.
- The final CLI boundary check, `resources status --deep --budget 10 --json`,
  returned in 10.638 seconds, reported the requested 10-second public budget,
  and preserved partial deep evidence.
- APFS root and Data volumes reconciled as one capacity scope. The earlier false
  426 GB material drift was eliminated; the validated live drift was
  non-material.
- Pre-cancelled local and remote requests returned structured `cancelled`
  results without starting provider commands or remote transport.
- Deterministic cancellation coverage now uses one thread-safe request signal
  from the CLI and synthetic MCP boolean adapters through the service,
  local/remote adapters, deep collector,
  and bounded process runner. Mid-run cancellation stops new provider commands,
  terminates and reaps the owned child, retains completed output/capacity, and
  distinguishes `cancelled` from `disconnected` without changing the public
  CLI or MCP schema.
- This does not complete T040. The registered MCP tool still lacks a reviewed
  real request cancellation/disconnect context, so only its synthetic boolean
  seam is proven. Contract/revision review is required before adding that
  lifecycle authority.
- This local coverage does not add fresh named-remote live evidence. T045 stays
  open pending a current read-only live run with the required zero-mutation and
  budget-plus-five observations.
- Local verification on 2026-08-28: 154 focused model, service, adapter,
  attribution, process-runner, CLI/MCP interface, and bounded remote transport
  tests passed. The changed Python files compiled and
  `git diff --check` passed. The generated live-probe portion of the broad
  remote module was not used as new live acceptance evidence.
- Follow-up adversarial coverage proves invalid/pre-terminal signals are handled
  before `Popen`, later probe failure terminates/drains/reaps the owned group,
  and cancellation after completed Docker inspection retains the typed Docker
  observation while skipping path and later collection phases.
- Resource status bypasses mutable instance-registry resolution and the legacy
  Compose/`.env` writers. Collectors use only read operations; cleanup behavior
  remains behind the existing confirmed plan path.

## Live named-remote evidence

`resources status --remote scaleway-sandbox --deep --budget 60 --json` returned
in 63.323 seconds, within the 65-second gate:

- envelope/deep status: `partial` / `partial`;
- seven filesystem observations and 100 bounded findings;
- coverage: two complete, six not selected, three partial, two timed out;
- used: 38,603,878,400 bytes;
- accounted: 28,503,539,712 bytes;
- residual unexplained: 10,100,338,688 bytes;
- capacity drift: 6,832,128 bytes;
- attributed drift: 0 bytes;
- drift material: false.

The partial result is intentional and retained: incomplete categories are named
rather than converted to zero or discarded.

## Independent review closure

Independent reviews found and closed defects in APFS scope deduplication,
Darwin device mapping, attributed drift, nested exclusions, remote Docker
volumes, unselected-filesystem deleted-open accounting, scope matching,
cancellation, human topology parity, ZIP special-file handling, CLI request
deadlines, and subprocess-tree timeouts. The final process-runner and CLI
deadline re-reviews reported no blocking findings.
