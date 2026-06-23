# Quickstart / Validation Guide: Unified Slug-Keyed Plugin Config Map

Live-stack validation for spec 010 (constitution IV). Each section maps to a user story and
is the proof of done. Use a scratch project dir + a scratch `SANDBOX_HOME` where noted to
avoid disturbing real instances.

## Prerequisites

- Branch `feat/agent-tooling-specs`, this feature implemented.
- Docker running. A local checkout of a real plugin to point sources at (e.g. templately).

## §1 — US1: per-slug merge, nothing dropped (P1)

```bash
# Project declares the addon + an org plugin; override changes ONE source.
# sandbox.config.json:        { "plugins": { "my-addon": ".", "woocommerce": true } }
# sandbox.config.override.json: { "plugins": { "woocommerce": "~/src/woocommerce" } }

# Resolve effective config and assert both survive, only woocommerce re-sourced:
./sb  # (any command triggers load) — or inspect via:
python3 -c "import sandbox_core as c, json; print(json.dumps(c.load_project_config('<proj>')['plugins'], indent=2, default=str))"
# EXPECT: my-addon present (source path '.'), woocommerce present (source path ~/src/woocommerce)
#         — my-addon NOT dropped.
```

```bash
# Canonical case: project state + user-global catalog path → both kept.
# ~/sandbox/config.json:   { "plugins": { "templately": "~/Sites/git/templately" } }
# project sandbox.config.json: { "plugins": { "templately": true } }
python3 -c "import sandbox_core as c; e=c.load_project_config('<proj>')['plugins']['templately']; print(e)"
# EXPECT: active=true AND source=path(~/Sites/git/templately) — org fallback NOT applied.
```

## §2 — US2: correct slug on a worktree (P1)

```bash
# In a worktree dir named e.g. 'templately-ai-builder-fsi-rewrite':
# sandbox.config.json: { "plugins": { "templately-ai-builder": "." } }
./sb ensure
./sb wp plugin list --format=csv | grep templately-ai-builder
ls "$SANDBOX_HOME/runtime/wp-<inst>/wp-content/plugins/" | grep -x templately-ai-builder
# EXPECT: installed + active under slug 'templately-ai-builder', NOT the dir name.
```

## §3 — US3: on-demand local sourcing + admin UI (P2)

```bash
# Catalog-only Pro plugin, on-demand:
# project: { "plugins": { "elementor-pro": { "path": "~/Sites/plugins-pro/elementor-pro", "onDemand": true } } }
./sb ensure
./sb wp plugin is-installed elementor-pro; echo "installed? $?"   # EXPECT: non-zero (absent)

# Interception via wp-cli install → served from local, no download:
./sb wp plugin install elementor-pro
./sb wp plugin is-installed elementor-pro && echo "now installed (from local)"
# EXPECT: installed; verify the installed version matches the LOCAL checkout (not org).

# Interception via FSI / wp-admin upload uses the same upgrader hook (spot-check one).

# Admin UI (v1): visit the on-demand admin page, confirm elementor-pro is listed and the
# one-click "Install from local" works (auth-gated).
./sb visit /wp-admin/  # then navigate to the Sandbox on-demand page (or via MCP visit tool)
```

```bash
# Catalog path present but project doesn't opt in → NOT enabled:
# ~/sandbox/config.json: { "plugins": { "templately": "~/Sites/git/templately" } }
# project B: { "plugins": { "betterdocs": "." } }   (no templately)
./sb ensure
./sb wp plugin is-installed templately; echo "templately present? $?"   # EXPECT: non-zero
```

## §4 — US4: legacy keys unchanged (P1)

```bash
# A config using ONLY the 3 legacy keys (incl. the user-global mappings_inactive Pro set)
# boots identically to before.
./sb ensure
./sb wp plugin list --status=active --format=csv     # same active set as pre-change
./sb wp plugin list --status=inactive --format=csv   # mappings_inactive ones present, inactive
./sb ensure 2>&1 | grep -i 'deprecat'                # EXPECT: one deprecation hint
```

## §5 — Conflict + validation

```bash
# Same slug in a legacy key AND the map → map wins + warning:
# { "plugins": { "x": false }, "mappings": { "wp-content/plugins/x": "/p" } }
./sb ensure 2>&1 | grep -iE 'x.*(map|conflict|wins|override)'   # EXPECT: warning naming 'x'
# x resolves to the MAP entry (inactive), not the mapping.

# Malformed entry → clear error naming the slug:
# { "plugins": { "y": { "path": "/p", "zip": "http://z.zip" } } }
./sb ensure 2>&1 | grep -i 'y'   # EXPECT: error: more than one source for 'y'

# Missing local source → skip + warning, no silent org install:
# { "plugins": { "z": "/does/not/exist" } }
./sb ensure 2>&1 | grep -i 'z'   # EXPECT: warning; z not installed from org
```

## §6 — Unit layer (fast, pre-live)

```bash
.cli-venv/bin/python -m unittest tests.test_sandbox -v 2>&1 | grep -iE 'normalize|merge|plugin'
# EXPECT: _normalize_plugins + _merge_plugin_maps cases pass (shorthands, UNSET non-clobber,
#         precedence, legacy fold-in, catalog default on-demand).
```

## Pass criteria summary

| Check | Maps to |
|-------|---------|
| Override re-sources one slug, others kept | SC-002, US1 |
| project-state + catalog-path → both kept | SC-007, US1 |
| Worktree slug correct | SC-003, US2 |
| On-demand absent fresh; served local on install (all paths) | SC-004, US3 |
| Catalog-only path never auto-enables | SC-008, US3 |
| Admin UI lists + installs on-demand from local | FR-013, US3 |
| Legacy configs unchanged + one deprecation hint | SC-005, SC-006, US4 |
| Map-wins+warning; malformed/missing handled | FR-012, FR-011 |
| Single map expresses all legacy cases | SC-001 |
