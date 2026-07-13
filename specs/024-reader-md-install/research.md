# Research: Default Reader.md Bootstrap

## Decision

Use Reader.md's upstream Homebrew cask as the macOS default.

## Evidence

- Reader.md documents Homebrew as its recommended install path and says this
  route puts `reader` on `PATH` automatically.
- The upstream cask pins Reader.md v1.7.0 with a SHA-256 archive checksum and
  declares the `reader` binary.
- The direct DMG requires a first-launch Gatekeeper exception and a separate
  in-app CLI installation step, which makes it unsuitable for repeatable
  bootstrap automation.
- Current Homebrew releases refuse third-party casks until they are explicitly
  trusted. The required command can be limited to
  `jnahian/reader.md/reader-md`, rather than trusting all casks in the tap.

## Trade-offs

- The installer adds a macOS GUI application users may not require. The
  `SANDBOX_SKIP_READER_MD=1` opt-out keeps CI and minimal workstations lean.
- An upstream tap is a supply-chain dependency. The bootstrap grants trust only
  to its `reader-md` cask, and Homebrew validates the cask's pinned archive
  checksum; any failed tap, trust, or cask install is non-fatal.
- Reader.md remains local-only and is not installed on Hermes or other servers.
