# Multi-instance-per-project-root support — implementation spec

Author: drafted 2026-06-02 (xspeed zip-test session); **rewritten 2026-07-08** after
spec 001/009 landed the per-project-root registry. Status: ready to implement, not yet
implemented.

## Why

Today `./sb` and the MCP server enforce a hard 1:1: one project root (a git worktree /
plugin dir) maps to exactly one running instance. To test a release zip in isolation, run
two PHP/WP version pins side by side, or keep a QA copy alongside a dev-symlink copy of
the *same* checkout, the only options are workarounds (clone the repo into a sibling dir,
use an external tool like `wp-now`).

Goal: let ONE project root own **one or more** simultaneously-running, independently
addressable instances, distinguished by a `label`, with **zero behavior change** for the
(overwhelmingly common) case of a project with exactly one instance.

### Revision note

The original (2026-06-02) version of this doc modeled instances as **globally** named
(`main`, `qa`) in a shared `sandbox.yml` `instances:` block, selected by a CLI `--instance`
flag. That predates spec 001 (removal of the `main`/`DEFAULT_INSTANCE` model) and spec 009
(the per-project-root `registry.json`, the single source of truth for project→instance
mapping — there is no `sandbox.yml` projects catalog). This revision replaces that model
entirely: instances are now **per-root**, distinguished by `label`, discovered from the
on-disk registry — not declared globally.

Reusable from the original doc: compose-per-instance templating (now implemented, not
speculative), per-instance port allocation, `-p sandbox-<instance>` compose-project
isolation, the shared-`SANDBOX_PLUGINS_HOST`-across-instances risk note, per-instance app
password. Discarded: the `main` default, the global `instances:` declaration, and the
`.focus`/`runtime/wp` migrations (both already fully per-instance-name since spec 001/009).

## Verified starting state

The 1:1 constraint is **not** in the port/compose/dir machinery — that is already fully
keyed by *instance name*, not by root: `wp_dir(inst)`, `plugins_dir(inst)`,
`project_name(inst)` (`f"sandbox-{inst}"`), `snapshots_dir(inst)`, `focus_file(inst)`
(`sandbox/core/_instances.py:116-135`), and the MCP compose-args helper
(`mcp/wp-server/app.py:117-146`). Instance names are already **globally unique**:
`_derive_instance_name(root, taken)` (`_instances.py:265`) uniquifies against every name
already in the registry (`_instances.py:502-505`).

The 1:1 lives in exactly four places:

1. **Registry shape** (`sandbox_core.py:598-644`): `{"instances": {canonical_root:
   single_entry}}`. `registry_get(root)` returns that one entry; `registry_put(root,
   **fields)` shallow-merges into it; `registry_remove(root)` drops it. One key, one value.
2. **`ensure_instance`** (`_instances.py:468-586`): idempotent per root — if a ready,
   reachable entry already exists for `root`, it's returned as-is; there is no path to
   "mint a second instance for this same root."
3. **Resolvers**: `_cwd_instance()` (`_instances.py:249-262`) and MCP
   `_project_instance(project_dir)` (`app.py:96-115`) both do `registry_get(root)["instance"]`.
4. **Reverse/remove paths** keyed on the single entry (`instances_cmd.py:234-236`,
   `debug.py:133`).

Everything else that touches the registry iterates `registry_all().values()` and keys off
`e["instance"]` (already globally unique) — those survive a schema change untouched.

## 1. Data model

**Composite string key `f"{canonical_root}::{label}"`, flat dict retained.**

```jsonc
{
  "version": 2,
  "instances": {
    "/Users/alim/Sites/git/xspeed::default": {
      "root": "/Users/alim/Sites/git/xspeed", "label": "default",
      "instance": "xspeed", "wordpress_port": 8188, "db_port": 3318,
      "mailpit_port": 8125, "server": "nginx", "status": "ready", "url": "…",
      "is_default": true
    },
    "/Users/alim/Sites/git/xspeed::php81": {
      "root": "/Users/alim/Sites/git/xspeed", "label": "php81",
      "instance": "xspeed-php81", "wordpress_port": 8189, "db_port": 3319,
      "mailpit_port": 8126, "server": "nginx", "status": "ready", "url": "…",
      "is_default": false
    }
  }
}
```

