"""`./sb cache` — inspect or clear the shared plugin/theme/core download cache.

Registry-wide (not instance-scoped): the cache is one shared dir under the
sandbox root, mounted into every instance. See sandbox/core/_docker.py
(dl_cache_info / dl_cache_clear) and the dl-cache mu-plugin."""
from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


def cmd_cache(cfg, args) -> None:
    action = getattr(args, "action", "info") or "info"
    layer = getattr(args, "layer", None)

    if action == "clear":
        nfo = dl_cache_info()
        target = layer or "all layers"
        if nfo["total_files"] == 0:
            ok(f"Download cache already empty ({nfo['dir']}).")
            return
        if not getattr(args, "yes", False):
            ans = input(f"Clear download cache ({target}, "
                        f"{_human_bytes(nfo['total_bytes'])} across "
                        f"{nfo['total_files']} files)? [y/N] ").strip().lower()
            if ans != "y":
                info("Aborted.")
                return
        res = dl_cache_clear(layer)
        ok(f"Cleared {', '.join(res['cleared'])} — freed "
           f"{_human_bytes(res['freed_bytes'])}.")
        return

    # default: info
    nfo = dl_cache_info()
    ok(f"Download cache: {nfo['dir']}  (shared across all instances)")
    for l in nfo["layers"]:
        info(f"  {l['name']:8} {l['files']:>4} files  "
             f"{_human_bytes(l['bytes']):>9}  — {l['label']}")
    ok(f"Total: {nfo['total_files']} files, {_human_bytes(nfo['total_bytes'])}. "
       f"Clear with `./sb cache clear`.")


register({"cache": cmd_cache})
