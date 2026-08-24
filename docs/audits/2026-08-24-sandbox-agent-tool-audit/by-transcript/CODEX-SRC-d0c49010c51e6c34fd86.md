# Safe source `CODEX-SRC-d0c49010c51e6c34fd86`

Source class: accessible Codex app thread with no matching local metadata ID
Evidence role: CI agent-use cross-check

## Findings sourced here

### ATO-002 — CI request identity (P1)

The visible transcript contained two identical `ci_run` calls. The MCP CI
contract has no durable `request_id`, and remote CI generates a random run ID.
An uncertain retry can therefore create another aggregate parent and another set
of matrix cells. Add replay-safe request identity across CLI, MCP, parent, and
children.

The same transcript called `ensure_instance` before `ci_run`; CI provisions its
own isolated matrix cells, so the extra call is unnecessary and should be removed
from agent guidance.

### ATO-010 — Matrix request identity (P1)

The Luna Max source review found the generic `job_matrix` path has the same gap:
no request ID reaches the matrix parent/children even though the registry can
deduplicate ordinary jobs when one is supplied. Apply the shared request-ID seam
to matrix jobs rather than making CI a one-off exception.