**Why composite key over a nested `{root: {default, instances: {}}}` shape:** a nested
shape breaks every `registry_all().values()` iterator (7 call sites) and changes what
`registry_all()` returns. The composite key changes only the *key string*; every value
iterator (`_instances.py:101,503`, `lifecycle.py`, `_dash.py:29`) keeps working unchanged
because it reads `e["instance"]` / `e["root"]` fields, never the key.
`registry_find_instance(name)` (`sandbox_core.py:647`) is unaffected — instance names stay
globally unique, so reverse lookup still resolves to exactly one entry. Only readers that
treat the registry **key itself** as the root (one spot: `_dash.py:351`,
`for r, e in registry_all().items()`) need a one-line change to `e["root"]`. Migration is a
single-pass rewrite. This is the lowest-blast-radius option.

**New/changed core functions (`sandbox_core.py`):**

| Function | Change |
|---|---|
| `registry_get(root, label=None)` | `label=None` + exactly one entry for `root` → return it (**back-compat fast path**). `label=None` + multiple entries → return the `is_default` one. `label` given → return that composite entry or `None`. |
| `registry_list_for_root(root) -> list[dict]` | **New.** All entries whose `root == canonical(root)`, default first. Powers "which one?" errors and CLI listing. |
| `registry_put(root, label="default", **fields)` | Keys on `f"{canonical(root)}::{label}"`; stores `root`, `label`, `is_default`. First entry for a root gets `is_default=True`. |
| `registry_remove(root, label=None)` | `label=None` + single entry → remove it (back-compat). Multiple entries + no label → raise (ambiguous). `label` given → remove that composite entry. |
| `registry_default_label(root) -> str \| None` | **New.** The `is_default` label for a root, for resolvers. |
| `registry_find_instance(name)` | Unchanged. |

**One-time migration (registry v1 → v2)**, run inside the registry read path (mirrors
spec 009's existing migration pattern) and eagerly by `sb migrate`:

```python
if data.get("version", 1) == 1:
    new = {}
    for root, entry in data["instances"].items():
        label = "default"
        new[f"{root}::{label}"] = {**entry, "root": root, "label": label,
                                     "is_default": True}
    data = {"version": 2, "instances": new}
    _registry_write(data)
```

Idempotent, guarded by the existing `_registry_lock()`. Every currently-running instance's
name, ports, wp-dir, compose project, and `sandbox.local.yml` block are **untouched** —
only the registry *key* gains a `::default` suffix. Zero downtime.

## 2. API surface (MCP tools)

**New parameter, uniform across every instance-routed tool:** `label: str | None = None`,
appended after `project_dir`. Resolution is centralized in one helper so no tool
re-implements it:

```python
def _project_instance(project_dir, label=None):
    root = find_project_root(project_dir)
    entries = registry_list_for_root(root)
    if not entries:
        return None, {"error": "no instance … call ensure_instance first."}
    if label:
        e = next((x for x in entries if x["label"] == label), None)
        return (e["instance"], None) if e else (
            None, {"error": f"no instance labelled '{label}' for {root}. "
                             f"Labels: {[x['label'] for x in entries]}"})
    if len(entries) == 1:
        return entries[0]["instance"], None            # back-compat
    default = next((x for x in entries if x["is_default"]), None)
    if default:
        return default["instance"], None
    return None, {"error": "multiple instances; pass label=",
                  "labels": [x["label"] for x in entries]}
```

**Semantics when `label` is omitted:** resolves to the sole instance (identical to today),
else the `is_default` instance, else a structured "which one?" error listing labels. **No
existing call breaks** — a project with one instance never sees `label` in practice.

**Full inventory of tools gaining `label` (28 tools):**

| File | Tools |
|---|---|
| `instances.py` | `ensure_instance`, `destroy_instance`, `recreate_instance`, `secure_instance`, `apply_config` |
| `wp.py` | `wp_cli`, `wp_exec`, `wp_rest`, `run_tests`, `wp_cli_async`, `wp_cli_job`, `wp_cli_job_kill` |
| `fs.py` | `tail_log`, `fs_read`, `fs_write`, `fs_list` |
| `context.py` | `focus_get`, `activate_plugin`, `deactivate_plugin` |
| `mail.py` | `mail_list`, `mail_get` |
| `data.py` | `db_query`, `import_content`, `wp_reset` |
| `debug.py` | `qm_capture`, `xdebug` |
| `abilities.py` | `wp_eval_live` |

**Special cases:**

- **`ensure_instance(project_dir, label="default", create=False)`** — the only tool that
  *mints*. `label="default"` preserves today's behavior exactly. A non-existent
  non-default label errors unless `create=True` (guards against typo-spawning a whole new
  stack by accident). Returns the entry including `label`.
- **`recreate_instance` / `destroy_instance`** — resolve one entry via
  `_project_instance(project_dir, label)`; recreate preserves *that entry's* ports (already
  does, via `saved_ports` in `instances.py:99`).
