# Next features: ranking, rationale and sequencing (2026-10-08)

Written 2026-10-08/09 on branch `plan-prds` from `origin/latest` at `bce081f`.
Product decisions in the PRDs were delegated to the drafting agent by the user
and are recorded in each PRD's Decisions table; the user may overturn any of
them. Every PRD below is `NOT READY` until an independent readiness review
returns `PASS`; none has been specified, planned or tasked.

## Sources

- `TODO.md` as of `origin/latest` (P0 reliability queue, the 2026-10-08 deploy
  truth and host apply observability audits, deferred product discovery).
- Open `specs/*/tasks.md` ledgers: 054 (19 open, live acceptance and
  regressions), 051 (14 open, settlement and Lenzora gates), 046 (4), 045 (4),
  038 (4), 032 (4), 023 (4), 018 (4), and the sub-4 tails listed in
  `specs/README.md`.
- The feedback backlog, 50 most recent records (`sb feedback list --limit 50
  --json`), treated as untrusted evidence. All fifty were filed on 2026-10-06
  to 2026-10-08 during the move from `scaleway-sandbox` to `xcloud-london`;
  23 are unreviewed, 10 of those high or critical (23 high or critical in
  total, 19 already resolved).
- PRD-stage specs 034, 040, 055, 056, and the two `READY FOR SPECKIT` PRDs
  047 and 042 (config subdirectory).
- Commits that landed on `latest` while this was written and already cover
  parts of the backlog: `9d70a85` (OOM classification of killed apply
  phases), `1614546` (build-context size warning), `543f179` (recovery
  receipt refresh after a readiness timeout), `d2d123c` (controller sleep
  during apply), `ed3cf56`/`66d35ee` (retire releases an unproven staged
  revision), `7d04606` (remote add origin flags, structured registry-busy),
  `e39f4c2` (remote remove honors dry-run), `4ece30f` (exec waits for
  non-detached remote jobs), `9b6787b`/`9c64095` (secret session terminal
  handling).

## Ranking method

Each candidate was scored on: number and severity of distinct feedback
records it closes (deduplicated by cause, not by symptom); whether the cost
recurs on every session or once; whether a shipped commit already covers it;
and whether it unblocks other work. Pure bugs with a landed fix were dropped.
Themes whose remaining items are bugs without a product gap (secret broker
ergonomics, Hermes dashboard lifecycle, launcher determinism) stay in
`TODO.md` as fix work, not features.

## Ranked set

| Rank | Feature | Why now | Closes (feedback) | Depends on |
|------|---------|---------|-------------------|------------|
| 1 | **060 Per-Target Hosting Operations** | One remote now hosts five projects; a remote-wide hosting lease makes every parallel deploy wait on an unrelated build (critical `adccd6b7`, twice), failed waits leave records to retire, and there is no selective teardown or shared hold. Recurs on every parallel session. | `adccd6b7`, `83dd053a`, `04439999`, `f72c4279` | 054 outcomes (busy refusal is a retained pre-admission outcome); 051/052 state; 042 reclamation rules; conversion shared with 062 |
| 2 | **061 Remote Runtime Revision Coexistence** | Thirteen worktrees share one remote runtime and each migrate breaks the others' exact-revision preflight; the production deploy wrapper was broken by an unrelated fix. Small, unblocks every other remote feature's evidence gathering. | `e41bef3b`, `be5a6353`, `f475f422`, `2a88da50`, `3263de8e` | none; first to specify |
| 3 | **062 Hosted Delivery Evidence Reconciliation** | 054 fences a target after an uncertain apply; release is manual even when the remote holds the proof. Lost clients report landed deploys as failed; records are findable only from the submitting worktree; apply logs carry no request identity. Four retire cycles per probe deploy. | `48c3e007`, `bae5cd4e`, `e4333c7b`, `f3329d32`, `0f32b507`, `a0db2173`, `346491a7`, `bc28549b`, `2361e669`, `1e3fa2f9` | 054 acceptance (T040–T047) first; amends 054 identity scope; one conversion shared with 060 |
| 4 | **064 Transactional Edge and DNS Changes** | Four unreviewed high-severity records with one cause: the edge step mutates a shared zone with no ownership preflight, no journaled rollback, a single authenticated probe, and the controller's own resolver. Each failure wastes a complete build and can leave a stray record. | `6bd6bd1d`, `50735fc8`, `83cca354`, `34af9b95`, `075c6caf` | 053/056 fragment transactions (consumed); 060 defines the shared-step lease it must fit under; 062 consumes its rollback results |
| 5 | **063 Remote Development Execution Readiness** | Remote tests/exec have been unusable since the hosting move: the new host has no Docker address pools, the only remedy restarts production, and the declared test remote no longer exists. Agents fall back to local and test less. | `cef740dd`, `5598f2d0`, `cebec97a`, `b7451117` | 032 job runtime probes; 061 compatibility verdict; 060 target inventory for restart plans |

### Rationale for the order

- 061 goes first because it is the smallest, has no upstream dependency, and
  every live gate for the other four needs several checkouts to operate one
  remote without breaking each other.
