# Feature Specification: EB-Aware Attribute-Schema Resolver for `editor-schema`

**Feature Branch**: `011-eb-attribute-schema`

**Created**: 2026-06-25

**Status**: Draft

**Input**: User description: "EB-aware attribute-schema resolver for the sandbox `editor-schema` ability — Essential Blocks blocks return only 3 generic attributes because EB declares 0 attributes in block.json and assembles the real set (hundreds) at JS runtime; resolve the full set from the EB source checkout when available, and report fidelity honestly when it is not."

## Clarifications

### Session 2026-06-25

- Q: When the installed EB build lacks attribute source, where should the resolver look for the full source? → A: Scan the developer's configured plugin-home directories for an EB checkout that has `src/blocks` + the controls helper (independent of the installed build).
- Q: Should the resolved full attribute set be cached or recomputed each call? → A: Cache it, keyed by block + source version/mtime, and invalidate when the source checkout changes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Full EB attribute discovery when source is available (Priority: P1)

An agent (or developer) authoring Essential Blocks content asks `editor-schema` for a
specific EB block (e.g. `essential-blocks/advanced-heading`). The block's real attribute
set — every controllable attribute name, its type, and its default — is returned, not just
the handful of generic wrapper keys. The agent can now set the correct attribute (e.g.
`titleText`, not a guessed `title`) and the block renders as intended on the first pass.

**Why this priority**: This is the entire point of the feature. Today the schema returns
~3 attributes for a block that has ~787; the agent is blind to >99% of the block's surface,
which is the direct cause of empty-render bugs. Without P1 there is no feature.

**Independent Test**: Request `editor-schema` for `essential-blocks/advanced-heading` on an
instance where the EB source checkout is present. Confirm the returned attribute count and
that named attributes (`titleText`, `tagName`, typography/dimension/border/background keys)
are present with types and defaults — verified live against the running stack.

**Acceptance Scenarios**:

1. **Given** an EB source checkout is discoverable for the focused project, **When** a caller
   requests the schema for `essential-blocks/advanced-heading`, **Then** the response includes
   the block's full attribute set (well into the hundreds), each entry carrying name + type +
   default where the source defines one.
2. **Given** the same checkout, **When** a caller requests the schema for any EB block whose
   `src/blocks/<name>/src/attributes.js` exists, **Then** the explicit attributes AND the
   attributes contributed by generator helpers (typography, dimensions, border/shadow,
   background, responsive range, responsive align) are all included.
3. **Given** a successful full resolution, **When** the response is returned, **Then** it
   states the achieved fidelity level and the total attribute count.

---

### User Story 2 - Honest fidelity reporting when source is unavailable (Priority: P1)

An agent requests `editor-schema` for an EB block on an instance running only the
distributed (`.org`) build, which ships no resolvable attribute source. Instead of silently
returning a misleading 3-attribute schema as if it were complete, the response clearly signals
that the result is reduced-fidelity and explains why, so the agent knows not to trust the
short list and can take an alternative path.

**Why this priority**: A silently-wrong "complete" schema is worse than a flagged partial one —
it is what produced the original false "production ready" conclusion. Honest degradation is a
correctness requirement, not a nicety, so it shares P1.

**Independent Test**: Request `editor-schema` for an EB block on an instance with no EB source
checkout available. Confirm the response is explicitly marked reduced-fidelity, names the
reason, and does not present the generic keys as the full set.

**Acceptance Scenarios**:

1. **Given** no EB source checkout is discoverable, **When** a caller requests an EB block's
   schema, **Then** the response is returned with an explicit reduced-fidelity indicator and a
   human-readable reason, preserving today's behavior.
2. **Given** a partial resolution (e.g. the block's attributes file is found but one or more
   generator helpers cannot be resolved), **When** the response is returned, **Then** it
   reports partial fidelity and indicates what could not be resolved, rather than claiming full
   fidelity.

---

### User Story 3 - No regression for Elementor and core blocks (Priority: P2)

A caller continues to use `editor-schema` for Elementor/Essential Addons widgets and for core
Gutenberg blocks. These already resolve at full fidelity and MUST behave exactly as before —
same shape, same counts, same fields — because the change is additive and EB-scoped.

**Why this priority**: Protecting existing behavior matters but is secondary to delivering the
EB fix; it is a guardrail on the P1 work rather than new value.

**Independent Test**: Request schemas for an Elementor widget (e.g. `eael-info-box`) and a core
block (e.g. `core/heading`) before and after the change; confirm identical results.

**Acceptance Scenarios**:

1. **Given** a request for an Elementor/EA widget schema, **When** resolved, **Then** the result
   is unchanged from current behavior (full control set with ids and defaults).
2. **Given** a request for a core Gutenberg block schema, **When** resolved, **Then** the result
   is unchanged (full block.json attribute set).

---

### Edge Cases

- **Pro blocks**: An EB Pro block whose attributes live in the `essential-blocks-pro` source —
  resolved at full fidelity when that checkout is also discoverable; otherwise reported as
  reduced/partial for that block.
- **Nested generator spreads**: A generator helper that itself spreads another helper (e.g.
  border/shadow spreads inner dimension sets) must contribute the nested attributes too, so the
  count is not undercounted.
- **Unknown or future generator helper**: A block references a generator the resolver does not
  recognize — the known attributes are still returned and the response reports partial fidelity
  naming the unresolved helper, rather than failing outright.
- **Block requested but absent from source**: A block name with no matching
  `src/blocks/<name>/src/attributes.js` — handled as reduced fidelity for that block (not an error).
- **Source checkout present but stale/mismatched version**: The resolved attribute set may differ
  from the installed build; the response notes that the schema reflects the source checkout, not
  necessarily the running build, and identifies which checkout was used.
