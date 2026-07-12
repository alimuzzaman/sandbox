# Quickstart Validation: Generic Project Instances

These scenarios are implementation acceptance checks, not current commands guaranteed to work before the feature is built.

## 1. Legacy WordPress baseline

From the Sandbox project, capture current behavior before shared dispatch changes:

```bash
SB=/absolute/path/to/sandbox/sb
cd /path/to/wordpress-plugin
"$SB" ensure --json
"$SB" status
"$SB" wp option get siteurl
"$SB" test
```

Record instance, URL, registry fields, WP-CLI result, and tests. This is the parity reference.

## 2. Minimal explicit Compose project

Use `tests/fixtures/generic-compose/`, whose directory alias includes a dot and whose configuration follows [project-config.md](contracts/project-config.md).

```bash
SB=/absolute/path/to/sandbox/sb
cd /absolute/path/to/sandbox/tests/fixtures/generic-compose
"$SB" ensure --json
"$SB" ensure --json
"$SB" status
```

Expected: one registry identity, `kind=compose`, no WordPress/database/mail services, stable URL, and successful health probe.

Repeat three cycles:

```bash
"$SB" down
"$SB" up
"$SB" apply --project-dir "$PWD" --json
```

Expected: no orphaned containers, stable identity/port/URL, and project-owned volumes intact.

## 3. Capability rejection

Call representative WordPress CLI and MCP operations against the generic fixture.

Expected: structured `unsupported_capability` results before any WP-CLI, REST, database, mail, or WordPress filesystem subprocess begins.

## 4. Astro guided initialization

Copy `tests/fixtures/astro/` to a disposable directory, remove Sandbox config, then run:

```bash
SB=/absolute/path/to/sandbox/sb
cd /tmp/sandbox-astro-validation
"$SB" init --type astro
"$SB" ensure --json
```

Review the generated `sandbox.config.json` and `sandbox.compose.yml`. Expected: explicit Compose kind, reviewed package command, port 4321 unless the fixture overrides it, reachable health path, and live source update behavior.

## 5. Safe destroy

Create a marker in a project-owned named volume, then destroy through CLI and MCP.

Expected: registry entry and `$SANDBOX_HOME/runtime/projects/<instance>/` are removed; source and named-volume marker remain. No destructive volume flag appears in executed Compose arguments.

## 6. WordPress parity after generic validation

Repeat the baseline commands and compare externally visible results. Then run:

```bash
python3 -m unittest discover -s tests -v
./sb selftest
git diff --check
```

Implementation is not complete until both live stacks and the full suite pass, and a human reviews the registry/lifecycle diff.
