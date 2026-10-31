# CLI Contract

## Local build

```text
./sb host image build-local --project-dir DIR --environment ENV \
  [--revision SHA] [--timeout SECONDS] [--json]
```

Success returns bounded source/render/platform/service/image summaries plus local receipt path and digest. It is not deployment evidence.

## Transfer

```text
./sb host image push-local --remote NAME --receipt PATH --confirm \
  [--timeout SECONDS] [--artifact-timeout SECONDS] [--json]
```

Success returns the immutable remote receipt digest and bounded loaded/skipped/verified summaries. Timeout/disconnect is unknown or failed, never success. The named registration
is locked from resolution/authentication through publication; the readiness gate
receives that resolved target before reservations, engine locks, or uploads.
Typed readiness admission errors retain their JSON envelope. `--timeout` caps
all transfer steps including probes and lease acquisition. Expiry before any
remote write is `local_images_transfer_timeout`; after a possible write it is
`local_images_transfer_unknown`. A surviving remote loader fences subsequent
transfer and receipt-bound apply owners until it exits.

## Apply

```text
./sb host apply --project-dir DIR --environment ENV --remote NAME \
  --local-images sha256:<64-hex> --confirm [--json]
```

Requires `compose.build: false`. The selector is part of durable admission. Verification includes the actual remote Compose environment, files, override,
service references, and exact portable image IDs. Verification failure precedes Compose mutation; later results use existing hosted apply semantics.

## Compatibility

- Existing image action requirements remain unchanged.
- Receipt-free apply is unchanged.
- `--local-images` is invalid outside `host apply`.
- `build-local` takes no remote; `push-local` needs no project manifest.