- **`skills.py` tools** (`list_skills`, `skill_write`, `skill_edit`, `skill_delete`) and the
  CLAUDE.md/skills portion of `focus_get` read the **project source repo**, shared across
  every instance of a root — no `label`. The instance-state portion of `focus_get` (per-
  instance `.focus.<inst>` file) DOES need `label` (see §5).
- **No change** (global or URL-routed): `setup_domains`, `http_fetch`, `pixelmatch_diff`,
  `visit` (disambiguated by the URL you pass — the second instance's URL comes from its own
  `ensure_instance` return), `load_context`, `load_skill`, `load_workflow`, `cache_info`,
  `cache_clear`.

## 3. CLI surface

The global `--instance <name>` flag already exists (`cli.py:398-411`), resolving by
precedence `--instance → $SANDBOX_INSTANCE → _cwd_instance()`. Multi-instance additions:

- **`--label <label>`** — new global flag, sibling to `--instance`, threaded identically
  (registered on the top-level parser + injected onto every subparser). `--instance` wins
  outright when both given (instance names are unique). With only `--label`, resolution
  becomes `registry_list_for_root(cwd_root)` filtered by label. Bare invocation in a
  multi-instance cwd with no `--label` → the existing `die()` (`cli.py:446-451`) gains the
  label list: *"project has 2 instances (default, php81); pass --label."* A single-instance
  cwd is unchanged.
- **`_cwd_instance()`** (`_instances.py:249`) gains a `label` arg, calls
  `registry_list_for_root`; returns the sole/default instance name when unambiguous
  (back-compat), else `None` so the caller emits the disambiguation error.
- **`./sb instances [--project-dir X]`** (`cmd_instances`, `instances_cmd.py:261`) already
  lists every instance registry-wide. Add a `LABEL` column and an optional `--project-dir`
  filter to show only one root's instances.
