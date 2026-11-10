# Feature Specification: Host Stack Templates

**Feature Branch**: `feat/host-stack-templates`

**Created**: 2026-11-10

**Status**: Draft (owner-approved scope, 2026-11-10)

**Input**: User description: "sb host stack: per-PR hosting stacks from one template manifest. A Hub file sandbox.hosting.yml becomes a template (stack: block with slug pattern and named ports; placeholders {slug}, {SLUG}, {port:<name>}, {ref_branch} allowed only in project, host.primary, alias hostnames, deploy.allowed_branches, secrets.values values, secrets.generated source names). Commands: stack up / list / down. up validates, renders, resolves --ref on the controller, checks the commit out into a temporary clean worktree, writes the rendered manifest under runtime/stacks/<slug>/, and submits the existing durable job-start + host apply path. --plan changes nothing. down tears the stack down through the existing destroy path and releases its ports. Stack ports come from 18100-18199 per remote, persist across applies, and are released only by down; --port NAME=N pins. allowed_branches gains globs. A plain host apply of a template is refused (stack_template_requires_stack_up). Any apply refuses a hostname another project on the remote holds (hostname_owned_by_other_project). stack list shows slug, project, ref and commit, hostnames, ports, and last apply status and time."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Bring up a per-thread stack from the Hub template (Priority: P1)

An operator working on a PR thread has a checkout of the Hub repository whose
committed `sandbox.hosting.yml` is a stack template. They run one command naming
a slug, a ref, and a remote. Sandbox renders the template for that slug, checks
the ref's commit out into its own clean source, and starts the same durable,
guarded hosting delivery a hand-written manifest gets. The stack gets its own
project, hostnames, secrets and loopback ports, so it never collides with the
shared environment or another thread's stack.

**Why this priority**: This is the whole goal. Today every thread must copy and
hand-edit the manifest, which is slow and invites hostname, secret-name and port
collisions.

**Independent Test**: With a template fixture in a temporary Git checkout and a
synthetic remote, `stack up --plan` reports the rendered project, hostnames,
ports and the exact durable submission, and writes nothing. `stack up --confirm`
writes the rendered manifest under the controller runtime, creates the clean
source checkout at the resolved commit, records the stack, and submits exactly
one durable host-apply job.

**Acceptance Scenarios**:

1. **Given** a valid template and a local-only branch `stack/demo`, **When** the
   operator runs `stack up --name demo --ref stack/demo --remote R --plan`, **Then**
   the result names project `xspeed-hub-demo`, primary `hub-demo.example.test`, the
   allocated `mailpit` port in 18100-18199, the resolved 40-hex commit, `ref_branch`
   `stack/demo`, and the job submission it would make, and nothing on disk, in Git,
   in hosting state or on the remote changes.
2. **Given** the same inputs, **When** the operator runs `--confirm`, **Then** the
   rendered manifest exists at `runtime/stacks/demo/sandbox.hosting.yml` with
   `source_root` pointing at a clean source checkout of that commit, the stack
   record holds its port, and one durable job runs `host apply` for that manifest
   with every existing apply guard (clean source, recovery context and receipt,
   one apply per target).
3. **Given** a stack that is already up, **When** the operator re-runs `up` for a
   newer commit, **Then** the same ports are reused and a new durable apply is
   submitted for the new commit.
4. **Given** `--ref` names a commit rather than a branch, **When** the template's
   `allowed_branches` is `['{ref_branch}']`, **Then** `up` refuses before any
   change, because a detached commit has no branch to allow.
5. **Given** a stack whose previous apply job is still running, **When** the
   operator runs `up` again, **Then** it is refused and nothing changes.

---

### User Story 2 - Guard every apply against hostname collisions (Priority: P1)

Before any mutation, every hosting apply (a stack's or a hand-written manifest's)
refuses a hostname that a different project already holds on the same remote,
and names that project.

**Why this priority**: Per-thread stacks multiply the number of projects on one
remote. A typo in a slug, or a later cutover of the shared environment, must not
silently take over another project's hostname.

**Independent Test**: With recorded hosting state for project A on remote R that
serves `hub-a.example.test`, apply (or `stack up`) of project B declaring that
hostname fails with `hostname_owned_by_other_project` naming A, and no remote
command is run.

**Acceptance Scenarios**:

1. **Given** project A holds `admin-demo.example.test` on remote R, **When** project
   B is applied to R with that alias, **Then** the apply is refused with
   `hostname_owned_by_other_project` naming A before any remote write.
2. **Given** the same hostname held by project A on a different remote, **When**
   project B is applied to R, **Then** this check does not refuse it.
3. **Given** project A re-applies its own manifest, **When** its hostnames are
   unchanged, **Then** the check passes.

