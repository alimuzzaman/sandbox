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

Emit this block, verbatim shape, then stop:

```
PHASE 1 — ESTABLISH

FEATURE: <one-line title>
SIZE: <S | M | L>     # S = single file or surface, M = 3+ files one layer, L = 3+ layers (DB + REST + UI etc.)

INTENT
- Problem: <what's hurting today, in one sentence>
- Audience: <who's it for — end user, plugin author, admin, integrator>
- Trigger: <what made you ask — support ticket, FluentBoards card, conversation>

SUCCESS CRITERIA (testable, live-verifiable)
- <statement 1 — "X URL returns Y", "block Z saves attribute W", etc.>
- <statement 2>
- <statement 3>  # 2-4 max; if you can't name them concretely, the feature isn't ready to build

OUT OF SCOPE (we are NOT building these in this pass)
- <thing 1>
- <thing 2>

IMPACT / WHAT COULD BREAK
- <existing surface 1>: <one-line risk + mitigation, or "no risk because X">
- <existing surface 2>: ...
- BC traps: <Gutenberg save() change? schema migration? hook signature? — name them, or "none">
- Performance: <new N+1? new heavy query? — name them, or "negligible">
- Security: <new input boundary? new auth path? — name them, or "none new">

EDGE CASES — committed to handling in this build
- Empty state: <how we handle | out of scope for v1>
- RTL / i18n: <strings translatable, layout mirrored | out of scope>
- Multisite: <per-site or network-wide | out of scope>
- License (free vs Pro): <gated where, fallback for free | out of scope>
- Cache / object cache: <invalidation hooks | not applicable>
- Concurrency: <race condition handling | not applicable>
- # Only include rows that genuinely apply. Don't pad.

# WAIT FOR USER SIGN-OFF before Phase 2.
```

**Gate 1.** The user reads, says "yes do this," or kicks back with
corrections. No `Grep`, no `Read` past the plugin's CLAUDE.md, no
`Edit` until they confirm. If they redirect ("don't gate behind a
setting, make it always on"), update the block and re-confirm.

---

### Phase 2 — PLAN ("How do we build it, efficiently?")

Now you read the relevant code surfaces — but in **one pass**, like the
fix skill's step 2. Grep the focused plugin (AND its `-pro` sibling if
one exists — Pro commonly overrides free's rewrite rules, hooks, REST
handlers; ignoring Pro at this step costs an entire iteration later)
for every function, hook, block name, REST route, asset handle,
template partial, setting key relevant to the feature.

Then emit:

```
PHASE 2 — PLAN

REUSE AUDIT (what already exists; we ride on it)
- <helper or hook or block we'll extend>: <file:line>
- <existing setting / option we'll read>: <key>
- <existing REST route we'll piggyback on, or "n/a, new route">
- <existing block scaffolding skill that applies>: e.g. .claude/skills/add-block/SKILL.md
- # If reuse audit is empty, you may be reinventing — pause and reconsider.

ROUTING INTO PLUGIN SCAFFOLDING (when applicable)
- This feature touches a <block | shortcode | REST endpoint | setting | Elementor widget>.
- Plugin ships <skill-name> for this scaffold at <path>. Phase 3 will follow it.
- # If the plugin has no scaffolding skill for this surface, note "manual" here.

FILE-LEVEL EDIT PLAN
- <path/file1>: <what changes — new function | new attribute | new hook>
- <path/file2>: <what changes>
- <path/file3>: ...
- # Every file, every change. No "and possibly other files." Be specific.

SLICING STRATEGY (REQUIRED for SIZE: L; optional for M; skip for S)
- Slice 1 (thin vertical): <smallest end-to-end thing that proves the integration works>
  Verify: <which MCP call confirms slice 1 works>
- Slice 2: <next vertical increment>
  Verify: <MCP call>
- Slice 3 (if needed): <final increment>
  Verify: <MCP call>
- # "Thin vertical" means slice 1 touches every layer (DB → backend → API → UI)
  # for the simplest possible case. Bugs surface at integration boundaries; the
  # vertical slice flushes them on day 1.

BC STRATEGY
- Gutenberg block save() changes: <conditional emission pattern, deprecated[] entry needed at path X, or "no save() change">
- Schema migration: <migration class location, fresh AND upgrade-path test plan, or "no schema change">
- Hook signature changes: <none expected | new hook, will document>
- # Match each item in IMPACT > BC traps from Phase 1 with a strategy here.

ROLLOUT
- Default-off settings toggle: <key name, where it lives, default false> — OR — "n/a, this is internal/always-on"
- Version gate: <activate behind plugin version >= X.Y.Z, flush rewrite rules on upgrade if needed>
- Free vs Pro gating: <free renders skeleton with upsell | Pro implements full behavior | n/a>
- Changelog entry (draft): <one-line user-facing string for the release notes>

VERIFICATION PLAN (one row per SUCCESS CRITERIA from Phase 1, plus one per EDGE CASE)
- Criterion: <statement 1> → <MCP call that proves it live>
- Criterion: <statement 2> → <MCP call>
- Edge case: <empty state> → <MCP call or "punted, see out-of-scope">
- Edge case: <RTL/i18n> → <MCP call or "punted">
- # Every Phase 1 row gets a verification row here.

# WAIT FOR USER SIGN-OFF before Phase 3.
```

**Gate 2.** The user confirms the plan or redirects ("don't extend
class X, it's deprecated — use Y", "skip the wp-pilot path, use
wp_cli instead"). No `Edit` until signed off.

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

After the last slice, run the FULL VERIFICATION PLAN from Phase 2 —
every success criterion + every edge case row. Each must produce
EVIDENCE from a live MCP call.

Emit the final block:

```
STATUS: SHIPPED

SUMMARY: <one-line — what now works that didn't before>

FILES:
  <path>: <one-line per file>

EVIDENCE (one per Phase 1 success criterion + one per edge case)
- <criterion 1>: <MCP command + observed output, trimmed>
- <criterion 2>: <MCP command + observed output>
- <edge case 1>: <MCP command + observed output, or "deferred — see DEFERRED">

DEFERRED (from Phase 1 OUT OF SCOPE, restated so the user sees what they're NOT getting)
- <thing 1>
- <thing 2>

ROLLOUT NOTES
- Toggle: <key name and default> — OR — "always-on for v1"
- Version gate: <triggers on upgrade to X.Y.Z>
- Flush required on existing installs: <yes/no — if rewrite rules changed, flag this>

NEXT (what the human / outer agent needs to do)
- Branch + commit (suggested branch name: feat/<short-slug>)
- Update changelog with the draft entry from Phase 2
- Open PR / move FluentBoards card / etc. — your call
```

If you cannot complete a slice and verification fails for reasons
outside the spec, return:

```
STATUS: BLOCKED

REASON: <one sentence>
PROGRESS: <slices completed, slices remaining>
NEXT: <what the user needs to decide or supply>
```

---

## After SHIPPED — optional retro

If the build surfaced anything non-obvious — a cross-plugin runtime
quirk, a BC trap that almost fired, a helper you wish existed —
write a short note to `memory/feature-history/<feature-slug>.md` so the
team learns from it. Skip if the build was unremarkable.

---

## Anti-patterns that invalidate the workflow

- **Starting Phase 3 without user confirm on Phase 1 + Phase 2.** The
  gates exist because a wrong assumption at Phase 1 wastes the
  entire Phase 3 effort. If you find yourself editing before two
  separate user confirmations have landed, stop and back up.
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
