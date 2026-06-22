from __future__ import annotations
import shutil
from app import *  # noqa: F401,F403  (SANDBOX_ROOT, mcp)

# Mirrors sandbox/core/_docker.py (dl_cache_info / dl_cache_clear) — kept
# self-contained so the MCP venv needn't import the sb CLI package. The cache is
# ONE shared dir under the sandbox root, bind-mounted into every instance.
_DL_CACHE_DIR = SANDBOX_ROOT / "runtime" / "dl-cache"
_DL_CACHE_LAYERS = {
    "wp-cli": "wp-cli install cache (plugins/themes/core)",
    "wp-http": "WP runtime cache (Templately FSI etc.)",
}


def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{n} B"


def _cache_stats() -> dict:
    layers = []
    for sub, label in _DL_CACHE_LAYERS.items():
        d = _DL_CACHE_DIR / sub
        files = [f for f in d.rglob("*") if f.is_file()] if d.is_dir() else []
        b = sum(f.stat().st_size for f in files)
        layers.append({"name": sub, "label": label, "path": str(d),
                       "files": len(files), "bytes": b, "human": _human_bytes(b)})
    total = sum(l["bytes"] for l in layers)
    return {"dir": str(_DL_CACHE_DIR), "layers": layers,
            "total_files": sum(l["files"] for l in layers),
            "total_bytes": total, "total_human": _human_bytes(total)}


@mcp.tool()
def cache_info() -> dict:
    """Inspect the shared plugin/theme/core download cache (per-layer file count
    + size). It lives under the sandbox root (runtime/dl-cache) and is shared by
    ALL instances: wp-cli installs (plugins/themes/core) and Templately-FSI /
    WP-runtime downloads both reuse it instead of re-downloading. Read-only —
    global, so no project_dir is needed."""
    return {"ok": True, **_cache_stats()}


@mcp.tool()
def cache_clear(layer: str = "") -> dict:
    """Empty the shared download cache. Pass layer='wp-cli' or 'wp-http' to clear
    just one (default: both). Safe — only deletes cached zips; the next install
    re-downloads (and re-fills the cache). Returns the freed byte count. Global —
    no project_dir needed."""
    layer = (layer or "").strip()
    if layer and layer not in _DL_CACHE_LAYERS:
        return {"ok": False,
                "error": f"unknown layer '{layer}'; expected one of "
                         f"{', '.join(_DL_CACHE_LAYERS)} (or omit for both)"}
    subs = [layer] if layer else list(_DL_CACHE_LAYERS)
    freed = 0
    for sub in subs:
        d = _DL_CACHE_DIR / sub
        if d.is_dir():
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o777)
        except OSError:
            pass
    return {"ok": True, "cleared": subs,
            "freed_bytes": freed, "freed_human": _human_bytes(freed)}