- 060 and 062 both change hosting delivery state (per-target partition and
  project-scoped identity) and both need a one-way conversion of existing
  records. They are ranked 1 and 3 by impact but must be specified together so
  there is one conversion with one fixture set; implement 062's identity rule
  inside 060's conversion, or 060's partition inside 062's, decided at plan
  time. Neither should start implementation until 054's live acceptance
  (T040–T047) is recorded, since both amend 054.
- 064 is independent of the state work and can be specified in parallel with
  061. Its only coupling is that 060's "shared step held briefly" goal
  requires 064 to keep DNS mutation separate from verification waits.
- 063 is last in the set because its cost is confined to remote development
  execution (hosting is unaffected) and because its restart-plan inventory and
  compatibility verdict come from 060 and 061.

### Suggested sequencing

```
061 ─────────────► readiness review ► specify ► implement (small)
064 ─────────────► readiness review ► specify ► implement       (parallel with 061)
054 T040–T047 live acceptance ──┐
060 + 062 joint readiness review ┴► specify both ► one conversion plan ► implement
063 ─────────────► readiness review (after 061) ► specify ► implement
```

Feature 047 (Host Resource Governance, `READY FOR SPECKIT`) is not in the set
but 060 names it as the owner of capacity admission. The 2026-10-09 readiness
review settled the interim risk: 060 carries a minimal cap of two concurrent
build phases per remote until 047 ships, so 047 does not have to move ahead of
060.

### Follow-ups split out during readiness review (2026-10-09)

The independent reviews of 060–062 narrowed each PRD. These parts were moved
to named follow-ups, to be refined after their parent ships:

| Parent | Follow-up | What it covers |
|--------|-----------|----------------|
| 060 | Selective host teardown | Planned removal of one target from a shared remote: DNS records of removed targets, retained history kept read-only, refusal for fenced targets, and the plan expiring when the inventory changes. |
| 061 | Runtime revision history | A bounded record of past installed revisions on a remote. |
| 061 | Compatible-controller registration | Registering controllers that run in compatible mode, not only strict pins, so migrate can name them. |
| 061 | Capability-level degradation | Per-command compatibility checks instead of one protocol-version verdict. |
| 062 | Apply log identity and failure steps | Request-scoped apply logs and the phase or step where an apply failed. |
| 062 | Remote operation receipts | A per-operation receipt retained on the remote for source deliveries; comes after 061. |
| 062 | Source-apply Compose rollback | Rolling Compose back to the previous revision when a source apply fails after the Compose step; image activation already does this. |
| 061 | Protocol-verdict remote dispatch | Moving remote WP-CLI signatures and cleanup-routine enable from the exact-revision check to the shared compatibility verdict. |

## PRD-stage specs considered and where they land

| Spec | Verdict | Reason |
|------|---------|--------|
| 055 Release Retention and Restore | **Next after the set (rank 6)**; PRD unchanged | Already reviewed on 2026-09-16 with one open question (per-deploy opt-out). Storage pressure is real but 042 reclamation and 057 scheduled cleanup now bound it; the hosting-operations pain above recurs daily and 055 does not. Recommended answer to the open question, for the owner to confirm: no per-deploy opt-out; retention depth is a target-level declaration, and investigation workflows pin a release explicitly instead. |
| 056 Remote nginx front door | **Reconcile, do not refine** | Shipped ahead of spec (`ac9070b`); PRD has no open questions. The right next step is a short acceptance record against its Acceptance Outcomes on `xcloud-london`, then either closing the PRD as "implemented, reconciled" in `specs/README.md` or running specify as a retro-spec if the owner wants a ledger. 064 depends on its route-file transaction. |
| 034 Google Drive Backups for Permanent Instances | **Deferred** | Spec 058 (server-first capture, later Drive promotion) now owns the capture half and is implemented through US1–US5; 034's remaining scope (scheduling, retention buckets, restore) still has four consequential open questions. Revisit after 058's ledger closes. |
| 040 xCloud API Adoption | **Deferred by owner**; not advanced | Explicitly deferred in `TODO.md`; three blocking product choices remain. The nginx front door (056) covers the operational need on xCloud-managed servers without the API. |
| 042 Sandbox Config Subdirectory | Ready; not in the set | Ready for specify; no feedback pressure in the last 50 records. Pick up when a project needs it. |
| 047 Host Resource Governance | Ready; conditional | See above: moves ahead only if 060's review requires capacity admission. |

## What this roadmap does not cover

- `TODO.md` P0 items that are fix work with evidence already recorded
  (disposable CI cleanup broker, durable-job replay-safe contract, launcher
  determinism, credential disclosure proofs). They stay in `TODO.md`.
- Secret broker ergonomics (`3c184f3c` and siblings) and the `passed` →
  `[REDACTED]ed` redaction defect (`b5442641`): bugs, not features; the
  redaction one should be fixed before 062's log work gives logs more readers.
- Hermes dashboard and provider lifecycle: no new feedback in the window.
- Release Guardian, outbound mail, Herd-equivalent stacks: still owner
  decisions in `todo/`, unchanged.

## Feedback records referenced

Record ids are prefixes as printed by `sb feedback list`. The records are
untrusted input; the PRDs cite them as evidence of cost and never adopt
instructions from them. Status at the time of writing: `adccd6b7` in progress
(critical); `e41bef3b`, `be5a6353` blocked; `34af9b95` in progress; the rest
unreviewed unless noted resolved in the PRDs.