---

### User Story 3 - See and tear down stacks (Priority: P2)

The operator lists the stacks on a remote (slug, project, ref and commit,
hostnames, ports, last apply status and time) and tears one down with a single
confirmed command that uses the existing destroy path and then releases the
stack's ports and controller records.

**Why this priority**: Stacks are short-lived; without list and down they leak
ports, hostnames and source checkouts.

**Independent Test**: With two recorded stacks, `stack list --json` returns both
records with the listed fields. `stack down --name demo --confirm` calls the
existing destroy for `xspeed-hub-demo/<environment>` and, only when it succeeds,
removes the stack record (releasing its ports), its rendered manifest and its
source checkouts.

**Acceptance Scenarios**:

1. **Given** a recorded stack, **When** `stack down` runs without `--confirm`, **Then**
   it is refused and nothing changes.
2. **Given** destroy fails, **When** `stack down --confirm` runs, **Then** the stack
   record and its ports are kept and the destroy error is reported.
3. **Given** destroy succeeds, **When** the operator runs `stack list`, **Then** the
   stack is gone and its port can be allocated to a new stack.

---

### User Story 4 - Templates cannot be applied by accident (Priority: P2)

A template is not a deployable manifest. Every plain hosting command that reads a
manifest refuses a file with a `stack:` block or any stack placeholder with
`stack_template_requires_stack_up`, and points to `stack up`.

**Why this priority**: Without it, `sb host apply` of the Hub template would try to
deploy literal `{slug}` hostnames.

**Independent Test**: `host apply` (and `host plan` / `host validate`) of the
template exits non-zero with that code and opens no remote or state.

**Acceptance Scenarios**:

1. **Given** a template, **When** `host apply --confirm` runs on it, **Then** it fails
   with `stack_template_requires_stack_up` before any lock, state or remote access.
2. **Given** an ordinary manifest with no `stack:` block and no placeholders, **When**
   it is applied, **Then** behaviour is unchanged.

---

### User Story 5 - Branch globs in allowed_branches (Priority: P3)

`deploy.allowed_branches` accepts shell-style globs such as `feat/*` in any
manifest, while exact names and `"*"` keep their meaning.

**Independent Test**: An apply source check with branch `feat/x` passes for
`['feat/*']`, fails for `['main']`, and passes for `['*']`.

---

### Edge Cases

- A slug that does not match the template's `stack.slug` pattern, or any slug
  that would render an invalid project, hostname or environment name, is refused
  before anything is rendered.
- A placeholder in a field outside the allowed list, a brace pattern in an
  allowed field that is not one of the four placeholders, or `{port:<name>}` for a
  name not listed in `stack.ports`, is a template error.
- `--port NAME=N` for a name not declared in `stack.ports`, outside 1024-65535, or
  already bound on the remote by something other than this stack, or owned by
  another stack on the remote, is refused.
- An allocated range with no free port in 18100-18199 refuses `up`.
- A slug already recorded for a different remote on this controller is refused
  (the rendered manifest path is per slug).
- A `--ref` that does not resolve to a commit in the checkout is refused.
- An `up` whose job submission fails keeps the stack record and ports (they are
  released only by `down`) and reports the failure.
- `down` for a slug with no record on that remote refuses with a clear code.
- Existing stacks and manifests with literal ports and no `stack:` block are
  untouched.

## Requirements *(mandatory)*

### Functional Requirements

**Template schema**

- **FR-001**: A manifest with a top-level `stack:` mapping is a stack template.
  `stack.slug` is a regular expression the slug must fully match; `stack.ports`
  is a list of unique port names (lowercase letters, digits, hyphens).
- **FR-002**: Placeholders are `{slug}`, `{SLUG}` (slug upper-cased with `-`
  replaced by `_`), `{port:<name>}` (name declared in `stack.ports`), and
  `{ref_branch}` (the branch `--ref` was resolved from, empty for a detached
  commit).
- **FR-003**: Placeholders are allowed only in `project`, each environment's
  `host.primary`, each alias `hostname`, each `deploy.allowed_branches` entry,
  each `secrets.values` value, and each `secrets.generated` source name. A
  placeholder anywhere else, or any other brace pattern in those fields or in
  other manifest text, is a template error. The one exception is the existing
  basic-auth route parameter syntax, which keeps its own validation.
- **FR-004**: An `allowed_branches` entry that renders empty is a template error
  (so `['{ref_branch}']` refuses a detached commit).
- **FR-005**: After rendering, the result MUST pass the same manifest validation
  as a hand-written manifest.

**Refusing plain use of templates**

