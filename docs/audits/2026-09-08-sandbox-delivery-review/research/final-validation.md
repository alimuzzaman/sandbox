# Final research package verification

Verified on 8 September 2026 against Sandbox `fd7d650f8bfbb1760d931e2484cc01fa0badf546`. This is validation of the research package, not implementation or release acceptance.

- All saved JSON documents parse, and both saved Python programs parse.
- Source links resolve at their explicit Git revisions with in-bounds anchors. The materializer publication/capture references were tightened to lines 51/82 and the source-binding digest to line 139. The Lenzora package script and Node/revision guard references match the pinned application source.
- Local Markdown links resolve, including the application's `absolute/path:line` link format. An initial validator treated `:line` as part of the filename; correcting that validator resolved those false missing-file reports without changing the linked source files.
- The complete feedback snapshot has 748 distinct IDs. The 281 distinct detail/disposition IDs cover every one of the 278 high/critical records plus the three selected medium records, with no read failures. The other 467 medium/low records have metadata coverage only.
- The report has 16 distinct numbered findings. Nine synthetic source-boundary probe families reproduced their recorded conditions. The two late feedback-derived probes use synthetic capture content and a synthetic login marker; no native archive acceptance, real content mutation or credential exposure was performed.
- A bounded sensitive-pattern check found no private keys, GitHub tokens, AWS access keys or JWT-shaped values in the saved package. This check supplements the allowlisted evidence collection; it is not a general security certification.
- Original owner files used for the final Amar Sonar evidence were checked by size/SHA-256. Final apply/status and browser markers are kept separate from exact bundle/runtime assertions and initializer execution claims.

The earlier independent mechanical review is preserved in [artifact-verification.md](artifact-verification.md). Its counts predate later findings and feedback expansion. The final machine-readable check is [research-validation.json](../evidence/research-validation.json), and [manifest.json](../evidence/manifest.json) binds the saved package by hash and size.

No product source was changed. No new full suite, runtime creation, deployment, browser session, image build, restore, credential operation or release was run by this research task. The earlier 88-test invocation's real read-only Docker preflight and temporary Compose overlay writes are explicitly documented in F13. Concurrent PostgreSQL edits in the original checkout remain with their owner.

The saved reproduction script deliberately asserts the baseline defects. After implementation, use proper corrected-behavior regressions and the affected runtime acceptance gates from the plan; do not use this baseline script's exit zero as evidence that the product is fixed.
