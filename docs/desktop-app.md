# Sandbox desktop for macOS

The desktop package is a thin, secure Electron shell around the existing `./sb web`
dashboard. It does not duplicate lifecycle logic, start Docker, invoke SSH, read tokens,
or own the backend process. Closing the window therefore does not stop the dashboard or
any Sandbox instance.

## Develop

Start the loopback backend and the Vite UI in separate terminals:

```bash
./sb web --port 8765
cd src/web && npm run dev
```

Then start Electron:

```bash
cd src/desktop
npm install
SANDBOX_DESKTOP_DEV_URL=http://127.0.0.1:5199 npm run dev
```

Without `SANDBOX_DESKTOP_DEV_URL`, development and packaged builds load
`SANDBOX_DESKTOP_URL`, defaulting to `http://127.0.0.1:8765`. Only loopback HTTP
endpoints are accepted. Start `./sb web` first; a future supervised `sandboxd` can own
backend startup and health independently of the window.

## Security boundary

- Renderer sandboxing and context isolation are enabled; Node integration is disabled.
- The preload exposes one typed operation: a native project-directory picker.
- IPC channels are allowlisted. There is no generic filesystem, shell, Docker, SSH,
  environment, credential, or process-execution bridge.
- New windows are denied. Credential-free HTTP(S) links can be handed to the system
  browser after main-process validation; other protocols are rejected.
- Permission requests are denied and navigation is restricted to the configured
  dashboard origin.

The create-instance page shows **Choose folder** when it detects the desktop preload.
Revealing logs is intentionally deferred until the backend exposes a typed log-artifact
identifier; accepting renderer-supplied arbitrary paths would break this boundary.

## Build for macOS

```bash
cd src/desktop
npm run package:mac
```

This creates unsigned DMG and ZIP artifacts under `src/desktop/release/`. Distribution
still requires an Apple Developer ID certificate, hardened-runtime entitlements,
notarization, stapling, and update-channel signing. Those credentials are deliberately
not stored or requested by this package.

## Windows roadmap

The first Windows release should keep this renderer/preload contract and connect to a
supervised backend running in WSL2 with Docker Desktop integration. The backend, not the
renderer, should translate Windows/WSL paths and own Docker access. Packaging can then
add signed NSIS artifacts and code signing. Native Windows container/runtime support is
a separate adapter project after POSIX-only assumptions are isolated and tested.