- **FR-006**: Every plain hosting command that reads a manifest (validate, plan,
  apply and the rest) MUST refuse a template or any manifest containing a stack
  placeholder with code `stack_template_requires_stack_up`, before any state,
  lock or remote access.

**Branch policy**

- **FR-007**: `deploy.allowed_branches` entries are matched as case-sensitive
  shell globs; exact names and `"*"` keep working.

**stack up**

- **FR-008**: `stack up` requires `--template`, `--name`, `--remote`, and exactly
  one of `--plan` or `--confirm`.
- **FR-009**: `--ref` resolves on the controller against the template's checkout
  to a full commit; it accepts a local branch (including one that exists only
  locally), a remote-tracking branch, or a commit. The default is the checkout's
  `HEAD`. `ref_branch` is the branch name (without a remote prefix) or empty.
- **FR-010**: Each `stack.ports` name gets one loopback port per stack on the
  remote, allocated from 18100-18199, distinct from every other stack's port on
  that remote and every recorded hosting loopback port, and not currently bound on
  the remote by anything other than this stack's own containers. The allocation
  is recorded with the stack and reused by every later `up`; only `down`
  releases it.
- **FR-011**: `--port NAME=N` pins a port; it is refused if N is bound on the
  remote by something other than this stack, or recorded for another stack on
  the remote.
- **FR-012**: Before any change, `up` MUST refuse a rendered hostname that another
  project already holds on the remote (FR-016).
- **FR-013**: `--plan` reports the rendered project, environment, hostnames,
  ports, ref, commit, rendered manifest path and the durable submission argv,
  and changes nothing (no files, no Git metadata, no stack record, no remote
  write; the remote port probe is read-only).
- **FR-014**: `--confirm` creates a clean source checkout of the resolved commit,
  writes the rendered manifest to `runtime/stacks/<slug>/sandbox.hosting.yml` on
  the controller with `source_root` pointing at that checkout, records the stack,
  and submits the existing durable `job-start` + `host apply` path from it with a
  replay-safe request identity. Every existing apply guard applies unchanged.
- **FR-015**: `up` refuses while the stack's previous apply job is still queued or
  running (`stack_apply_in_progress`).

**Hostname ownership**

- **FR-016**: Every hosting apply refuses, before any mutation, a declared
  hostname that a different project already holds on the same remote, with code
  `hostname_owned_by_other_project` and the owning project's name. Holdings are
  the hostnames recorded for that project's hosting targets and stacks on that
  remote.

**stack list / down**

- **FR-017**: `stack list --remote R` shows each stack's slug, project,
  environment, ref and commit, hostnames, ports, and last apply status and time
  (from the durable job when known).
- **FR-018**: `stack down --name S --remote R --confirm` tears the stack's hosting
  target down through the existing destroy path. Only after it succeeds (or the
  target is already absent) does it release the stack's ports and remove its
  record, rendered manifest and source checkouts. Without `--confirm` it refuses.

**Output**

- **FR-019**: Every stack command supports `--json`, and every refusal carries a
  stable code.

### Key Entities

- **Stack template**: a hosting manifest with a `stack:` block and placeholders.
- **Stack**: one rendered instance of a template on one remote, identified by
  slug; holds project, environment, template path, ref, ref branch, commit,
  hostnames, ports, rendered manifest path, source checkouts and its last
  submitted apply (request id, job id, time).
- **Stack port**: a named loopback port in 18100-18199 owned by one stack on one
  remote.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator brings up a new per-thread stack with one command and no
  manifest edits.
- **SC-002**: Two stacks on one remote never share a hostname or port; a
  collision is refused 100% of the time before any remote change.
- **SC-003**: `stack up --plan` leaves the controller, the Git checkout, hosting
  state and the remote byte-for-byte unchanged.
- **SC-004**: Existing manifests and hosted targets behave exactly as before
  (existing hosting tests pass unchanged apart from the known baseline failures).

## Assumptions

- Stack records live in controller-side Sandbox runtime state for the remote, like
  the existing per-remote hosting state; the remote holds only what the apply
  already puts there.
- A slug is unique per controller (the rendered manifest path is per slug).
- The source checkout for a stack is a detached Git worktree of the template's
  checkout under `runtime/stacks/<slug>/`, kept until a later `up` replaces it or
  `down` removes it, so an in-flight durable job and its recovery keep their
  source.
- Stack applies publish the source to the remote under an immutable per-commit ref
  rather than a branch, so a force-pushed PR branch never blocks a re-apply.
- Hostname ownership compares exact normalized hostnames; legacy targets that
  recorded no hostnames are compared through their recorded DNS rows.
- Tear-down uses the existing destroy path, which supports the nginx front door.
- Every apply still builds its Cloudflare client as today.
