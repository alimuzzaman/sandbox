# Claude corpus summary

Snapshot: 2026-08-24. This is a read-only, local-only inventory of Claude
JSONL files under the local Claude project store. Selection was limited to the
encoded Sandbox root and encoded T3 worktree roots; cloud history, team history,
other owners, and unrelated project roots were not read.

The output is intentionally aggregated. It does not persist prompts, assistant
outputs, tool-input values, attachment values, secrets, tokens, cookies, private
keys, working-directory values, branch names, or session/record identifiers.

## Method and pilot gate

The pilot read one file from the Sandbox root and one file from a T3 worktree.
Each line was parsed as one JSON object, top-level shapes were inspected, UUID
and exact-line duplicate checks were run, and timestamp ordering was measured.
The pilot was clean for parsing and redaction, so the same metadata-only pass was
expanded to every selected root. File line order was retained; it was not
rewritten into chronological order.

| pilot | lines/records | malformed lines | exact duplicate line occurrences* | duplicate UUID occurrences* | timestamped records | adjacent timestamp inversions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sandbox (one file) | 33/33 | 0 | 0 | 0 | 30 | 2 |
| T3 worktree (one file) | 2,080/2,080 | 0 | 92 | 0 | 1,871 | 68 |

The T3 pilot's 92 repeated lines were one repeated `mode` marker shape, not
repeated UUID-bearing conversation records. This distinction is preserved in
the full-corpus duplicate metrics below.

## Corpus totals

| measure | result |
| --- | ---: |
| selected source roots | 21 (1 Sandbox, 20 T3 worktrees) |
| JSONL files | 64 |
| parsed records | 37,027 |
| malformed lines | 0 |
| records with a parseable timestamp | 32,666 |
| distinct session IDs | 64 |
| timestamp span (timestamped records only) | 2026-07-22T06:34:37.879Z to 2026-08-24T15:02:12.324Z |
| adjacent timestamp inversions | 946 |
| exact duplicate line occurrences beyond the first | 1,888 across 61 repeated hashes |
| duplicate UUID occurrences beyond the first | 6 across 6 UUIDs |
| top-level key-set schema variants | 86 |

Exact duplicate lines are common marker/state rows (especially `mode` and
`atis-latch` records) and are not treated as duplicate conversations. The six
duplicate UUID occurrences are confined to one T3 file. Duplicate metrics are
diagnostic counts, not a deduplication instruction.

## Source-root manifest

The identifiers below retain only the project class and final encoded worktree
suffix. The common home-directory and Claude-store path components are omitted.
Dates are day-level to keep the manifest compact. `dup-lines` means exact JSONL
line repeats beyond the first in that root; `dup-UUID` has the same meaning for
UUID-bearing records.

| source ID | files | records | date span (UTC) | malformed | dup-lines | dup-UUID |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| sandbox | 28 | 13,990 | 2026-07-22 to 2026-08-22 | 0 | 668 | 0 |
| t3/lenzora-3c344294 | 1 | 1,294 | 2026-08-05 | 0 | 57 | 0 |
| t3/templately-backend-a60c354e | 1 | 120 | 2026-08-10 | 0 | 1 | 0 |
| t3/templately-frontend-3e9fbd00 | 1 | 506 | 2026-07-27 | 0 | 28 | 0 |
| t3/templately-01c8b61e | 2 | 284 | 2026-07-28 to 2026-07-29 | 0 | 3 | 0 |
| t3/templately-1cbaceb5 | 1 | 134 | 2026-08-04 to 2026-08-05 | 0 | 0 | 0 |
| t3/templately-211b010b | 1 | 1,260 | 2026-08-02 to 2026-08-03 | 0 | 74 | 0 |
| t3/templately-2ac4d684 | 1 | 70 | 2026-08-02 | 0 | 0 | 0 |
| t3/templately-339ea762 | 1 | 381 | 2026-07-26 | 0 | 0 | 0 |
| t3/templately-360e3021 | 2 | 6,820 | 2026-07-29 to 2026-08-11 | 0 | 427 | 6 |
| t3/templately-47a0738f | 1 | 2,080 | 2026-08-02 to 2026-08-03 | 0 | 92 | 0 |
| t3/templately-5ceae060 | 14 | 1,219 | 2026-07-24 to 2026-08-04 | 0 | 141 | 0 |
| t3/templately-66c0ae9a | 1 | 87 | 2026-07-26 | 0 | 0 | 0 |
| t3/templately-68386d36 | 1 | 1,038 | 2026-08-06 | 0 | 35 | 0 |
| t3/templately-71342f34 | 1 | 863 | 2026-08-13 to 2026-08-16 | 0 | 46 | 0 |
| t3/templately-868a77b4 | 1 | 1,196 | 2026-07-28 | 0 | 40 | 0 |
| t3/templately-9bd38e3f | 1 | 2,464 | 2026-07-29 | 0 | 126 | 0 |
| t3/templately-ab31667d | 1 | 1,406 | 2026-08-24 | 0 | 104 | 0 |
| t3/templately-bc25cd0d | 1 | 602 | 2026-08-03 | 0 | 3 | 0 |
| t3/templately-f2930199 | 2 | 840 | 2026-08-20 | 0 | 34 | 0 |
| t3/templately-ff1d192a | 1 | 373 | 2026-07-29 | 0 | 9 | 0 |

