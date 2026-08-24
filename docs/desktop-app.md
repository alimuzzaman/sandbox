# Sandbox desktop for macOS

Sandbox Desktop is the hardened packaging boundary around the existing `./sb web`
dashboard. The current artifacts are unsigned release candidates, not production
distribution artifacts.
The packaged app owns its window and an ephemeral authenticated loopback proxy. It does
not own Docker, SSH, remote credentials, or the Sandbox backend process.

## Runtime contract

Start the backend explicitly before opening the app:

```bash
./sb web --port 8765
```

The default endpoint is `http://127.0.0.1:8765`. An operator can select another
loopback origin with `SANDBOX_DESKTOP_URL`; development can use
`SANDBOX_DESKTOP_DEV_URL`. Paths, query strings, embedded credentials, non-loopback
hosts, and arbitrary protocols are rejected.

Startup performs a 15-second, bounded `/api/instances` protocol-shape handshake. This
allows for a cold or inventory-heavy dashboard response while still failing closed when
the configured backend does not respond.
Until it succeeds, the app displays its packaged `sandbox-app://` recovery UI. Retry is
a typed IPC operation. The app never responds to a failed handshake by starting `sb`,
Docker, SSH, or an arbitrary process.

After the handshake, the renderer connects through a random-port loopback proxy using
an unguessable, HttpOnly session cookie. Anonymous requests are rejected. The proxy
bounds request and response bodies, strips hop-by-hop/cookie headers, pins the upstream
origin, and replaces inline allowances with content hashes in the dashboard CSP. This
authenticates the app-to-proxy channel; the upstream `./sb web` service still relies on
its existing loopback access boundary. End-to-end backend bearer authentication needs a
versioned backend contract and is intentionally not simulated in Electron.

## Production-readiness plan

The secure shell, universal unsigned package, icon, hardened-runtime configuration,
Electron fuses, recovery page, authenticated browser-to-proxy hop, deterministic artifact
manifest, and unsigned CI build are implemented. Production readiness still requires the
following phases in order.

### Phase 1: managed backend ownership

Add a separately supervised `sandboxd` process that owns the dashboard API and activation
scheduler. Electron must install and upgrade a Sandbox-owned per-user LaunchAgent using a
versioned payload under Application Support. The daemon remains alive when the window
closes and must prevent duplicate schedulers and port ownership conflicts.

Migration from the existing `dev.sandbox.activation` LaunchAgent is transactional: start
the new daemon, prove dashboard and activation health, then stop the old agent. A failed
install or upgrade keeps the previously healthy service and payload active.

**Gate**: clean install, login/reboot, window close and reopen, daemon crash/restart,
upgrade, failed upgrade rollback, and uninstall all leave exactly one healthy owned
service and preserve instance lifecycle behavior.

### Phase 2: versioned daemon protocol and immutable renderer

Add `/api/meta` with protocol schema, daemon/app versions, build revision, capabilities,
and the supported compatibility range. The app refuses mutations on an incompatible or
unidentified backend and presents bounded repair/update actions instead.

Ship the dashboard assets inside the signed application and load them from the privileged
`sandbox-app://` scheme. Remove executable dashboard HTML/JavaScript from the loopback
trust boundary and remove all inline-script allowances. Authenticate every daemon API
request with a generated local capability stored with mode `0600`; never expose it to the
renderer. Preserve the typed, top-frame IPC boundary and add typed log-artifact IDs rather
than renderer-provided paths.

**Gate**: protocol mismatch, stale daemon, malicious process on the expected port,
missing/invalid capability, CSP/navigation/IPC escape, oversized request/response, and
log-path traversal tests all fail closed without starting Docker, SSH, or arbitrary
processes.

### Phase 3: reliability and supportability

Add a single-instance lock, bounded daemon-start timeout, offline/recovery states,
redacted structured logs with rotation, crash-loop detection, and explicit repair/restart
controls. Crash reporting remains local unless the operator separately opts into upload.
Updates must not replace the daemon during an active destructive or durable job.

**Gate**: forced renderer and daemon crashes, corrupt state, unavailable Docker, occupied
ports, missing helper payload, and interrupted upgrade all recover to an actionable UI
without data loss or an uncontrolled restart loop.

### Phase 4: signed release and updates

Build the universal application from pinned arm64 and x64 daemon payloads. Publish only a
Developer ID signed, Apple-notarized, stapled DMG/ZIP with a signed update manifest.
Daemon upgrades use an atomic version switch with a retained previous version for
rollback. Release evidence records source SHA, lockfile and helper digests, Electron
version, architectures, Team ID, notarization result, artifact sizes, and SHA-256 hashes.

