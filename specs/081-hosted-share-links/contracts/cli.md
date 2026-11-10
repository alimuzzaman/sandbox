# Contract: `sb host share` and MCP tools

```text
sb host share create --remote R SELECTOR [--entry NAME] [--path SCOPE] [--ttl DURATION]
                     [--uses N] [--email ADDR] [--json]
sb host share list   --remote R SELECTOR [--entry NAME] [--json]
sb host share revoke ID|all --remote R SELECTOR [--entry NAME] [--json]

SELECTOR: --project-dir D [--environment E] | --target P/E | --name STACK | --stack STACK
```

- `--stack STACK` makes a stack-page link for that stack and resolves the stack's target
  like `--name`. It cannot be combined with `--path`.
- `--entry` defaults to `share` when the manifest has a `share:` block. Otherwise it
  defaults to the only `site_gate` entry. With several, it is required
  (`share_entry_required`).
- `--path` defaults to `/`. `--ttl` is `<n>m|h|d` or seconds, defaults to `7d`, and is at
  most `30d`. `--uses` is 1 to 20; omitted means no limit.
- `--email` applies only to `--stack` with a `magic_link` sign-in entry. It defaults to
  the target's saved default.
- No `--confirm`: the manifest's `share.agent_mint` is the standing authorization. Without
  it, the link goes to the controlling terminal only (075 rule).

## `create` result (`--json`)

```json
{"ok": true, "link": "https://share.dev.example.test/s/l-<32hex>#<token>",
 "link_id": "l-<32hex>", "target": "mockups/dev", "entry": "share", "kind": "site",
 "scope": "/designs/home/", "expires_at": 1700604800, "expires_clamped": false,
 "uses": null, "hostname": "share.dev.example.test"}
```

Without `agent_mint`, on a terminal: no `link` key, and `"link_shown_on": "terminal"`.

## `list` result

`{"ok": true, "entry": ..., "links": [{"id", "kind", "scope", "issued_at", "expires_at",
"uses", "used"}], "sessions": [{"id", "link", "created_at", "expires_at"}]}`. No link, no
hash.

## `revoke` result

`{"ok": true, "revoked_links": n, "revoked_sessions": n, "revoked_keys": n}`.

## Refusals

`{"ok": false, "code": "<code>", "message": "..."}`, exit 1. Codes:
`share_not_declared`, `agent_mint_not_enabled`, `terminal_required`, `job_output_refused`,
`share_ttl_invalid`, `share_scope_invalid`, `uses_out_of_range`, `share_entry_required`,
`share_entry_unknown`, `stack_share_not_declared`, `share_options_conflict`,
`site_link_limit`, `site_link_unknown`, `site_entry_unknown`, `login_service_expired`,
`email_required`, `share_failed`, plus the target-resolution codes.

## MCP tools (group `share`)

`host_share_create(remote, project_dir=None, environment=None, target=None, name=None,
stack=None, entry=None, path=None, ttl=None, uses=None, email=None)`,
`host_share_list(remote, ...selector, entry=None)`, and
`host_share_revoke(remote, link_id, ...selector, entry=None)`. Each runs the CLI with
`--json` and returns its JSON object. MCP never has a terminal, so a target without
`agent_mint` refuses with `agent_mint_not_enabled`.

## Panel hook

`sandbox.commands.host_share.create_share(remote, *, stack=None, target=None, name=None,
project_dir=None, environment=None, entry=None, path=None, ttl=None, uses=None,
email=None, caller="panel") -> dict` returns the `create` result, or raises
`ShareRefusal(code, message)`. The panel's "Share this stack" button calls it with
`stack=<slug>`.
