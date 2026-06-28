"""`./sb schema-catalog` — generate/inspect the bundled editor-schema catalog (spec 012).

Maintainer/CI tool. `generate` dumps the live runtime registries (Elementor via PHP
`get_controls()`; Gutenberg/EB via the PHP source-based resolver) on an instance with the
free + Pro plugins active, packs them into the committed gzipped catalog under
`sandbox/assets/editor-schema/`, and prints a coverage report. End users never run this —
they consume the committed asset (served by `editor-schema`'s fallback).
"""
from __future__ import annotations
import json
import subprocess

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register


# ---------------------------------------------------------------------------
# PHP dump scripts (piped to `wp eval-file -`)
# ---------------------------------------------------------------------------

_ELEMENTOR_DUMP_PHP = r"""<?php
// Elementor v4+ strips label/tab/options in non-admin/non-REST context via
// Performance::should_optimize_controls(). The static flag is set during WP init
// (before REST_REQUEST is defined), so simply defining REST_REQUEST is too late.
// Solution: reset the static via reflection, then clear the controls manager cache
// so each widget's stack is rebuilt with labels on the next get_controls() call.
if (!class_exists('Elementor\Plugin')) {
    echo json_encode(['_error' => 'elementor_not_loaded']);
    return;
}

// Reset Performance::$is_frontend (private static) via reflection so the next
// get_controls() call sees REST_REQUEST=true and preserves labels/tabs/options.
try {
    if (!defined('REST_REQUEST')) { define('REST_REQUEST', true); }
    $perf_ref = new ReflectionClass('Elementor\Core\Frontend\Performance');
    $prop = $perf_ref->getProperty('is_frontend');
    $prop->setAccessible(true);
    $prop->setValue(null, null);  // reset to null so it re-evaluates next call
} catch (\Throwable $_) {}
// Flush the cached control stacks so each widget rebuilds them with labels.
\Elementor\Plugin::$instance->controls_manager->clear_stack_cache();

/** Extract rich metadata from one Elementor control definition. */
function _sandbox_el_control_entry(array $c): array {
    $e = [
        'type'    => $c['type'] ?? null,
        'label'   => isset($c['label']) ? (string) $c['label'] : null,
        'default' => $c['default'] ?? null,
        'section' => $c['section'] ?? null,
        'tab'     => $c['tab'] ?? null,
    ];
    // selectors: string→string CSS maps (skip callable / array values).
    if (!empty($c['selectors']) && is_array($c['selectors'])) {
        $sel = [];
        foreach ($c['selectors'] as $selector => $css) {
            if (is_string($selector) && is_string($css)) { $sel[$selector] = $css; }
        }
        if ($sel) { $e['selectors'] = $sel; }
    }
    // options: for select/choose/icon controls — flatten to key→label strings.
    // Cap at 30 entries; large icon/font packs (100+ items) are bloat for agents.
    if (!empty($c['options']) && is_array($c['options'])) {
        $opts = [];
        foreach ($c['options'] as $k => $v) {
            if (is_string($v) || is_numeric($v)) {
                $opts[$k] = (string) $v;
            } elseif (is_array($v) && isset($v['title'])) {
                $opts[$k] = (string) $v['title'];
            } elseif (is_array($v) && isset($v['label'])) {
                $opts[$k] = (string) $v['label'];
            }
            if (count($opts) >= 30) { break; }
        }
        if ($opts) { $e['options'] = $opts; }
    }
    return $e;
}

$wm = Elementor\Plugin::$instance->widgets_manager;
$types = method_exists($wm, 'get_widget_types') ? $wm->get_widget_types() : [];
$dump = [];
foreach ($types as $name => $w) {
    $cls = get_class($w);
    try {
        $controls = [];
        foreach ($w->get_controls() as $cid => $c) {
            $controls[$cid] = _sandbox_el_control_entry($c);
        }
        $dump[$name] = ['controls' => $controls, 'class' => $cls];
    } catch (\Throwable $e) {
        $dump[$name] = ['_error' => $e->getMessage(), 'class' => $cls];
    }
}
echo json_encode($dump);
"""

