# Contract: remote source receipt

## Write (install command in `migrate_remote_mcp_service`)

After the unit is enabled, restarted and active, and after the rollback trap is
cleared, the install command writes
`$HOME/sandbox/runtime/source-revision.json` (mode `0600`) atomically:

```json
{"schema_version":1,"source_revision":"<40-hex>"}
```

The write is best effort: a failure does not fail the install (the service is
already live), and the local hint is still recorded from the controller side.

## Local record

When the confirmed install returns success, `migrate_remote_mcp_service` calls
`state.record_hint(<remote name>, source_revision, "install")`. The remote name
is the internal `_remote_name` marker that `get_remote` adds.

## Read (`remote_mcp_service_status`)

The existing probe command gains one segment, run in the same ssh call:

```text
remote_source=<40-hex> | unavailable | unknown
```

`unknown` covers a present but malformed receipt. The status envelope gains
`installed_source_revision: <40-hex> | null` (also `null` in the `unavailable()`
shape). When it is a valid revision and the remote dict carries `_remote_name`,
the probe calls `state.record_hint(name, revision, "probe")`.

No other envelope key changes. No control-protocol bump: the probe and the
install command are controller-sent shell, not runtime payloads.