**Gate**: native arm64 and Intel clean-account installs launch without a repository
checkout, Node, or a separately started `sb web`; update and rollback pass on both
architectures; Gatekeeper accepts the stapled artifact; published hashes match the release
manifest.

### Release-only blockers

All daemon, protocol, renderer, recovery, update, packaging, test, and documentation work
can be completed without Apple credentials. Distribution remains blocked until an
authorized release operator provides the Developer ID Application identity and
notarization credentials in an isolated release environment, completes Apple
notarization and stapling, verifies Gatekeeper on clean arm64 and Intel machines, and
publishes the signed artifacts and update feed. Certificates, private keys, Apple
credentials, and update-signing secrets must never enter this repository or local build
logs.

## Security boundary

- One application instance may run per user session.
- Renderer sandboxing and context isolation are enabled; Node integration is disabled.
- IPC callers must be the current top-level frame and its expected origin.
- Native folder selection is the only filesystem operation exposed to the dashboard.
- Permission requests/checks, webviews, and new windows are denied.
- Navigation is restricted to the recovery page or authenticated dashboard origin.
- Only credential-free HTTP(S) links may open in the system browser.
- No filesystem, generic shell, subprocess, Docker, SSH, environment, token, or remote
  control bridge is exposed to the renderer.
- Electron fuses disable RunAsNode, NODE_OPTIONS, CLI inspect flags, and non-ASAR app
  loading; embedded ASAR integrity validation and cookie encryption are enabled.

Log reveal remains deferred until the backend returns a typed, owned artifact ID.
Renderer-provided arbitrary paths are not accepted.

## Develop and test

```bash
cd src/desktop
npm ci
npm test
npm run dev
```

The test suite covers URL restrictions, top-frame IPC boundaries, the backend handshake,
anonymous proxy rejection, hash-based CSP, fuses, and packaging policy.

## Deterministic macOS package

An identity-unsigned local universal build is reproducible from the lockfile:

```bash
cd src/desktop
npm ci
npm run package:mac
```

This generates universal arm64+x64 DMG/ZIP artifacts and
`release/RELEASE-MANIFEST.json` with byte sizes and SHA-256 checksums. The source SVG is
converted to ICNS with macOS `sips` and `iconutil`. `package:mac:dir` creates an unpacked
smoke-test app. The local commands disable certificate auto-discovery deliberately, flip
the production Electron fuses, then apply a local ad-hoc signature so macOS can execute
the universal bundle. That ad-hoc signature is not a Developer ID signature and is not
acceptable for distribution.

Open the real packaged app directly; never open `node_modules/electron/dist/Electron.app`,
which is only Electron's raw runtime and displays its `path-to-app` screen:

```bash
npm run run:mac       # rebuild, validate, and open release/mac-universal/Sandbox.app
npm run open:mac      # validate and open an existing packaged Sandbox.app
npm run smoke:mac:launch # launch for five seconds, fail on crash, then stop it
```

The smoke check requires the `Sandbox.app` name and bundle identifier, rejects the raw
Electron runtime path, verifies the complete code-signing graph and universal framework,
and can prove that the packaged executable stays alive through startup.

## Release-operator checklist

Code completion cannot substitute for these Apple-controlled release gates:

1. Install the `Developer ID Application` certificate in an isolated release keychain.
2. Provide electron-builder signing inputs only in the protected release environment.
3. Run `npm ci`, `npm audit --audit-level=high`, `npm test`, and the web build/typecheck.
4. Run `npm run package:mac:signed`; this mode does not apply the local ad-hoc signing
   pass and fails if a signing identity is unavailable. Verify both architectures with
   `lipo -archs`.
5. Verify signing with `codesign --verify --deep --strict --verbose=2`.
6. Submit both artifacts to Apple's notary service, wait for acceptance, and staple the
   DMG/app. Notarization credentials must never enter the repository.
7. Run `spctl --assess --type execute --verbose=4` on a clean macOS account.
8. Compare every artifact against `RELEASE-MANIFEST.json`, then publish through the
   authorized release channel. Tagging, publishing, and update feeds remain explicit
   operator actions.

The repository CI workflow builds and uploads unsigned universal evidence. It does not
claim distribution readiness or access signing credentials.

## Windows roadmap

The first Windows release should preserve the renderer/preload/proxy contract and use a
supervised backend in WSL2 with Docker Desktop. The backend—not Electron—will translate
Windows/WSL paths and own Docker access. Packaging then adds signed NSIS artifacts and a
protected update channel. Native Windows runtime support remains a separate adapter once
POSIX assumptions have been isolated and tested.