_GUTENBERG_DUMP_PHP = r"""<?php
// Dump all Gutenberg/EB block schemas.
// For EB blocks: uses sandbox_editor_eb_resolve() (PHP source-based, available
// on the gen instance where the source checkout is mounted) to get the full
// attribute set — NOT block.json which only has ~3 keys.
// For non-EB blocks: block.json attributes (always full for core/third-party).
$reg = WP_Block_Type_Registry::get_instance();
$dump = [];
foreach ($reg->get_all_registered() as $bn => $bt) {
    $is_eb = strpos($bn, 'essential-blocks/') === 0;
    if ($is_eb && function_exists('sandbox_editor_eb_resolve')) {
        $full = sandbox_editor_eb_resolve($bn, $bt, []);
        if ($full !== null) {
            $level = $full['eb_attribute_fidelity'] ?? 'partial';
            // Infer owning plugin from the source checkout path.
            $checkout = $full['fidelity']['source_checkout'] ?? null;
            $plugin_slug = null;
            if ($checkout) {
                foreach (['essential-blocks-pro', 'essential-blocks'] as $slug) {
                    if (strpos($checkout, '/' . $slug) !== false
                        || strpos($checkout, DIRECTORY_SEPARATOR . $slug) !== false) {
                        $plugin_slug = $slug;
                        break;
                    }
                }
            }
            $dump[$bn] = [
                'attributes' => $full['attributes'],
                'supports'   => (array) $bt->supports,
                'dynamic'    => (bool) $bt->render_callback,
                'coverage'   => $level,
                'plugin'     => $plugin_slug,
            ];
            continue;
        }
    }
    // Non-EB or no source available: block.json attributes (full for core, partial for EB).
    $attrs = [];
    foreach ((array) $bt->attributes as $k => $def) {
        $attrs[$k] = ['type' => $def['type'] ?? null, 'default' => $def['default'] ?? null];
    }
    $dump[$bn] = [
        'attributes' => $attrs,
        'supports'   => (array) $bt->supports,
        'dynamic'    => (bool) $bt->render_callback,
        'coverage'   => $is_eb ? 'partial' : 'full',
        'plugin'     => null,
    ];
}
echo json_encode($dump);
"""

_PLUGIN_VERSIONS_PHP = r"""<?php
if (!function_exists('get_plugins')) { require_once ABSPATH . 'wp-admin/includes/plugin.php'; }
$active = get_option('active_plugins', []);
$all = get_plugins();
$out = [];
foreach ($active as $file) {
    $slug = dirname($file);
    if ($slug === '.') { $slug = basename($file, '.php'); }
    $out[$slug] = $all[$file]['Version'] ?? null;
}
echo json_encode($out);
"""

