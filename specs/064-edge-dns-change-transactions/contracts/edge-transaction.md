# Contract: Edge transaction

## CLI

```
./sb host plan  <project> <env> [--json]
./sb host apply <project> <env> [--confirm] [--adopt-records] [--allow-zone-ssl-change] [--json]
./sb host edge-journal <project> <env> [--transaction ID] [--json]   # read-only inspection
./sb remote provision <name> ... [--control-dns-only | --control-transport tailscale] [--confirm]
```

- `--adopt-records` is the only flag that adopts. `--confirm` never adopts.
- `host plan` returns `data.dns`, with `{verdict, items:[plan item], zone:
  {ssl_mode: {current, required, action}}, leftovers:[...]}`.
- `host apply` returns `data.edge_transaction` (the transaction result).
  Failures use codes `edge_preflight_refused`, `edge_verification_failed`,
  `edge_rollback_incomplete` and `edge_leftovers_unresolved`, each carrying
  the per-item lists.
- MCP hosting tools return the same `data` shapes.

## Package API (`sandbox/edge_txn`)

```
desired.compute(validated, origin) -> DesiredZone
ownership.classify(desired, live_records, *, target, previews, journal_history) -> list[PlanItem]
plan.build(desired, items, leftovers, *, adopt: bool, allow_ssl: bool) -> Plan
journal.open(target, remote) -> Journal; Journal.intent/done/rollback/end; journal.leftovers(target, remote)
executor.apply(plan, client, edge_adapter, lease, journal) -> TransactionResult
executor.rollback(journal, client, edge_adapter, lease) -> TransactionResult
verify.run(desired, credentials, *, budget_s=300) -> list[Evidence]
provision_check.run(remote_entry, control_client) -> {ok, code?, offered_modes}
```

Errors are `EdgeTxnError(code, data)`, with codes from the plan-item reasons
and result codes above. Messages never include tokens, zone secrets or basic
auth credentials.

## Results shared with 062

- `rollback_complete`: every journaled item is `restored` or `unchanged`, and
  the front-door result is complete.
- `rollback_incomplete`: anything else, with leftovers.
- Feature 062 releases a fence only on `rollback_complete`.
