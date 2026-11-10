# Quickstart: validating spec 077

Placeholders: remote `R`, template `<checkout>/sandbox.hosting.yml`, owners
`a@example.test` and `b@example.test`. Never run these against a guided or live
xspeed-dev target; live runs need the orchestrator's authorization and a migrated
runtime.

## Automated (run on xspeed-dev by a test helper)

```
sb job-start --remote xspeed-dev -- python3 -m unittest tests.test_target_lifetime tests.test_host_stack
```

Covers: record parse/validation, TTL parsing, eligibility, limits, claim races
(two processes), generated names, owner/copy refusals, merge check against a
temporary Git repo, the reaper against a temporary `SANDBOX_HOME` with a fake shell
runner (manifest intent lines precede every removal; hosted, permanent,
unclassified and invalid records untouched; `reaping` resumes).

## Manual (with authorization, on a scratch remote)

1. `sb host stack up --template T --remote R --plan` shows `lifetime: temporary`,
   `ttl_seconds: 1209600`, a generated slug and `owner`.
2. `--confirm`, then `sb host stack list --remote R` from a second controller shows
   the stack with the first owner.
3. From the second controller, `stack up --name <same slug> --confirm` →
   `stack_slug_taken`.
4. `sb host stack ttl --name S --remote R --ttl 1h --confirm`; after an hour
   `sb host stack reap --remote R --plan` lists it; `--confirm` reaps it and
   `runtime/resources/deletions/<run>.jsonl` holds the intents.
5. `sb host stack down --name S --remote R --confirm` removes the reaped tombstone
   and controller state.
6. `sb host classify --remote R --target <project>/<env> --lifetime permanent --confirm`
   on a scratch target; `stack reap --plan` never lists it.
