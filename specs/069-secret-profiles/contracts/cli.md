# Contract: CLI grammar

New action `ownership` under `sb secrets` (added to `ACTIONS` in `sandbox/commands/secrets.py`), plus two options on `run`. No MCP tool is added; MCP secret tools stay unchanged (FR-051). All commands accept `--project-dir`, `--json`.

```
sb secrets ownership classify  --profile P                         # dry-run, credential-free unless --check-destination
                                [--check-destination]              # value-free existence via read grant -> would_create/pre_existing/unknown
sb secrets ownership credential-set --profile P --capability read|create --stdin [--create-only|--replace-only]
                                                                   # operator stores bootstrap client secret in OS store (TTY or stdin pipe; never argv)
sb secrets ownership evidence-check --profile P --evidence FILE    # parse + admission report (operator_attested), no provider call
sb secrets ownership approve   --profile P --operation migrate|cleanup
                                [--evidence FILE] [--minutes 1..60]  # human-only TTY; prints approval_id
sb secrets ownership migrate   --profile P --approval ID [--confirm] [--timeout-seconds 1..3600]
                                                                   # without --confirm: dry-run plan only (FR-008)
sb secrets ownership status    --profile P                         # journal + last run record, names/states only
sb secrets ownership cleanup   --profile P --approval ID [--confirm]
sb secrets ownership retention --profile P [--confirm]             # removes completed run records/journals with no unknown/unresolved names
sb secrets run --ownership-profile P --ownership-key NAME [--ownership-key NAME ...]
               [--timeout-seconds N] -- argv...                   # mutually exclusive with --source/--key/--secret/--destination/--session
```

Rules: `approve` refuses (`approval_human_only`, exit 3) without interactive TTY on stdin and stdout in the foreground process group, or when invoked through MCP. `migrate`/`cleanup` run non-interactively inside an unexpired approval. `--ownership-profile` without `--ownership-key` → `ownership_keys_required` (exit 2), zero values read.

Exit codes: 0 success / dry-run ok; 2 usage or classification blocking; 3 authority refusal (approval missing/expired/mismatch/human-only, evidence inadmissible, continuity changed/unreliable, credential scope); 4 provider contract/storage refusal; 5 run stopped with conflict/unresolved/acceptance_unknown (journal kept); 6 cleanup_unknown after otherwise successful run (revocation not confirmed); child exit code for scoped run (as existing `run`).

JSON output: `{"ok":bool,"code":str,"profile":str,"counts":{},"names":{state:[...]},"approval_id"?,"run_id"?,"revocation"?,"next_action"?,"storage_attestation":"operator_attested"?}`. Never values, hashes of values, lengths, prefixes, `r1_` revisions.

Refusal codes (stable): `ownership_not_configured, profile_unknown, classification_blocked, source_unregistered, source_unsupported, source_changed, continuity_unreliable, approval_required, approval_expired, approval_mismatch, approval_human_only, authority_mismatch, evidence_required, evidence_inadmissible, evidence_expired, provider_contract_unconfirmed, provider_policy_requires_approval, existence_unknown, pre_existing, journal_locked, journal_foreign_controller, journal_missing_owner_elsewhere, credential_missing, credential_scope_invalid, other_domain, ownership_keys_required, cleanup_ineligible`.

`--ownership-key NAME` selects the entry's mapped destination `name`, not a
consumer-environment alias or an arbitrary source path. Names are unique within
one profile. Duplicate consumer destinations within one profile are blocking
mapping conflicts; consumer names may be reused across isolated profiles.
Validate the selected names, destinations and argv before any value read.

Additional fixed refusal codes: `journal_corrupt` (exit 3), `provider_failed`
and `response_invalid` (exit 4). Classification BlockReason `unregistered_source`
maps to CLI `source_unregistered`. No approval MCP tool exists: operator-only
approval is enforced by foreground TTY checks at the CLI; non-TTY agent/MCP
subprocesses refuse, with the documented same-UID trust limitation.
