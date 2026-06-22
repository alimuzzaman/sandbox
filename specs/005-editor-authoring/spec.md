# Feature Specification: AI Editor Authoring — Elementor/EA Widgets + Gutenberg/EB Blocks

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Let the agent programmatically insert and modify page-builder
content — Elementor (with Essential Addons widgets) and Gutenberg (with Essential Blocks) —
producing valid, correctly-styled output; create the skills/schemas for EA and EB; learn
from Elementor's Angie SDK / editor-mcp and comparable projects; determine whether EL/EA/EB
expose the WP Abilities API."

## Context

Agents can edit core blocks by hand-authoring markup, but page-builder content
breaks: Elementor needs a structured element tree + CSS regeneration, and Gutenberg
re-validates static/third-party blocks against their own save logic, flagging
hand-written markup as "invalid/recovery". This feature gives agents reliable
insert/modify operations for **Elementor (incl. Essential Addons)** and **Gutenberg
(incl. Essential Blocks)** that produce content which renders correctly, is styled,
and stays editable in the real editor — plus machine-readable schemas of EA widgets
and EB blocks, and skill packs teaching the recipes.

The supporting research (data models, how comparable projects do it, the
WP-Abilities determination, in-house prior art) lives in
[research.md](./research.md). Implementation detail (concrete ability/tool names,
the finalizer mechanics, CSS-regen calls, file paths) is deferred to `plan.md`.

## Clarifications

### Session 2026-06-22

- Q: Which page-builder engine ships first, and do we commit to both? → A: Both — **Gutenberg/EB first** (our own blocks; the real-editor finalizer is the hardest, highest-value piece and the biggest ecosystem gap), then Elementor/EA.
- Q: How is the editor authoring engine exposed to agents? → A: As **WP abilities** on the spec 003 in-instance Abilities layer; **spec 005 depends on spec 003**. Reachable by external MCP clients and, via the host-side proxy, in-session.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Insert an Essential Addons widget (Priority: P1)

An agent adds an EA widget to an Elementor page; it renders, is styled, and opens
cleanly in the Elementor editor.

**Why this priority**: Inserting a widget correctly is the core Elementor capability;
everything else (modify, schema) supports it.

**Independent Test**: Insert one EA widget into a page and confirm it renders on the
frontend, is styled, and the editor opens it without errors.

**Acceptance Scenarios**:

1. **Given** a page and a chosen EA widget with settings, **When** the agent inserts
   it, **Then** the widget appears on the rendered page, styled, and editable in the
   editor with no errors.
2. **Given** a widget that is not currently enabled in the builder, **When** insert
   is requested, **Then** the system enables it first and verifies the inserted node
   survived (it is not silently dropped).
3. **Given** a full-width layout is intended, **When** the page is authored, **Then**
   the page renders full-width (not constrained inside the theme container).

### User Story 2 — Modify a widget's settings (Priority: P1)

An agent changes settings on an existing widget without corrupting the rest of the
page.

**Why this priority**: Iterative editing is as important as insertion; multi-turn
edits must not break the page.

**Independent Test**: Locate an existing widget by its identity, change a setting, and
confirm the change renders and the page is otherwise intact.

**Acceptance Scenarios**:

1. **Given** an existing widget, **When** the agent updates a setting, **Then** the
   change is applied (located by identity, not position) and re-rendered, with styling
   regenerated.
2. **Given** complex settings (responsive, typography, media, repeaters), **When**
   updated, **Then** they round-trip correctly and media references resolve (not blank).

### User Story 3 — Insert an Essential Blocks block (Priority: P1)

An agent adds an EB block to a post; it renders correctly with no editor validation
error.

**Why this priority**: EB is WPDeveloper's own Gutenberg product and the primary
target; getting a block in validly is the core Gutenberg capability.

**Independent Test**: Insert an EB block into a post and confirm it renders and the
editor shows no "invalid/recovery" warning.

**Acceptance Scenarios**:

1. **Given** a post and a chosen EB block with attributes, **When** the agent inserts
   it, **Then** it renders correctly and carries a unique block identity (no
   duplicate-id styling bleed).
2. **Given** the block, **When** opened in the editor, **Then** it is valid (no
   "unexpected/invalid content" recovery prompt).

### User Story 4 — Inserted blocks are correctly styled (Priority: P1)

EB blocks an agent inserts come out styled, not bare HTML.

**Why this priority**: EB stores per-block CSS that the server never recomputes from
raw style attributes; an unstyled block is a broken result.

**Independent Test**: Insert a styled EB block and confirm the frontend shows its
styling, not just structural markup.

**Acceptance Scenarios**:

1. **Given** a block with styling, **When** inserted, **Then** the rendered page shows
   the block styled.
2. **Given** a static/third-party block, **When** inserted, **Then** the saved content
   matches what the block's own save logic would produce (valid from first save).

### User Story 5 — Real-editor finalization for stateful blocks (Priority: P1)

For blocks whose validity/styling can only be produced by the real editor runtime,
the system finalizes them through a real (headless) editor with no human step.

**Why this priority**: This is the only robust way to make static/third-party blocks
valid-and-styled from first save; it's the hardest, highest-value piece.

**Independent Test**: Queue an attribute-level block spec, let the finalizer process
it headlessly, and confirm the post ends up with valid, styled content.

**Acceptance Scenarios**:

1. **Given** an attribute-level block spec (not raw markup), **When** the agent
   submits it, **Then** a headless editor serializes and validates it and writes back
   valid content, generating the block's styling as a side effect.
2. **Given** finalization is in progress, **When** the agent checks, **Then** it can
   observe completion headlessly (no human action required).

### User Story 6 — Discover available widgets/blocks and their settings (Priority: P2)

An agent looks up which EA widgets / EB blocks exist on the instance and their
settings/attributes (names, types, defaults).

**Why this priority**: Enables correct authoring without guessing; supports the insert/
modify stories.

**Independent Test**: Request the schema for one widget/block and for the full set,
and confirm accurate names/attributes are returned.

**Acceptance Scenarios**:

1. **Given** a running instance, **When** the agent requests a widget/block schema,
   **Then** it gets that item's settings/attributes with types and defaults,
   introspected live.
2. **When** requested without a name, **Then** it lists all registered EA widgets / EB
   blocks present on the instance.

### Edge Cases

- All-raw-HTML content is refused — the agent is steered to registered blocks/widgets.
- Deprecated/legacy widgets or blocks are refused with a suggested current replacement.
- Parent/child blocks (e.g. accordion → item) carry the correct parent linkage when
  authored.
- Destructive operations (delete widget/block, reset settings) are flagged and gated;
  read/list operations are read-only.
- Concurrent edits to the same post are detected (base-state check) rather than
  silently overwriting.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST insert, modify, read, and delete Elementor elements
  (including Essential Addons widgets) such that results render on the frontend, are
  styled, and remain valid/editable in the Elementor editor.
- **FR-002**: Elementor authoring MUST generate canonical element identities, enable a
  required widget before use and verify the node survived, regenerate per-element
  styling, set the page template when full-width is intended, and resolve media
  references fully.
- **FR-003**: Elementor edits MUST locate elements by identity (not position) and read
  current state before writing, so multi-turn edits don't corrupt the page.
- **FR-004**: The system MUST insert, modify, read, and delete Gutenberg blocks
  (including Essential Blocks) via structured parse → mutate → re-serialize, addressing
  blocks by identity, never by raw string concatenation.
- **FR-005**: For static/third-party Gutenberg blocks, the system MUST produce content
  that is valid from first save and correctly styled — using a real-editor finalizer
  when needed — and MUST observe finalization completion headlessly.
- **FR-006**: Gutenberg authoring MUST enforce unique per-block identity, carry the
  per-block styling EB requires, and preserve parent/child linkage for nested blocks.
- **FR-007**: The system MUST refuse all-raw-HTML content and refuse deprecated
  widgets/blocks with a suggested replacement.
- **FR-008**: The system MUST expose live schemas of EA widgets and EB blocks (names,
  settings/attributes, types, defaults), both per-item and as a full listing.
- **FR-009**: The system MUST ship skill packs teaching the Elementor/EA and
  Gutenberg/EB authoring recipes and gotchas, discoverable when EA/EB is the focused
  plugin.
- **FR-010**: Authoring operations MUST be exposed as abilities on the in-instance
  Abilities layer (spec 003), reachable by external clients and in-session via the
  host-side proxy.
- **FR-011**: Every operation MUST enforce a capability check; destructive operations
  MUST be flagged destructive (eligible for confirmation) and read operations
  read-only.
- **FR-012**: Delivery MUST sequence Gutenberg/EB first, then Elementor/EA.

### Key Entities

- **Element/Widget node**: an Elementor tree node (section/column/container/widget)
  with identity and settings; the unit of insert/modify.
- **Block**: a Gutenberg block with name, attributes, identity, per-block styling, and
  optional parent linkage.
- **Block spec**: an attribute-level description of intended blocks submitted to the
  finalizer (not raw markup).
- **Schema**: the introspected catalog of a widget/block's settings/attributes.
- **Finalization job**: a queued request the headless editor processes to produce
  valid/styled block content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An inserted EA widget renders, is styled, and opens in the editor with
  zero validation errors.
- **SC-002**: A modified widget setting persists and re-renders without disturbing any
  other element on the page.
- **SC-003**: An inserted EB block produces zero "invalid/recovery" prompts in the
  editor and is visibly styled on the frontend.
- **SC-004**: A static/third-party block authored via the finalizer round-trips to
  valid, styled content with no human step.
- **SC-005**: A schema request returns accurate settings/attributes for a chosen
  widget/block and a complete list for the instance.
- **SC-006**: Multi-turn edits to the same page never corrupt unrelated elements in
  test (address-by-identity + read-before-write).

## Assumptions

- **Depends on spec 003** (the in-instance Abilities layer) — the engine registers as
  abilities there.
- The target builders are WPDeveloper's own (Essential Addons, Essential Blocks) plus
  Elementor/Gutenberg core; Elementor Pro/EA/EB do not expose the WP Abilities API
  (only Elementor core does, behind a hidden experiment that can't write the element
  tree — see [research.md](./research.md)).
- The existing in-house `skills/wp-pilot/recipes/` (already on this branch) are the
  starting point and are re-architected into the ability engine; their element-ID
  generation is corrected to the canonical format.
- Essential Blocks' control sources are a git submodule that must be checked out for
  complete schema introspection.
- Builders beyond Elementor + Gutenberg (Bricks/Divi/Oxygen) are out of scope for v1.