- **`./sb ensure --project-dir X --label php81`** — mints an additional instance for a
  root. Without `--label`, targets `default` (today's behavior).
- **`./sb instance delete <name>`** — unchanged (operates by unique instance name);
  internally becomes `registry_remove(owner["root"], owner["label"])`.

Every existing `./sb <cmd>` in a single-instance project dir behaves exactly as today.

## 4. Naming

Two identifiers, cleanly separated:

- **`label`** — the per-root, human-facing tag (`default`, `qa`, `php81`, `zip`).
  User-supplied via `--label` / `label=`; defaults to `"default"` for the first instance.
  Validated `^[a-z0-9][a-z0-9_-]{0,20}$`. Unique *within a root* only.
- **`instance` name** — the globally-unique physical id (compose project, wp-dir, ports).
  Derived, never user-typed directly.

**Derivation rule** (extends `_derive_instance_name`): default label →
`<project-basename>-<git-branch>` (linked/generated worktrees use the primary repository
basename rather than the generated directory name; a matching generated namespace such
as `t3code/` is removed from the branch). A non-git root, a detached HEAD, or a branch
echoing the basename falls back to the bare basename; the branch is capped at half the
24-char budget so it can never swallow the repo identity. Existing instances are unaffected —
`ensure_instance` reuses the registry record for a (root, label) and only derives a name
for a brand-new one, so switching branches in place never renames a live stack.
Non-default label → seed with `<basename>-<branch>-<label>`, then run
the *existing* global collision-avoidance loop (`_instances.py:273-276`) so a cross-project
clash still appends `-2`. Example: root `xspeed`, label `php81` → instance `xspeed-php81`;
if that's taken, `xspeed-php81-2`. This reuses the truncate-then-strip-hyphen safety
(`_instances.py:270`) so derived domains/names stay valid.

`_pick_instance_ports` (`_instances.py:154`) already walks *all* instances' claimed ports
and bumps into the next free slot — a second instance of a root transparently lands on the
next port neighborhood (e.g. 8189/3319/8126) with no extra code.

## 5. State files

- **`.focus.<instance>`** — already per-instance-*name* (`focus_file(inst)`), and instance
  names are unique, so **no layout change**. The "singleton focus" steal logic
  (`cmd_focus`, `instances_cmd.py:38-59`) already scans `.focus.*` across instances and
  keeps working unmodified. `focus_get` gains `label` to pick which of a root's instances'
  focus file to read.
- **`.active-project.<instance>`** — same: per-instance-name, no change.
- **`_cwd_instance()`** — the one real behavioral change, from "the instance for this root"
  to "the sole/default instance for this root, else ambiguous" (§3).
- Legacy suffix-less `.focus` / `.active-project` do not exist in this codebase — already
  fully migrated to the per-instance form by spec 001. The original doc's "state file
  conventions" migration section is therefore **obsolete** and dropped in this revision.

## 6. Backward-compatibility guarantee

A project with exactly one instance, after this ships:

- `registry_get(root)` with no label returns that entry (single-entry fast path).
- `_project_instance(project_dir)` / `_cwd_instance()` with no `label` resolve unchanged.
- Every MCP tool call that omits `label` (i.e. every existing call anywhere) behaves
  bit-identically to today.
- Its instance name, ports, wp-dir, compose project, `sandbox.local.yml` block, `.focus.*`,
  and snapshots are untouched by the v1→v2 migration (only the registry key gains
  `::default`).
- `./sb <cmd>` with no `--label` in that project dir is unchanged.

`label` / `--label` are **purely additive optional parameters** — no required-parameter
changes anywhere. This is the release gate.

## 7. Spec impact audit

| Spec | Section / FR | Change needed |
|---|---|---|
| **009-runtime-user-dir** | FR-011 ("registry MUST continue to key state by project-root path"); data-model "Registry entry... keyed by project-root path → instance name" | **Amend.** Key becomes `root::label`; a root maps to *one-or-more* instances. FR-011's "keyed by project root" stays true (root is still in the key); "→ instance" (singular) becomes "→ instance(s)". Add the v1→v2 migration to the migration entity. |
| **001-per-project-modular** | Key Entity "Instance" ("owned by exactly one project root"); FR-006 (existing per-project features work unchanged) | **Amend (clarify, not contradict).** "Owned by exactly one root" stays true — it's a many-instances-to-one-root relation, not the reverse. Add: a root may own N instances distinguished by `label`. FR-006 is *satisfied*, not broken, by the back-compat guarantee in §6. |
| **002-dashboard-snapshots** | Dashboard/registry iteration (`_dash.py:351`) | **Amend.** Switch key→root reads to `e["root"]`; add a LABEL column to the instance table. Snapshot store is already per-instance-name — no store change. |
| **008-db-snapshots-reset** | "one baseline per instance"; `wp_reset` | **No FR change.** Baseline is per instance-*name*, which stays unique. `wp_reset` MCP tool gains `label` for routing only. |
| **003-wp-abilities-adapter** | Independent Test phrased "with one instance running"; `_write_abilities_muplugin(name)` | **No FR change.** Mu-plugin is already written per instance-name (`apply_config`, `_instances.py:663`); each labelled instance gets its own independently. Reword the test description to not imply "the" (singular, project-wide) instance. |
| **010-plugin-config-map** | Plugin map resolution is per **project root** (source), not per instance | **No FR change, add a note.** All instances of a root share the same resolved plugin map (same source repo, same bind-mounts) — call out that per-instance plugin-set divergence (e.g. a php81 instance excluding a plugin the default instance has) is out of scope for this change; labelled instances of one root always share the root's plugin map. |
| **004, 006, 007** | async jobs, skill authoring, debugging tools | **Unaffected.** Jobs/logs/xdebug/qm are per instance-name; skill tools read the shared source repo. Their MCP tools gain the routing-only `label` param but no FR changes. |

A new speckit spec should own the FR-level language for this feature itself (the next free
spec number — check `specs/` before numbering, since an unlanded licensing spec already
references "spec 013" internally at `_instances.py:664`): *"a project root MAY have ≥1
instances distinguished by a `label`; absent a label, operations target the sole/default
instance; ≥2 instances with no label is an actionable error, never a silent guess."*

## 8. Implementation order (registry-first)

1. **Registry v2 + migration** (`sandbox_core.py`). Composite key, `label`/`is_default`,
   `registry_list_for_root`, label-aware `get`/`put`/`remove`, v1→v2 auto-migration.
   Extend `_selftest_registry` to cover multi-instance-per-root + migration idempotency.
   **Done when:** self-test passes, and an existing v1 `registry.json` loads, migrates,
   and every prior instance still resolves via `registry_get(root)`.
2. **Fix the key-as-root reader** (`_dash.py:351`) to use `e["root"]`. **Done when:**
   `./sb instances` and the dashboard render unchanged on a single-instance setup.
3. **Resolver + `ensure_instance`** (`_instances.py`, `app.py`). `_cwd_instance(label)`,
   MCP `_project_instance(project_dir, label)`, `ensure_instance(cfg, project_dir,
   label="default", create=False)` minting a second instance for a root. **Done when:**
   `./sb ensure --project-dir X --label php81` boots a second stack alongside the default
   with distinct ports and both `registry_list_for_root` entries `ready`.
4. **MCP tools** — thread `label` through all 28 routed tools via the shared
   `_project_instance` helper. **Done when:** `wp_cli(project_dir=X, label="php81")` hits
   the php81 stack; omitting `label` on a 2-instance root returns the "which one?" error;
   single-instance calls unchanged.
5. **CLI `--label`** flag + disambiguation errors + `./sb instances --project-dir` LABEL
   column. **Done when:** the acceptance criteria below pass with `--label`.
6. **Delete/snapshot/reset per label** — verify `destroy_instance(label)`,
   `wp_reset(label)`, snapshots isolate correctly. **Done when:** deleting `php81` leaves
   `default` running and registered.
7. **Docs + specs** — this doc + the spec-impact audit table (§7); update `CLAUDE.md`'s
   MCP tool table and "one project directory ↔ one instance" language, and MCP tool
   docstrings, to "one-or-more."

Each step keeps `./sb doctor` green for a single-instance project with no flags.

## Acceptance criteria

A PR ships when:

1. `./sb apply` (or `ensure`) on a project with no prior instance still produces exactly
   one `default`-labelled instance, indistinguishable from today's single-instance
   behavior.
2. `./sb ensure --project-dir X --label qa` boots a second, independent stack for the same
   root; `./sb instances --project-dir X` lists both with distinct ports/labels.
3. Both instances run simultaneously with no port or compose-project collisions (verify
   with `docker ps`).
4. `wp_cli(project_dir=X, label="qa")` and `wp_cli(project_dir=X, label="default")` (or the
   bare/no-label call when unambiguous) show independent plugin lists / DB state.
5. `./sb wp --label qa plugin list` and the bare (default) call show independent results.
6. Omitting `label`/`--label` on a project with exactly one instance behaves identically
   to the pre-change code path (no new required params, no new prompts).
7. Omitting `label`/`--label` on a project with ≥2 instances and no `is_default` set
   errors with the full label list, never guesses.
8. `./sb snapshot --label qa baseline` creates a snapshot isolated from `default`'s.
9. `./sb instance delete <qa-instance-name>` removes only that entry; `default` is
   untouched and still resolves.
10. A v1 `registry.json` (pre-existing, single-entry-per-root) loads, auto-migrates to v2,
    and every previously-registered instance still resolves and boots with zero manual
    steps.

## 9. Per-label `sandbox.config` (implemented 2026-07-09)

**Supersedes the "per-instance divergent plugin/theme sets... out of scope" note this doc
originally shipped with (§ Out of scope, below) — implemented while building
`docs/ci-e2e-runner-spec.md`'s CI matrix runner, which needed exactly this.**

`load_project_config(project_dir, label=None)` (`sandbox_core.py`) gained an optional
`label` param. When given (and not `"default"`), it layers an OPTIONAL
`sandbox.config.<label>.json` (`.yml`/`.yaml` too) at the HIGHEST precedence — above
`sandbox.config.override.json` — via the same `_deep_merge` + spec-010 plugin-map-merge
machinery the override layer already used. Absent, it's silently skipped (back-compat: a
project with no per-label files behaves identically to before). Malformed labels
(path-traversal shapes, disallowed characters) are validated against the same
`^[a-z0-9][a-z0-9_-]{0,20}$` pattern instance labels use and silently ignored rather than
raising — config loading must never fail because of an untrusted label string.

