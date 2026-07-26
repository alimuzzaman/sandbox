# Durable remote job runtime security review

**Review date:** 2026-07-26  
**Scope:** host-local implementation and mocked remote-control transport. This is
not a substitute for the disposable remote acceptance required by T137/T139.

## Verified controls

| Control | Evidence | Result |
|---|---|---|
| Secret redaction and safe output decoding | `tests/test_job_output.py` exercises a secret split across stdout/stderr chunks, verifies `[REDACTED]`, preserves event order, and safely decodes invalid UTF-8. | Pass |
| Bounded retained output and artifact reads | `OutputQuery` and `ArtifactQuery` enforce page limits; `tests/test_job_cli.py` rejects invalid artifact bounds before any transport read and verifies chunked download SHA-256/size validation. | Pass |
| Artifact containment | `sandbox/jobs/artifacts.py` rejects absolute/traversal paths, symlinks, non-regular entries, FIFO/device-style entries, count/size excess, and files changing while collected. `tests/test_job_artifacts.py` covers those paths. | Pass |
| Process ownership before cancellation | `sandbox/jobs/process.py` binds boot ID, PID, start identity, nonce hash, and process group. `tests/test_job_process_identity.py` verifies PID reuse/boot change rejection and no signal on an identity mismatch. | Pass |
| Disk reserve behavior | `JobStorage.require_capacity()` rejects writes that would cross the reserve; `tests/test_job_output.py` proves output fails explicitly before a write. | Pass |
| Remote control boundary | `RemoteJobTransport` invokes the staged remote `sb` CLI with bounded JSON responses for submit, status, output, metrics, cancellation, retry, cleanup, and reconciliation. `tests/test_remote_job_transport.py` covers command construction and unreachable behavior. | Pass in mocked transport |

## Finding: scoped internal Docker recovery

`RemoteJobTransport._prepare_workspace()` contains one fixed, internal fallback
that invokes `docker run` as root only when ordinary removal of the deterministic
workspace contents fails. It mounts only that precomputed workspace path and does
not expose arbitrary Docker arguments through CLI or MCP. The public remote job
interface remains `sb` JSON control, not a raw Docker surface.

This distinction is supported only by static/transport tests. A disposable remote
acceptance must still verify that the bounded fallback cannot escape its intended
workspace and that errors remain redacted. Therefore T138 remains open pending that
remote evidence rather than being represented as fully complete.

## Command run

```sh
.cli-venv/bin/python -m unittest \
  tests.test_job_output tests.test_job_artifacts tests.test_job_process_identity \
  tests.test_remote_job_transport tests.test_job_cli -v
```

**Result:** PASS, 34 tests.
