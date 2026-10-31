# Sandbox gradual execution plan

Date: 2026-10-27. Companion: [review](review.md).

Use one small work package at a time. The plan covers the confirmed findings,
feedback/TODO reconciliation, existing proof gaps, unread source and later
features. It does not start implementation or authorize operational effects.

The next implementation instruction can name a package, for example
`implement W02`. That instruction covers its routine scoped work; do not add
another approval step for every reversible edit. Credentials, production,
destructive cleanup, migrations and releases retain the user's existing named
authorization requirements. A checked task or a feedback record grants none.

## Working rule and handoff

Before starting a package, record current HEAD, its relevant dirty diff, one
owner, exact files, the failure scenario and acceptance checks. Preserve the
existing pending patch. Do not treat the reviewed revision as immutable forever.

Complete the agreed implementation and matching docs in the repository's required
order. Then exercise its supported workflow, author focused regressions from
observed gaps, and run required checks through the approved test runner. For a
source-only task, retain that boundary and mark runtime proof unverified. A real
test/provider availability block does not authorize silent provider substitution.

Each handoff records files changed, commands and observed results, remaining
limits, source/controller identities where relevant, and the next card. If a
session stops, save and deliver its handoff first. Do not run overlapping
implementers or review fleets. No new agents are authorized by this plan; when
delegation is later requested, retain the latest user choice of Sol medium,
standard service tier, one at a time unless changed explicitly.

For a shared-branch shipping task, follow the required independent review and
current Git rules. This review-only task performs no commit or push. Never infer
deployment, release, PR merge, tag, credential or GitHub workflow authority from
a successful source review.

S/M/L describe relative scope, not elapsed-time estimates. Split every L group
into one named S/M contract before implementing. Stop after the selected card's
acceptance and handoff, rather than expanding to its whole neighboring subsystem.

## Phase 0: freeze the candidate and make the queue reliable

### W00. Freeze one current work package — S

- **Input:** current source, the 18 pending paths and the selected finding or at
  most five tightly related feedback records.
- **Do:** revalidate its source references and falsifier; separate missing code,
  stale status, a product decision and a missing proof. Record one owner and
  dependencies. Use supported feedback reads if a record is needed.
- **Done:** a bounded scenario/files/acceptance card with no unresolved writer
  overlap. Do not close records, change remote state or invent a new scope.
- **Dependency:** first step for all implementation and proof work below.

### W01. Reconcile provider rules, release guidance and current queue — S/M

- **Findings:** R12–R14. **Files:** `AGENTS.md`, `CLAUDE.md`, `TODO.md`,
  `specs/README.md`, `docs/release-readiness.md`, and canonical instruction
  sources only where they actually own generated text.
- **Do:** use consistent `main` protection, implementation/verification order
  and tooling boundaries. Distinguish existing helper compatibility from new
  injected dependencies. Refresh current counts, 046/045/033/042 state, resolved
  feedback references, 063 counts, missing 066–069 rows and stale owner branches.
  Preserve dated history and remaining external gates.
- **Done:** every current queue entry has a source-backed state/owner or an
  explicit unresolved owner; providers receive consistent operative rules;
  all 67 feature directories are discoverable. A small count/dependency check
  may prevent demonstrated drift. Keep T026b and human/live gates open.
- **Boundary:** documentation maintenance, no authorization expansion, casual
  renumbering or hand-editing formal Spec Kit artifacts. Use their skills when
  changing those ledgers or managed context. Do not rebuild completed features.

## Phase 1: job execution safety

### W02. Stop owned child work before releasing job authority — M

- **Finding:** R01, highest priority. **Files:** `sandbox/jobs/supervisor.py`,
  narrowly required process/lease seams, `tests/test_job_supervisor.py` and job docs.
- **Do:** one bounded finalization path for output pressure/write errors and
  generic post-spawn failures. Stop and reap owned work before terminal cleanup
  and lease release. If cessation cannot be established, preserve exclusion and
  report uncertainty rather than announcing safely finished work.
- **Done:** synthetic emit-then-sleep child and descendants stop before lease
  release for each injected failure; no overlapping workspace admission or
  premature cleanup; durable reason and partial-output truth survive.
