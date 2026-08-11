# Synthetic secret-format fixtures

These fixtures model credential-file shapes documented by their providers.
They contain no downloaded credentials and no usable private keys or tokens.
Every value is synthetic, uses reserved example domains where applicable, and
is covered by tests that reject accidental non-synthetic content.

`manifest.json` is the provenance record. Each entry names the explicit broker
format, the official documentation URL, and the selectors the parser must
inventory. Add a fixture only when all of the following are true:

1. An authoritative platform document defines the file shape.
2. Every value can be replaced with an unmistakably synthetic marker.
3. The fixture contains the minimum structure needed to exercise the parser.
4. The manifest and exact-selector tests are updated in the same change.

Do not copy credentials from a workstation, SDK cache, cloud console, issue,
log, public repository, or provider example that resembles a live secret.
