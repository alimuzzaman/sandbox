# Data Model: Secrets unset and remote copy

All types are non-secret except where a field is marked *secret*.

## CopyAction

`Literal["created", "updated", "already_current"]`: the remote's answer.

## RemoteSetRequest (frozen dataclass)

| Field | Type | Rule |
|---|---|---|
| remote | `str` | registered remote name; `validate_remote_name` |
| target_source | `str` | `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` |
| target_key | `str` | dotenv key rule (`policy.validate_key`) |
| intent | `Literal["create", "replace", "either"]` | default `either` |
| target_project_dir | `str \| None` | absolute POSIX path, no control characters, at most 4096 bytes, normalized; `None` = remote Sandbox home |

`destination` property: `"remote:<remote>/<target_source>/<target_key>"`.

The value is never a field. It is passed separately to `RemoteSecretSink.put`.

## RemoteSetResult (frozen dataclass)

| Field | Type |
|---|---|
| action | `CopyAction` |
| revision | `str \| None` (`r1_` + 32 hex) |

## RemoteSecretSink (Protocol)

`put(request: RemoteSetRequest, value: str) -> RemoteSetResult`; raises
`SecretBrokerError` with a bounded code. `SshRemoteSecretSink` is the production
implementation; tests inject a fake that records what it received.

## Audit event (extended)

Adds optional `destination: str` (only on `copy` events). Ownership operations refuse
it, as they refuse `revision` and `input_channel`.

## update_source outcome

`update_source(..., skip_unchanged=False) -> tuple[Literal["created", "updated",
"already_current"], str]`. `already_current` only when `skip_unchanged` and the
stored value is byte-identical; no write, revision unchanged.

## Unset result

`{"ok": true, "operation": "unset", "source", "key", "action": "removed",
"revision", "correlation_id"}`.

## Copy result

`{"ok": true, "operation": "copy", "source", "key", "remote", "target_source",
"target_key", "action": CopyAction, "remote_revision", "reason_code": action,
"correlation_id"}`.

## State transitions

Unset: `present --unset--> absent`; `absent --unset--> absent` (refused `key_missing`).

Copy (remote key): `absent --copy--> present(v)` [`created`];
`present(v') --copy(either|replace)--> present(v)` [`updated`];
`present(v) --copy(any intent)--> present(v)` [`already_current`, no write];
`present(v') --copy(create)--> refused intent_conflict`;
`absent --copy(replace)--> refused intent_conflict`.
