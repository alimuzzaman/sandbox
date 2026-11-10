# Research: Host Stack Templates

## R1. Where the template is read from

- **Decision**: read the template from the resolved commit (`git show <sha>:<path>`), not the working file.
- **Rationale**: the worktree that is deployed contains exactly that file; an uncommitted edit can never reach a stack, matching the clean-source rule.
- **Alternatives**: the working file (rejected: diverges from the deployed source when `--ref` is not `HEAD`).

## R2. Where the rendered manifest may point

- **Decision**: a rendered manifest carries a `stack_instance` block and an absolute `source_root`. `validate_manifest` accepts an absolute `source_root` only when the manifest is exactly `RUNTIME_DIR/stacks/<slug>/sandbox.hosting.yml`, the slug matches, and `source_root` is inside a Git checkout.
- **Rationale**: the rendered file lives outside the checkout; the location check keeps the relaxation inside Sandbox-owned runtime state.
- **Alternatives**: copy the manifest into the worktree (rejected: dirties the clean source the apply guard checks).

## R3. Branch policy for a detached worktree

- **Decision**: the worktree is detached at the commit. `_validate_apply_source` uses the stack's recorded `ref_branch` when HEAD is detached and equals the stack commit; an empty `ref_branch` passes only `"*"`. Stack applies push to the immutable `sandbox-source-<sha>` ref (`allow_detached=True`).
- **Rationale**: Git refuses a second checkout of a branch already checked out in the user's checkout; a PR branch is often force-pushed, which a branch push would refuse as diverged.
- **Alternatives**: `git worktree add -b` (rejected: creates or moves branches the operator owns).

## R4. Port allocation and probing

- **Decision**: one read-only SSH probe lists listening TCP ports (`ss -H -ltn`) and Docker published ports with their Compose project label. A port is usable when it is free, or bound only by this stack's Compose project. Allocation is deterministic per remote/slug/name inside 18100-18199, skipping ports recorded for other stacks on the remote, recorded hosting loopback ports, and bound ports.
- **Rationale**: re-apply must accept the stack's own Mailpit binding; anything else holding the port must refuse.
- **New web allocations** skip 18100-18199 so the two ranges cannot meet; existing recorded web ports are kept.

## R5. Hostname ownership

- **Decision**: holdings per remote = for each hosting-state target `remote/<project>/<env>`: its new `hostnames` list, its `dns_coverage.hostname`, and its `records[].applied.name`; plus each stack record's hostnames. A hostname is refused when held by a different project.
- **Rationale**: older targets never recorded their hostnames; DNS rows and coverage cover them. The check runs under the apply locks right after state load, before any remote probe or write.

## R6. Submission

- **Decision**: run `sb job-start --local --project-dir <source_root> --request-id <id> --source-commit <sha> --timeout <t> --json -- sb host apply --project-dir <rendered> --remote R --environment E --confirm` as a subprocess and record the returned `job_id`. Request id defaults to `stack-<slug>-<sha12>-<6 hex>`; `--request-id` overrides it for replay. Timeout is `max(900, build_timeout_seconds + 600)`.
- **Rationale**: this is the exact documented durable path (`_host_delivery_guidance`), so recovery context and receipt guards apply unchanged.

## R7. Worktree lifetime

- **Decision**: worktree at `runtime/stacks/<slug>/source/<sha12>-<token>`; `up --confirm` removes the stack's earlier worktrees (their jobs are terminal, since `up` refuses while one is active); `down` removes all.
