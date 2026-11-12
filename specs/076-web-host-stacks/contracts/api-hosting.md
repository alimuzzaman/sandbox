# Contract: `GET /api/hosting`

- Access: the same `_web_request_allowed` gate as every other dashboard route (this machine, or tailnet peers when bound to the tailnet).
- Query: none. Any query string gives `400 {"error": "no query parameters are supported"}`. No parameter can change which hosts are probed.
- Methods: GET only. There is no POST, and `/api/action` gains no action.
- Response: `200` with the `HostingView` from [data-model.md](../data-model.md), served with `Cache-Control: no-store`. The whole payload passes through `remote_core.redact_structure` before it is sent.
- Latency: the endpoint reads local JSON files and the in-memory health cache only. It schedules stale health checks in the background and returns without waiting.
- Lifetimes (follow-up 2026-11-10): `lifetimes` and each card's `lifetime` follow [data-model.md](../data-model.md). On a controller they come from a background cache of `sb host stack list --remote R --json`. A remote not yet fetched reads `unknown`/`loading`, and an unreachable remote adds `lifetime_unavailable:<remote>` to `partial_reasons`. The endpoint never waits for a fetch.
- Failure: an unreadable source adds a reason to `partial_reasons` and the endpoint still returns `200`. An unexpected exception returns `500 {"ok": false, "error": "hosting view unavailable"}` with no detail.


## Mailpit access (2026-11-12)

Each `stacks[]` entry adds `mail_access` while retaining `mailpit_port`:

- `{ "status": "ready", "url": "https://<managed-fqdn>/mail/", "upstream": "127.0.0.1:<port>" }`.
- `{ "status": "unavailable", "reason": <code>, "upstream": <loopback-or-null> }`.

Codes, in decision order: `no_port`, `not_same_server`, `inventory_unavailable`,
`inventory_partial`, then `ambiguous` (multiple exact upstream matches),
`not_found` (no exact match), or `not_ready` (one match without ready records).
Unavailable results never include a URL. The frontend renders Open inbox only
for `ready`, and otherwise shows the reason and recorded upstream.

Only `this-server` rows can use this machine's metadata. Controller rows cannot
borrow local records, even when the port or remote label matches. Managed-name
metadata must bind the same tailnet IPv4 as the actual panel listener (never
the request Host header). Other tailnet IPs are excluded. A listener without
a tailnet IPv4 reports `inventory_unavailable`. Discovery is
bounded to 1,000 directory entries and an eight-second helper budget, using the existing no-symlink, trusted-owner
file readers. Unreadable/unmanaged entries or truncation fail closed for the
whole inventory. Public/private non-loopback upstreams and different ports never
match. Hostnames must pass the managed-name parser; HTTPS and `/mail/` are fixed,
not taken from an arbitrary URL. A matching root-held loaded activation generation,
managed directory ownership, a private-key file's presence (stat only), unexpired
public certificate name coverage, and a loaded activation journal for the exact
proxy/certificate generation are required. No private-key contents, mailbox
contents, credentials, network probes, SSH, DNS lookup, or provisioning are used.
`ready` reports retained activation metadata; postdeployment browser/TLS proof
remains necessary to establish current reachability. Existing access gates and
health checks are unchanged.

# Contract: `sb deploy --stack <slug>`

- Requires `--remote` and `--expose`. Refusals happen before any remote effect and exit 1. With `--json` they print `{"ok": false, "error_code": "<code>", "error": "..."}`:
  - `deploy_stack_requires_expose`: `--stack` without `--expose`;
  - `deploy_stack_invalid`: the slug does not match the stack slug pattern;
  - `deploy_stack_unknown`: no `<remote>/<slug>` in the controller stack registry.
- On success, the JSON result gains `stack_link: {"slug", "hostname", "recorded": bool, "error_code"?}`. Recording is best-effort and never changes `ok`.
