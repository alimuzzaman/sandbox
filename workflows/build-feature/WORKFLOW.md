---
name: build-feature
description: Three-phase playbook for building a NEW feature in a focused plugin — establish (spec + impact + edge cases) → plan (file plan + slicing + reuse + rollout) → build (slice-by-slice with live verification). Generic across plugins. Routes into per-plugin scaffolding skills (add-block, add-shortcode, add-rest-endpoint, add-setting, add-elementor-widget) when applicable. Use when the user says "add", "build", "implement", or "create a new" X — NOT for bug fixes (use skills/fix/SKILL.md), NOT for trivial one-liners.
---

# build-feature — the canonical playbook for net-new feature work

**Load this workflow whenever the user asks you to *add*, *build*,
*implement*, or *create* a new feature inside a focused plugin.** Skip
it for bug fixes (use `skills/fix/SKILL.md` — one-pass loop), trivial
one-liners, and pure refactors.

Bug-fixing is reactive — reproduce, fix, verify, stop. Feature work is
constructive — define what done means, find what could break, slice
the build to de-risk, ship the smallest end-to-end thing first. The
two need different contracts; this is the feature contract.

---

## What this workflow optimizes for

The six things the user asked us to cover, in plain language:

1. **Edge cases** — name them at the start, verify each one before
   declaring done.
2. **How the feature works** — write a spec the user signs off on
   before any code runs.
3. **What could be broken** — impact analysis is a Phase 1 deliverable,
   not an afterthought.
4. **Efficiency** — Phase 2 audits what already exists and routes
   through it instead of reinventing. Three similar lines beats a
   premature abstraction.
5. **Speed** — out-of-scope list + size classification keep the
   feature from sprawling. Slicing surfaces integration bugs day 1,
   not day 14.
6. **Accuracy** — verify against the spec criteria, not against the
   diff. Live MCP call against the running stack is the only evidence
   that counts.

---

## The three phases (do not skip ahead)

You finish each phase with a structured output, then **wait for the
user's confirmation before starting the next phase**. The gates are
real — they exist because a wrong assumption at Phase 1 wastes the
entire Phase 3 effort.

---

### Phase 1 — ESTABLISH ("Do we actually want this, and what does done look like?")

Goal: capture the smallest spec that lets you measure "done" later.
You produce this **before reading any plugin code beyond a single
focus_get call to load conventions**. Code reading is Phase 2.

Emit the spec as **natural readable prose with bold section headers**,
NOT as a fenced code block. Code-fenced output renders as monospace and
looks like a code dump in chat UIs — this is the only thing the user
will actually read, so it has to be readable. Use this shape:

> **PHASE 1 — ESTABLISH**
>
> **Feature:** <verb-led plain English, what a teammate would naturally
> say — "Show view counts on PDF embeds," not "Visitor-facing view
> counter system." NOT a PM ticket title.>
>
> **Size:** S | M | L
>   *S = one file or one surface; M = 3-5 files, one layer; L = 3+ layers (DB + backend + REST + UI, etc.)*
>
> **What it does** — one or two sentences in plain English, verb-led.
> "Adds a small label next to PDF embeds showing the view count from the
> existing analytics table."
>
> **Why we want it** — either a capability add ("users want a popularity
> signal on their embeds") or a problem being solved ("X is hurting
> today because Y") — BOTH are valid framings. Don't force a "problem"
> story for a pure capability add.
>
> **Audience** — who benefits (end visitor, plugin admin, plugin author,
> integrator).
>
> **Trigger** — what made the user ask (one-line: user request, support
> ticket, FluentBoards card #, conversation).
>
> **Success criteria** *(testable, live-verifiable)*
> - 2-4 concrete statements. "Visiting URL X with embed Y shows a label
>   matching the DB count." Each must be verifiable by an MCP call later.
>
> **Out of scope** *(NOT building in this pass)*
> - Explicit list. Future passes can pick these up.
>
> **Impact / what could break**
> - One row per surface that changes. Include BC traps (Gutenberg
>   save(), schema, hook signatures), performance (new queries, N+1),
>   security (new input boundaries, auth paths). For each, one-line
>   risk + mitigation, or "no risk because X."
>
> **Edge cases — committed to handling**
> - Only the rows that genuinely apply. Don't pad.
> - Empty state, RTL/i18n, multisite, license (free vs Pro), cache
>   invalidation, concurrency — pick whichever apply, mark "n/a" or
>   "out of scope for v1" otherwise.

After emitting the spec, decide what happens next based on **Size**:

- **Size S** → no gate. Announce ("Size S → no gate. Proceeding through
  Phase 2 and Phase 3 autonomously.") and start Phase 2 immediately.
  Single final report at the end. The user can always interrupt.
- **Size M** → one gate, right here. End with "Waiting on your go before
  Phase 2." When confirmed, run Phase 2 + Phase 3 autonomously.
- **Size L** → one gate here, one more after Phase 2. End with "Waiting
  on your go before Phase 2." When confirmed, run Phase 2 and pause
  for a second sign-off before Phase 3.

The user can override mid-stream at any time ("stop," "change X,"
"actually this is bigger than M, treat it as L"). Gates are formal
pause points, not the only feedback channel.

---

### Phase 2 — PLAN ("How do we build it, efficiently?")

Now you read the relevant code surfaces — but in **one pass**, like the
fix skill's step 2. Grep the focused plugin (AND its `-pro` sibling if
one exists — Pro commonly overrides free's rewrite rules, hooks, REST
handlers; ignoring Pro at this step costs an entire iteration later)
for every function, hook, block name, REST route, asset handle,
template partial, setting key relevant to the feature.

**Cross-surface features** (anything that touches block + shortcode +
Elementor widget, or any other combination of two-or-more render
paths) need an extra pass: grep EVERY emit point of the shared
attribute or class name across all surfaces, not just the one you
started with. Different surfaces often emit subtly different values —
the block might use `data-embed-type="PDF"`, the shortcode
`data-embed-type="document_pdf"`, and the Elementor widget puts the
attribute on the iframe instead of the wrapper. Discovering this in
Phase 2 saves a full Phase 3 iteration where the feature works on
one surface and silently breaks on another.

Emit the plan as **prose with bold section headers**, same as Phase 1
— not a fenced code block. Use this shape:

> **PHASE 2 — PLAN**
>
> **Reuse audit** *(what already exists; we ride on it)*
> - Helpers, hooks, blocks, settings, REST routes we'll extend or read
>   from. Include `file:line` references.
> - Plugin scaffolding skills that apply (e.g.
>   `.claude/skills/add-block/SKILL.md`). Phase 3 will follow them.
> - If this list is empty, you may be reinventing — pause and re-grep.
>
> **File-level edit plan**
> - Every file, every change. No "and possibly other files." Be
>   specific: "`<path>`: new function `foo()`," "new block attribute
>   `showViewCount`," etc.
>
> **Slicing strategy** *(required for Size L; recommended for M when
> 3+ layers are touched; skip for S)*
> - Slice 1 (thin vertical): smallest end-to-end thing that proves the
>   integration works → verify with `<MCP call>`.
> - Slice 2: next vertical increment → verify with `<MCP call>`.
> - "Thin vertical" = touches every layer (DB → backend → API → UI) for
>   the simplest case. Integration bugs surface day 1, not day 14.
>
> **BC strategy** *(match every BC trap from Phase 1)*
> - Gutenberg `save()` changes: conditional emission pattern, or
>   `deprecated[]` entry at `<path>`, or "no save() change."
> - Schema migration: migration class location + fresh AND upgrade-path
>   test plan, or "no schema change."
> - Hook signature changes: "none" or "new hook, documented at `<path>`."
>
> **Rollout**
> - Settings toggle (default off + key name + location), or "always-on
>   for v1."
> - Version gate (activate behind plugin >= X.Y.Z; flush rewrite rules
>   on upgrade if rules changed).
> - Free vs Pro gating (free renders skeleton + upsell / Pro implements
>   full behavior / n/a).
> - Draft changelog entry: one-line user-facing string for release notes.
>
> **Verification plan** *(one row per Phase 1 success criterion + one
> per edge case)*
> - Criterion → MCP call that proves it live.
> - Edge case → MCP call or "deferred (see out-of-scope)."

After emitting the plan, decide what happens next based on **Size**
(from Phase 1):

- **Size S** → there is no Phase 2 pause. You came here autonomously
  after announcing the auto-proceed; now run Phase 3 immediately.
- **Size M** → there is no Phase 2 gate. Run Phase 3 immediately.
- **Size L** → second and final gate. End with "Waiting on your go
  before Phase 3." When confirmed, run Phase 3.

---

### Phase 3 — BUILD (slice by slice, live-verify each)

For each slice from Phase 2:

1. Apply all edits for that slice in one pass (NOT one file at a time
   with verify between — that's the bug-fix slicing anti-pattern; here
   you batch edits within a slice).
2. Run the slice's verification MCP call from the plan.
3. If it fails: diagnose, fix, re-verify within the same slice. Do
   NOT advance until the slice's verify is green.
4. If it passes: tail_log to catch warnings, then advance to next slice.
5. Apply non-negotiables on every Edit:
   - Auth: nonce + capability on every handler
   - Sanitize input / escape output; SQL through $wpdb->prepare only
   - Prefix every option/transient/post-meta/hook/JS handle/CSS handle with the plugin slug
   - WP APIs over raw PHP (wp_remote_get not curl; wp_enqueue_script not inline <script>)
   - Sandbox control-plane features register through the schema/adapter/command/MCP
     manifests, use repository and bounded-service contracts, and preflight
     capabilities before side effects. Never extend a compatibility facade or read
     registry/state files directly.

After the last slice, run the FULL VERIFICATION PLAN from Phase 2 —
every success criterion + every edge case row. Each must produce
EVIDENCE from a live MCP call.

Emit the final report as **prose with bold section headers**, NOT a
fenced code block. Use this shape:

> **STATUS: SHIPPED**
>
> **Summary** — one line on what now works that didn't before.
>
> **Files**
> - `<path>` — one-line description per file.
>
> **Evidence** *(one row per Phase 1 success criterion + one per edge
> case)*
> - Criterion: MCP command + observed output, trimmed.
> - Edge case: MCP command + observed output, or "deferred — see below."
>
> **Deferred** *(from Phase 1 Out of scope, restated so the user sees
> what they're not getting)*
> - Items punted to a future pass.
>
> **Spec drift** *(include ONLY if the user redirected mid-build, or
> Phase 2/3 discovered something that changed what shipped vs. what
> Phase 1 committed to. Omit entirely if the build matched the spec
> as confirmed.)*
> - What Phase 1 said: ...
> - What shipped instead: ...
> - Why: user redirect / Phase 2 discovery / code reality forced it.
>
> **Rollout notes**
> - Toggle: key name + default, or "always-on for v1."
> - Version gate: triggers on upgrade to X.Y.Z (if applicable).
> - Flush required on existing installs: yes/no (flag if rewrite rules
>   changed).
>
> **Next** *(what the human needs to do)*
> - Branch + commit (suggested branch name: `feat/<short-slug>`).
> - Update changelog with the draft entry from Phase 2.
> - Open PR / move FluentBoards card / etc. — user's call.

If you cannot complete a slice and verification fails for reasons
outside the spec, emit instead:

> **STATUS: BLOCKED**
>
> **Reason** — one sentence on what stopped you.
>
> **Progress** — slices completed, slices remaining.
>
> **Next** — what the user needs to decide or supply.

---

## After SHIPPED — optional retro

If the build surfaced anything non-obvious — a cross-plugin runtime
quirk, a BC trap that almost fired, a helper you wish existed —
write a short note to `memory/feature-history/<feature-slug>.md` so the
team learns from it. Skip if the build was unremarkable.

---

## Anti-patterns that invalidate the workflow

- **Skipping the size-appropriate gate.** Gates are scaled to feature
  size — Size S has none, Size M has one (after Phase 1), Size L has
  two (after Phase 1 + Phase 2). The right number of confirmations for
  the right size matters: skipping a Size L plan gate is risky, and
  forcing a Size S to pause is friction. Honor the rule for the size
  you declared in Phase 1.
- **Phase 1 success criteria you can't verify with an MCP call.** "It
  feels right" is not a criterion. "When you click X, response body
  contains Y" is.
- **Empty REUSE AUDIT.** If the plugin has 30 helpers and you're
  inventing a 31st, you didn't read enough. Pause and re-grep.
- **Slicing horizontally** ("first I'll build all the DB, then all
  the REST, then all the UI"). Wrong direction. Slice vertically — a
  thin end-to-end through every layer first, then thicken it. This is
  the single most important pattern in the workflow.
- **Speculative scaffolding.** Dead flags, "for later" hooks, error
  handling for cases that can't happen, wrapper functions that add
  nothing over WP core. If it's not on the success criteria list or
  the BC strategy list, it doesn't ship in this feature.
- **Skipping Phase 1 because "this is small."** If it's truly that
  small (one file, one line change), it's a fix or a one-shot, not a
  feature. Use the right tool. But if there's any question, the spec
  costs nothing and prevents scope creep.

---

## When to use this vs. other skills

| Situation | Tool |
|---|---|
| Bug, error, "X doesn't work" | `skills/fix/SKILL.md` (one-pass) |
| Trivial one-liner (rename, comment) | Direct edit, no workflow |
| Refactor existing code without changing behavior | Direct edit + verify-once |
| Add a new block/shortcode/REST/widget/setting | `build-feature` → routes through the plugin's `.claude/skills/add-*/SKILL.md` |
| Build a new multi-layer feature from scratch | `build-feature` (size L, full slicing) |
| Cross-plugin integration | `build-feature` (grep BOTH repos in Phase 2) |
| New admin page, new CLI subcommand, new MCP tool | `build-feature` (treat the sandbox itself as the plugin) |

The workflow is intentionally generic — it doesn't assume Gutenberg or
Elementor or any specific plugin. It works for any WPDeveloper plugin
the focused dev is in, and it works for non-WP code (the sandbox CLI
itself) when extended that way.