- **Multiple/ambiguous checkouts found**: More than one EB checkout exists in the configured
  plugin-home directories (e.g. a fork or a worktree) — the resolver picks one deterministically,
  reports which checkout it resolved from, so an unexpected match is visible rather than silent.
- **List vs single-block requests**: A whole-builder listing (`eb_only`) must not become
  prohibitively slow by deep-resolving every block; per-block full resolution is expected on a
  named-block request.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When asked for the schema of a named Essential Blocks block and a resolvable EB
  source checkout is available, the system MUST return the block's complete attribute set —
  every attribute name with its type and, where defined in source, its default value.
- **FR-002**: The system MUST include attributes contributed by generator helpers (typography,
  dimensions, border/shadow, background, responsive range, responsive align, and any further
  helpers a block uses), expanding each helper to the same attribute names it produces at runtime,
  including helpers nested inside other helpers.
- **FR-003**: Every EB schema response MUST state the achieved fidelity level (e.g. full, partial,
  reduced) and the total attribute count, so a caller can judge how much to trust it.
- **FR-004**: When no resolvable EB source checkout is available, the system MUST return a
  reduced-fidelity result with a human-readable reason and MUST NOT present the generic wrapper
  keys as if they were the complete attribute set.
- **FR-005**: When a block's attribute source is found but one or more contributing helpers cannot
  be resolved, the system MUST report partial fidelity and indicate what was not resolved, rather
  than claiming full fidelity or failing the whole request.
- **FR-006**: The system MUST locate the EB source checkout by scanning the developer's
  configured plugin-home directories for an Essential Blocks checkout that contains the per-block
  attribute source (`src/blocks/<name>/src/attributes.js`) and the controls helper package —
  independent of which build is installed in the instance, and without the caller passing a
  filesystem path. When multiple candidate checkouts exist, it MUST choose deterministically and
  record which checkout was used.
- **FR-007**: The change MUST be additive and EB-scoped: schema results for Elementor/Essential
  Addons widgets and for core Gutenberg blocks MUST be byte-for-byte unchanged.
- **FR-008**: EB Pro blocks MUST resolve at full fidelity when the corresponding Pro source
  checkout is discoverable, using the same mechanism as free blocks.
- **FR-009**: The resolver MUST be safe to call repeatedly with the same result (idempotent reads;
  no mutation of plugin source or instance state).
- **FR-010**: A whole-builder EB listing request MUST remain usable in performance, deferring
  expensive full per-block resolution to named-block requests unless full resolution is explicitly
  requested. Listing latency MUST be no worse than the pre-feature listing.
- **FR-011**: The system MUST cache the resolved full attribute set, keyed by block plus a
  fingerprint of the source checkout (e.g. version/mtime), and MUST invalidate and recompute when
  that source changes, so repeat requests are fast while never serving a stale schema after the
  source checkout is updated.

### Key Entities *(include if feature involves data)*

- **Attribute schema entry**: One controllable attribute of a block — its name, value type, and
  default (when defined). The unit the caller ultimately consumes.
- **Block attribute source**: The per-block declaration that lists explicit attributes and the
  generator helpers it pulls in; the authoritative description of a block's real attribute surface.
- **Generator helper**: A reusable producer of a deterministic set of attribute entries from a
  prefix/control name (e.g. a typography group expands to a fixed family of keys across viewports).
- **Fidelity report**: The metadata accompanying every EB schema response — achieved level
  (full/partial/reduced), reason for any shortfall, and total attribute count.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For `essential-blocks/advanced-heading` with the EB source checkout present,
  `editor-schema` returns at least 700 attributes (up from 3 today), each with a name and type.
- **SC-002**: The content attribute `titleText` (and other named keys such as `tagName`) appear in
  the returned schema for that block — the keys an agent must set for a non-empty render.
- **SC-003**: 100% of EB schema responses carry an explicit fidelity level and an attribute count;
  no EB response presents only the generic wrapper keys without a reduced-fidelity indicator.
- **SC-004**: For at least 10 different EB blocks with source present, the returned attribute set
  includes every explicitly-declared attribute plus all generator-contributed attributes (verified
  against the block's source declaration) with zero blocks silently truncated to the generic keys.
- **SC-005**: Schema results for a representative Elementor widget and a representative core block
  are identical before and after the change (no regression).
- **SC-006**: An agent using the returned schema to author an EB block via `gutenberg-insert`
  produces a block that renders non-empty content on the live frontend on the first attempt.

## Assumptions

- The agent/developer driving `editor-schema` is the primary user; "stakeholder value" here is
  correct, trustworthy schema output that prevents empty-render authoring bugs.
- EB source checkouts (free and Pro) may be present on the developer's machine as git checkouts
  with `src/blocks/<name>/src/attributes.js` and the `@essential-blocks/controls` helper package
  available; the instance's installed plugin may be the distributed build that lacks them. The
  resolver discovers the source by scanning the developer's configured plugin-home directories for
  an EB checkout that has the per-block attribute source, rather than relying on a hardcoded path
  or the installed build.
- Generator helpers produce deterministic attribute-name families from their inputs, so their
  output can be expanded from the helper definitions without executing the editor at runtime.
- The schema reflects the resolved source checkout; when the checkout and the installed build
  differ in version, minor attribute differences are acceptable and noted, not treated as errors.
- This feature changes only the `editor-schema` ability's EB path; the insert/update/get/delete/
  finalize abilities and the Elementor and core-block schema paths are unchanged.
- Per the constitution, "done" means the above outcomes are demonstrated by live `editor-schema`
  calls against a running instance, captured as evidence — not by code review alone.
