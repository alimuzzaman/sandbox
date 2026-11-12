# CLI contract

`sb host login-url --project-dir PROJECT --environment ENV --remote REMOTE --confirm --permanent`

Default behavior is unchanged. `--ttl-seconds` and `--permanent` conflict. Permanent mode ignores only the manifest's ordinary TTL default. Unsupported login_service/share selectors and unrelated host actions refuse the new flag.

Permanent foreground response retains url/expires_at/one_time/user, with expiry0 and one_timefalse. Its URL carries a public query marker and a secret fragment. Host state stores only safe metadata. No new remote protocol or MCP tool.
