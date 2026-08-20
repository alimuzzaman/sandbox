# MCP contract: tier parity on the resource tools

Group `resources` in `mcp/wp-server/tools/manifest.py`. Tool names are unchanged; the group
gains one declared dependency, `reclaim_service_factory`, beside the existing
`resource_service_factory`.

## `resource_cleanup_plan(scope=None, tier=None, remote=None, thorough=True, budget_seconds=60)`

- `tier` accepts `"safe" | "tmp" | "all"` and routes to the tiered planner, returning the
  same payload as `sb resources plan --tier <t>`: `plan_id`, `tier`, `candidates`,
  `skipped`, `estimated_reclaimable_bytes`, `tier_totals`, `requires_confirmation`.
- `scope` accepts `"cache" | "stale"` and behaves exactly as before.
- Both supplied → refused, `invalid_mode`.
- Neither supplied → refused, `invalid_scope`.
- Unknown tier → refused, `invalid_tier`, nothing planned.

## `resource_cleanup_apply(plan_id=None, tier=None, remote=None, confirm=False)`

- `confirm` must be `true`; otherwise refused with `confirmation_required`, unchanged.
- `tier` without `plan_id` plans and executes that tier in one call, exactly as
  `sb resources cleanup --tier <t> --confirm` does.
- `plan_id` alone continues to apply a stored scope plan.
- Both `plan_id` and `tier` → refused, `invalid_mode`, so a tier can never silently override
  the plan the caller reviewed.
- Neither → refused, `invalid_mode`.

## Invariants preserved

- The tiered path goes through `ReclaimService`, so the deletion manifest, the
  protected-volume rules, the hosted-site protection, and the host-side re-assertion in the
  probe all apply identically to an MCP-driven run.
- No MCP tool exposes the automatic path. Unattended reclamation is reachable only through
  `sb resources monitor` with a configured, confirmed policy.