- **Boundary:** preserve PID identity and ownership checks. No production ENOSPC
  experiment, unrelated cancellation redesign or broad job-service rewrite.

### W03. Enforce deadlines independently of output pipes — S/M

- **Finding:** R02. **Depends:** W02; serialize the same owner/files.
- **Do:** supervise process/group liveness separately from draining stdout and
  stderr; reuse the bounded stop path and finite final drain.
- **Done:** closed pipes/live child, exited leader/descendant-held pipes, silent
  child and normal fast completion meet the deadline/grace contract. Record
  owned descendants and output completeness; no unbounded `wait()` remains.
- **Boundary:** do not add automatic operation replay or new execution profiles.

### W04. Make bounded retention scans progress — S/M

- **Finding:** R04. **Files:** `sandbox/application/job_service.py`,
  `sandbox/jobs/registry.py`, service/repository tests and matching CLI docs if
  envelopes change.
- **Do:** eligible-query ordering or durable continuation with a finite budget.
  Retain deterministic ordering and truthful failures.
- **Done:** more than one page, newest rows already cleaned or active, older
  retained work, timestamp ties and retries all progress safely. Under pressure,
  selection is oldest eligible across the intended population. Active and
  protected data remain untouched.
- **Boundary:** increasing the page limit or deleting durable metadata is not
  a fix. Keep terminal-workspace retention policy unchanged unless separately
  selected. Real cleanup requires exact disposable-target authority.

## Phase 2: bounded correctness repairs

These packages need W00 but are otherwise separate from each other. The listed
order favors authority reporting and operator recovery before UI conveniences.

### W05. Bind storage support to exact accepted authority — M

- **Finding:** R03. **Files:** `owned_storage_lifecycle` service, its model and
  repository only as needed, `commands/owned_storage.py`, focused review tests/docs.
- **Do:** validate the acceptance → promotion → current binding chain for the
  requested remote, source/service/contract revisions, freshness and revocation.
  Keep observed state distinct from accepted authority.
- **Done:** A's acceptance cannot prove B; repeated R2 reads after R1 acceptance
  remain drifted; expired/revoked/missing/mismatched records refuse; unknown CLI
  observation never reports proven/adoptable.
- **Boundary:** no automatic promotion or live requalification. Review schema/wire
  changes and historical-record handling explicitly. Human review precedes release
  of consequential authority changes. A mutation bypass was not demonstrated.

### W06. Preserve retirement JSON on second-lock timeout — S

- **Finding:** R08. **Files:** `sandbox/commands/hosting.py`, focused hosting and
  destroy tests, retirement CLI docs.
- **Do:** convert expected lock exhaustion to a stable bounded outcome after
  tracing whether the earlier retirement phase already changed state.
- **Done:** second-lock timeout after synthetic retirement returns nonzero valid
  JSON, no traceback, truthful partial/inspect guidance and preserved fences;
  success remains correct for text/JSON.
- **Boundary:** no automatic retry, lock bypass or new repair authority. Real
  retirement and consequential release remain separately gated.

### W07. Classify local ownership configuration failures accurately — S

- **Finding:** R09. **Files:** `sandbox/commands/secrets.py` or the narrow typed
  config-composition boundary, ownership CLI acceptance tests and docs/contracts
  only if the public code set changes.
- **Do:** map known local input/configuration failures to an existing safe usage
  category. Keep arbitrary backend exceptions opaque.
- **Done:** synthetic invalid descriptor yields the correct code/exit, zero
  provider/native-store calls and no raw exception/config/path disclosure.
- **Boundary:** no actual secret source, native credential, approval, migration
  or provider operation. Use Spec Kit skills for material contract changes.

### W08. Recognize host-file commands after WP-CLI global options — M

- **Finding:** R10. **Files:** `sandbox/commands/wp.py`,
  `tests/test_wpcli_timeout.py`, `docs/wp-passthrough.md`.
- **Do:** parse supported leading option forms and identify only legitimate
  staged operands. Keep the separate ZIP-suffix feedback as an unconfirmed
  consumer question until traced or observed.
