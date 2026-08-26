# E2E browser preflight

`sb e2e` performs a bounded, read-only browser check before provisioning any
WordPress worker. It resolves the project's `playwright` or `@playwright/test`
package with Node, reads the configured Chromium executable path, and verifies
that the file exists.

If the package, Node.js, or Chromium binary is missing, the command returns a
typed error and points to the project-specific install command. It never runs a
browser install or creates a worker as part of the preflight.
