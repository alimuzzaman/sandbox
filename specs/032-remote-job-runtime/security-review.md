# Durable remote job runtime security review

**Review date:** 2026-07-28
**Scope:** host-local implementation, mocked remote-control transport, and
disposable acceptance on the provisioned `scaleway-sandbox` remote.

## Verified controls

| Control | Evidence | Result |
|---|---|---|
| Secret redaction and safe output decoding | `tests/test_job_output.py` exercises a secret split across stdout/stderr chunks, verifies `[REDACTED]`, preserves event order, and safely decodes invalid UTF-8. | Pass |
| Bounded retained output and artifact reads | `OutputQuery` and `ArtifactQuery` enforce page limits; `tests/test_job_cli.py` rejects invalid artifact bounds before any transport read and verifies chunked download SHA-256/size validation. | Pass |
| Artifact containment | `sandbox/jobs/artifacts.py` rejects absolute/traversal paths, symlinks, non-regular entries, FIFO/device-style entries, count/size excess, and files changing while collected. `tests/test_job_artifacts.py` covers those paths. | Pass |
| Process ownership before cancellation | `sandbox/jobs/process.py` binds boot ID, PID, start identity, nonce hash, and process group. `tests/test_job_process_identity.py` verifies PID reuse/boot change rejection and no signal on an identity mismatch. | Pass |
| Disk reserve behavior | `JobStorage.require_capacity()` rejects writes that would cross the reserve; `tests/test_job_output.py` proves output fails explicitly before a write. | Pass |
| Remote control boundary | `RemoteJobTransport` invokes the staged remote `sb` CLI with bounded JSON responses for submit, status, output, metrics, cancellation, retry, cleanup, and reconciliation. `tests/test_remote_job_transport.py` covers command construction and unreachable behavior; live E2E, workspace, CI, and cleanup jobs use the same boundary. | Pass |
| Inventory secret containment | `sb instances --json` omits authentication-bearing `login_url` values. `tests/test_runtime_transport.py` proves the field and a sentinel token are absent. | Pass |

## Finding: scoped internal Docker recovery

`RemoteJobTransport._prepare_workspace()` contains one fixed, internal fallback
that invokes `docker run` as root only when ordinary removal of the deterministic
workspace contents fails. It mounts only that precomputed workspace path and does
not expose arbitrary Docker arguments through CLI or MCP. The public remote job
interface remains `sb` JSON control, not a raw Docker surface.

Disposable remote acceptance exercised this fallback with a root-owned nested
directory. Job `a9b8be60cf12c39f097a3f9fd3dbe84d` created the content and
rerun `6128894c312173c36516d434f5238bfd` removed it while preserving the
workspace sentinel and existing top-level bind-mount directory. The refreshed
source remained inside the deterministic workspace. The public boundary
remained durable `sb` job control, and bounded failures did not expose remote
credentials.

## Command run

```sh
.cli-venv/bin/python -m unittest \
  tests.test_job_output tests.test_job_artifacts tests.test_job_process_identity \
  tests.test_remote_job_transport tests.test_job_cli tests.test_runtime_transport -v
```

The final full-suite result is recorded in `implementation-evidence.md`.
