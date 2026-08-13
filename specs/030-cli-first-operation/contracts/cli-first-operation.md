# CLI contract: CLI-first operation

## `sb guide`

```text
sb guide [--project-dir DIR] [--json]
```

- Reads a project descriptor when supplied or discoverable from the current
  directory.
- Selects only the WordPress or Compose command catalog.
- `--json` emits `mode`, `project_kind`, optional `project_root`, `skill`,
  `commands`, and `mcp`.
- Does not resolve, create, or mutate an instance.

## `sb exec`

```text
sb exec [--instance NAME] [--label LABEL] [--json] -- <argv...>
```

- Requires an existing resolved instance owned by a Compose project.
- Requires a non-empty argv list without NUL bytes.
- Checks `compose.exec` before invoking the runtime.
- Executes the list in the descriptor's declared public service.
- Prints command output, or an operation envelope with `--json`.
- Rejects WordPress and malformed input before command execution.

## Compatibility

`sb mcp --project-dir DIR` remains valid. It is an optional MCP transport and
continues to use its runtime-scoped catalog.

## Convergence amendment — 2026-08-13: selection, output, and feedback

### Config home and label intent

The project config home is selected exactly as specified by the unchanged
`specs/042-config-subdirectory/prd.md`: root or `.config/sandbox/`, one complete
family, no cross-home layer merge. This CLI contract does not edit or supersede
that placement PRD. Once a home is selected, an explicit `--label NAME` is
preserved through global and subcommand parsing. A missing explicit label returns
an error envelope with a stable `label_not_found` (or more specific equivalent)
code and exit status 2; it never silently selects `default` or another label.

### Guide source and registry

`sb guide` resolves the installed/active Sandbox command entry point rather than
requiring `./sb` to exist in the checkout. Public `commands` are derived from
the same registry used for dispatch. A registry entry may be omitted only when
listed in a checked-in internal exclusion set; the JSON guide includes the
resolved project kind, config home, and selected label when applicable.

### Canonical output and WP assertions

For every `--json` operation, stdout is one UTF-8 JSON document and nothing
else. Diagnostics/progress are stderr-only. `status --json` uses the shared
renderer and a stable envelope; callers must parse the complete captured stdout,
not a line chosen by truthiness. WordPress command tests capture stdout, stderr,
and integer exit status from the child process and assert those values.

### Shared identity and feedback

CLI and MCP adapters call the same project-identity service and return matching
canonical root, kind, label, and capability fields. The feedback surface retains
`submit` and `list`; its additive contract defines bounded `detail`, filtered
`list`, safe machine-readable `export`, and explicitly requested retention
planning. Export/detail never reveal secrets or interpret report text as
instructions. `limit` is valid only when it is a non-boolean integer in the
documented inclusive range; omitted uses the documented default, while an
explicit invalid value fails before a read.

### PHP extension CLI semantics

`sb init` for a new WordPress project MUST emit an explicit, reviewable
`phpExtensions` profile using the immutable `wordpress@1` profile unless the user
chooses the no-profile legacy mode. It MUST NOT silently add extension requirements
to an existing project. The generated form uses canonical `{state,version}` objects;
human shorthand is accepted on input and normalized before validation.

`sb status --json`, `sb doctor --json`, and their text counterparts MUST report the
requested profile, normalized digest, safe provenance identities, and web/WP-CLI/
bounded-exec/PHPUnit observations when the field is present. They MUST distinguish
missing, version-mismatch, unobservable, unsupported-provisioning, unsupported-
disable, and plane-drift failures. The omission path retains the legacy response
shape. A generic Compose project that supplies `phpExtensions` receives a stable
unsupported-capability error before any image or package operation.

All extension diagnostics follow the one-document JSON/stdout rule above; progress
and build/package details go to stderr and never include credentials, tokens, URLs
with embedded auth, private source contents, arbitrary shell, or untrusted manifest
text interpreted as instructions.
