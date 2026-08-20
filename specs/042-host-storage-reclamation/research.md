# Research: One-Click Host Storage Reclamation

Source evidence: `memory/plugin-behavior/scaleway-sandbox-deploy-src-space-2026-08-16.md`
and `…-cache-unknown-space-2026-08-16.md`, plus the code read of
`sandbox/resources/{remote,adapters,service,models,plans}.py` and
`sandbox/application/workspace_service.py`.

## D1 — Where the classification code belongs

**Decision**: raw evidence in the shipped probe; policy on the operator's machine.

**Rationale**: The probe is transmitted over stdin on every call
(`RemoteResourceAdapter._ssh` pipes `_program(request)` into `python3 -`), so probe changes
ship instantly and need no host runtime deploy. But the probe is a giant string literal —
untestable, unlintable, and unimportable. Splitting it so the probe returns evidence and a
pure module decides policy gives full unit coverage of every safety rule while keeping the
zero-deploy property.

**Alternatives rejected**: (a) classify inside the probe — untestable; (b) classify in
`adapters.py` for local and in the probe for remote — two implementations that will drift,
which is exactly what the existing `size`/`indexed_size` split already shows.

## D2 — Reuse `PlanStore` for tier plans

**Decision**: tier plans are `CleanupPlan` records with `scope ∈ {safe, tmp, all}`.

**Rationale**: `PlanStore` already gives atomic writes, an exclusive `flock`, expiry, a
`planned → in_progress → completed|indeterminate` state machine, and immutable run receipts.
Those are precisely the resumability and single-writer properties FR-015/FR-020 need.
`CleanupPlan.__post_init__` only needed `PLAN_SCOPES` widened; the existing
`cache`-cannot-contain-persistent-resources rule is untouched.

**Alternative rejected**: a second plan store — duplicate locking and expiry logic for no
gain.

## D3 — Batched apply

**Decision**: one bounded SSH session carries the whole reviewed candidate set.

**Rationale**: `ResourceService.cleanup` performs `revalidate` + `remove` per candidate, i.e.
two SSH sessions per candidate. The manual audit removed ~190 resources; 380 SSH sessions is
minutes of handshakes and 380 chances to lose the connection mid-run. Batching also lets the
manifest be a single append-only file with monotonic sequence numbers, which is what makes
"what happened to X" answerable.

**Trade-off**: revalidation moves host-side. That is mitigated by the probe re-asserting
every protection rule (contract `probe.md` §1–4) rather than trusting the request.

## D4 — Working with 0 bytes free

**Decision**: no temp files on the target filesystem; manifest opened `O_APPEND|O_CREAT` and
written with `os.write` + `fsync`; directory created before candidates are enumerated.

**Rationale**: `PlanStore._write` uses `tempfile.mkstemp` + `os.replace`, which fails on a
full disk. The plan store lives on the *operator's* machine (or the host's runtime dir before
the run), while the manifest must be writable on a host with zero free bytes. Appending a
~200-byte record to an existing file succeeds where creating a temp file does not, because
ext4 keeps 5% reserved for root and the append reuses the last block until it is full. The
first record may fail on a truly 100%-full-for-root filesystem; the probe therefore creates
the manifest file **before** the free-space situation can worsen and reports
`manifest_unavailable` and refuses the whole run if it cannot.

**Alternative rejected**: writing the manifest only on the operator's machine — a run killed
by a dropped SSH connection would then lose the record of what it had already deleted.

## D5 — Root-owned trees

**Decision**: attempt unprivileged `shutil.rmtree` with an `onerror` collector; on any
`EPERM`/`EACCES`, retry once with `sudo -n timeout -k 1 N rm -rf --`; then `lstat` the path
and only report `removed` when it is genuinely gone.

**Rationale**: `.pnpm-store` trees inside workspaces are created by containers running as
uid 0. The existing probe already relies on passwordless `sudo -n du` on this host, so the
elevation mechanism exists and is bounded (`bounded()` wraps `sudo -n` with `timeout -k 1`).
Verification after removal is the part that was missing: a partial `rmtree` raises after
deleting most of a tree, and reporting that as success would corrupt the byte accounting and
hide a real failure.

## D6 — Liveness is not "a container exists"

**Decision**: `in_use` = active job binding, unexpired lease, or recent mtime.

**Rationale**: nine speckit workspaces held 28.8 GiB behind containers whose command was
`node -e "http.createServer().listen(3000)"`, up two days with no traffic. Any definition
based on process existence keeps them forever. Conversely, a running container is real
evidence of *something*, so it still produces class `LIVE` and keeps the entry out of the
`safe` tier — it just cannot outvote an explicit release or an expired retention window.

## D7 — Growth detection

**Decision**: compare mtime; treat a size delta with an unchanged mtime as a measurement
race, not growth.

**Rationale**: during the manual audit one directory appeared to be growing between two `du`
runs. It was the audit's own `du` racing page-cache writeback, not the workspace. mtime is
the cheap, correct signal: a directory tree that is genuinely being written has an advancing
mtime somewhere in it, and `stat` on the root plus the recorded plan-time value is enough.

## D8 — Registry reconciliation

**Decision**: after removal, drop index records whose deployment locator is gone and registry
records whose root is gone; report both counts. `status` reports drift in both directions
without changing anything.

**Rationale**: the index listed 12 `lenzora-workspace-*` records with 4 on disk; the disk had
104 directories the index had never heard of. Treating either side as truth loses space or
deletes live data, so both are reported and only removal reconciles.

## D9 — What still needs the host runtime

`sb workspace list|status|create|reset|destroy --remote R` execute `sb …` **on the host**
through `RemoteWorkspaceTransport`, so they depend on the host's `sb-src` copy. The new
`release`, `ttl`, and `reap` verbs deliberately do **not** use that transport: they use the
shipped probe's `lease`/`reclaim` actions, so they work against a host running an older
runtime. Only the pre-existing `workspace list --remote` failure needs a host sync to fix,
and that fix already landed locally in `116a63b`.
