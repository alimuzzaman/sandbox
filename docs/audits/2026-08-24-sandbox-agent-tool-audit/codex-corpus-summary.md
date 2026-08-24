# Codex corpus summary

Snapshot: 2026-08-24T16:47:19Z (UTC). This is a read-only, local-only
inventory of Codex rollout JSONL ledgers. Selection was limited to
JSONL records in `CODEX-LOCAL-EXACT-CWD` whose session metadata exactly
identified `SANDBOX-CHECKOUT`. Transcript bodies, prompts, tool arguments and results,
tokens, cookies, credentials, private keys, and sensitive path components were
not written to this report.

## Method and pilot gate

The pilot used three small exact-CWD ledgers selected from the current-date
sample. It parsed 244 records, found zero malformed records, observed
nondecreasing event timestamps in each file, and found no within-file session
ID duplicates. It exercised both `custom_tool_call`/`custom_tool_call_output`
and `function_call`/`function_call_output` shapes, including an inter-agent
metadata shape. A generic secret-like pattern probe matched 33 untrusted raw
lines; only the count was retained and no matched value was emitted. Ordering,
redaction, and duplicate checks passed, so the full bounded pass proceeded.

The full pass used a structural `jq` projection. It retained only metadata,
event kinds, status/exit-code fields, normalized Sandbox signatures, and
boolean text-hint flags. The projection discarded command bodies, argument
values, output text, and nested payload contents before Python aggregation.

## Corpus totals

| measure | result |
| --- | ---: |
| candidate rollout files | 2,125 |
| exact-CWD files included | 549 |
| candidate bytes | 8,285,118,338 (about 7.71 GiB) |
| included bytes | 1,766,298,073 (about 1.64 GiB) |
| unique session IDs | 75 |
| session metadata records | 3,351 |
| timestamp span (UTC) | 2026-07-14T18:25:39.229Z to 2026-08-24T16:45:58.700Z |
| malformed included files / parse errors | 0 / 0 |

The count is a point-in-time snapshot. The current rollout directory can grow
while an audit is running; a later scan may therefore have a different file or
byte count.

## Duplicate and malformed ledger

Repeated metadata is common in rollover/continuation ledgers and was counted,
not silently collapsed:

- 3,276 metadata rows were repeats beyond the 75 unique session IDs.
- 340 included files repeated session metadata; the largest had 66 metadata
  rows.
- 16 session IDs occurred in more than one included file, accounting for 490
  file-memberships. The largest duplicate groups spanned 227, 59, and 42
  files. These IDs are represented only by short SHA-256 prefixes in this
  ledger (`4c8c068f5a91`, `51dc492c0480`, and `d5e276113b09`).
- No included metadata row lacked a session ID. The structural parser reported
  no malformed included file or JSON parse error.

These are duplicate-ledger diagnostics, not evidence that the underlying
conversation ran more than once. Repeated metadata can reflect rollover or
child-agent lifecycle records.

## CommandExecution completions

The pass identified `event_msg` records with `type=item_completed` and
`item.type=CommandExecution` as CommandExecution completions:

| measure | result |
| --- | ---: |
| completions | 3,092 |
| sessions with one or more completions | 5 |
| completed status | 2,925 |
| failed status | 167 |
| explicit exit code 124 (timeout convention) | 1 |

Other nonzero exit codes were observed (notably 1, 2, 126, 127, 128, 130,
143, 255, and a small number of higher numeric codes). Status/exit-code counts
are the failure evidence; text-only error/timeout words are reported below as
heuristics and are not promoted to failures.

## Sandbox CLI and MCP signatures

The full pass observed 1,375 normalized Sandbox calls. A CLI signature is the
first safe command word following an `sb`/`./sb` executable token. An MCP
signature is the `sandbox/<tool>` server/tool pair. Argument values and command
tails were discarded. The highest-volume CLI signatures were:

| normalized CLI signature | calls |
| --- | ---: |
| `resources` | 138 |
| `remote` | 97 |
| `test` | 60 |
| `workspace` | 59 |
| `job-output` | 52 |
| `job-status` | 47 |
| `job-list` | 37 |
| `job-start` | 25 |
| `guide` | 24 |
| `logs` | 23 |
| `status` | 23 |
| `mcp` | 22 |
| `cache` | 15 |
| `instances` | 15 |
| `connect` | 13 |
| `hermes` | 13 |
| `selftest` | 13 |
| `exec` | 12 |
| `mcp-install` | 12 |
| `abilities` | 11 |
| `snapshot` | 11 |
| `apply` | 6 |
| `doctor` | 3 |
| `workspace`-adjacent/option forms (`--help`, `-h`, `--instance`) | 19 |

Additional low-volume CLI signatures included `async-job`, `ci`, `claude`,
`clean`, `command`, `dashboard`, `deploy`, `domains`, `down`, `dump`, `e2e`,
`ensure`, `focus`, `focus_get`, `global`, `home`, `host`, `init`, `install`,
`instance`, `introspect`, `job`, `job-artifact-get`, `job-artifacts`,
`job-cancel`, `job-matrix`, `job-metrics`, `job-reconcile`,
`job-reconciliation`, `job-retention`, `job-retry`, `jobs`, `license`, `ls`,
`migrate`, `native`, `onboard`, `open`, `plugin-check`, `preview`, `pxdiff`,
`qm`, `recovery`, `remote`, `reset`, `restore`, `secrets`, `secure`, `seed`,
`server`, `setup`, `shell`, `skill`, `smoke`, `snapshots`, `specdiff`,
`specextract`, `specgate`, `stop`, `tail_log`, `ui`, `uninstall`, `up`,
`update`, `visit`, `vrdiff`, `web`, `wp`, and `xdebug`. A few option/prose
forms were retained as observed signatures and should not be interpreted as
successful command executions.

The observed MCP signatures were:

| MCP signature | calls |
| --- | ---: |
| `sandbox/ensure_instance` | 22 |
| `sandbox/hermes_worktree_inspect` | 14 |
| `sandbox/focus_get` | 12 |
| `sandbox/http_fetch` | 9 |
| `sandbox/load_workflow` | 7 |
| `sandbox/list_mcp_resources` | 7 |
| `sandbox/instance_status` | 6 |
| `sandbox/run_plugin_check` | 6 |
| `sandbox/load_skill` | 4 |
| `sandbox/hermes_status` | 2 |
| `sandbox/hermes_cron_list` | 2 |
| `sandbox/apply_config` | 2 |
| `sandbox/hermes_worktree_list` | 2 |
| `sandbox/hermes_cron_output` | 1 |
| `sandbox/instance_exec` | 1 |
| `sandbox/instance_logs` | 1 |
| `sandbox/list_mcp_resource_templates` | 5 |
| `sandbox/load_context` | 1 |
| `sandbox/run_tests` | 4 |
| `sandbox/tail_log` | 1 |
| `sandbox/visit` | 1 |
| `sandbox/wp_cli` | 6 |
| `sandbox/wp_eval_live` | 5 |
| `sandbox/wp_exec` | 3 |
| `sandbox/wp_rest` | 3 |

## Failures, timeouts, retries, and poll loops

- Explicit CommandExecution status/exit evidence was 167 failures and one
  exit-code-124 timeout.
- Boolean text hints (searched only inside discarded output/error fields) were
  19,542 failure-like, 11,036 timeout-like, and 8,290 retry-like matches.
  These are deliberately labeled heuristic: command output and agent prose can
  mention an error, timeout, or retry without representing the enclosing call's
  result.
- There were 321 adjacent repeated normalized Sandbox signatures. This is a
  mechanical retry/repeat signal, not proof of an intentional retry.