**Layer precedence, low to high:** user-global < `sandbox.config.json` <
`sandbox.config.override.json` < `sandbox.config.<label>.json`. Applies identically to
both the generic key merge (scalars/dicts/lists) and the spec-010 slug-keyed plugin map
(a label's plugin declarations win last, and count toward `opted_in` alongside
project/override).

**Threaded through `ensure_instance`/`apply_config`** (`sandbox/core/_instances.py`) via a
param **separate** from the instance `label` itself: `config_label`. For durable labels
(`qa`, `php81`) the two are the same by default (label IS its own stable config key). The
CI matrix runner (`docs/ci-e2e-runner-spec.md` §3.5) needs them to DIFFER: a CI cell's
actual instance label is randomized per run (`ci-<runid>-<slug>`, for concurrency-safety —
two simultaneous `ci run`s of the same workflow must never collide on the same label), so
a user could never pre-author a config file matching a random label. The CI runner instead
computes a STABLE per-cell slug (`_cell_slug`, e.g. `68-84` for `{wp:'6.8', php:'8.4'}` —
the same across every run of that cell) and passes THAT as `config_label`, while `label`
stays randomized. A project can then author `sandbox.config.68-84.json` once and have it
apply to every run of that specific matrix cell — a genuinely different plugin set per
cell, not just a different PHP/WP version.