# Map PHP class top-level namespace → plugin slug for Elementor widgets.
_NS_TO_PLUGIN: dict[str, str] = {
    'Elementor':                   'elementor',
    'ElementorPro':                'elementor-pro',
    # EA free + Pro share the Essential_Addons_Elementor namespace;
    # Pro subclasses live under \Pro\ but we attribute all to free (the carrier plugin).
    'Essential_Addons_Elementor':  'essential-addons-for-elementor-lite',
    'EssentialAddons':             'essential-addons-for-elementor-lite',
    'EssentialAddonsPro':          'essential-addons-elementor',
    'EAEL':                        'essential-addons-for-elementor-lite',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_php(inst: str, php_src: str) -> dict:
    """Run PHP via `wp eval-file -` (stdin) on an instance, return parsed JSON."""
    proc = subprocess.run(
        ["docker", "compose",
         "-p", project_name(inst),
         "-f", str(compose_file(inst)),
         "--project-directory", str(ROOT),
         "run", "--rm", "-T",
         "wpcli", "eval-file", "-"],
        input=php_src, text=True, capture_output=True, cwd=str(ROOT),
    )
    if proc.returncode != 0:
        die(f"PHP dump failed (exit {proc.returncode}):\n{proc.stderr[:600]}")
    raw = proc.stdout
    start = min((i for i in [raw.find('{'), raw.find('[')] if i >= 0), default=-1)
    if start < 0:
        die(f"PHP dump: no JSON in output:\n{raw[:600]}")
    try:
        return json.loads(raw[start:].strip())
    except json.JSONDecodeError as e:
        die(f"PHP dump: bad JSON ({e}):\n{raw[:600]}")


def _ns_to_plugin(class_name: str) -> str | None:
    # EA Pro subclasses live under Essential_Addons_Elementor\Pro\...
    if "Essential_Addons_Elementor" in class_name and "\\Pro\\" in class_name:
        return "essential-addons-elementor"
    top = class_name.split("\\")[0] if "\\" in class_name else class_name.split("_")[0]
    return _NS_TO_PLUGIN.get(top)


def _build_elementor_entries(raw: dict, plugin_versions: dict) -> dict:
    """Convert the PHP Elementor dump → {name: CatalogEntry}."""
    entries = {}
    for name, data in raw.items():
        if "_error" in data:
            continue  # skip widgets whose get_controls() threw
        cls = data.get("class", "")
        plugin = _ns_to_plugin(cls) or "elementor"
        version = plugin_versions.get(plugin)
        controls = data.get("controls", {})
        entries[name] = make_entry(
            builder="elementor",
            name=name,
            schema=controls,
            version=version,
            plugin=plugin,
            coverage="full",
        )
    return entries


def _build_gutenberg_entries(raw: dict, plugin_versions: dict) -> dict:
    """Convert the PHP Gutenberg dump → {name: CatalogEntry}."""
    entries = {}
    for name, data in raw.items():
        plugin = data.get("plugin")
        if plugin is None:
            # Infer from block name prefix
            if name.startswith("core/"):
                plugin = "wordpress-core"
            elif name.startswith("essential-blocks/"):
                plugin = "essential-blocks"
            # else leave as None (third-party)
        version = plugin_versions.get(plugin) if plugin else None
        attrs = data.get("attributes", {})
        coverage = data.get("coverage", "full")
        entries[name] = make_entry(
            builder="gutenberg",
            name=name,
            schema=attrs,
            version=version,
            plugin=plugin,
            coverage=coverage,
            dynamic=data.get("dynamic"),
        )
    return entries


def _print_coverage(entries: dict, builder: str, plugin_versions: dict) -> None:
    by_plugin: dict[str, dict] = {}
    for e in entries.values():
        p = e.get("plugin") or "(unknown)"
        if p not in by_plugin:
            by_plugin[p] = {"full": 0, "partial": 0, "version": plugin_versions.get(p)}
        by_plugin[p][e.get("coverage", "partial")] += 1
    info(f"  {builder} ({len(entries)} entries):")
    for plugin, d in sorted(by_plugin.items()):
        v = f"v{d['version']}" if d['version'] else "(no version)"
        full, partial = d['full'], d['partial']
        info(f"    {plugin:45} {v:15} full={full} partial={partial}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

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
        bound = 5 * 1024 * 1024
        flag = "OK" if size <= bound else "OVER ~3MB bound"
        ok(f"Total: {st['total_entries']} entries, {_human_bytes(size)} compressed ({flag}).")
        return

    if action == "generate":
        inst = getattr(args, "gen_instance", None) or getattr(args, "resolved_instance", None)
        if not inst:
            die("`sb schema-catalog generate` requires `--instance <gen>` — "
                "point at a running instance with EB free + Pro and Elementor + Pro/EA active.")
        _cmd_generate(inst)
        return

    die(f"unknown action '{action}' — choose from: status, generate")


def _cmd_generate(inst: str) -> None:
    info(f"Generating schema catalog from instance '{inst}'…")

    # 1. Plugin versions (for version-keying catalog entries).
    info("Fetching active plugin versions…")
    plugin_versions = _run_php(inst, _PLUGIN_VERSIONS_PHP) or {}

    # 2. Elementor widget dump.
    info("Dumping Elementor widget controls (get_controls())…")
    el_raw = _run_php(inst, _ELEMENTOR_DUMP_PHP)
    if isinstance(el_raw, dict) and "_error" in el_raw:
        warn(f"Elementor not available on '{inst}' — skipping elementor builder.")
        el_raw = {}

    # 3. Gutenberg / EB block dump (PHP source-based resolver).
    info("Dumping Gutenberg/EB block schemas (PHP source resolver)…")
    gb_raw = _run_php(inst, _GUTENBERG_DUMP_PHP)
    if not gb_raw:
        die("Gutenberg dump returned no data.")

    # 3b. Merge JS runtime dump (written by the headless schema-dump page).
    # The JS dump captures the full wp.blocks registry — including EB Pro blocks
    # where the PHP source resolver can't reach full fidelity (dist build, no src/).
    # JS dump wins for any block it contains (it has the real attribute set from
    # the JS runtime); PHP source resolver wins for blocks not in the JS dump.
    js_dump_path = wp_dir(inst) / "wp-content" / "sandbox-schema-dump" / "gutenberg.json"
    if js_dump_path.exists():
        info(f"Merging JS runtime dump ({js_dump_path.stat().st_size // 1024}KB)…")
        try:
            with open(js_dump_path, encoding="utf-8") as fh:
                js_raw: dict = json.load(fh)
            merged = 0
            for block_name, js_block in js_raw.items():
                js_attrs = js_block.get("attributes", {})
                php_entry = gb_raw.get(block_name, {})
                php_attrs = php_entry.get("attributes", {})
                # JS wins when it has more attributes OR the PHP entry is partial.
                if len(js_attrs) > len(php_attrs) or php_entry.get("coverage") == "partial":
                    # Infer plugin for EB Pro blocks by name prefix.
                    plugin = php_entry.get("plugin")
                    if plugin is None:
                        if "essential-blocks/pro-" in block_name:
                            plugin = "essential-blocks-pro"
                        elif block_name.startswith("essential-blocks/"):
                            plugin = "essential-blocks"
                        elif block_name.startswith("core/"):
                            plugin = "wordpress-core"
                    gb_raw[block_name] = {
                        "attributes": js_attrs,
                        "supports":   js_block.get("supports", php_entry.get("supports", {})),
                        "dynamic":    php_entry.get("dynamic", False),
                        "coverage":   "full",
                        "plugin":     plugin,
                    }
                    merged += 1
            info(f"  JS dump merged {merged} blocks (upgraded coverage to full).")
        except Exception as exc:
            warn(f"JS dump load failed ({exc}) — using PHP-only results.")
    else:
        warn(f"No JS dump found at {js_dump_path} — EB Pro blocks will be partial. "
             "Run the headless schema-dump page first: "
             "visit https://sandbox.tst/wp-admin/admin.php?page=sandbox-schema-dump")

    # 4. Build catalog entries.
    el_entries = _build_elementor_entries(el_raw, plugin_versions)
    gb_entries = _build_gutenberg_entries(gb_raw, plugin_versions)

    # 5. Pack into committed gzipped catalog.
    info("Packing catalog…")
    gb_report = pack_builder("gutenberg", gb_entries)
    el_report = pack_builder("elementor", el_entries) if el_entries else {"count": 0, "compressed_bytes": 0}

    # 6. Coverage report.
    ok("Coverage report:")
    _print_coverage(gb_entries, "gutenberg", plugin_versions)
    if el_entries:
        _print_coverage(el_entries, "elementor", plugin_versions)
    else:
        info("  elementor: skipped (plugin not active on gen instance)")

    total = gb_report["compressed_bytes"] + el_report["compressed_bytes"]
    bound = 5 * 1024 * 1024
    flag = "OK" if total <= bound else "OVER ~3MB bound!"
    ok(f"Total: {gb_report['count'] + el_report['count']} entries, "
       f"{_human_bytes(total)} compressed ({flag}).")
    ok("Committed under sandbox/assets/editor-schema/ — commit and push to ship.")


register({"schema-catalog": cmd_schema_catalog})
