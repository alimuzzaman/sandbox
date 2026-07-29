# Product Requirements Draft: Deep Disk Attribution

**Status**: Ready for Specification

**Created**: 2026-07-29

**Last Refined**: 2026-07-29

**Input**: "Use open-source tools to find the 74.13 GB of genuinely unlocated
storage, then implement the capability in Sandbox."

**Drafting Model**: current GPT-5 root configuration (fallback; the preferred
`gpt-5.6-terra` Medium root was not selected for this turn)

**Final Validation**: `PASS` — `gpt-5.6-sol` High

**Validated On**: 2026-07-29

**Artifact Owner**: `speckit.prd.refine`

**Next Stage**: `speckit.specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox resource monitoring can measure managed worktrees, volumes, runtime
directories, images, containers, and selected host roots, but a completed
thorough scan still left 74.13 GB of filesystem use unattributed. Several
important explanations sit outside ordinary directory totals: deleted files
held open by processes, nested or excluded mounts, inaccessible paths, Docker
shared-layer accounting, and filesystem allocation or metadata.

Operators currently have to leave the `sb` workflow and assemble privileged,
ad hoc host commands to distinguish these cases. This makes the capacity gap
hard to reproduce, unsafe to automate, and easy to mistake for reclaimable
space. Sandbox needs a bounded read-only diagnostic that explains both measured
bytes and measurement gaps before any cleanup decision is made.

## Users and Desired Outcomes

- **Sandbox operator**: Identify the largest observed allocation consumers of a local or
  named remote filesystem and understand why any remaining bytes cannot be
  attributed.
- **Host administrator**: Distinguish live files, deleted-open files, mounted
  filesystems, container storage, and filesystem overhead before deciding what
  can be reclaimed.
- **Automation client**: Receive stable structured evidence, including tool,
  permission, timeout, and mount coverage outcomes, without parsing an
  interactive terminal.

## Goals

- Reduce a large unexplained capacity gap by measuring observed allocated storage at
  safe filesystem boundaries and reconciling it with host capacity.
- Detect deleted files that still consume blocks because a process keeps them
  open.
- Explain Docker storage using detailed shared and unique accounting without
  performing a prune.
- Report excluded mounts, permission failures, unavailable tools, timeouts,
  and overlapping totals explicitly.
- Rank evidence-backed cleanup guidance while preserving the existing
  confirmation-gated cleanup policy.
- Keep local, named-remote, CLI, and MCP behavior consistent.

## Non-Goals

- Installing packages or downloading diagnostic binaries during a scan.
- Providing an interactive disk browser through Sandbox.
- Automatically deleting arbitrary host files, open-file owners, Docker
  resources, filesystem metadata, or unmanaged data.
- Treating logical image or build-cache totals as additive physical disk use.
- Claiming byte-exact physical allocation on filesystems whose snapshots,
  reflinks, compression, sparse extents, or copy-on-write semantics cannot be
  resolved by available host interfaces.
- Changing filesystem reserved-block settings, storage drivers, mount layouts,
  or running processes.

## Product Scenarios

### Scenario 1 — Attribute a capacity gap

- **Starting state**: A thorough resource report has a large unknown byte count.
- **User action**: The operator requests deep attribution with a finite budget.
- **Expected outcome**: Sandbox ranks allocated storage under the relevant
  filesystem, reports mount boundaries and scan coverage, and reconciles the
  result against capacity without changing the host.

### Scenario 2 — Find deleted-open storage

- **Starting state**: Filesystem capacity is consumed but directory walks cannot
  find the corresponding paths.
- **User action**: The operator runs deep attribution.
- **Expected outcome**: The report identifies deleted files still held open,
  their aggregate bytes, and non-secret process identity evidence, or explains
  that the check was unavailable or permission-limited.

### Scenario 3 — Explain Docker overlap

- **Starting state**: Docker reports images, containers, volumes, and build
  cache whose logical totals overlap.
- **User action**: The operator requests deep attribution.
- **Expected outcome**: Sandbox reports Docker's detailed shared, unique,
  active, and reclaimable values separately and does not add overlapping
  logical values to physical attribution.

### Scenario 4 — Tool or privilege is unavailable

- **Starting state**: A preferred scanner is absent, a path is unreadable, or a
  category exceeds its deadline.
- **User action**: The operator requests deep attribution.
- **Expected outcome**: Sandbox uses a safe existing fallback where possible,
  labels its limitations, and reports the unresolved byte gap rather than
  implying completeness.

### Scenario 5 — Filesystem sharing obscures allocation

- **Starting state**: A filesystem uses hard links, sparse files, reflinks,
  snapshots, compression, or copy-on-write layers.
- **User action**: The operator requests deep attribution.
- **Expected outcome**: Sandbox reports observed allocated bytes, the
  deduplication behavior and filesystem capabilities it could determine, and
  any unresolved overlap; it does not claim byte-exact physical ownership.

### Scenario 6 — Review cleanup guidance

- **Starting state**: Deep attribution has identified a large consumer.
- **User action**: The operator reviews the result.
- **Expected outcome**: Sandbox distinguishes existing exact managed cleanup
  actions from manual host remediation and non-cleanable overhead; the scan
  itself performs no deletion.

## Proposed Product Behavior

- Deep attribution is an explicit read-only extension of thorough resource
  status and accepts an overall finite time budget.
- The report starts with filesystem capacity and mount topology. It presents
  capacity-accounted observed allocation separately from logical diagnostics,
  and records whether hard-link deduplication and other overlap behavior are
  known, partial, or unavailable.
- Directory measurements count allocated blocks on one filesystem at a time,
  avoid virtual filesystems, and disclose every excluded or incomplete
  boundary.
- Every discovered writable local filesystem is inventoried. The default deep
  scan measures the root filesystem plus the filesystems containing Sandbox
  home and the container engine data root when those are distinct. Other
  discovered filesystems remain visible with capacity and an explicit
  not-scanned reason unless they contain a known Sandbox-managed root.
- When already installed, a purpose-built open-source scanner provides the
  bounded directory inventory. Otherwise Sandbox must use the host's standard
  allocated-block directory utility as a fallback. The report identifies the
  selected capability, version when safely available, hard-link behavior, and
  limitations. If neither capability is usable, that filesystem is partial.
- Deleted-open-file evidence is aggregated by filesystem and process while
  redacting file contents, credentials, command-line secrets, environment
  values, and sensitive mount options.
- Container-engine diagnostics remain read-only and distinguish unique storage
  from shared or potentially reclaimable logical totals.
- Each category reports completion, duration, confidence, errors, and whether
  sufficient privilege was available. Missing evidence reduces confidence.
- The final reconciliation separates capacity-accounted observed allocation,
  filesystem reserved or metadata bytes where observable, deleted-open bytes,
  overlapping diagnostics, and a residual unexplained gap. It never describes
  observed allocation as byte-exact physical ownership when filesystem
  semantics prevent that conclusion.
- Cleanup guidance can reference only existing Sandbox cleanup scopes for
  exact managed resources. Other findings are monitoring-only and require a
  human decision outside this feature.

## Constraints and Dependencies

- All runtime inspection must remain available through `sb`; operators must not
  need raw SSH or Docker commands for the supported workflow.
- Scans must work without installing new host packages. Optional tools improve
  coverage only when already present and compatible; a standard directory
  accounting fallback is required on supported hosts.
- Sandbox may use already configured passwordless non-interactive elevation
  for read-only measurement. It must never prompt, modify privilege policy, or
  fail closed when an unprivileged partial result can be returned.
- Local and remote probes must be bounded, cancellable, and return partial
  structured results on timeout or disconnect.
- Filesystem roots, Docker data roots, and mount boundaries vary by host and
  must be discovered rather than assumed.
- The same physical blocks can appear in directory, overlay, image, build-cache,
  and deleted-file views; reports must prevent double counting.
- Paths and process metadata may contain secrets or tenant-identifying data and
  must be minimized and redacted.
- Existing active instances, permanent deployments, jobs, backups, and
  non-Sandbox workloads remain protected.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Delivery surface | Extend resource status rather than add raw host commands | Operators need one reconciled source of truth | User request and existing feature 035 |
| Scan mutation | Read-only; no installation, process restart, or cleanup | Attribution must be safe to run routinely | Existing safety policy |
| Preferred scanner behavior | Use a capable installed open-source scanner, with a required standard host-utility fallback; never install during a scan | Improves scale and structured evidence without making package state a prerequisite | User request plus least-privilege policy |
| Mount coverage | Inventory every writable local filesystem; deeply scan `/` plus distinct Sandbox-home, container-data, and known managed filesystems by default | Covers capacity-relevant storage while bounding unrelated mounts | Existing host-wide scope and least-privilege policy |
| Read-only privilege | Use existing `sudo -n` capability when available, otherwise return unprivileged partial evidence | Closes common visibility gaps without prompting or changing host policy | Existing non-interactive remote policy |
| Filesystem accounting | Report capacity-accounted observed allocation, deduplication/capability status, and residual separately from overlapping logical diagnostics | Prevent inflated or falsely precise totals | Evidence from prior Buildx cleanup drift |
| Missing evidence | Preserve residual bytes as unexplained and name the failed boundary | Unknown is not reclaimable | Existing resource-monitoring policy |
| Cleanup result | Guidance only unless an exact item is already eligible under an existing cleanup scope | Deep inspection does not prove arbitrary deletion safety | Existing confirmation and ownership policy |

## Open Questions

- None.

## Acceptance Outcomes

- Given a host with readable files, nested mounts, Docker data, and a
  deleted-open file, one bounded deep report identifies each applicable class,
  states its measurement status, and performs zero mutations.
- Every deep report provides a byte-level reconciliation whose components
  distinguish capacity-accounted observed allocation from overlapping logical
  diagnostics, state deduplication and filesystem capability limits, and keep
  the residual non-negative.
- A missing optional scanner, insufficient privilege, unreachable remote, or
  expired budget produces a partial result with a named reason and retained
  residual gap.
- Structured CLI and MCP results expose equivalent target, capacity,
  attribution, coverage, and diagnostic semantics.
- A deterministic fixture containing 6 GiB of readable allocated files and
  1 GiB of deleted-open files reduces the unexplained gap by at least 7 GiB,
  with exact fixture-size tolerance of one filesystem allocation block, and
  reports those two classes separately.
- A deterministic hard-link fixture counts one inode's allocated blocks once
  per scanned filesystem. Sparse, reflink, compressed, snapshot, and
  copy-on-write fixtures or capability simulations report explicit overlap or
  capability limitations rather than unsupported physical totals.
- A scan that reaches its budget returns within 5 seconds of that budget, marks
  every unfinished filesystem or category partial, and preserves all remaining
  bytes in the residual gap.
- Re-running a deterministic fixture without intervening writes produces the
  same ranked resources and totals. On a live host, results are considered
  consistent when capacity drift and attributed-byte drift are each no more
  than the greater of 1% of used bytes or 64 MiB; larger drift is reported.

## Risks and Assumptions

- **Risk**: Privilege boundaries can hide the very paths or process descriptors
  needed to close the gap; the product must make this visible instead of
  claiming success.
- **Risk**: Scanner output and Docker accounting can count shared physical
  blocks more than once; the report must isolate overlap.
- **Risk**: A full root walk can create unacceptable I/O load; budgets, mount
  boundaries, ranked limits, and category isolation must constrain it.
- **Assumption**: Supported hosts provide basic POSIX capacity, mount, and
  directory-accounting facilities even when optional scanners are absent.
- **Assumption**: Existing Sandbox remote execution can run compact read-only
  probes without deploying or changing the target.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