7 unit tests (`tests/test_sandbox.py::TestPerLabelConfig`) cover: scalar override
precedence over `sandbox.config.override.json`, the layer being skipped entirely when no
label (or `label="default"`) is given, missing-file fallback, malformed/path-traversal-
shaped labels being ignored rather than raised, plugin-map precedence, and the
config_label-vs-instance-label substitution the CI runner relies on.

## Out of scope

- Cross-instance operations (copy a snapshot from `default` to `qa`, diff two labels) —
  punt to a follow-up.
- Sharing a DB between instances of the same root — no; isolation is the point.
- A cross-instance licensing-activation policy (see the unlanded licensing mu-plugin at
  `_instances.py:664`) — flagged as a risk below, owned by that spec.
- Cross-instance orchestration for parallel e2e workers and running a project's GitHub
  Actions CI locally — these are real consumers of this primitive but live in their own
  doc: `docs/ci-e2e-runner-spec.md`.

## Risks / things to watch

- **Compose-project / container collisions**: none new — `project_name = "sandbox-
  {instance}"` and instance names stay globally unique, so `sandbox-xspeed` and
  `sandbox-xspeed-php81` never clash. The whole isolation guarantee already rides on
  unique instance names, not on root.
- **Port collisions**: `_pick_instance_ports` (`_instances.py:154`) and
  `_resolve_port_conflicts` (`_instances.py:201`) already scan every instance and bump; a
  second instance of a root gets the next free port trio automatically.
- **`SANDBOX_PLUGINS_HOST` bind-mount shared across a root's instances**: this is correct
  and intentional. Both instances bind-mount the *same* plugin source (the same git
  worktree) at the same absolute host path (gotcha #3 in `CLAUDE.md`), so they test the
  *same code* against *different DB/WP state* — exactly the point (dev-symlink vs. zip,
  php80 vs. php81). Document clearly: a code edit is instantly live in **all** of a root's
  instances; only DB/uploads/WP-version differ between labels.
- **Per-label mu-plugins & secrets**: `_write_*_muplugin(name)` and
  `bridge_token`/`app_password`/`autologin_token` are already per instance-name
  (`_build_instance_block`, `_instances.py:461-464`); each labelled instance mints its own.
  No cross-contamination.
- **Snapshot/restore per label**: `snapshots_dir(inst)` is per instance-name — already
  isolated. The `wp_reset`/`@install` baseline (spec 008) is per instance — fine as-is.
- **Herd instances**: a root can have multiple herd-mode labels since `_herd_domain(name)`
  keys on instance name (unique) — `xspeed.test` vs. `xspeed-php81.test` are distinct
  domains with distinct linked dirs (`wp_dir(inst)`). Safe, but verify Herd PHP isolation
  per linked dir doesn't leak between two labels of one root.
- **Multisite conversion**: per instance-name; a labelled instance can be multisite while
  its sibling label is single-site with no interference.
- **Unlanded "spec 013" licensing mu-plugin** (`_instances.py:664`) activates a Pro license
  *cross-instance*; with N instances per root sharing one license, confirm the licensing
  server's activation-count tolerates N local activations from the same machine. Out of
  scope here — flag for that spec's owner.
- **`ensure_instance` typo-spawning**: mitigated by the `create=True` gate on non-default
  labels (§2) — a mistyped `label` errors instead of silently building a whole new stack.
- **`./sb setup`/`apply` time multiplies by instance count per root** — acceptable for 2;
  reconsider UX (parallelize, or require explicit `--label` per invocation) for 5+.