- **Done:** leading `--url` and `--require`, split/equals forms, operand delimiter,
  an outside-mount script and file-like unrelated arguments behave correctly;
  no duplicate execution; Herd passthrough and failure cleanup remain correct.
- **Boundary:** no broader host-file upload, remote stdin, sibling packaging or
  arbitrary WP option redesign. Disposable runtime acceptance needs the relevant
  provisioning/mutation authority.

### W09. Reconcile activation catalog removal safely — M

- **Finding:** R05. **Files:** activation scheduler and coordinator only if its
  invariant requires it, activation gateway tests/docs.
- **Do:** retain correct route/claim generation and keep missing catalog entries
  or catalog failure from terminating the scheduler.
- **Done:** removed due route, catalog exception, missing and valid route together,
  and later reappearance do not crash or strand claims; no stop for absent routes;
  valid unrelated routes continue to suspend.
- **Boundary:** no delete/stop authority inferred from disappearance. Actual
  lifecycle acceptance uses separately authorized disposable instances.

### W10. Give dashboard polling a bounded owner — M

- **Finding:** R06. **Files:** `src/web/src/ui/console.ts` and minimal API/state
  callers, focused web checks and dashboard docs.
- **Do:** one request in flight per job, cancellation/cleanup for the polling
  owner, observation-unavailable state and release of owned pause/busy flags.
- **Done:** rejected request, 404 after backend restart, delayed response,
  overlapping sessions and terminal exit cannot freeze UI or duplicate chunks.
  Preserve the job ID. Never resubmit the action to learn its outcome.
- **Boundary:** no dashboard redesign or new durable-job protocol. Browser
  acceptance uses a disposable local backend and synthetic data.

### W11. Report the final document response — S

- **Finding:** R07. **Files:** `tools/visit/visit.py`, focused visit fixtures/docs.
- **Do:** make final navigation response authoritative; retain intermediate
  redirect evidence separately if useful.
- **Done:** 302→200, 301→404, multiple redirects and direct 200 have matching
  document status; final HTTP failure does not rely on console messages to fail.
- **Boundary:** no login/credential changes or arbitrary public-site testing.

### W12. Resolve explicit-image ownership, then align destroy — decision S; code M

- **Finding:** R11. **Files:** `sandbox/hosting/destroy.py`, destroy tests/docs,
  existing ownership/manifest contracts where needed.
- **Do first:** choose what proves an explicit tag and its aliases are owned by
  the target. Existing tests deliberately preserve shared/base images. A label
  listing alone is not a mandate to remove every tag.
- **Then:** use the approved removal manifest consistently in plan, teardown,
  post-check and state outcome. Retain non-forced removal and reference guards.
- **Done:** owned explicit/generated tags, referenced images, shared aliases,
  ambiguous/raced ownership and repeat plans converge or refuse truthfully.
- **Boundary:** no global prune, forced removal, production cleanup or silent
  clearing of target state under ambiguity. Ownership policy and destructive
  acceptance need explicit approval and disposable targets.

## Phase 3: finish the pending patch and proof obligations

### W13. Validate the frozen 18-path candidate — three S slices

Do not combine these slices with a new feature. Their source predates this review;
freeze the final candidate and account for later edits before relying on tests.

1. **W13A:** Compose bounded success stdout, declared-test streams/exits and
   status disclosure. Read final caller contracts; test actual supported paths.
2. **W13B:** retained-terminal labels/guidance and fail-closed resource evidence.
   Execute the isolated malformed/index-cap/legacy-fallback boundaries. Reporting
   must never become deletion authority.
3. **W13C:** DNS/TLS direct/fallback behavior and deterministic coordination
   regressions. Preserve authority, no redirect second request and negative TLS
   controls; do not confuse foundational coordination tests with feature 060
   lease/conversion completion.

The previous task supplied 401 passing identities. Its six later NT1–NT6 additions
remain unexecuted. The exact pending four-module job and synthetic environment
requirements are saved in `terra-feedback-todo/quota-resume-coverage-r3.txt` under
the shared scratch root. Expected discovery is 97 cases across those four
modules, not 97 additional unique cases. No result is assumed by this plan.

