# Data Model: Test Execution Modes

## Test Mode

| Field | Values | Rule |
|---|---|---|
| requested | `auto`, `unit`, `integration`, or omitted | Validated before side effects |
| configured | `auto`, `unit`, `integration` | Read from `tests.suite`; default `auto` |
| resolved | `unit` or `integration` | Explicit/configured values pass through; auto uses bounded evidence |
| source | `explicit`, `config`, or `auto` | Additive observability only |

## Mode Evidence

Evidence is not persisted. It is a bounded read-only classification of project-local
files under the canonical root.

- `wordpress_markers`: presence of WordPress test bootstrap/classes/environment names
- `pure_unit_markers`: presence of Brain/Monkey references or package metadata
- `unsafe_paths`: candidate bootstrap/config path escapes or invalid path values
- `classification`: `unit`, `integration`, or `ambiguous`

## Test Run Result

The existing result envelope remains authoritative:

```text
ok: bool
passed: bool
summary: string | null
output: bounded string
mode: resolved mode
```

No new database, registry record, or persistent runtime state is introduced.

## State transitions

```text
requested/configured mode
        ↓ validate
bounded auto classification (only when needed)
        ↓
unit runner OR integration harness runner
        ↓
existing PHPUnit result + resolved mode
```
