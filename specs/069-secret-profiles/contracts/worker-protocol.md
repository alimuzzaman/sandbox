# Contract: parent ↔ ownership worker pipe protocol

Process: `[sys.executable, "-I", "-c", "import sys;sys.path.insert(0,<root>);from sandbox.secrets.ownership.worker import main;main()"]`, env = same allowlist as `BoundedProviderBroker._environment()`, `stdin=PIPE, stdout=PIPE, stderr=DEVNULL, start_new_session=True, close_fds=True`. Parent always ends with `terminate_process_group(process, grace_seconds=0.5, kill_wait_seconds=0.5)`; failure → `RevocationOutcome("cleanup_unknown", …)`.

Frame: 1 byte type + 4 byte big-endian length + payload. Types: `J` (ASCII canonical JSON, max 64 KiB), `V` (raw value bytes, max 64 KiB = `MAX_PROVIDER_VALUE_BYTES`), `E` (ASCII error code). Unknown type, oversize, or out-of-order frame → worker exits nonzero after attempting revoke; parent maps to `provider_failed`.

Sequence:
```
P→W J {"version":2,"op":"open","grant":{profile,capability,endpoint,projectId,environment,path,clientId,authorityDigest,names[],timeoutSeconds,gateway,projectRoot,ownership},"deadline":<epoch>}
W   validates/recomputes complete grant authority, then reads bootstrap secret from OS store via locator_for_ownership(grant); one login
W→P J {"op":"opened","tokenExpiresAt":<epoch>}          | E <code>
P→W J {"op":"exists","names":[...]}                       W→P J {"op":"exists","result":{name:"absent|present|unknown"},"owners":{name:"controller-id"}}
P→W J {"op":"policy"}                                     W→P J {"op":"policy","requiresApproval":true|false|null}
P→W J {"op":"create","name":N,"comment":C} ; P→W V <value>
W→P J {"op":"ready","name":N}                             (request built, nothing sent)
P   journals 'attempted' + fsync
P→W J {"op":"go","name":N}       (or {"op":"abort"})
W→P J {"op":"created","name":N,"state":"created|conflict|unresolved|acceptance_unknown|failed","reason":…}
P→W J {"op":"readback","name":N} ; P→W V <expected>       W→P J {"op":"readback","name":N,"equal":true|false|null}
P→W J {"op":"read","names":[...]}  (capability read)      W→P (J {"op":"value","name":N} ; V <bytes>)* ; J {"op":"done"}
P→W J {"op":"close"}                                      W→P J {"op":"closed","revocation":"revoked|cleanup_unknown"}
```
Worker invariants: every name in an op ⊆ `grant.names`; `create` only when capability `create`; `readback` under a `read` grant is allowed for its exact names (cleanup uses read); under `create` it requires an attempted name; `read` only when `read`; token, bootstrap secret and values never written to stdout except `V` frames for `read`; worker wipes buffers; SIGTERM handler attempts revoke within remaining deadline then exits. No list/search/export endpoints exist in the worker.

`gateway` must be explicit in the normalized open frame. The worker validates
bootstrap input using the shared broker validator before login. The complete
ownership authority and canonical project root are carried as metadata; the
worker recomputes the digest and validates the exact destination/client and name
subset before opening the native store. Never trust only the provided digest.

The `owners` map contains only controller IDs parsed from the exact Sandbox migration-comment grammar with matching qualified run ID. Arbitrary comments never cross the pipe or appear in reports. Missing or malformed markers give no ownership claim and do not authorize adoption.
