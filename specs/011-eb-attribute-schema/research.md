# Research: EB-Aware Attribute-Schema Resolver

**Feature**: 011-eb-attribute-schema · **Phase 0** · 2026-06-25

All Technical Context unknowns are resolved below. Each decision records what was chosen, why, and
the alternatives rejected.

## D1 — Where the resolver runs (in-instance PHP vs host-side Python)

**Decision**: In-instance PHP, inside the existing `sandbox_editor_schema()` ability in
`sandbox/assets/abilities/sandbox-editor.php`.

**Rationale**:
- The spec scopes the change to "the editor-schema ability only." editor-schema is reached by agents
  as a WP ability (via `wp_eval_live` / the `/wp-json/sandbox/mcp` endpoint), not as a Python MCP
  tool — so a host-side Python resolver would not be hit on a direct ability call.
- The EB source checkout IS visible to in-container PHP: the sandbox bind-mounts `plugins_home` at
  the same absolute host path inside the container (`{plugins_host}:{plugins_host}`, gotcha #3) and
  exports it as the `SANDBOX_PLUGINS_HOST` env var. So the in-instance ability can both *know* and
  *read* the plugin-home tree without any new mount.
- Keeps the feature a single-file enhancement; no new MCP tool, CLI command, or host/container
  resolution split to keep in lockstep (Constitution II/III).

**Alternatives rejected**:
- *Host-side Python resolver*: cleaner file access in theory, but editor-schema isn't a Python tool,
  so it wouldn't enrich direct ability calls; also adds a second resolution site to maintain.
- *Bake the full schema at provision time*: stale the moment the dev edits source; wasteful for
  blocks never queried.

## D2 — How the EB source checkout is discovered

**Decision**: Scan the bind-mounted plugin-home root (`SANDBOX_PLUGINS_HOST`) for a directory that
contains `src/blocks/<block>/src/attributes.js` AND a controls helper package
(`src/controls/src/helpers/` or a resolvable `@essential-blocks/controls`). Match free
(`essential-blocks`) and Pro (`essential-blocks-pro`) checkouts. When multiple candidates match,
pick deterministically (shortest path, then lexicographic) and record the chosen checkout path in the
response.

**Rationale**: Matches the clarified answer ("scan configured plugin-home directories"). The
plugin-home tree is exactly what is mounted and env-advertised, so it is the reliable, already-present
scan root. Recording the chosen checkout makes an unexpected fork/worktree match visible (US edge
case) rather than silent.

**Constraint surfaced**: Only paths under the mounted tree are readable in-container. An EB checkout
outside `plugins_home` (and not mapped) is invisible to the ability → reduced fidelity with the reason
"no EB source checkout found under the mounted plugin-home." This is acceptable and honest (US2). It
is documented in the SKILL + memory note so a dev knows to keep (or map) their EB source under
`plugins_home` to get full fidelity.

**Alternatives rejected**:
- *Scan arbitrary catalog paths from sandbox config*: those paths are not guaranteed mounted into the
  container, so the ability could "find" a path it cannot read. Rejected for the in-instance design.
- *Require an explicit config path*: more setup friction; the scan covers the normal case with no new
  config.

## D3 — Parsing `attributes.js` without running the JS build

**Decision**: Lightweight literal parsing in PHP. From a block's `attributes.js`, extract (a) the
explicit attribute keys — top-level `name: { ... }` entries with a `type:` — and (b) the generator
spread calls — `...generateXxxAttributes(PREFIX, opts?)` — capturing the generator name and the
prefix constant. Resolve each prefix constant from the block's `constants/*.js`.

**Rationale**: The attribute set is deterministic and declared as plain object literals + a fixed set
of generator calls; it does not require executing JSX/webpack. A scoped tokenizer (brace-aware, not a
naive regex) is sufficient and was already prototyped during investigation (explicit-key and
generator-call extraction both worked).

**Alternatives rejected**:
- *Execute the JS via Node in-container*: Node isn't present in the WP image; heavy and fragile.
- *Query the live editor's `wp.blocks` registry headlessly*: requires a browser + admin login (the
  finalizer path) — far too heavy for a schema read, and the editor-login path is unreliable in
  headless runs.