## Schema families and variants

The parser observed 86 distinct top-level key sets. The table groups those
sets by record `type`; optional fields explain compatibility and lifecycle
variants without exposing their values.

| type | records | key-set variants | observed optional/variant fields (names only) |
| --- | ---: | ---: | --- |
| assistant | 16,858 | 21 | `requestId`, `slug`, `attributionSkill`, `attributionMcpServer`, `attributionMcpTool`, `session_id`, API-error fields |
| user | 10,556 | 34 | `toolUseResult`, `sourceToolAssistantUUID`, `slug`, `promptSource`, `permissionMode`, `session_id`, metadata/denial/interruption fields |
| attachment | 3,190 | 4 | `slug`, `session_id` |
| last-prompt | 2,366 | 2 | `lastPrompt` absent on a small marker subset |
| mode | 1,537 | 1 | none observed |
| queue-operation | 1,495 | 2 | `content` |
| system | 492 | 15 | hook lifecycle fields, `level`, `isMeta`, `slug`, `content`, duration/message-count fields, retry/error fields |
| permission-mode | 127 | 1 | none observed |
| ai-title | 107 | 1 | none observed |
| atis-latch | 150 | 1 | none observed |
| pr-link | 57 | 1 | none observed |
| file-history-snapshot | 48 | 1 | none observed |
| agent-name | 26 | 1 | none observed |
| file-history-delta | 18 | 1 | none observed |

The common assistant/user/system fields (for example `message`, `uuid`,
`timestamp`, `sessionId`, and provenance envelope fields) were present across
their respective families. Field names are reported only to describe schema
shape; values were not retained.

## Claude tool and Sandbox command signatures

Tool names and command signatures below are counts of observed transcript
metadata. They are not execution telemetry. The command pass tokenized
assistant `Bash`/`Monitor` inputs, retained only allow-listed Sandbox command
words and flag names, and discarded all argument values.

### Built-in content blocks

The corpus contains 9,831 assistant content blocks with 45 names. The most
frequent names were:

| name | blocks |
| --- | ---: |
| `Bash` | 7,043 |
| `Edit` | 1,028 |
| `Read` | 533 |
| `Write` | 376 |
| `TaskUpdate` | 87 |
| `Skill` | 82 |

`attributionSkill=sandbox-cli` occurred on 103 assistant records (all in one
T3 root), and one explicit `Skill(sandbox-cli)` tool call was observed. No
`mcp__sandbox__*` content-block name or Sandbox MCP attribution server was
observed in this local selection. That absence does not prove that no such
tool was used outside the selected files.

### Normalized Sandbox CLI signatures

After filtering prose and assignment text, there were 929 lexical `sb`/`./sb`
executable-token hits: 343 in the Sandbox root and 586 in T3 roots. The
extractor encountered 193 shell tokenization errors, so these are lower-bound
and approximate counts rather than execution telemetry.

| normalized signature | hits |
| --- | ---: |
| `sb wp` | 266 |
| `sb test` | 236 |
| `sb selftest` | 101 |
| `sb secrets` | 32 |
| `sb remote` | 29 |
| `sb ensure` | 27 |
| `sb apply` | 20 |
| `sb domains` | 19 |
| `sb status` | 15 |
| `sb instances` | 14 |
| `sb feedback` | 14 |
| `sb host apply` | 14 |
| `sb host` | 13 |
| `sb visit` | 10 |
| `sb resources status` | 9 |
| `sb job-cleanup` | 8 |
| `sb job-status` | 7 |

Flag values, request IDs, paths, and command bodies were discarded. Flag-name
counts are intentionally omitted because nested test/shell options make them
less reliable than the normalized command signatures above.

## Coverage limitations

- The corpus is local Claude JSONL only. It excludes cloud/team history,
  unavailable files, deleted or moved roots, and every project root outside the
  encoded Sandbox/T3 selection.
- All 64 selected files were readable and had zero malformed lines, but that is
  not evidence that the files are complete or that an omitted root is empty.
- 4,361 records have no parseable timestamp. Timestamp order is not a reliable
  chronology: 946 adjacent inversions were observed, likely reflecting
  asynchronous writes and lifecycle markers.
- Exact marker repeats are counted rather than removed. The six duplicate UUID
  occurrences may represent continuation/replay artifacts; this pass does not
  adjudicate them.
- Sandbox command signatures are lexical observations from embedded command
  strings. They do not prove that a command ran, completed, succeeded, or used a
  particular host, remote, permission mode, or argument value; 193 inputs could
  not be tokenized cleanly.
- Tool inputs, tool results, prompts, outputs, attachments, cwd values, branch
  names, and identifiers were intentionally not retained, so this summary
  cannot support content-level or success/failure conclusions.

Verification for this file: `git diff --check --
docs/audits/2026-08-24-sandbox-agent-tool-audit/claude-corpus-summary.md` was
run. Because the file is new and untracked, the content-aware check was also
run as `git diff --no-index --check /dev/null <path>`; it produced no
whitespace diagnostics (the expected exit status was only the differing-files
status).
