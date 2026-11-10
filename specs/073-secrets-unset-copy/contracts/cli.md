# CLI contract: secrets unset, secrets copy, set --skip-unchanged

## sb secrets unset

```text
sb secrets unset --source ALIAS KEY [--if-revision REV] [--json] [--project-dir DIR]
```

- Success (exit 0, `--json`): `{"ok": true, "operation": "unset", "action": "removed",
  "source": ALIAS, "key": KEY, "revision": "r1_...", "correlation_id": "..."}`.
- Human output: `secrets unset: ok` then `  removed: KEY`.
- Refusals (exit 1): `update_denied` (provider source), `update_unsupported`
  (non-dotenv), `key_invalid`, `source_unknown`, `key_missing`, `revision_conflict`,
  `journal_locked`, `continuity_unreliable`, `syntax_unsupported`, `duplicate_key`.
- Audit: intent then outcome, `operation: "unset"`, `keys: [KEY]`.

## sb secrets copy

```text
sb secrets copy --source ALIAS --key KEY --remote NAME --target-source ALIAS
                [--as NEWKEY] [--create-only | --replace-only]
                [--target-project-dir REMOTE_DIR] [--json] [--project-dir DIR]
```

- Success (exit 0): `action` is `created`, `updated` or `already_current`; plus
  `source`, `key`, `remote`, `target_source`, `target_key`, `remote_revision`.
- Human output: `secrets copy: ok` then `  <action>: KEY -> NAME:ALIAS/NEWKEY`.
- Refusals before any read (no audit intent): `update_denied` (provider source),
  `key_invalid`, `input_invalid` (bad remote name, alias or project dir),
  `remote_unknown`, `remote_not_provisioned`.
- Refusals after intent: `key_missing`, `value_unavailable`, `input_invalid` (value
  has a line break or is too large), `remote_unreachable`, `remote_timeout`,
  `remote_unsupported`, `remote_failed`, or the remote's own refusal code
  (`intent_conflict`, `source_unknown`, `update_denied`, `revision_conflict`, ...).
- Audit (controller): `operation: "copy"`, `keys: [KEY]`, `input_channel:
  "remote_stdin"`, `destination: "remote:NAME/ALIAS/NEWKEY"`; succeeded outcome
  `reason_code` = action. Audit (remote): its own `set` events.

## sb secrets set --skip-unchanged

Opt-in. When the key exists with a byte-identical value, nothing is written and
`action` is `already_current` with the current revision. Applies to `--stdin`, TTY,
`--from-ref` and `--generate` input.

## Never

No value in argv, environment, stdout, stderr, audit, logs or temp files on either
host. No automatic retry. No MCP surface.
