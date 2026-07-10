# wp-pilot

Drive a real WordPress admin screen with a headless browser. Use it for generic
dashboard, settings, onboarding, licensing, and frontend smoke-test flows where
browser state matters.

Do not use it for Gutenberg or Elementor page authoring, schema discovery, or
builder conversion. Those capabilities belong to `alims-builder-authoring`.

Use `recipes/dashboard-flow.js`, `recipes/pro-gating.js`, or
`recipes/screenshot.js` as a starting point. The reusable browser helpers are in
`lib/runner.js`.

Before a mutating flow, capture a Sandbox snapshot. Verify a save by reloading
the affected screen, and report only the resulting behavior and safe evidence.