- Poll-like signatures (`job-status`, `job-output`, status/wait/poll variants)
  occurred 130 times; six sessions repeated the same poll signature at least
  twice. No raw sleep interval or job ID was retained.

## Parent/subagent signals

The parser retained only safe lifecycle markers:

| signal | result |
| --- | ---: |
| sessions with `parent_thread_id` metadata | 16 |
| sessions with child-agent markers (`agent_path`, nickname, or subagent source) | 16 |
| inter-agent metadata records | 3,439 |
| sessions with inter-agent metadata | 14 |
| event records labeled `agent_message` | 110,173 |
| event records labeled `sub_agent_activity` | 60,562 |

These markers support a parent/child signal, not a reconstructed conversation
graph. Parent IDs and agent paths were not persisted.

## Safe-source coverage

The current audit safe source is
`CODEX-SRC-bfd3327034a9366b6d43`. It is present in the exact-CWD corpus
and has no source-specific finding file yet.

The audit directory currently contains seven source-specific files. Six of those
references occur in the exact-CWD corpus; one existing file is outside this exact-CWD
selection: `CODEX-SRC-d0c49010c51e6c34fd86`. Conversely, 69 of the 75
unique exact-CWD sessions have no source-specific file. Nineteen sessions meet
the mechanical substantive criterion (at least one normalized Sandbox call or
CommandExecution completion) and remain unfiled.

Notable unfiled safe sources, ranked by normalized Sandbox-call count, are:

| safe source | Sandbox calls | poll calls | adjacent repeats |
| --- | ---: | ---: | ---: |
| `CODEX-SRC-b9c191e50ec57d8aadc6` | 445 | 15 | 90 |
| `CODEX-SRC-78883bcf117688eb6877` | 22 | 1 | 0 |
| `CODEX-SRC-b503bb3ba6cb5c509d5d` | 20 | 0 | 11 |
| `CODEX-SRC-a172ccfba1dd7513689e` | 19 | 0 | 8 |
| `CODEX-SRC-1adcb78e4465af1e9735` | 18 | 2 | 0 |
| `CODEX-SRC-76f785a5f1f3d1a887d3` | 17 | 0 | 0 |
| `CODEX-SRC-144f099476278c360415` | 15 | 3 | 0 |
| `CODEX-SRC-21aac41299f994d47b7a` | 14 | 0 | 0 |
| `CODEX-SRC-cfb13584fc1ead9a7aed` | 11 | 0 | 0 |
| `CODEX-SRC-dbd99f56be79640d57fb` | 10 | 4 | 0 |
| `CODEX-SRC-4a1df30868e08779c923` | 7 | 0 | 0 |
| `CODEX-SRC-06e49586081e11986d0a` | 7 | 0 | 0 |

The current audit ID is also substantive by the broader completion criterion
(648 CommandExecution completions), despite having no normalized Sandbox call
in this projection; it is listed separately above rather than treated as a
Sandbox-call ranking result.

## Limits and interpretation

- This is a local Codex rollout corpus, not product telemetry. A recorded call
  signature does not prove that the command ran, succeeded, reached a host, or
  used a particular argument.
- Exact-CWD matching is metadata-based. Files with no parseable matching
  `session_meta` were excluded; no attempt was made to infer scope from prose.
- Rollover files can repeat metadata and session IDs. The pass reports those
  repetitions and does not delete, merge, or rewrite source ledgers.
- Text-hint, adjacent-repeat, parent, and child labels are bounded mechanical
  signals. They are not semantic judgments about user intent, causality, or
  completion.
- The current audit transcript and the listed notable IDs are identifiers only;
  no transcript content is reproduced.

Verification: `git diff --check --
docs/audits/2026-08-24-sandbox-agent-tool-audit/codex-corpus-summary.md` was
run after writing this file. For an untracked file, the equivalent no-index
check is `git diff --no-index --check /dev/null <path>`; the expected differing-
files status is not a content error.
