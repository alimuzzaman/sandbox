# CLI contract: `sb host login-url` for `site_gate` entries

All three forms take the usual `--project-dir`, `--environment` and `--remote`.

## Mint

```bash
./sb host login-url --service mockup --confirm [--uses N] [--ttl-seconds S] [--json]
```

- The command opens the controlling terminal first. Without one it refuses with
  `terminal_required` and mints nothing.
- It writes `https://<hostname><path>#sandbox_autologin=<token>` to the terminal once.
- Stdout (`--json`):
  `{"ok":true,"service":"mockup","hostname":"share.example.test","link_id":"l-…","expires_at":…,"uses":N,"link_shown_on":"terminal"}`
  The output has no `url`, no token and no hash.
- Stdout (human): `Share link l-… for share.example.test, N use(s), expires <UTC>; shown on the terminal.`
- `--uses`: 1 to 20, default 1. `--ttl-seconds`: 60 to 3600, default the entry's.

## List

```bash
./sb host login-url --service mockup --list [--json]
```

- No `--confirm` is needed, because it is read-only.
- `--json` returns `{"ok":true,"service":"mockup","links":[{"id","issued_at","expires_at","uses","used"}],"sessions":[{"id","link","created_at","expires_at"}]}`.
- Only live links and active sessions appear.

## Revoke

```bash
./sb host login-url --service mockup --revoke <l-…|s-…|all> --confirm [--json]
```

- Returns `{"ok":true,"service":"mockup","revoked_links":n,"revoked_sessions":m}`.
- A link id also removes that link's sessions.

## Refusals (`{"ok":false,"code":…,"message":…}` with `--json`, else `code: message` on stderr, exit 1)

Existing codes still apply: `login_service_not_declared`, `login_entry_required`,
`login_entry_unknown`, `login_service_expired`, `login_link_failed`.

New codes:

| Code | When |
|---|---|
| `terminal_required` | Minting with no controlling terminal |
| `uses_out_of_range` | `--uses` outside 1 to 20 |
| `site_gate_only` | `--uses`, `--list` or `--revoke` on a non-`site_gate` entry |
| `site_options_conflict` | More than one of mint, `--list` and `--revoke` at once, or `--email` on a `site_gate` entry |
| `site_link_limit` | 32 live links already exist for the entry |
| `site_link_unknown` | `--revoke` names no live link or session |
| `site_entry_unknown` | The running service predates the entry; run `host apply` |