Run appropriate required checks only after implementations/docs are complete.
Record command, exact outcomes, timing/skip/failure gaps and candidate identity.
Do not repeat the entire suite to compensate for a guessed diagnosis. A required
quota-blocked check remains open. Installed-controller revision and live proof
remain separate from synthetic test results. Remote rollout is not part of W13.

### W14. Reconcile and close one existing proof surface at a time — S/M slices

- First reconcile 033 and 068 dated identity/ancestry evidence before repeating
  historical work. T026's evidence does not automatically satisfy T026b.
- Then select one feature and one surface, such as 046 install/live/reboot,
  061 revision coexistence, 063 disposable readiness, 066 floor acceptance,
  045/native qualification, 069 provider/cleanup/platform, or recovery restore.
- Keep source, local observed test, installed revision, disposable live evidence,
  human security review and release permission distinct. The later dated secret
  wire evidence supersedes blanket “no live proof” statements only for its cases.
- Done requires the named requirement, exact revision/target, finite timeout,
  replay-safe identity where applicable, negative controls, evidence and truthful
  cleanup. Unavailable proof stays open.
- No schedule activation, swap/reboot, provider credentials, production migration,
  restore or destructive cleanup is authorized by selecting a ledger row.

### W15. Trace backlog clusters in bounded groups — S each

Use `work-item-index.tsv`: one record or at most five related records per card.
Revalidate details/source before implementing. Start with reported ENOSPC read
paths, output/source identity, archive-suffix consumer, snapshot/mount semantics,
Plugin Check selection, hosting DNS provenance and recovery initializer issues.

An active status is not proof of a current defect. Existing code already addresses
some origin-IP, config-home, venv anchor, terminal-session and cleanup-routine
requests. Installed timer/host behavior is a separate question. Do not treat all
70 “other” summaries as bugs, reopen resolved records from an old paragraph, or
move a foreign-project incident into this repository without its owner.

Done is a source-backed defect, addressed/partial classification, explicit design
decision or named proof gap, with original record IDs retained. Feedback updates
follow the supported append-only review mechanism only when authorized and backed
by observed evidence. Bulk closure is outside this plan.

### W16. Finish uncovered high-risk source review — one S/M group per card

Trust/security review was interrupted and no new reviewer will be started in this
task. Its PHP abilities path-containment lead is unconfirmed. Remaining local and
remote coverage is indexed, not implicitly approved.

Later review order: secrets/auth/update trust; hosting image activation and recovery;
workspace authority; restore/capture adapters; owned-storage effects; readiness and
pins/installers; server-config and remote-network effects; remaining CLI/MCP/config;
native adapters, desktop and Hermes. Each card traces one caller-to-effect
contract and tests, records full-read/sampled/unread files, and attempts to disprove
candidates. No finding means no automatic implementation package. No broad
security or release clearance until the relevant coverage and proof exist.

## Phase 4: larger features, only after reliability prerequisites

### W17. Resolve and complete exact 051/054 prerequisites — M slices

Re-read current source against the open rows before assigning code. Separate
immutable graph/private-source/settlement and delivery outcome/nested-source/
trace contracts. Do not collapse all remaining rows into “only live testing”.
Use the corresponding Spec Kit workflow when extending material scope.

Done requires the supported local/owned CLI outcomes, no-replay/conflict/expiry
limits and named acceptance evidence. Do not rewrite OCI authority or
automatically retire an operation to simplify a demo.

### W18. Complete 060 lease, fencing and conversion integration — L split into M

Depends on W17's required 054 behavior. Order the slices: target lease/renewal;
fencing before every effect; shared-step boundary; resumable conversion including
cross-checkout identity needs; holds/listing/build cap. Keep coordination disabled
until safe conversion is ready.

Acceptance includes two controllers/targets, lease loss, stale tokens, predecessor
cessation, interrupted conversion, FIFO bounds and old-controller refusal. Real
conversion requires approval. Concurrency/migration changes require the applicable
independent review before release; this plan does not lower that gate.

