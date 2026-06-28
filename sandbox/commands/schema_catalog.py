"""`./sb schema-catalog` — generate/inspect the bundled editor-schema catalog (spec 012).

Maintainer/CI tool. `generate` dumps the live runtime registries (Elementor via PHP
`get_controls()`; Gutenberg via the headless `wp.blocks` dump page) on an instance
with the free + Pro plugins active, packs them into the committed gzipped catalog
under `sandbox/assets/editor-schema/`, and prints a coverage report. End users never
run this — they consume the committed asset (served by `editor-schema`'s fallback).
"""
from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


def cmd_schema_catalog(cfg, args) -> None:
    action = getattr(args, "action", "status") or "status"

    if action == "status":
        st = catalog_status()
        if not st["builders"]:
            info("No bundled catalog yet — run `./sb schema-catalog generate --instance <gen>`.")
            return
        ok("Bundled schema catalog (committed asset):")
        for b, d in st["builders"].items():
            info(f"  {b:10} {d['count']:>5} entries   {_human_bytes(d['compressed_bytes']):>9}")
        size = st["total_compressed_bytes"]
        bound = 3 * 1024 * 1024
        flag = "OK" if size <= bound else "OVER ~3MB bound"
        ok(f"Total: {st['total_entries']} entries, {_human_bytes(size)} compressed ({flag}).")
        return

    if action == "generate":
        die("`schema-catalog generate` (the headless registry dump + pack) is not wired in this "
            "build yet. The consumer side (catalog fallback in editor-schema) is live; generation "
            "needs the headless wp.blocks dump driven on a Pro-active instance. See "
            "specs/012-bundled-schema-catalog/tasks.md (T003/T006/T007).")

    die(f"unknown action '{action}' — choose from: status, generate")


register({"schema-catalog": cmd_schema_catalog})
