# Sandbox desktop for macOS

Sandbox Desktop is the signed-app boundary around the existing `./sb web` dashboard.
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

Startup performs a five-second, bounded `/api/instances` protocol-shape handshake.
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

An unsigned local universal build is reproducible from the lockfile:

```bash
cd src/desktop
npm ci
npm run package:mac
```

This generates universal arm64+x64 DMG/ZIP artifacts and
`release/RELEASE-MANIFEST.json` with byte sizes and SHA-256 checksums. The source SVG is
converted to ICNS with macOS `sips` and `iconutil`. `package:mac:dir` creates an unpacked
smoke-test app. The unsigned command disables certificate auto-discovery deliberately.

## Release-operator checklist

Code completion cannot substitute for these Apple-controlled release gates:

1. Install the `Developer ID Application` certificate in an isolated release keychain.
2. Provide electron-builder signing inputs only in the protected release environment.
3. Run `npm ci`, `npm audit --audit-level=high`, `npm test`, and the web build/typecheck.
4. Run `npm run package:mac:signed`; verify both architectures with `lipo -archs`.
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