### W19. Implement 064 edge transaction ownership/journal — L split into M

Depends on W17 and compatibility with W18 shared leases. Ownership planning with
zero writes comes first; then journal before effect, ambiguous acknowledgement,
drift-preserving rollback, authoritative propagation outside the short mutation
lease, and explicit adoption/refusal.

Done requires Caddy/nginx parity, failure/interruption cases, authoritative
DNS/address/SNI evidence, finite waits and retained fences for leftovers. No
ambient credential discovery, automatic zone takeover or invented shared-zone
cleanup. Disposable provider/DNS authority is separate.

### W20. Implement 062 evidence reconciliation/fence release — L split into M

Depends on W17/W18 and consumes W19 rollback facts. Order: bounded read-only
classification, durable phase evidence, conservative release rules,
cross-checkout identity, retry linkage.

Done requires contradiction/insufficient/unavailable states retaining fences,
two clones finding the same operation, one adoption under concurrency and old
identities remaining readable. Never replay an effect to learn its status or
release unsupported legacy fences by inference. Review data/authority changes
and any migration before release.

## Phase 5: optional discovery

### W21. Choose one deferred PRD or RexEnv inspection slice — S discovery

Resolve existing owner questions before starting more code. Distinguish the seven
PRD-only directories, superseded 018 work and separately deferred xCloud/research
ideas. Follow refine → independent readiness review → specify → clarify → plan
→ tasks → analyze → implement for a material feature. This portfolio plan is not
a replacement for its formal artifacts.

For RexEnv, the existing independent report supports an explicitly selected
fixed-argv version/status/site inventory integration as a possible first slice.
Qualify the CLI/app build pair, enforce bounds and strict response projection,
and refuse malformed/unknown results. Full runtime support needs shared-service
ownership, mutation reconciliation, execution and DNS/TLS coexistence designs.
No install, enabling MCP, host socket inspection, credential access, lifecycle
mutation or adoption was performed or authorized by this review.

**Headless follow-up, checked 2026-10-27:** upstream's current install guide says
Linux headless setup shipped in 0.9.3; the latest published release checked was
0.9.4. Source at `a4c039b01715e63caca3a3df3480509a52ad3604` implements
`--headless` using an owned Xvfb and `--headless-setup` for service/host setup.
The guide documents systemd boot, administrator-controlled MCP consent, and
alternate edge ports before creating sites. A Linux desktop session is therefore
not a prerequisite for a future disposable-server evaluation. Qualify this
documented path instead of designing a new headless launcher. It does not settle
Sandbox ownership, uncertain mutation, execution or ingress contracts. No setup,
service start, MCP enablement or runtime test was performed. See the
[upstream install guide](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/docs/INSTALL.md#server-headless--no-desktop-driven-by-rex-and-rex-mcp)
and [Linux implementation](https://github.com/rexenv/rexenv/blob/a4c039b01715e63caca3a3df3480509a52ad3604/src-tauri/src/platform/linux/headless.rs).

The subsequent [preferred-provider assessment](rexenv-provider-fit.md) proposes
qualification for trusted WordPress/PHP projects, with separate runtime, ingress
and resolver adapters. It does not propose a universal default switch. TLD/CA
ownership, mixed Compose routes, off-443 rewrite refusals, execution/recovery
contracts and shared-host limits remain explicit qualification requirements.

## Suggested starting sequence

Start with W00 and the small W01 reconciliation, then W02 and W03 as one serialized
ownership stream. Continue with W04 and W05. Pick individual W06–W11 fixes next
according to operator impact. W12 waits for its ownership policy decision.
Keep W13 validation and W14 proof tasks tied to an exact candidate; they may be
chosen earlier when the goal is to finish the existing pending patch rather than
start another fix. W15/W16 remain finite review cards. Do not begin the large
W17–W20 feature chain merely to close backlog counts. W21 is optional.

There is no automatic cadence, agent restart, spending estimate or implementation
commitment attached to the sequence. Every card has a useful stopping point and
can be resumed from its saved evidence without recreating the entire audit.
