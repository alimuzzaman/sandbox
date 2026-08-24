# L1.1 synthetic fixtures

This directory is the isolated, audit-only output for Luna task L1.1. Every
value is invented for contract testing. These files do not come from a
transcript, application store, browser store, credential source, remote host,
or Sandbox runtime, and they do not implement a parser.

## Versioned files

- `synthetic-events.jsonl` is the line-oriented input fixture. It has 19
  physical lines; line 18 is intentionally malformed JSONL so a line-level
  parser can exercise an explicit malformed terminal state.
- `expected-normalized.jsonl` contains the 15 expected emitted rows in input
  order.
- `expected-exclusions.jsonl` contains the two duplicate rows, one unsupported
  record row, and one malformed row.
- `expected-accounting.json` is the hand-reviewed terminal accounting and
  ordering contract.

The schema identifiers are `audit-fixture-v1` for synthetic inputs,
`audit-normalized-v1` for expected rows, and `audit-accounting-v1` for the
manifest. They are fixture contracts only; no public Sandbox schema or command
is being introduced.

## Coverage map

| Input line(s) | Contract branch | Expected terminal state | Expected row |
| ---: | --- | --- | --- |
| 1–4 | Codex initial/rollover records; line 3 repeats the event key from line 2 | emitted, duplicate, emitted | normalized, exclusions |
| 5–6 | Claude text plus tool content block; line 6 is a replay | emitted, duplicate | normalized, exclusions |
| 7 | Nested sensitive-looking values under a tool argument | emitted with nested values redacted | normalized |
| 8 | Formula-leading labels (`=`, `+`, `-`, `@`) | emitted with formula-safe values | normalized |
| 9 | Timestamp field absent | emitted with timestamp unavailable | normalized |
| 10 | Timestamp goes backwards relative to the last present timestamp | emitted; file order is retained | normalized |
| 11–14 | Partial, unknown, blocked, timeout, unverified, and ambiguous status combinations | emitted without collapsing status layers | normalized |
| 15–16 | Parent and child candidate signals without an approved join | emitted with relation state unknown | normalized |
| 17 | Unsupported future record kind | excluded | exclusions |
| 18 | Malformed JSONL | malformed | exclusions |
| 19 | Transport unavailable while other status layers are unknown | emitted with unavailable/unknown distinctions | normalized |

## Terminal accounting

The hand-reviewed identities are:

```text
input_records = malformed + duplicate + excluded + emitted
19 = 1 + 2 + 1 + 15

parsed_records = duplicate + excluded + emitted
18 = 2 + 1 + 15
```

The same identities are recorded per source in `expected-accounting.json`. The
malformed line is assigned to an `unknown` bucket there because a failed JSON
decode cannot safely establish its source label.
`malformed`, `duplicate`, `excluded`, and `emitted` are disjoint terminal
states. A missing timestamp, an unknown outcome, a partial transport, an
unverified task, and an unverified parent/child signal are emitted uncertainty,
not exclusions or inferred success. No record is globally sorted by timestamp;
the explicit event index and file order remain authoritative, including the
intentional inversion on line 10.

The four status layers stay separate in every expected normalized row:

| Layer | Examples in this fixture | Meaning |
| --- | --- | --- |
| `transport_status` | `completed`, `partial`, `unavailable` | Delivery/transport evidence |
| `tool_call_status` | `completed`, `failed`, `partial`, `unknown` | Tool invocation evidence |
| `command_exit_status` | `success`, `failure`, `timeout`, `unknown` | Command/process receipt |
| `task_outcome` | `completed`, `blocked`, `unverified`, `ambiguous`, `unknown` | User-task evidence |

A completed tool or successful command never upgrades an unknown or ambiguous
task outcome.

## Redaction and formula expectations

The input deliberately nests keys that look sensitive, but all such values are
the literal placeholder `<redacted>`. Expected normalized rows retain only
bounded signatures such as `metadata: redacted`; they never carry the nested
values. The fixture contains no UUID-like identifier, private path, private URL,
credential value, cookie value, private key, environment value, or copied
transcript prose. Synthetic source references are not reversible source IDs and
there is no reverse map.

Formula-leading input labels are represented in the expected normalized row
with a leading apostrophe (`'=` / `'+` / `'-` / `'@`) so a later tabular writer
cannot evaluate them. The expected row records only the safe projection, not an
unbounded argument or message body.

Parent and child candidates carry an explicitly unverified join signal. The
expected rows preserve the signal class but set `relation_state` to `unknown`
and do not infer a parent relation from a missing request/job identifier.

## Checks for this slice

The acceptance checks are intentionally local and read-only:

1. Parse each physical line with a JSON decoder, accepting exactly one known
   malformed line (line 18) and 18 valid records.
2. Verify the expected normalized and exclusion row counts and the arithmetic
   identities in `expected-accounting.json`.
3. Run the audit-wide forbidden-field scanner from the parent directory. It
   must report zero matches across content and filenames.
4. Run `git diff --check` plus a no-index whitespace check for each new file.

These checks inspect only the synthetic fixture directory and durable audit
documents. They do not access any approved corpus root or runtime.
