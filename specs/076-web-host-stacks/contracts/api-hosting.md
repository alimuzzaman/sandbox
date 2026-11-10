# Contract: `GET /api/hosting`

- Access: the same `_web_request_allowed` gate as every other dashboard route (this machine, or tailnet peers when bound to the tailnet).
- Query: none. Any query string gives `400 {"error": "no query parameters are supported"}`. No parameter can change which hosts are probed.
- Methods: GET only. There is no POST, and `/api/action` gains no action.
- Response: `200` with the `HostingView` from [data-model.md](../data-model.md), served with `Cache-Control: no-store`. The whole payload passes through `remote_core.redact_structure` before it is sent.
- Latency: the endpoint reads local JSON files and the in-memory health cache only. It schedules stale health checks in the background and returns without waiting.
- Failure: an unreadable source adds a reason to `partial_reasons` and the endpoint still returns `200`. An unexpected exception returns `500 {"ok": false, "error": "hosting view unavailable"}` with no detail.

# Contract: `sb deploy --stack <slug>`

- Requires `--remote` and `--expose`. Refusals happen before any remote effect and exit 1. With `--json` they print `{"ok": false, "error_code": "<code>", "error": "..."}`:
  - `deploy_stack_requires_expose`: `--stack` without `--expose`;
  - `deploy_stack_invalid`: the slug does not match the stack slug pattern;
  - `deploy_stack_unknown`: no `<remote>/<slug>` in the controller stack registry.
- On success, the JSON result gains `stack_link: {"slug", "hostname", "recorded": bool, "error_code"?}`. Recording is best-effort and never changes `ok`.
