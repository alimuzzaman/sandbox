"""Bundled editor-schema catalog (spec 012).

A committed, gzipped, version-keyed catalog of widget/block schemas so the
in-instance `editor-schema` ability can serve full fidelity — including EB Pro
and Elementor Pro — with no source checkout and no per-user regeneration.

Layout (committed): `sandbox/assets/editor-schema/<builder>.json.gz`, one gzipped
JSON object per builder mapping `name -> entry`. Each entry carries the full
attribute/control set + `coverage` (full|partial) + the source plugin `version`.
Provisioning copies these into each instance so the ability (in-container PHP)
can gzdecode + look one up. Pure data + gzip — no per-plugin code.

Elementor catalogs use format v2 (normalized pool):
  `_format`: "v2"
  `_pool`:   {ctrl_id -> definition}  — controls shared across ≥2 widgets
  per widget: {controls: [id,...], overrides: {id: def,...}, own: {id: def,...}}
  Consumer resolves: pool[id] for each id in controls, then merge overrides, then own.
"""
from __future__ import annotations
import collections
import gzip
import json
from pathlib import Path

# The committed catalog dir (repo asset). Resolve relative to this file so it
# follows the package, not the cwd.
CATALOG_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "editor-schema"
BUILDERS = ("gutenberg", "elementor")
DESCRIPTIONS_FILE = CATALOG_ASSET_DIR / "control-descriptions.json"


def load_descriptions() -> dict[str, str]:
    """Load the hand-curated control descriptions companion file (empty dict if absent)."""
    if not DESCRIPTIONS_FILE.exists():
        return {}
    with open(DESCRIPTIONS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _catalog_file(builder: str) -> Path:
    return CATALOG_ASSET_DIR / f"{builder}.json.gz"


def normalize_elementor_catalog(entries: dict, descriptions: dict | None = None) -> dict:
    """Convert a flat {name: entry} Elementor catalog to the normalized v2 format.

    Pool: control definitions that appear in ≥2 widgets. Uses the most-common
    value for each control ID as the pool definition.
    Per-widget:
      controls  — ordered list of IDs resolved verbatim from the pool
      overrides — full definitions for pool controls that differ in this widget
      own       — full definitions for widget-unique controls (not in pool)
    """
    # Collect all (ctrl_id, serialized_value) pairs per widget.
    ctrl_appearances: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    widget_data: dict[str, dict] = {}

    for name, entry in entries.items():
        controls = entry.get("controls")
        if not isinstance(controls, dict):
            continue
        meta = {k: v for k, v in entry.items() if k not in ("controls", "builder", "name", "count")}
        widget_data[name] = {"controls": controls, "meta": meta}
        for ctrl_id, ctrl_val in controls.items():
            ctrl_appearances[ctrl_id].append((name, json.dumps(ctrl_val, sort_keys=True)))

    # Build the pool: most-common definition for each control in ≥2 widgets.
    pool: dict[str, object] = {}
    pool_ids: set[str] = set()
    for ctrl_id, appearances in ctrl_appearances.items():
        if len(appearances) >= 2:
            pool_ids.add(ctrl_id)
            common_json = collections.Counter(v for _, v in appearances).most_common(1)[0][0]
            pool[ctrl_id] = json.loads(common_json)

    out: dict = {"_format": "v2", "_pool": pool}

    for name, entry in entries.items():
        if name not in widget_data:
            out[name] = entry  # passthrough (e.g. entries with non-dict controls)
            continue
        controls = widget_data[name]["controls"]
        meta = widget_data[name]["meta"]

        ctrl_list: list[str] = []
        overrides: dict[str, object] = {}
        own: dict[str, object] = {}

        for ctrl_id, ctrl_val in controls.items():
            if ctrl_id in pool_ids:
                if json.dumps(ctrl_val, sort_keys=True) == json.dumps(pool[ctrl_id], sort_keys=True):
                    ctrl_list.append(ctrl_id)
                else:
                    overrides[ctrl_id] = ctrl_val
            else:
                own[ctrl_id] = ctrl_val

        widget_entry: dict = {**meta, "controls": ctrl_list}
        if overrides:
            widget_entry["overrides"] = overrides
        if own:
            widget_entry["own"] = own
        out[name] = widget_entry

    # Inject descriptions into pool entries AFTER per-widget splitting so the
    # identity check (widget control vs pool entry) isn't affected by description keys.
    if descriptions:
        for ctrl_id, desc in descriptions.items():
            if ctrl_id in pool:
                pool[ctrl_id]["description"] = desc

    return out


def pack_builder(builder: str, entries: dict) -> dict:
    """Write entries for one builder to the committed gzipped catalog.
    Returns a small report (count, compressed bytes). Idempotent: overwrites.

    `entries` may be a flat {name: entry} dict (v1, Gutenberg) or a normalized
    v2 dict with `_format`/`_pool` keys (Elementor). Count is the number of
    widget/block entries (keys not starting with `_`)."""
    if builder not in BUILDERS:
        raise ValueError(f"unknown builder '{builder}' (expected {', '.join(BUILDERS)})")
    CATALOG_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")
    out = _catalog_file(builder)
    # mtime=0 so the gzip is reproducible (same input → same bytes → clean diffs).
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(out, "wb"), mtime=0) as gz:
        gz.write(payload)
    count = sum(1 for k in entries if not k.startswith("_"))
    return {"builder": builder, "count": count,
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