## D4 — Expanding generator helpers (and nesting)

**Decision**: Derive each generator's emitted key-family by parsing the helper source in the controls
package (`src/controls/src/helpers/*.js`) — collecting the `[`${prefix}Suffix`]:` and
`[`TAB${prefix}…`]` / `[`MOB${prefix}…`]` attribute-definition templates — and substituting the
block's prefix constant. Resolve nested generator spreads recursively (border/shadow spreads four inner
`generateDimensionsAttributes` calls: `Bdr_`, `Rds_`, `HRds_`, `HBdr_`). Ship a built-in fallback
key-family table (the verified counts: typography 24/prefix, dimensions 16, border/shadow 21 own + 4×16
nested = 85, background 155, responsive range 7, responsive align 3) used only when a helper file
cannot be parsed.

**Rationale**: Parsing the helper source makes the resolver track EB changes automatically (full
fidelity that follows the checkout), while the fallback table guarantees a useful answer if a helper's
shape is unparseable — in which case the response is marked *partial* and names the helper (FR-005).
Verified ground truth: advanced-heading resolves to ~787 attributes via this method.

**Alternatives rejected**:
- *Hardcode the key-families only*: drifts when EB changes a helper; no signal that it drifted.
- *Count attributes only (no names/types)*: insufficient — agents need the actual key names
  (`titleText`, not a count) to author correctly (SC-002).

## D5 — Caching and invalidation

**Decision**: Cache the resolved attribute set in a WordPress transient keyed by
`eb_schema_<block>_<fingerprint>`, where the fingerprint hashes the chosen checkout path plus the
mtimes of the block's `attributes.js`, its referenced constants files, and the controls helper files
used. Invalidate implicitly by key change when any of those mtimes move; no manual bust needed.

**Rationale**: Matches the clarified answer ("cache, invalidate on source change"). mtime-fingerprint
keying means a dev editing the source naturally misses the old cache without an explicit clear, while
repeat reads of an unchanged block are near-instant (FR-011, SC perf).

**Alternatives rejected**:
- *No cache*: re-parses helper files on every call; wasteful for repeated authoring loops.
- *TTL-only cache*: could serve a stale schema within the window after a source edit; mtime keying is
  both fresher and simpler.

## D6 — Fidelity reporting model

**Decision**: Replace the current boolean-ish `eb_attribute_fidelity` string with a structured report
on every EB response: `level` ∈ {`full`, `partial`, `reduced`}, `count`, `source_checkout` (path or
null), and `reason`/`unresolved` when not full. `full` = all generators expanded from source;
`partial` = block source found but ≥1 generator fell back / was unknown; `reduced` = no source
checkout found (today's behavior, block.json attributes only).

**Rationale**: FR-003/FR-004/FR-005 require honest, machine-readable fidelity. A structured report lets
an agent branch on `level` rather than parse prose. Keeping the `eb_attribute_fidelity` key present
(now carrying the structured value, or a back-compat string alongside) avoids breaking existing
readers.

**Alternatives rejected**:
- *Keep the free-text string*: not reliably machine-readable; conflates "reduced" with "partial."

## D7 — No regression for Elementor and core blocks

**Decision**: The resolver is gated strictly on `builder === 'gutenberg'` AND
`strpos($name, 'essential-blocks/') === 0`. All other paths (Elementor widgets, core/third-party
Gutenberg blocks) return through the unchanged code, byte-for-byte.

**Rationale**: FR-007 + SC-005. The change is purely additive on the EB-named branch; a regression test
captures Elementor + core schemas before/after.

**Alternatives rejected**: applying generator expansion to all Gutenberg blocks — unnecessary (core
blocks have full block.json fidelity already) and risky.

## Open risks (carried into tasks)

- **Helper-shape drift**: a future EB refactor of the controls helpers could change key-families; the
  source-parse approach tracks it, but the fallback table would go stale — mitigated by marking
  *partial* and naming the helper, plus a memory note to re-verify counts on major EB bumps.
- **Container visibility**: the most common "why is it still reduced?" will be an EB checkout outside
  the mounted `plugins_home`; the reason string + SKILL note address this directly.
