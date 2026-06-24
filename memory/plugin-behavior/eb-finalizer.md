# EB / Gutenberg headless finalizer gotchas (spec 005 US5)

Running real `wp.blocks` outside the editor to serialize static blocks (the
finalizer, `00-sandbox-eb-finalizer.php`) hits three non-obvious traps on WP 6.9:

1. **Core blocks aren't registered in the top JS frame.** WP 6.9's editor canvas
   is an iframe; `wp-block-library` does NOT auto-call `registerCoreBlocks()`
   outside it. Without it, `wp.blocks.createBlock('core/x')` **infinitely
   recurses** (RangeError: Maximum call stack) building an unregistered fallback
   block. Fix: call `wp.blockLibrary.registerCoreBlocks()` once before
   createBlock. EB's own blocks register fine via `do_action('enqueue_block_editor_assets')`.

2. **A raw footer `<script>` runs before the enqueued `wp-*` bundles** →
   `ReferenceError: wp is not defined`. Don't print inline JS in
   `admin_print_footer_scripts`; attach it with `wp_add_inline_script()` to a
   src-less aggregator handle that `deps` on
   `wp-blocks, wp-block-library, wp-dom-ready, wp-api-fetch, wp-data` so WP
   guarantees it prints after them.

3. **EB's first-run "quick setup" wizard redirects every admin load** (gated on
   the `essential_blocks_quick_setup_shown` option) and hijacks headless
   automation. The finalizer mu-plugin sets that option truthy on `admin_init`.

Driving it: hit `/?sandbox_autologin=<token>` then `admin.php?page=sandbox-eb-finalizer`
in ONE browser context (the autologin cookie must persist), wait for
`#sandbox-finalizer-done`, poll `sandbox_eb_finalizer_status('<job_id>')`. The
MCP `visit`/`wp_cli` tools may resolve a stale `$SANDBOX_HOME` base until Claude
Code is restarted; CLI `./sb` + the tools venv at
`$SANDBOX_HOME/runtime/.venv-tools/bin/python` work regardless.

Concurrency: each job records the post hash at enqueue; a successful apply
re-bases sibling queued jobs on the same post (so batched appends chain) while a
genuine external edit before the drive is still rejected as `conflict`.
See [[elementor-save-needs-current-user]].
