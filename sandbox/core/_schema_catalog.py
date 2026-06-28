"""Bundled editor-schema catalog (spec 012).

A committed, gzipped, version-keyed catalog of widget/block schemas so the
in-instance `editor-schema` ability can serve full fidelity — including EB Pro
and Elementor Pro — with no source checkout and no per-user regeneration.

Layout (committed): `sandbox/assets/editor-schema/<builder>.json.gz`, one gzipped
JSON object per builder mapping `name -> entry`. Each entry carries the full
attribute/control set + `coverage` (full|partial) + the source plugin `version`.
Provisioning copies these into each instance so the ability (in-container PHP)
can gzdecode + look one up. Pure data + gzip — no per-plugin code.
"""
from __future__ import annotations
import gzip
import json
from pathlib import Path

# The committed catalog dir (repo asset). Resolve relative to this file so it
# follows the package, not the cwd.
CATALOG_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "editor-schema"
BUILDERS = ("gutenberg", "elementor")


def _catalog_file(builder: str) -> Path:
    return CATALOG_ASSET_DIR / f"{builder}.json.gz"


def pack_builder(builder: str, entries: dict) -> dict:
    """Write `{name: entry}` for one builder to the committed gzipped catalog.
    Returns a small report (count, compressed bytes). Idempotent: overwrites."""
    if builder not in BUILDERS:
        raise ValueError(f"unknown builder '{builder}' (expected {', '.join(BUILDERS)})")
    CATALOG_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")
    out = _catalog_file(builder)
    # mtime=0 so the gzip is reproducible (same input → same bytes → clean diffs).
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(out, "wb"), mtime=0) as gz:
        gz.write(payload)
    return {"builder": builder, "count": len(entries),
            "compressed_bytes": out.stat().st_size, "file": str(out)}


def read_builder(builder: str) -> dict:
    """Load + gunzip one builder's catalog from the committed asset ({} if absent)."""
    f = _catalog_file(builder)
    if not f.exists():
        return {}
    with gzip.open(f, "rb") as gz:
        return json.loads(gz.read().decode("utf-8"))


def lookup(builder: str, name: str) -> dict | None:
    """The catalog entry for one widget/block, or None."""
    return read_builder(builder).get(name)


def catalog_status() -> dict:
    """Per-builder counts + compressed size for `sb schema-catalog status`."""
    total = 0
    out = {"builders": {}, "total_compressed_bytes": 0}
    for b in BUILDERS:
        f = _catalog_file(b)
        if f.exists():
            entries = read_builder(b)
            size = f.stat().st_size
            out["builders"][b] = {"count": len(entries), "compressed_bytes": size}
            out["total_compressed_bytes"] += size
            total += len(entries)
    out["total_entries"] = total
    return out


def make_entry(builder: str, name: str, schema: dict, version: str | None,
               plugin: str | None, coverage: str = "full", dynamic=None) -> dict:
    """Normalize one widget/block into a catalog entry. `schema` is the
    name->{type,default} map (attributes for gutenberg, controls for elementor)."""
    key = "attributes" if builder == "gutenberg" else "controls"
    entry = {
        "builder": builder,
        "name": name,
        key: schema,
        "count": len(schema),
        "coverage": coverage,
        "version": version,
        "plugin": plugin,
    }
    if dynamic is not None:
        entry["dynamic"] = bool(dynamic)
    return entry
