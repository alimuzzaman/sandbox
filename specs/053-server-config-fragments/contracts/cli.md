# CLI Contract: `sb server config`

## Command Ownership and Compatibility

The top-level `server` command is registered by one feature-owned `CommandSpec`.
Its parser accepts these legacy switch forms unchanged:

```text
sb server <server-type>
sb server <instance-name> <server-type>
```

`<server-type>` keeps the existing `apache|nginx|litespeed|herd` choices and switch
behavior. The literal first token `config` enters the fragment grammar and can never
be interpreted as an instance name. Invalid or ambiguous token shapes fail before
instance/runtime/state mutation.

Global `--instance`/`--label` precedence remains authoritative for config operations.
A positional instance is not accepted inside `server config`; use the existing global
selector or run from the owning project directory.

## Public Operations

```text
sb server config apply \
  --name <id> \
  [--authority wordpress-cache-v1] \
  (--file <path> | --stdin) \
  [--json]

sb server config list [--json]

sb server config show \
  --name <id> \
  [--content | --output <path>] \
  [--json]

sb server config revert \
  --name <id> \
  [--json]
```

Rules:

- `--authority` defaults to and accepts only `wordpress-cache-v1` in v1.
- `--file` and `--stdin` are required and mutually exclusive. `--file -` is not an
  alias for stdin. The file basename may appear in a human input error; its parent path
  never appears in routine output.
- File input must be one stable regular file opened without symlink following. Stdin is
  consumed once with the same byte/deadline bound. Neither form is parsed as argv,
  environment, interpolation, or shell text.
- `show` without a content option returns metadata only.
- `show --content` writes only the exact fragment bytes to stdout. It emits no heading,
  newline, JSON, progress, or success message. Errors occur before any byte is emitted.
- `show --content --json` is invalid.
- `show --output PATH` writes exact bytes to a safe owner-only regular-file destination
  and may combine with `--json`; the JSON remains content-free and reports only a safe
  destination basename. Symlink, special-file, non-owner, unsafe-parent, or unstable
  destinations are refused before replacement.
- `list` and metadata `show` request the command-owned read-only pre-dispatch path. They
  perform no auto-migration, Compose/environment regeneration, recovery, pruning,
  timestamp update, or feature-state write.
- All mutations use one whole-operation 180-second deadline. Each validation,
  activation/readiness, and rollback subdeadline is at most 60 seconds.

## Instance and Capability Refusals

Before fragment-state access, the command refuses:

- missing, unknown, ambiguous, generic, remote, or identity-mismatched instance;
- a stopped/unknown target for mutation;
- unsupported server (`apache` or `herd` in v1);
- missing incarnation or server-config mount identity;
- running container without the exact instance mount (guidance:
  `sb apply --instance NAME`);
- server/runtime/image drift, corrupt state, or unresolved transaction.

Routine errors use a stable code, bounded message, safe next action, `ok:false`, and
`mutated:false` when no live state may have changed. No error includes fragment bytes,
caller path, native stderr, Compose/container details, or credentials.

## Human Output

Human metadata output is one bounded phase summary containing:

- selected instance name and opaque incarnation ID;
- active server type (`nginx` or `litespeed`);
- operation and normalized name;
- authority, content ID, and fragment-set/generation ID where applicable;
- terminal mutation outcome;
- validation, activation/reload, readiness, and rollback phase status;
- one safe next action for refusal, degraded, or recovery-needed results.

`list` is sorted by normalized name. It never prints content, private locators, source
paths, image tags, raw image/container IDs, or native diagnostics.

## Structured Result

Every metadata operation returns one JSON object. Exact-content stdout is the only mode
without this envelope.

```json
{
  "ok": true,
  "mutated": true,
  "operation": "apply",
  "outcome": "active",
  "instance": {
    "name": "plugin-a1b2c3d4e5",
    "incarnation_id": "sci_opaque",
    "server_type": "nginx"
  },
  "fragment": {
    "name": "xspeed-static-cache",
    "authority": "wordpress-cache-v1",
    "content_id": "sha256:...",
    "size": 2048,
    "state": "active"
  },
  "fragment_set": {
    "id": "sha256:...",
    "generation_id": "sha256:...",
    "count": 1,
    "health": "healthy"
  },
  "phases": {
    "policy": {"status": "accepted", "code": "authority_accepted"},
    "validation": {"status": "succeeded", "code": "candidate_valid"},
    "activation": {"status": "succeeded", "code": "generation_activated"},
    "reload": {"status": "succeeded", "code": "server_reloaded"},
    "readiness": {"status": "succeeded", "code": "server_ready"},
    "rollback": {"status": "not_required", "code": "rollback_not_required"}
  },
  "transaction_id": "sct_opaque",
  "next_action": null
}
```

Content-free guarantees:

- `fragment.content_id` is a digest, never a substring or encoding of content.
- `phases` contain allowlisted status/code/timing/digest fields only.
- Native stdout/stderr and user source/destination paths are never copied.
- `show --output --json` adds
  `"export":{"written":true,"basename":"fragment.conf"}` only.

## Outcomes and Exit Status

| Outcome | `ok` | `mutated` | Exit | Meaning |
|---|---:|---:|---:|---|
| `active` | true | true | 0 | Candidate proven ready and committed |
| `no_op` | true | false | 0 | Exact healthy state already satisfied request |
| `rolled_back` | false | true | nonzero | Candidate failed; exact prior state restored and proven |
| `refused` | false | false | nonzero | Input/policy/validation/precondition refused before live change |
| `conflict` | false | false | nonzero | Per-instance writer lock unavailable inside bound |
| `recovery_needed` | false | possible | nonzero | Active state cannot be proven; later mutations blocked |
| `degraded` | false | false | nonzero | Read-only inspection found mismatch/corruption without repair |

`mutated:true` on `rolled_back` means live activation was attempted, not that the
requested candidate remains active. `recovery_needed` uses `mutated:null` when whether
the live server changed is itself unknown.

## Stable Refusal Families

- `instance_*`: resolution, incarnation, runtime, mount, lifecycle
- `fragment_name_*`, `fragment_source_*`, `fragment_size_*`, `fragment_encoding_*`
- `authority_*`: common or adapter policy violation
- `server_unsupported`, `server_mismatch`, `server_switch_blocked`
- `state_*`: missing, corrupt, drifted, interrupted, recovery needed
- `validation_*`: exact-image, native syntax, inclusion, timeout
- `activation_*`, `reload_*`, `readiness_*`
- `rollback_*`
- `operation_conflict`, `operation_timeout`
- `content_output_*`

Specific codes are enumerated in implementation tests and docs; adding/removing a public
code is a public contract change requiring docs, tests, and runtime revision evidence.
