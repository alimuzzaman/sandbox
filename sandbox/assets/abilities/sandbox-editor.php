<?php
/**
 * Sandbox editor-authoring helpers + abilities (spec 005).
 *
 * Gutenberg/EB: parse_blocks -> mutate -> serialize_blocks (unique blockId).
 * Elementor/EA: build the element tree (7-hex ids) -> Document::save(['elements'=>...]).
 * Plus editor-schema introspection, the shared read-before-write guards
 * (all-raw-HTML / deprecated / base-state-hash conflict), EA auto-enable, and
 * routing of static/third-party blocks to the headless finalizer (spec 005 US5).
 * Registered as sandbox/* abilities on the spec-003 Abilities layer; also
 * callable directly (host-side verification).
 *
 * Loaded by 00-sandbox-abilities.php when present. Dev/staging only.
 */

if (!defined('ABSPATH')) {
    exit;
}

/** 7-char lowercase hex id, matching Elementor's getUniqueId() format. */
function sandbox_editor_hexid(): string
{
    return substr(bin2hex(random_bytes(4)), 0, 7);
}

/**
 * Elementor's Document::save() gates on is_editable_by_current_user(); a bare
 * wp-cli / direct host call has no current user, so the save silently no-ops.
 * In the real (authenticated) ability call a user is already set, so this is a
 * no-op there. Dev/staging only. See memory/plugin-behavior.
 */
function sandbox_editor_ensure_user(): void
{
    if (get_current_user_id()) {
        return;
    }
    $admins = get_users(['role' => 'administrator', 'number' => 1, 'fields' => 'ID']);
    if ($admins) {
        wp_set_current_user((int) $admins[0]);
    }
}

/**
 * Deprecated widget/block slugs -> suggested replacement. Empty by default;
 * filterable so a plugin/test can register known-deprecated items (spec 005
 * guard, US-guards). Keyed by Gutenberg block name OR Elementor widgetType.
 */
function sandbox_editor_deprecated(): array
{
    return (array) apply_filters('sandbox_editor_deprecated', []);
}

/** Refuse a deprecated slug with a replacement suggestion, else null. */
function sandbox_editor_deprecation_guard(string $slug)
{
    $dep = sandbox_editor_deprecated();
    if (isset($dep[$slug])) {
        $repl = $dep[$slug];
        return new WP_Error('deprecated', "'$slug' is deprecated; use '$repl' instead.",
            ['replacement' => $repl]);
    }
    return null;
}

/* ----------------------------- Gutenberg / EB ----------------------------- */

/** Recursively build a parsed-block array from a spec (supports inner_blocks). */
function sandbox_editor_build_block(array $spec, ?string $parent_block_id = null): array
{
    $name  = (string) ($spec['name'] ?? '');
    $attrs = (array) ($spec['attributes'] ?? []);
    if (!isset($attrs['blockId'])) {
        $attrs['blockId'] = 'sb-' . sandbox_editor_hexid();
    }
    // EB child blocks carry their parent's blockId so per-block CSS/state nests.
    if ($parent_block_id !== null) {
        $attrs['parentBlockId'] = $parent_block_id;
    }
    $html         = (string) ($spec['inner_html'] ?? '');
    $inner_blocks = [];
    $inner_content = [];
    if ($html !== '') {
        $inner_content[] = $html;
    }
    foreach ((array) ($spec['inner_blocks'] ?? []) as $child) {
        $inner_blocks[]  = sandbox_editor_build_block((array) $child, $attrs['blockId']);
        $inner_content[] = null; // placeholder for the child block (serialize_blocks fills it)
    }
    if (empty($inner_content)) {
        $inner_content[] = $html;
    }
    return [
        'blockName'    => $name,
        'attrs'        => $attrs,
        'innerBlocks'  => $inner_blocks,
        'innerHTML'    => $html,
        'innerContent' => $inner_content,
    ];
}

/**
 * Insert a Gutenberg block at the end of a post's content.
 * @param array $input {post_id:int, name:string, attributes?:array, inner_html?:string,
 *                      inner_blocks?:array, base_hash?:string}
 * @return array|WP_Error
 */
function sandbox_editor_gutenberg_insert($input)
{
    $post_id = (int) ($input['post_id'] ?? 0);
    $name    = (string) ($input['name'] ?? '');
    if (!$post_id || !$name) {
        // all-raw-HTML guard: an inner_html with no block name is refused.
        if (!$name && !empty($input['inner_html'])) {
            return new WP_Error('raw_html_refused',
                'Raw HTML insertion refused: provide a block "name" (e.g. core/paragraph), not bare inner_html.');
        }
        return new WP_Error('bad_input', 'post_id and name are required');
    }
    if (!($post = get_post($post_id))) {
        return new WP_Error('not_found', "post $post_id not found");
    }
    if ($err = sandbox_editor_deprecation_guard($name)) {
        return $err;
    }
    // base-state conflict rejection (optional): caller passes the hash it read.
    if (isset($input['base_hash']) && md5($post->post_content) !== (string) $input['base_hash']) {
        return new WP_Error('conflict', 'Base state changed since read; refusing to overwrite.',
            ['current_hash' => md5($post->post_content)]);
    }

    $block  = sandbox_editor_build_block([
        'name'         => $name,
        'attributes'   => (array) ($input['attributes'] ?? []),
        'inner_html'   => (string) ($input['inner_html'] ?? ''),
        'inner_blocks' => (array) ($input['inner_blocks'] ?? []),
    ]);
    $blocks   = parse_blocks($post->post_content);
    $blocks[] = $block;
    $content  = serialize_blocks($blocks);
    $r = wp_update_post(['ID' => $post_id, 'post_content' => $content], true);
    if (is_wp_error($r)) {
        return $r;
    }
    $new = get_post($post_id);
    return ['post_id' => $post_id, 'inserted' => $name, 'blockId' => $block['attrs']['blockId'],
            'block_count' => count($blocks), 'state_hash' => md5($new->post_content),
            'note' => 'Dynamic blocks render styled immediately; static/third-party blocks should '
                    . 'route through the finalizer (sandbox_editor_gutenberg_finalize) for editor-valid save markup.'];
}

/** Return the parsed-block tree of a post (compact) + its state hash. */
function sandbox_editor_gutenberg_get($input)
{
    $post = get_post((int) ($input['post_id'] ?? 0));
    if (!$post) {
        return new WP_Error('not_found', 'post not found');
    }
    $walk = function ($blocks) use (&$walk) {
        $out = [];
        foreach ($blocks as $b) {
            if (!$b['blockName']) {
                continue;
            }
            $out[] = [
                'name'     => $b['blockName'],
                'blockId'  => $b['attrs']['blockId'] ?? null,
                'attr_keys' => array_keys($b['attrs']),
                'children' => empty($b['innerBlocks']) ? [] : $walk($b['innerBlocks']),
            ];
        }
        return $out;
    };
    return ['post_id' => $post->ID, 'state_hash' => md5($post->post_content),
            'blocks' => $walk(parse_blocks($post->post_content))];
}

/** Locate a block by blockId anywhere in the tree and apply $cb by reference. */
function sandbox_editor_walk_block_id(array &$blocks, string $block_id, callable $cb): bool
{
    foreach ($blocks as &$b) {
        if (($b['attrs']['blockId'] ?? null) === $block_id) {
            $cb($b);
            return true;
        }
        if (!empty($b['innerBlocks']) && sandbox_editor_walk_block_id($b['innerBlocks'], $block_id, $cb)) {
            return true;
        }
    }
    return false;
}

/**
 * Update a block's attributes, located by blockId (not position).
 * @param array $input {post_id:int, block_id:string, attributes:array, base_hash?:string}
 */
function sandbox_editor_gutenberg_update($input)
{
    $post_id  = (int) ($input['post_id'] ?? 0);
    $block_id = (string) ($input['block_id'] ?? '');
    if (!$post_id || !$block_id) {
        return new WP_Error('bad_input', 'post_id and block_id are required');
    }
    if (!($post = get_post($post_id))) {
        return new WP_Error('not_found', "post $post_id not found");
    }
    if (isset($input['base_hash']) && md5($post->post_content) !== (string) $input['base_hash']) {
        return new WP_Error('conflict', 'Base state changed since read; refusing to overwrite.',
            ['current_hash' => md5($post->post_content)]);
    }
    $attrs  = (array) ($input['attributes'] ?? []);
    $blocks = parse_blocks($post->post_content);
    $found  = sandbox_editor_walk_block_id($blocks, $block_id, function (&$b) use ($attrs) {
        $b['attrs'] = array_merge($b['attrs'], $attrs); // located by identity; merge, never replace whole
    });
    if (!$found) {
        return new WP_Error('not_found', "block '$block_id' not found in post $post_id");
    }
    $content = serialize_blocks($blocks);
    $r = wp_update_post(['ID' => $post_id, 'post_content' => $content], true);
    if (is_wp_error($r)) {
        return $r;
    }
    $new = get_post($post_id);
    return ['post_id' => $post_id, 'block_id' => $block_id, 'updated_keys' => array_keys($attrs),
            'state_hash' => md5($new->post_content)];
}

/** Recursively drop a block (and its placeholder) by blockId; rebuilds innerContent. */
function sandbox_editor_remove_block(array $blocks, string $block_id, bool &$removed): array
{
    $out = [];
    foreach ($blocks as $b) {
        if (($b['attrs']['blockId'] ?? null) === $block_id) {
            $removed = true;
            continue;
        }
        if (!empty($b['innerBlocks'])) {
            $before = count($b['innerBlocks']);
            $b['innerBlocks'] = sandbox_editor_remove_block($b['innerBlocks'], $block_id, $removed);
            // If a child was dropped, re-derive innerContent so serialize stays consistent.
            if (count($b['innerBlocks']) !== $before) {
                $strings = array_values(array_filter($b['innerContent'], 'is_string'));
                $rebuilt = [];
                foreach ($b['innerBlocks'] as $i => $_child) {
                    $rebuilt[] = $strings[$i] ?? '';
                    $rebuilt[] = null;
                }
                $rebuilt[] = end($strings) !== false ? '' : '';
                $b['innerContent'] = $rebuilt;
            }
        }
        $out[] = $b;
    }
    return $out;
}

/**
 * Delete a block by blockId (destructive — requires confirm:true).
 * @param array $input {post_id:int, block_id:string, confirm:bool, base_hash?:string}
 */
function sandbox_editor_gutenberg_delete($input)
{
    $post_id  = (int) ($input['post_id'] ?? 0);
    $block_id = (string) ($input['block_id'] ?? '');
    if (!$post_id || !$block_id) {
        return new WP_Error('bad_input', 'post_id and block_id are required');
    }
    if (empty($input['confirm'])) {
        return new WP_Error('confirm_required', 'Destructive op: pass confirm:true to delete the block.');
    }
    if (!($post = get_post($post_id))) {
        return new WP_Error('not_found', "post $post_id not found");
    }
    if (isset($input['base_hash']) && md5($post->post_content) !== (string) $input['base_hash']) {
        return new WP_Error('conflict', 'Base state changed since read; refusing to overwrite.',
            ['current_hash' => md5($post->post_content)]);
    }
    $removed = false;
    $blocks  = sandbox_editor_remove_block(parse_blocks($post->post_content), $block_id, $removed);
    if (!$removed) {
        return new WP_Error('not_found', "block '$block_id' not found in post $post_id");
    }
    $content = serialize_blocks($blocks);
    $r = wp_update_post(['ID' => $post_id, 'post_content' => $content], true);
    if (is_wp_error($r)) {
        return $r;
    }
    $new = get_post($post_id);
    return ['post_id' => $post_id, 'deleted' => $block_id, 'state_hash' => md5($new->post_content)];
}

/**
 * Queue a static/third-party block spec for the headless real-editor finalizer
 * (spec 005 US5). The finalizer mu-plugin serializes it through real wp.blocks
 * so WP block validation passes. Returns the job id the agent polls.
 * @param array $input {post_id:int, block_spec:array|string, base_hash?:string}
 */
function sandbox_editor_gutenberg_finalize($input)
{
    if (!function_exists('sandbox_eb_finalizer_enqueue')) {
        return new WP_Error('no_finalizer', 'EB finalizer mu-plugin not loaded.');
    }
    $post_id = (int) ($input['post_id'] ?? 0);
    $spec    = $input['block_spec'] ?? null;
    if (!$post_id || !$spec) {
        return new WP_Error('bad_input', 'post_id and block_spec are required');
    }
    if (!($post = get_post($post_id))) {
        return new WP_Error('not_found', "post $post_id not found");
    }
    if (isset($input['base_hash']) && md5($post->post_content) !== (string) $input['base_hash']) {
        return new WP_Error('conflict', 'Base state changed since read; refusing to overwrite.',
            ['current_hash' => md5($post->post_content)]);
    }
    return sandbox_eb_finalizer_enqueue($post_id, $spec);
}

/* ----------------------------- Elementor / EA ----------------------------- */

/**
 * Ensure an EA (eael-*) widget is registered: if it exists in EA's element
 * catalog but is disabled, flip it on in eael_save_settings and re-run EA's
 * registration in-request. Returns true if the widget is registered afterwards.
 */
function sandbox_editor_elementor_enable_widget(string $widget): bool
{
    $wm = \Elementor\Plugin::$instance->widgets_manager;
    if (isset($wm->get_widget_types()[$widget])) {
        return true; // already registered
    }
    if (strpos($widget, 'eael-') !== 0
        || !class_exists('\\Essential_Addons_Elementor\\Classes\\Bootstrap')) {
        return false; // not an EA widget we can enable (e.g. Pro-only / absent)
    }
    $ea = \Essential_Addons_Elementor\Classes\Bootstrap::instance();
    try {
        $ref  = new ReflectionProperty($ea, 'registered_elements');
        $ref->setAccessible(true);
        $els = (array) $ref->getValue($ea);
    } catch (\Throwable $e) {
        return false;
    }
    // Map the requested widgetType back to its eael_save_settings key.
    $key = null;
    foreach ($els as $k => $def) {
        if (empty($def['class']) || !class_exists($def['class'])) {
            continue;
        }
        try {
            if ((new $def['class']())->get_name() === $widget) {
                $key = $k;
                break;
            }
        } catch (\Throwable $e) {
            // some widgets need Elementor editor context to instantiate; skip.
        }
    }
    if ($key === null) {
        return false; // genuinely not in this build (Pro-only / not installed)
    }
    $opt = (array) get_option('eael_save_settings', []);
    if (empty($opt[$key])) {
        $opt[$key] = true;
        update_option('eael_save_settings', $opt);
    }
    $ea->register_elements($wm); // re-register with the now-enabled set, this request
    return isset($wm->get_widget_types()[$widget]);
}

/** Regenerate an Elementor post's CSS file (delete cache + rebuild). */
function sandbox_editor_elementor_regen_css(int $post_id): void
{
    delete_post_meta($post_id, '_elementor_css');
    if (class_exists('\\Elementor\\Core\\Files\\CSS\\Post')) {
        try {
            \Elementor\Core\Files\CSS\Post::create($post_id)->update();
        } catch (\Throwable $e) {
            // best-effort; frontend regenerates lazily on next view.
        }
    }
}

/** True if any node in the tree has widgetType === $widget. */
function sandbox_editor_elementor_has_widget(array $tree, string $widget): bool
{
    $found = false;
    foreach ($tree as $sec) {
        array_walk_recursive($sec, function ($v, $k) use (&$found, $widget) {
            if ($k === 'widgetType' && $v === $widget) {
                $found = true;
            }
        });
    }
    return $found;
}

/**
 * Insert an Elementor widget into a page (wrapped in section>column).
 * @param array $input {post_id:int, widget:string, settings?:array, full_width?:bool, base_hash?:string}
 * @return array|WP_Error
 */
function sandbox_editor_elementor_insert($input)
{
    if (!did_action('elementor/loaded') && !class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor is not active');
    }
    $post_id = (int) ($input['post_id'] ?? 0);
    $widget  = (string) ($input['widget'] ?? '');
    if (!$post_id || !$widget) {
        return new WP_Error('bad_input', 'post_id and widget are required');
    }
    if ($err = sandbox_editor_deprecation_guard($widget)) {
        return $err;
    }
    sandbox_editor_ensure_user();
    // Enable the EA widget if it's an eael-* one and not yet registered.
    $enabled = sandbox_editor_elementor_enable_widget($widget);
    if (!$enabled) {
        return new WP_Error('widget_unavailable',
            "widget '$widget' is not registered and could not be enabled "
          . "(Pro-only or not installed in this build).");
    }

    $settings = (array) ($input['settings'] ?? []);
    $doc = \Elementor\Plugin::$instance->documents->get($post_id);
    if (!$doc) {
        return new WP_Error('no_doc', "no Elementor document for post $post_id");
    }
    $current = $doc->get_elements_data();
    if (isset($input['base_hash'])) {
        $h = md5((string) get_post_meta($post_id, '_elementor_data', true));
        if ($h !== (string) $input['base_hash']) {
            return new WP_Error('conflict', 'Base state changed since read; refusing to overwrite.',
                ['current_hash' => $h]);
        }
    }
    $node = [
        'id' => sandbox_editor_hexid(), 'elType' => 'section', 'settings' => [], 'isInner' => false,
        'elements' => [[
            'id' => sandbox_editor_hexid(), 'elType' => 'column',
            'settings' => ['_column_size' => 100], 'isInner' => false, 'elements' => [[
                'id' => sandbox_editor_hexid(), 'elType' => 'widget',
                'widgetType' => $widget, 'settings' => $settings, 'elements' => [],
            ]],
        ]],
    ];
    $tree   = $current;
    $tree[] = $node;
    $ok = $doc->save(['elements' => $tree]);
    update_post_meta($post_id, '_elementor_edit_mode', 'builder');
    if (!empty($input['full_width'])) {
        update_post_meta($post_id, '_wp_page_template', 'elementor_canvas');
    }
    sandbox_editor_elementor_regen_css($post_id);
    $survived = sandbox_editor_elementor_has_widget($doc->get_elements_data(), $widget);
    $widget_id = $node['elements'][0]['elements'][0]['id'];
    return ['post_id' => $post_id, 'widget' => $widget, 'widget_enabled' => $enabled,
            'element_id' => $widget_id, 'saved' => (bool) $ok, 'widget_survived' => $survived,
            'state_hash' => md5((string) get_post_meta($post_id, '_elementor_data', true))];
}

/** Return an Elementor post's element tree (compact: id/elType/widgetType) + hash. */
function sandbox_editor_elementor_get($input)
{
    if (!class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor is not active');
    }
    $post_id = (int) ($input['post_id'] ?? 0);
    $doc = \Elementor\Plugin::$instance->documents->get($post_id);
    if (!$doc) {
        return new WP_Error('no_doc', "no Elementor document for post $post_id");
    }
    $walk = function ($els) use (&$walk) {
        $out = [];
        foreach ($els as $e) {
            $out[] = [
                'id'         => $e['id'] ?? null,
                'elType'     => $e['elType'] ?? null,
                'widgetType' => $e['widgetType'] ?? null,
                'elements'   => empty($e['elements']) ? [] : $walk($e['elements']),
            ];
        }
        return $out;
    };
    return ['post_id' => $post_id, 'state_hash' => md5((string) get_post_meta($post_id, '_elementor_data', true)),
            'elements' => $walk($doc->get_elements_data())];
}

/** Locate an Elementor element by id in a tree and apply $cb by reference. */
function sandbox_editor_walk_element_id(array &$els, string $id, callable $cb): bool
{
    foreach ($els as &$e) {
        if (($e['id'] ?? null) === $id) {
            $cb($e);
            return true;
        }
        if (!empty($e['elements']) && sandbox_editor_walk_element_id($e['elements'], $id, $cb)) {
            return true;
        }
    }
    return false;
}

/**
 * Update an element's settings, located by id (not position). Merges per
 * control id so responsive/typography/media/repeater controls round-trip.
 * @param array $input {post_id:int, element_id:string, settings:array, base_hash?:string}
 */
function sandbox_editor_elementor_update($input)
{
    if (!class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor is not active');
    }
    $post_id    = (int) ($input['post_id'] ?? 0);
    $element_id = (string) ($input['element_id'] ?? '');
    if (!$post_id || !$element_id) {
        return new WP_Error('bad_input', 'post_id and element_id are required');
    }
    sandbox_editor_ensure_user();
    $doc = \Elementor\Plugin::$instance->documents->get($post_id);
    if (!$doc) {
        return new WP_Error('no_doc', "no Elementor document for post $post_id");
    }
    if (isset($input['base_hash'])) {
        $h = md5((string) get_post_meta($post_id, '_elementor_data', true));
        if ($h !== (string) $input['base_hash']) {
            return new WP_Error('conflict', 'Base state changed since read; refusing to overwrite.',
                ['current_hash' => $h]);
        }
    }
    $settings = (array) ($input['settings'] ?? []);
    $tree  = $doc->get_elements_data();
    $found = sandbox_editor_walk_element_id($tree, $element_id, function (&$e) use ($settings) {
        $e['settings'] = array_merge((array) ($e['settings'] ?? []), $settings); // merge per control id
    });
    if (!$found) {
        return new WP_Error('not_found', "element '$element_id' not found in post $post_id");
    }
    $ok = $doc->save(['elements' => $tree]);
    sandbox_editor_elementor_regen_css($post_id);
    return ['post_id' => $post_id, 'element_id' => $element_id, 'updated_keys' => array_keys($settings),
            'saved' => (bool) $ok, 'state_hash' => md5((string) get_post_meta($post_id, '_elementor_data', true))];
}

/**
 * Delete an Elementor element by id (destructive — requires confirm:true).
 * @param array $input {post_id:int, element_id:string, confirm:bool, base_hash?:string}
 */
function sandbox_editor_elementor_delete($input)
{
    if (!class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor is not active');
    }
    $post_id    = (int) ($input['post_id'] ?? 0);
    $element_id = (string) ($input['element_id'] ?? '');
    if (!$post_id || !$element_id) {
        return new WP_Error('bad_input', 'post_id and element_id are required');
    }
    if (empty($input['confirm'])) {
        return new WP_Error('confirm_required', 'Destructive op: pass confirm:true to delete the element.');
    }
    sandbox_editor_ensure_user();
    $doc = \Elementor\Plugin::$instance->documents->get($post_id);
    if (!$doc) {
        return new WP_Error('no_doc', "no Elementor document for post $post_id");
    }
    if (isset($input['base_hash'])) {
        $h = md5((string) get_post_meta($post_id, '_elementor_data', true));
        if ($h !== (string) $input['base_hash']) {
            return new WP_Error('conflict', 'Base state changed since read; refusing to overwrite.',
                ['current_hash' => $h]);
        }
    }
    $remove = function ($els) use (&$remove, $element_id, &$removed) {
        $out = [];
        foreach ($els as $e) {
            if (($e['id'] ?? null) === $element_id) {
                $removed = true;
                continue;
            }
            if (!empty($e['elements'])) {
                $e['elements'] = $remove($e['elements']);
            }
            $out[] = $e;
        }
        return $out;
    };
    $removed = false;
    $tree = $remove($doc->get_elements_data());
    if (!$removed) {
        return new WP_Error('not_found', "element '$element_id' not found in post $post_id");
    }
    $ok = $doc->save(['elements' => $tree]);
    sandbox_editor_elementor_regen_css($post_id);
    return ['post_id' => $post_id, 'deleted' => $element_id, 'saved' => (bool) $ok,
            'state_hash' => md5((string) get_post_meta($post_id, '_elementor_data', true))];
}

/* ------------------- EB attribute-schema resolver (spec 011) --------------- *
 * EB declares 0 attributes in block.json and assembles its real attribute set
 * (hundreds) at JS runtime from src/blocks/<name>/src/attributes.js plus generator
 * helpers in the controls package. The registered WP_Block_Type therefore only
 * exposes ~3 generic keys. This resolver reads the source checkout (discovered
 * under the bind-mounted plugin-home) and expands attributes.js + its generators
 * to the full set, with an honest fidelity report. Reads only; never mutates.    */

/** EB generator helper name -> source filename under controls/src/helpers. */
function sandbox_editor_eb_helper_file($generator)
{
    static $map = [
        'generateTypographyAttributes'             => 'typoHelpers.js',
        'generateDimensionsAttributes'             => 'dimensionHelpers.js',
        'generateBorderShadowAttributes'           => 'borderShadowHelpers.js',
        'generateBackgroundAttributes'             => 'backgroundHelpers.js',
        'generateResponsiveRangeAttributes'        => 'responsiveRangeHelpers.js',
        'generateResponsiveAlignAttributes'        => 'responsiveAlignControlHelpers.js',
        'generateShapeDividerAttributes'           => 'shapeDividerHelpers.js',
        'generateResponsiveSelectControlAttributes' => 'responsiveSelectControlHelpers.js',
        'generateTextControllerAttributes'         => 'responsiveTextControllerHelpers.js',
    ];
    return isset($map[$generator]) ? $map[$generator] : null;
}

/** Verified per-prefix key-family counts — fallback ONLY when a helper file cannot
 *  be parsed; using it marks the response 'partial'. (data-model.md) */
function sandbox_editor_eb_fallback_count($generator)
{
    static $counts = [
        'generateTypographyAttributes'      => 24,
        'generateDimensionsAttributes'      => 16,
        'generateBorderShadowAttributes'    => 85,
        'generateBackgroundAttributes'      => 155,
        'generateResponsiveRangeAttributes' => 7,
        'generateResponsiveAlignAttributes' => 3,
    ];
    return isset($counts[$generator]) ? $counts[$generator] : null;
}

/** Return the substring from the brace at $open to its string-aware match. */
function sandbox_editor_eb_brace_slice($s, $open)
{
    $len = strlen($s);
    $depth = 0;
    $inStr = false;
    $q = '';
    for ($i = $open; $i < $len; $i++) {
        $c = $s[$i];
        if ($inStr) {
            if ($c === '\\') { $i++; continue; }
            if ($c === $q) { $inStr = false; }
            continue;
        }
        if ($c === '"' || $c === "'" || $c === '`') { $inStr = true; $q = $c; continue; }
        if ($c === '{') { $depth++; }
        elseif ($c === '}') { $depth--; if ($depth === 0) { return substr($s, $open, $i - $open + 1); } }
    }
    return substr($s, $open);
}

/** Strip // line comments and block comments (so commented-out generator spreads
 *  in EB helpers are not mistaken for live ones). */
function sandbox_editor_eb_decomment($src)
{
    $src = preg_replace('!/\*.*?\*/!s', '', $src);
    $src = preg_replace('!^[ \t]*//.*$!m', '', $src);
    return $src;
}

/** Discover an EB source for this block: scan the EB plugins WP actually loads from
 *  (WP_PLUGIN_DIR, resolving symlinks to their mounted source), any explicit
 *  source roots passed in, and a mounted plugin-home if SANDBOX_PLUGINS_HOST is set.
 *  attributes.js (explicit attrs + generator calls) ships even in the .org build;
 *  the controls helpers ship only in a full source checkout. (FR-006) */
function sandbox_editor_eb_source_discover($block_dir_name, $extra_roots = [])
{
    $roots = [];
    foreach ((array) $extra_roots as $r) {
        if ($r && is_dir($r)) { $roots[] = rtrim($r, '/'); }
    }
    foreach (['essential-blocks', 'essential-blocks-pro'] as $slug) {
        $p = WP_PLUGIN_DIR . '/' . $slug;
        if (is_dir($p)) {
            $roots[] = $p;
            $rp = realpath($p);
            if ($rp && $rp !== $p) { $roots[] = $rp; }
        }
    }
    $home = getenv('SANDBOX_PLUGINS_HOST');
    if ($home && is_dir($home)) {
        foreach (['essential-blocks', 'essential-blocks-pro'] as $slug) {
            foreach (array_merge(glob("$home/$slug", GLOB_ONLYDIR) ?: [],
                                 glob("$home/*/$slug", GLOB_ONLYDIR) ?: []) as $d) {
                $roots[] = $d;
            }
        }
    }
    $roots = array_values(array_unique($roots));

    $attr = null; $blockDir = null; $checkout = null; $helpers = null; $helpers_from = null;
    foreach ($roots as $dir) {
        $base = $dir . '/src/blocks/' . $block_dir_name;
        // attributes.js lives either at <block>/src/attributes.js or
        // <block>/src/components/attributes.js depending on the block.
        foreach (['/src/attributes.js', '/src/components/attributes.js'] as $rel) {
            if ($attr === null && is_file($base . $rel)) {
                $attr = $base . $rel; $blockDir = $base; $checkout = $dir;
            }
        }
        $h = $dir . '/src/controls/src/helpers';
        if ($helpers === null && is_dir($h)) { $helpers = $h; $helpers_from = $dir; }
    }
    if ($attr === null) { return null; }
    return ['checkout' => $checkout, 'attributes_file' => $attr,
            'block_dir' => $blockDir, 'helpers' => $helpers, 'helpers_from' => $helpers_from];
}

/** Resolve prefix constants imported by a block's attributes.js to their string
 *  values (read from the block's ./constants/*.js). */
function sandbox_editor_eb_resolve_constants($src, $attr_dir)
{
    $map = [];
    // Any relative import (./… or ../…) may carry prefix-constant string values.
    if (preg_match_all('!import\s*\{[^}]*\}\s*from\s*["\'](\.[^"\']+)["\']!', $src, $im, PREG_SET_ORDER)) {
        foreach ($im as $imp) {
            $file = $attr_dir . '/' . $imp[1] . '.js';
            if (!is_file($file)) { continue; }
            $csrc = @file_get_contents($file);
            if ($csrc === false) { continue; }
            if (preg_match_all('!export\s+const\s+([A-Za-z0-9_]+)\s*=\s*["\']([^"\']*)["\']!', $csrc, $cm, PREG_SET_ORDER)) {
                foreach ($cm as $c) { $map[$c[1]] = $c[2]; }
            }
        }
    }
    return $map;
}

/** Parse a block's attributes.js -> explicit attrs (name=>{type,default}) and
 *  generator spread calls (generator + resolved prefix value). (D3) */
function sandbox_editor_eb_parse_attributes($attributes_file, $block_dir)
{
    $src = @file_get_contents($attributes_file);
    if ($src === false) { return ['explicit' => [], 'generators' => []]; }
    $constants = sandbox_editor_eb_resolve_constants($src, dirname($attributes_file));

    $body = $src;
    if (preg_match('/const\s+attributes\s*=\s*\{/s', $src, $m, PREG_OFFSET_CAPTURE)) {
        $open = $m[0][1] + strlen($m[0][0]) - 1;
        $body = sandbox_editor_eb_brace_slice($src, $open);
    }
    $clean = sandbox_editor_eb_decomment($body);

    // Generator spreads: ...generateXxxAttributes(PREFIX, ...)
    $generators = [];
    if (preg_match_all('/\.\.\.\s*(generate[A-Za-z]+Attributes)\s*\(\s*([A-Za-z0-9_]+)/', $clean, $gm, PREG_SET_ORDER)) {
        foreach ($gm as $g) {
            $generators[] = ['generator' => $g[1], 'const' => $g[2],
                             'prefix' => isset($constants[$g[2]]) ? $constants[$g[2]] : null];
        }
    }

    // Explicit depth-1 attrs: `name: { ... type ... }` (string- and depth-aware).
    $explicit = [];
    $len = strlen($clean);
    $depth = 0; $i = 0; $inStr = false; $q = '';
    while ($i < $len) {
        $c = $clean[$i];
        if ($inStr) {
            if ($c === '\\') { $i += 2; continue; }
            if ($c === $q) { $inStr = false; }
            $i++; continue;
        }
        if ($c === '"' || $c === "'" || $c === '`') { $inStr = true; $q = $c; $i++; continue; }
        if ($c === '[' || $c === '(') { $depth++; $i++; continue; }
        if ($c === ']' || $c === ')') { $depth--; $i++; continue; }
        if ($c === '{') {
            $depth++; $i++; continue;
        }
        if ($c === '}') { $depth--; $i++; continue; }
        if ($depth === 1 && (ctype_alpha($c) || $c === '_')
            && preg_match('/([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{/A', $clean, $km, 0, $i)) {
            $name = $km[1];
            $bracePos = strpos($clean, '{', $i);
            $obj = sandbox_editor_eb_brace_slice($clean, $bracePos);
            $type = null; $default = null;
            if (preg_match('/type\s*:\s*["\']([^"\']+)["\']/', $obj, $tm)) { $type = $tm[1]; }
            if (preg_match('/default\s*:\s*("(?:[^"\\\\]|\\\\.)*"|\'(?:[^\'\\\\]|\\\\.)*\'|true|false|-?\d+(?:\.\d+)?)/', $obj, $dm)) {
                $raw = $dm[1];
                if ($raw === 'true') { $default = true; }
                elseif ($raw === 'false') { $default = false; }
                elseif (is_numeric($raw)) { $default = $raw + 0; }
                else { $default = trim($raw, "\"'"); }
            }
            $explicit[$name] = ['type' => $type, 'default' => $default];
            $i = $bracePos + strlen($obj);
            continue;
        }
        $i++;
    }
    return ['explicit' => $explicit, 'generators' => $generators];
}

/** Expand one generator (recursively, incl. nested spreads) to its emitted attribute
 *  keys for the given prefix value, by parsing the helper source. Marks $unresolved
 *  when a helper file is missing/unparseable. (D4, FR-002) */
function sandbox_editor_eb_expand_generator($generator, $prefix, $helpers_dir, &$unresolved, $depth = 0)
{
    if ($prefix === null || $depth > 5) { return []; }
    $file = sandbox_editor_eb_helper_file($generator);
    if (!$file || !$helpers_dir) { $unresolved[$generator] = true; return []; }
    $path = $helpers_dir . '/' . $file;
    $src = @file_get_contents($path);
    if ($src === false) { $unresolved[$generator] = true; return []; }
    $src = sandbox_editor_eb_decomment($src);

    $keys = [];
    // Attribute-definition templates: [`...${x}...`]: { ... type: ...
    if (preg_match_all('/\[`([^`]*\$\{[^}]+\}[^`]*)`\]\s*:\s*\{/', $src, $mm, PREG_OFFSET_CAPTURE)) {
        foreach ($mm[1] as $idx => $cap) {
            $after = substr($src, $mm[0][$idx][1] + strlen($mm[0][$idx][0]), 160);
            if (strpos($after, 'type:') === false) { continue; }
            $key = preg_replace('/\$\{[^}]+\}/', $prefix, $cap[0]);
            $keys[$key] = true;
        }
    }
    // Nested generator spreads inside the helper: ...generateXxx(`...${x}...`, ...)
    if (preg_match_all('/\.\.\.\s*(generate[A-Za-z]+Attributes)\s*\(\s*`([^`]*)`/', $src, $nm, PREG_SET_ORDER)) {
        foreach ($nm as $n) {
            $nestedPrefix = preg_replace('/\$\{[^}]+\}/', $prefix, $n[2]);
            foreach (sandbox_editor_eb_expand_generator($n[1], $nestedPrefix, $helpers_dir, $unresolved, $depth + 1) as $k => $_) {
                $keys[$k] = true;
            }
        }
    }
    return $keys;
}

/** The true 'dynamic' signal for a Gutenberg block, EB-aware.
 *
 * `(bool) $block_type->render_callback` is what every call site used to report — but
 * Essential Blocks' base `Block::register()` ALWAYS attaches a generic render_callback
 * to EVERY block it registers, even ones with zero server-side content generation:
 * that wrapper only handles conditional display (`should_display_block`) + inline-SVG-
 * icon substitution, then falls through to `$this->render_callback(...)` ONLY when the
 * concrete block subclass defines its OWN `render_callback()` method — otherwise it
 * just returns $content unchanged. So `(bool) render_callback` is unconditionally TRUE
 * for every single EB block, dynamic or not, and is useless as a static/dynamic signal.
 * Verified by reading the plugin source directly: `infobox`, `number-counter`,
 * `pricing-table`, `row`, `column`, `testimonial`, and `advanced-heading` (in its
 * default 'custom' title-source mode) all report `dynamic:true` from the raw signal
 * while their ENTIRE visible markup/styling is baked into the STATIC `save.js` output
 * at editor-save time — a direct `gutenberg-insert` on any of them renders EMPTY on the
 * frontend (matches the gutenberg-eb skill's existing static-block warning) and needs
 * the real-editor finalizer instead. Only blocks whose class defines its own
 * `render_callback()` (AdvancedImage, Button, Text, PostGrid, AdvancedHeading's
 * 'dynamic-title' source, ...) actually generate content server-side from attributes.
 * Recovered here via the closure's bound `$this` (Reflection) + `method_exists` — the
 * exact same fork the plugin's own `register()` performs, just read back out. Only
 * applied to `essential-blocks/*` names; core/other builders keep the original signal
 * (already accurate for them — this is an EB-specific defect). */
function sandbox_editor_dynamic_flag($name, $block_type)
{
    if (!$block_type) { return null; }
    $cb = $block_type->render_callback ?? null;
    if (strpos((string) $name, 'essential-blocks/') === 0 && $cb instanceof \Closure) {
        try {
            $refl  = new \ReflectionFunction($cb);
            $bound = $refl->getClosureThis();
            if ($bound !== null) { return method_exists($bound, 'render_callback'); }
        } catch (\Throwable $e) {
            // fall through to the raw signal below
        }
    }
    return (bool) $cb;
}

/** Build the structured fidelity report + back-compat string. (D6, FR-003) */
function sandbox_editor_eb_fidelity($level, $count, $checkout, $unresolved)
{
    $reason = null;
    if ($level === 'reduced') {
        $reason = 'no EB source (src/blocks/<name>/.../attributes.js) found in the active EB plugin dirs or configured source roots; returning block.json attributes only';
    } elseif ($level === 'partial') {
        $reason = count($unresolved) . ' generator(s) could not be expanded from source; counts may be incomplete';
    }
    return [
        'level' => $level,
        'count' => $count,
        'source_checkout' => $checkout,
        'unresolved' => array_values(array_keys($unresolved)),
        'reason' => $reason,
    ];
}

/** Full EB schema for a named block: discover source, parse, expand, cache.
 *  Returns the named-block response array (FR-001..005, FR-011) or null to fall
 *  back to the reduced (block.json) path. */
function sandbox_editor_eb_resolve($block_name, $block_type, $extra_roots = [])
{
    $block_dir_name = substr($block_name, strlen('essential-blocks/'));
    $src = sandbox_editor_eb_source_discover($block_dir_name, $extra_roots);
    if ($src === null) {
        return null; // caller renders reduced fidelity
    }

    // Cache key fingerprints the checkout + relevant source mtimes. (D5)
    $fp = [$src['attributes_file']];
    $attr_dir = dirname($src['attributes_file']);
    foreach ((glob($attr_dir . '/constants/*.js') ?: []) as $f) { $fp[] = $f; }
    foreach ((glob($src['block_dir'] . '/src/constants/*.js') ?: []) as $f) { $fp[] = $f; }
    if ($src['helpers']) {
        foreach ((glob($src['helpers'] . '/*.js') ?: []) as $f) { $fp[] = $f; }
    }
    $sig = [];
    foreach ($fp as $f) { $sig[] = $f . ':' . (@filemtime($f) ?: 0); }
    $cache_key = 'sbx_eb_schema_' . md5($block_name . '|' . implode('|', $sig));
    $cached = get_transient($cache_key);
    if (is_array($cached)) { return $cached; }

    $parsed = sandbox_editor_eb_parse_attributes($src['attributes_file'], $src['block_dir']);
    $attrs = [];
    foreach ($parsed['explicit'] as $k => $def) {
        $attrs[$k] = ['type' => $def['type'], 'default' => $def['default']];
    }
    $unresolved = [];
    foreach ($parsed['generators'] as $g) {
        if ($g['prefix'] === null) { $unresolved[$g['generator'] . '(' . $g['const'] . ')'] = true; continue; }
        $expanded = sandbox_editor_eb_expand_generator($g['generator'], $g['prefix'], $src['helpers'], $unresolved);
        if (!$expanded && sandbox_editor_eb_fallback_count($g['generator']) !== null) {
            $unresolved[$g['generator']] = true; // helper unparseable; count-only knowledge
        }
        foreach ($expanded as $k => $_) {
            if (!isset($attrs[$k])) { $attrs[$k] = ['type' => null, 'default' => null]; } // explicit wins
        }
    }

    $level = $unresolved ? 'partial' : 'full';
    $resp = [
        'builder' => 'gutenberg',
        'name' => $block_name,
        'dynamic' => sandbox_editor_dynamic_flag($block_name, $block_type),
        'attributes' => $attrs,
        'fidelity' => sandbox_editor_eb_fidelity($level, count($attrs), $src['checkout'], $unresolved),
        'eb_attribute_fidelity' => $level,
    ];
    set_transient($cache_key, $resp, HOUR_IN_SECONDS);
    return $resp;
}

/* ------------------------------- Schema ----------------------------------- */

/* ------------------ Bundled schema catalog fallback (spec 012) ------------- *
 * editor-schema serves full fidelity from a provisioned, gzipped catalog when the
 * LIVE result is partial/reduced/absent and the catalog entry is richer. Live is
 * preferred when full; the catalog only fills gaps (e.g. EB Pro / no source). A
 * catalog-served (or EB) response carries `source`; installed Elementor/core live
 * results stay byte-identical (no marker). The catalog is provisioned to
 * mu-plugins/sandbox-schema-catalog/<builder>.json.gz.                            */

/** 730 hand-curated Elementor control descriptions (common `_`-prefixed controls
 *  + `eael_` Essential Addons controls + widget-content controls), provisioned to
 *  mu-plugins/sandbox-schema-catalog/control-descriptions.json alongside the
 *  catalog gz files. Was already being read by the host-side catalog-generation
 *  tooling and merged into catalog(source:"catalog") responses -- but never
 *  wired into THIS live path at all, so the far more common case (a widget that
 *  IS registered on this instance) got none of them. Cached; returns [] if the
 *  file isn't provisioned (older instance, catalog feature predates this file). */
function sandbox_editor_el_control_descriptions(): array
{
    static $cache = null;
    if ($cache === null) {
        $f = WPMU_PLUGIN_DIR . '/sandbox-schema-catalog/control-descriptions.json';
        $json = is_file($f) ? @file_get_contents($f) : false;
        $cache = ($json !== false) ? (json_decode($json, true) ?: []) : [];
    }
    return $cache;
}

/** Extract rich metadata from one Elementor control definition (spec 012 ext).
 *  Returns: type, label, default, section, tab, selectors (css map), options (key→label). */
function sandbox_editor_el_control_entry(string $cid, array $c): array
{
    $e = [
        'type'    => $c['type'] ?? null,
        'label'   => isset($c['label']) ? (string) $c['label'] : null,
        'default' => $c['default'] ?? null,
        'section' => $c['section'] ?? null,
        'tab'     => $c['tab'] ?? null,
    ];
    if (!empty($c['description'])) {
        // Rare (most controls' own definition has none) but real "how to use it"
        // text when Elementor itself provides one.
        $e['description'] = (string) $c['description'];
    } else {
        $curated = sandbox_editor_el_control_descriptions()[$cid] ?? null;
        if ($curated) { $e['description'] = (string) $curated; }
    }
    // Responsive: Elementor keeps ONE base key + an is_responsive flag; the per-device
    // keys ({key}_tablet/{key}_mobile) are derived, never listed by get_controls().
    if (!empty($c['is_responsive'])) { $e['responsive'] = true; }
    if (!empty($c['selectors']) && is_array($c['selectors'])) {
        $sel = [];
        foreach ($c['selectors'] as $selector => $css) {
            if (is_string($selector) && is_string($css)) { $sel[$selector] = $css; }
        }
        if ($sel) { $e['selectors'] = $sel; }
    }
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
            if (count($opts) >= 30) { break; }  // skip icon/font packs (100+ items)
        }
        if ($opts) { $e['options'] = $opts; }
    }
    return $e;
}

function sandbox_editor_catalog_entry($builder, $name)
{
    static $cache = [];
    if (!array_key_exists($builder, $cache)) {
        $f = WPMU_PLUGIN_DIR . '/sandbox-schema-catalog/' . $builder . '.json.gz';
        $cache[$builder] = ['_format' => 'v1', '_pool' => [], '_entries' => []];
        if (is_file($f)) {
            $raw  = @file_get_contents($f);
            $json = ($raw !== false && function_exists('gzdecode')) ? @gzdecode($raw) : false;
            if ($json !== false) {
                $data = json_decode($json, true) ?: [];
                if (($data['_format'] ?? 'v1') === 'v2') {
                    $cache[$builder]['_format']  = 'v2';
                    $cache[$builder]['_pool']    = $data['_pool'] ?? [];
                    $cache[$builder]['_entries'] = array_diff_key($data, ['_format' => 1, '_pool' => 1]);
                } else {
                    $cache[$builder]['_entries'] = $data;
                }
            }
        }
    }

    $entry = $cache[$builder]['_entries'][$name] ?? null;
    if ($entry === null) { return null; }

    // v2 Elementor: resolve pool + overrides + own back to a flat controls dict.
    if ($cache[$builder]['_format'] === 'v2' && $builder === 'elementor') {
        $pool     = $cache[$builder]['_pool'];
        $controls = [];
        foreach ((array) ($entry['controls'] ?? []) as $id) {
            if (isset($pool[$id])) { $controls[$id] = $pool[$id]; }
        }
        foreach ((array) ($entry['overrides'] ?? []) as $id => $val) {
            $controls[$id] = $val;
        }
        foreach ((array) ($entry['own'] ?? []) as $id => $val) {
            $controls[$id] = $val;
        }
        return array_merge(
            array_diff_key($entry, ['controls' => 1, 'overrides' => 1, 'own' => 1]),
            ['controls' => $controls, 'count' => count($controls),
             'groups'   => sandbox_editor_group_controls($controls, $entry['content_ids'] ?? [])]
        );
    }

    return $entry;
}

/** All names present in the committed catalog for a builder (global search /
 *  listing needs to enumerate catalog-only entries too, e.g. an EB block not
 *  live-registered on this instance at all). Small deliberate duplication of
 *  sandbox_editor_catalog_entry()'s file-read (its cache is function-local
 *  static, not exposable) -- just enough to list names, not resolve entries. */
function sandbox_editor_catalog_all_names($builder): array
{
    static $cache = [];
    if (!array_key_exists($builder, $cache)) {
        $f = WPMU_PLUGIN_DIR . '/sandbox-schema-catalog/' . $builder . '.json.gz';
        $names = [];
        if (is_file($f)) {
            $raw  = @file_get_contents($f);
            $json = ($raw !== false && function_exists('gzdecode')) ? @gzdecode($raw) : false;
            if ($json !== false) {
                $data = json_decode($json, true) ?: [];
                $entries = (($data['_format'] ?? 'v1') === 'v2')
                    ? array_diff_key($data, ['_format' => 1, '_pool' => 1]) : $data;
                $names = array_keys($entries);
            }
        }
        $cache[$builder] = $names;
    }
    return $cache[$builder];
}

function sandbox_editor_plugin_version($slug)
{
    if (!$slug) { return null; }
    if (!function_exists('get_plugins')) { require_once ABSPATH . 'wp-admin/includes/plugin.php'; }
    foreach (get_plugins() as $file => $data) {
        if (strpos($file, $slug . '/') === 0) { return $data['Version'] ?? null; }
    }
    return null;
}

/**
 * Attribute names WordPress core auto-registers on ANY block once the
 * matching `supports` flag is on, or unconditionally for editor bookkeeping
 * (lock/metadata) — i.e. Gutenberg's rough equivalent of Elementor's shared
 * "common"/Advanced-tab controls. Not declared per-block by the block's own
 * author; identical across unrelated blocks. Data-driven, not guessed: found
 * by diffing `attributes` across 8 unrelated core + EB blocks
 * (core/heading, paragraph, image, button, group, columns, list,
 * essential-blocks/advanced-heading) and keeping names that recurred across
 * most/all of them (lock/metadata/className/style/anchor: 8/8; borderColor:
 * 7/8; backgroundColor/textColor/gradient/fontSize/fontFamily: 6/8;
 * align: 5/8) — the rest were block-specific (content, url, level, ...).
 */
function sandbox_editor_gb_common_attrs(): array
{
    return ['lock', 'metadata', 'className', 'style', 'anchor', 'align',
            'backgroundColor', 'textColor', 'gradient', 'fontSize', 'fontFamily',
            'borderColor'];
}

/** Split a flat Gutenberg attributes map into block-specific vs shared/common,
 *  mirroring sandbox_editor_group_controls()'s content/common split for
 *  Elementor. Unlike Elementor there's no naming-prefix signal (no `_`/`eael_`
 *  convention) to detect shared attrs programmatically, so this uses the
 *  static, data-driven list above instead of a heuristic. */
/**
 * Meanings (from sandbox_editor_gb_token_meanings()) that indicate a generic,
 * recurring STYLE property -- margin/padding/border/shadow/background/color/
 * typography/alignment/sizing -- as opposed to a block-specific CONTENT or
 * behavior word (icon, image, item, label are all real dictionary tokens but
 * are naming WHICH sub-element, not a style property, so deliberately
 * excluded even though recognized). Used to classify an EB attribute as
 * `common`/global (the same *kind* of setting recurs on nearly every block,
 * even though the literal id differs per block: "wrpMrg_Top" here vs
 * "containerMrg_Top" there) vs `content` (genuinely specific to this block).
 */
function sandbox_editor_gb_style_leaf_meanings(): array
{
    return array_flip([
        'Margin', 'Padding',
        'Border width', 'Border radius', 'Border', 'Border + shadow (group prefix)',
        'Hover border width', 'Hover border radius',
        'Shadow', 'Shadow spread', 'Horizontal shadow offset', 'Vertical shadow offset',
        'Blur', 'Filter blur', 'Inset shadow toggle', 'Shadow type (normal/inset)',
        'Shadow color', 'Shadow transition duration', 'Border transition duration', 'Transition duration',
        'Background', 'Background color', 'Background image', 'Background image position',
        'Background image repeat', 'Background size', 'Background type (solid/gradient)',
        'Enable background overlay toggle', 'Background overlay', 'Overlay color', 'Overlay (prefix)',
        'Custom background position',
        'Opacity', 'Opacity transition duration',
        'Gradient', 'Gradient color',
        'Color', 'Text color',
        'Font', 'Font family', 'Font size', 'Font weight', 'Font style',
        'Typography', 'Typography letter spacing', 'Typography line height', 'Letter spacing', 'Line height',
        'Alignment', 'Vertical alignment', 'Vertical', 'Horizontal', 'Justify content',
        'Width', 'Width value', 'Width auto-mode toggle', 'Height', 'Height value', 'Height unit', 'Size',
        'Linked/uniform sides toggle', 'Unit', 'Value',
        'CSS filter', 'Enable CSS filters toggle',
    ]);
}

function sandbox_editor_gb_group_attrs(array $attrs): array
{
    $common_names = array_flip(sandbox_editor_gb_common_attrs());
    $leafMeanings = sandbox_editor_gb_style_leaf_meanings();
    // Modifier tokens (side/unit/linked-toggle/bare-value) describe HOW a
    // property is expressed, not WHAT it is -- e.g. "Bdr_Top" and "Bdr_Unit"
    // are the SAME border-width property, just different sub-fields of it.
    // Skip past these from the end to find the actual core-property token
    // (verified bug: checking only the literal last token classified
    // "...Bdr_Top" as content but "...Bdr_Unit" as common for the SAME
    // control group, since "Top side"/"Unit" themselves aren't style-leaf
    // meanings — only what precedes them is).
    static $modifiers = ['Top side' => 1, 'Bottom side' => 1, 'Left side' => 1, 'Right side' => 1,
                         'Unit' => 1, 'Linked/uniform sides toggle' => 1, 'Value' => 1];
    $content = [];
    $common  = [];
    foreach ($attrs as $k => $def) {
        $isCommon = isset($common_names[$k]);
        // EB attributes (post-enrich_attrs) carry a `decoded` breakdown -- core
        // block attributes don't, so this never fires for them (falls through
        // to the static-name check above, unchanged behavior).
        if (!$isCommon && is_array($def) && !empty($def['decoded']['decoded'])) {
            // A boolean "show/enable/display X" attribute is a FEATURE TOGGLE
            // (a content/behavior decision -- use this feature or don't), never
            // itself a style value, no matter what style-sounding word its id
            // ends in. Verified bug: "showIconBackground" (type boolean,
            // default true) decoded to a trailing "Background" token and was
            // misclassified common/style -- it's whether the icon HAS a
            // background at all, not a background color/image value.
            $isToggleName = (bool) preg_match('/^(show|enable|display)/i', $k);
            $isBool = (($def['type'] ?? null) === 'boolean');
            if (!($isToggleName && $isBool)) {
                $segs = array_reverse($def['decoded']['decoded']);
                foreach ($segs as $seg) {
                    $meaning = $seg['meaning'] ?? null;
                    if ($meaning === null || isset($modifiers[$meaning])) { continue; } // skip modifiers/unresolved
                    if (isset($leafMeanings[$meaning])) { $isCommon = true; }
                    break; // first non-modifier token found, decided either way
                }
            }
        }
        if ($isCommon) {
            $common[$k] = $def;
        } else {
            $content[$k] = $def;
        }
    }
    return ['content' => $content, 'common' => $common];
}

/**
 * Gutenberg's OTHER style surface, not visible in `attributes` at all: the
 * `supports` flags a block declares (color/spacing/typography/border/shadow/
 * dimensions/position) don't add named attributes — they enable the generic
 * `style` attribute (an opaque JSON object with no schema of its own) to
 * accept specific sub-paths, applied by core's block-supports renderers.
 * Elementor's equivalent is a control's own `selectors` map (which literal
 * CSS to write); this is Gutenberg's, one level removed — which literal
 * `style.*` JSON path to write for `gutenberg-update`'s `attributes.style`.
 *
 * Every path below is READ DIRECTLY off WP core's own block-supports source
 * (wp-includes/block-supports/{spacing,colors,typography,border,shadow,
 * dimensions,position}.php), not guessed, e.g. spacing.php:
 * `$block_styles['spacing']['padding'] ?? null` -> style.spacing.padding.
 *
 * A path only applies if the block's OWN `supports` has the matching flag
 * (not false) — sandbox_editor_gb_style_paths() below filters to just the
 * ones this specific block actually has.
 */
function sandbox_editor_gb_style_path_map(): array
{
    return [
        'spacing.padding'              => 'style.spacing.padding',
        'spacing.margin'               => 'style.spacing.margin',
        'spacing.blockGap'             => 'style.spacing.blockGap',
        'color.text'                   => 'style.color.text',
        'color.background'             => 'style.color.background',
        'color.gradients'              => 'style.color.gradient',
        'typography.fontSize'          => 'style.typography.fontSize (custom values only — a chosen PRESET writes the top-level `fontSize` attribute slug instead)',
        'typography.__experimentalFontFamily' => 'style.typography.fontFamily',
        'typography.__experimentalFontStyle'  => 'style.typography.fontStyle',
        'typography.__experimentalFontWeight' => 'style.typography.fontWeight',
        'typography.lineHeight'        => 'style.typography.lineHeight',
        'typography.textAlign'         => 'style.typography.textAlign',
        'typography.__experimentalTextDecoration' => 'style.typography.textDecoration',
        'typography.__experimentalTextTransform'  => 'style.typography.textTransform',
        'typography.__experimentalLetterSpacing'  => 'style.typography.letterSpacing',
        'typography.__experimentalWritingMode'    => 'style.typography.writingMode',
        'typography.textColumns'       => 'style.typography.textColumns',
        'typography.textIndent'        => 'style.typography.textIndent',
        '__experimentalBorder.color'   => 'style.border.color',
        '__experimentalBorder.width'   => 'style.border.width',
        '__experimentalBorder.style'   => 'style.border.style',
        '__experimentalBorder.radius'  => 'style.border.radius',
        'shadow'                       => 'style.shadow',
        'dimensions.aspectRatio'       => 'style.dimensions.aspectRatio',
        'dimensions.minHeight'         => 'style.dimensions.minHeight',
        'position'                     => 'style.position',
    ];
}

/** Intersect a block's own `supports` with the verified map above -> only the
 *  style.* paths THIS block actually accepts. `$supports[$a][$b]` truthy (not
 *  literal false) enables it; a couple of keys (shadow, position) are flags
 *  directly on `supports`, not nested under a feature group. */
function sandbox_editor_gb_style_paths($supports): array
{
    if (!is_array($supports)) { return []; }
    $out = [];
    foreach (sandbox_editor_gb_style_path_map() as $flag => $path) {
        if (strpos($flag, '.') === false) {
            if (!empty($supports[$flag])) { $out[$flag] = $path; }
            continue;
        }
        [$group, $sub] = explode('.', $flag, 2);
        $group_val = $supports[$group] ?? false;
        // color.text/color.background are the one pair WP core defaults to ENABLED
        // when the parent 'color' support exists at all and doesn't explicitly say
        // otherwise — verified straight from wp-includes/block-supports/colors.php:
        // `true === $color_support || (isset($color_support['text']) && ...) ||
        // (is_array($color_support) && !isset($color_support['text']))`. Every other
        // flag in the map defaults to disabled when the sub-key is simply absent.
        if (($flag === 'color.text' || $flag === 'color.background')
            && (true === $group_val || (is_array($group_val) && !array_key_exists($sub, $group_val)))) {
            $out[$flag] = $path;
            continue;
        }
        $val = is_array($group_val) ? ($group_val[$sub] ?? null) : null;
        if ($val !== null && $val !== false) { $out[$flag] = $path; }
    }
    return $out;
}

/** Block-level metadata missing from the flat attribute dump entirely:
 *  title/description (human-facing, like Elementor's widget label) and
 *  supports + the style_paths derived from it (non-block.json style attrs,
 *  see sandbox_editor_gb_style_paths()). Only available when $bt is a live
 *  WP_Block_Type — catalog-only entries don't carry these yet (the catalog
 *  dump captures `attributes`+`supports` per block, but the `sb
 *  schema-catalog generate` pipeline currently drops both title/description
 *  and supports when writing the committed catalog; a known gap, not fixed
 *  here — would need a catalog regen to take effect). */
/** 92 real Essential Blocks {title, description} pairs extracted directly
 *  from block.json in the free+pro plugin SOURCE (not generated). Needed
 *  because: (a) EB Pro's free-plugin stub registration (active when Pro
 *  isn't installed) carries NEITHER title NOR description (verified live:
 *  essential-blocks/pro-business-hours' $bt->title AND $bt->description are
 *  both empty even though it IS live-registered), and (b) the packaged/
 *  distributed Pro plugin (unlike a git checkout) ships no `src/` to read
 *  either from live regardless -- confirmed by checking is_dir() from
 *  inside the WP runtime itself. Provisioned alongside
 *  control-descriptions.json. Cached; [] if not provisioned. */
function sandbox_editor_gb_block_descriptions(): array
{
    static $cache = null;
    if ($cache === null) {
        $f = WPMU_PLUGIN_DIR . '/sandbox-schema-catalog/block-descriptions.json';
        $json = is_file($f) ? @file_get_contents($f) : false;
        $cache = ($json !== false) ? (json_decode($json, true) ?: []) : [];
    }
    return $cache;
}

function sandbox_editor_gb_meta($bt): array
{
    if (!$bt) { return []; }
    $supports = (array) ($bt->supports ?? []);
    $curated  = sandbox_editor_gb_block_descriptions()[$bt->name ?? ''] ?? [];
    $title       = $bt->title ?? null;
    $description = $bt->description ?? null;
    if (!$title)       { $title       = $curated['title'] ?? null; }
    if (!$description) { $description = $curated['description'] ?? null; }
    return [
        'title'       => $title,
        'description' => $description,
        'supports'    => $supports,
        'style_paths' => sandbox_editor_gb_style_paths($supports),
    ];
}

/**
 * Human search term -> alternate/near-synonym words for finding a BLOCK/
 * WIDGET by purpose (`find`, title+description/keywords), as opposed to
 * sandbox_editor_gb_search_synonyms() which is for finding an ATTRIBUTE by
 * name within one already-known Gutenberg block (`search`, EB abbreviation
 * decoding specifically). This one is plain English, shared by both
 * builders. Derived by reading the real title list across all 92 EB blocks
 * (Data Table, Post Grid, Pricing Table, Testimonial Slider, Advanced
 * Search, Business Hours, ...) and noting where a natural search word
 * wouldn't literally appear in the matching block's own title/description
 * (e.g. "table" wouldn't literally match "Post Grid", but a grid IS a kind
 * of tabular layout someone searching "table" might mean).
 */
function sandbox_editor_find_synonyms(): array
{
    return [
        'table' => ['grid'], 'grid' => ['table', 'gallery'],
        'slider' => ['carousel'], 'carousel' => ['slider'],
        'gallery' => ['grid', 'images', 'photos'], 'images' => ['gallery', 'photo'], 'photos' => ['gallery', 'image'],
        'form' => ['field', 'input', 'contact'], 'field' => ['form', 'input'],
        'pricing' => ['price', 'plans', 'plan'], 'price' => ['pricing'], 'plans' => ['pricing'],
        'testimonial' => ['review', 'rating', 'feedback'], 'review' => ['testimonial', 'rating'],
        'rating' => ['review', 'testimonial'],
        'menu' => ['navigation', 'nav'], 'navigation' => ['menu', 'nav'], 'nav' => ['menu', 'navigation'],
        'accordion' => ['toggle', 'collapse', 'faq'], 'toggle' => ['accordion'], 'faq' => ['accordion', 'toggle'],
        'tabs' => ['tab'], 'tab' => ['tabs'],
        'countdown' => ['timer'], 'timer' => ['countdown'],
        'social' => ['share', 'icons'],
        'video' => ['animation', 'media'],
        'map' => ['location', 'address'], 'location' => ['map'],
        'icon' => ['symbol'],
        'list' => ['contents', 'timeline'],
        'progress' => ['chart', 'bar', 'stats'], 'chart' => ['progress', 'graph'], 'stats' => ['progress', 'counter'],
        'hours' => ['schedule', 'time', 'business hours'], 'schedule' => ['hours', 'time'],
        'search' => ['find'],
        'popup' => ['modal', 'offcanvas', 'dialog'], 'modal' => ['popup'],
        'banner' => ['cta', 'promo', 'hero'], 'cta' => ['banner', 'call to action'], 'hero' => ['banner', 'promo'],
        'counter' => ['number', 'stats'], 'number' => ['counter'],
        'team' => ['staff', 'member'], 'staff' => ['team'], 'member' => ['team'],
        'shop' => ['woo', 'woocommerce', 'product', 'store'], 'product' => ['woo', 'shop'], 'store' => ['shop', 'woo'],
        'layout' => ['container', 'wrapper', 'row', 'column'], 'container' => ['wrapper', 'layout'],
        'wrapper' => ['container'],
        'text' => ['typography', 'heading'], 'typography' => ['text', 'heading'],
        'news' => ['ticker'], 'ticker' => ['news'],
        'loop' => ['query', 'dynamic'], 'query' => ['loop'],
        'divider' => ['separator', 'shape'], 'separator' => ['divider'],
        'notice' => ['alert', 'message'], 'alert' => ['notice'],
        'card' => ['flip', 'stacked'], 'flip' => ['card'],
        'category' => ['taxonomy', 'tag'], 'taxonomy' => ['category', 'tag'],
        'comparison' => ['before after', 'compare'], 'compare' => ['comparison'],
        'hotspot' => ['point', 'marker'],
        'captcha' => ['recaptcha'],
    ];
}

/** Full attribute definition, not just type/default — passes through every
 *  key WP/the block author declared (enum, source, selector, attribute,
 *  query, items, role, ...). `source`/`selector`/`attribute` are Gutenberg's
 *  "how to use it" info: they say which literal saved-markup element/HTML
 *  attribute this reads from — Elementor's rough equivalent of `selectors`.
 *  `enum` is the valid-values list, Elementor's equivalent of `options`. */
function sandbox_editor_gb_attrs(array $raw_attrs): array
{
    $attrs = [];
    foreach ($raw_attrs as $k => $def) {
        $attrs[$k] = is_array($def) ? $def : ['type' => $def];
    }
    return $attrs;
}

/**
 * Human search term -> literal abbreviation TOKENS used in Essential Blocks'
 * own attribute names, Gutenberg's equivalent of sandbox_editor_search_synonyms()
 * (Elementor's control-id synonym map). Unlike Elementor, EB attribute names have
 * no separate human-readable `label` field and no `_`/section-based naming
 * convention to lean on — they're one long abbreviated camelCase string (e.g.
 * `wrpMrg_isLinked`, `MOBclGp_Range`, `TABbtnBDRTop`) with NO literal substring
 * overlap with the words a person would actually search for ("gap" is not a
 * substring of "clGp"; "margin" is not a substring of "wrpMrg"). Naive substring
 * search silently fails on exactly the attributes it's most needed for.
 *
 * This dictionary is DATA-DRIVEN, not guessed: reverse-engineered by 10 parallel
 * agents each reading a ~1056-name slice of the full 10,557 unique attribute
 * names in the committed Gutenberg schema catalog (essential-blocks/* entries
 * only), each entry backed by real observed attribute names (not invented).
 * Tokens are case-sensitive on purpose — EB itself is inconsistent about it
 * (`Bdr` vs `BDR` vs `Brd` all mean border-width in different blocks) so the
 * matcher below does case-INsensitive substring matching against these tokens;
 * the tokens are kept mixed-case here only for readability/provenance.
 */
/**
 * Verified token -> short human meaning, for decomposing an abbreviated
 * Gutenberg/EB attribute id into its real parts (e.g. "wrpMrg_Top" -> wrp
 * "Wrapper (outer container)" + Mrg "Margin" + Top "Top side"). Every entry
 * was confirmed against the REAL Essential Blocks source by dedicated
 * agents — NOT guessed from the abbreviation shape. Each agent greped the
 * source for real attribute names containing the token, then found the
 * actual UI control's `label=`/`label:` string (an i18n `__('...',
 * 'essential-blocks')` call, or a code comment) physically next to where
 * that attribute is read/written. A token with no such evidence was left
 * out entirely rather than assigned a guessed meaning (e.g. bare "H",
 * "FontSource", "table", "overlayType", "radiusTransition" — no confirming
 * label/comment was found for these, so they decode as unresolved).
 *
 * Sorted longest-token-first so sandbox_editor_gb_decode_attr()'s greedy
 * tokenizer prefers "BackgroundColor" over "Background" over "Color" when
 * all three could start matching at the same position.
 */
function sandbox_editor_gb_token_meanings(): array
{
    return [
        'TypoLetterSpacing' => 'Typography letter spacing',
        'filtersTransition' => 'Filter transition duration',
        'opacityTransition' => 'Opacity transition duration',
        'borderTransition' => 'Border transition duration',
        'shadowTransition' => 'Shadow transition duration',
        'BackgroundColor' => 'Background color',
        'backgroundColor' => 'Background color',
        'TypoLineHeight' => 'Typography line height',
        'backgroundSize' => 'Background size',
        'backgroundType' => 'Background type (solid/gradient)',
        'LetterSpacing' => 'Letter spacing',
        'gradientColor' => 'Gradient color',
        'BorderShadow' => 'Border + shadow',
        'allowFilters' => 'Enable CSS filters toggle',
        'overlayColor' => 'Overlay color',
        'HeightRange' => 'Height value',
        'WidthIsAuto' => 'Width auto-mode toggle',
        'bgImgRepeat' => 'Background image repeat',
        'borderColor' => 'Border color',
        'borderStyle' => 'Border style',
        'hoverSpread' => 'Hover shadow spread',
        'isBgOverlay' => 'Enable background overlay toggle',
        'shadowColor' => 'Shadow color',
        'Background' => 'Background',
        'FontFamily' => 'Font family',
        'FontWeight' => 'Font weight',
        'HeightUnit' => 'Height unit',
        'Horizontal' => 'Horizontal',
        'LineHeight' => 'Line height',
        'Transition' => 'Transition duration',
        'Typography' => 'Typography',
        'WidthRange' => 'Width value',
        'background' => 'Background',
        'borderType' => 'Border style',
        'shadowType' => 'Shadow type (normal/inset)',
        'transition' => 'Transition duration',
        'Alignment' => 'Alignment',
        'Direction' => 'Layout direction',
        'FontStyle' => 'Font style',
        'TextColor' => 'Text color',
        'customPos' => 'Custom background position',
        'hoverBlur' => 'Hover shadow blur',
        'FontSize' => 'Font size',
        'GapRange' => 'Gap value',
        'Gradient' => 'Gradient',
        'Position' => 'Position',
        'Vertical' => 'Vertical',
        'bgImgPos' => 'Background image position',
        'fltrBlur' => 'Filter blur',
        'gradient' => 'Gradient',
        'isLinked' => 'Linked/uniform sides toggle',
        'BgColor' => 'Background color',
        'Filters' => 'Content filters',
        'GapUnit' => 'Gap unit',
        'Justify' => 'Justify content',
        'Opacity' => 'Opacity',
        'Overlay' => 'Background overlay',
        'Padding' => 'Padding',
        'Spacing' => 'Spacing',
        'VOffset' => 'Vertical shadow offset',
        'bgImage' => 'Background image',
        'hOffset' => 'Horizontal shadow offset',
        'opacity' => 'Opacity',
        'overlay' => 'Background overlay',
        'vOffset' => 'Vertical shadow offset',
        'wrapper' => 'Wrapper (outer container)',
        'Active' => 'Active state',
        'Border' => 'Border',
        'Bottom' => 'Bottom side',
        'BrdShd' => 'Border + shadow',
        'Column' => 'Column',
        'Height' => 'Height',
        'Margin' => 'Margin',
        'Offset' => 'Shadow offset',
        'Radius' => 'Border radius',
        'Shadow' => 'Shadow',
        'Spread' => 'Shadow spread',
        'VAlign' => 'Vertical alignment',
        'height' => 'Height',
        'shadow' => 'Shadow',
        'spread' => 'Shadow spread',
        'Align' => 'Alignment',
        'Color' => 'Color',
        'Hover' => 'Hover state',
        'Image' => 'Image',
        'Inset' => 'Inset shadow toggle',
        'Media' => 'Media',
        'Range' => 'Value',
        'Right' => 'Right side',
        'Space' => 'Spacing',
        'Width' => 'Width',
        'bgImg' => 'Background image',
        'hover' => 'Hover state',
        'image' => 'Image',
        'inset' => 'Inset shadow toggle',
        'label' => 'Label',
        'width' => 'Width',
        'Blur' => 'Blur',
        'Font' => 'Font',
        'HBdr' => 'Hover border width',
        'HRds' => 'Hover border radius',
        'Icon' => 'Icon',
        'Left' => 'Left side',
        'Size' => 'Size',
        'Text' => 'Text',
        'Typo' => 'Typography',
        'Unit' => 'Unit',
        'Wrap' => 'Wrapper (outer container)',
        'blur' => 'Blur',
        'fltr' => 'CSS filter',
        'hov_' => 'Hover-state variant (prefix)',
        'icon' => 'Icon',
        'item' => 'Item',
        'ovl_' => 'Overlay (prefix)',
        'Bdr' => 'Border width',
        'BDR' => 'Border width',
        'Brd' => 'Border',
        'Btn' => 'Button',
        'Gap' => 'Gap',
        'Img' => 'Image',
        'MOB' => 'Mobile (responsive)',
        'Mrg' => 'Margin',
        'Pad' => 'Padding',
        'Rds' => 'Border radius',
        'Shd' => 'Shadow',
        'TAB' => 'Tablet (responsive)',
        'Top' => 'Top side',
        'Wrp' => 'Wrapper (outer container)',
        'icn' => 'Icon',
        'img' => 'Image',
        'mob' => 'Mobile (responsive)',
        'ovl' => 'Overlay (prefix)',
        'row' => 'Row (flex direction)',
        'wrp' => 'Wrapper (outer container)',
        'BG' => 'Background',
        'Bg' => 'Background color',
        'Gp' => 'Gap',
        'Hv' => 'Hover state',
        'bg' => 'Background',
        'hv' => 'Hover state',
    ];
}

/**
 * Split an id into its device/state prefix layer + the bare remainder,
 * matching the stacking order verified empirically across the whole
 * catalog (99.9-100% of MOB/TAB/hov_-prefixed attributes have a matching
 * un-prefixed counterpart in the same block): hover wraps outermost, then
 * MOB/TAB, e.g. "hov_MOBwrpMrg_Top" -> hover=true, responsive=mobile,
 * base="wrpMrg_Top".
 */
function sandbox_editor_gb_strip_variant_prefixes(string $id): array
{
    $hover = false;
    $responsive = null;
    $rest = $id;
    if (strncmp($rest, 'hov_', 4) === 0) { $hover = true; $rest = substr($rest, 4); }
    if (strncmp($rest, 'MOB', 3) === 0) { $responsive = 'mobile'; $rest = substr($rest, 3); }
    elseif (strncmp($rest, 'TAB', 3) === 0) { $responsive = 'tablet'; $rest = substr($rest, 3); }
    return ['hover' => $hover, 'responsive' => $responsive, 'base' => $rest];
}

/**
 * Decompose one attribute id into {responsive, hover, decoded[], unresolved}
 * — the "glue adapter": mechanical tokenization + verified-dictionary
 * lookup only, NO composed sentence. The calling agent decides how (or
 * whether) to phrase the parts into prose; a synthesized template risks
 * silently misreading an unusual token order (the same class of mistake as
 * the earlier case-sensitivity bug in search) — returning verified facts
 * instead of a guessed sentence avoids that.
 *
 * Greedy longest-match against sandbox_editor_gb_token_meanings() (already
 * sorted longest-first); a bare `_` is a pure separator, skipped silently;
 * any other unmatched character run is reported in `unresolved`, never
 * silently dropped or guessed.
 */
function sandbox_editor_gb_decode_attr(string $id): array
{
    $prefix = sandbox_editor_gb_strip_variant_prefixes($id);
    $rest   = $prefix['base'];
    $dict   = sandbox_editor_gb_token_meanings();

    $decoded = [];
    $unresolved = '';
    $i = 0;
    $len = strlen($rest);
    while ($i < $len) {
        if ($rest[$i] === '_') { $i++; continue; } // pure separator, not content
        $matched = false;
        foreach ($dict as $tok => $meaning) {
            $tlen = strlen($tok);
            if ($tlen > 0 && substr($rest, $i, $tlen) === $tok) { // case-sensitive on purpose
                if ($unresolved !== '') {
                    $decoded[] = ['token' => $unresolved, 'meaning' => null];
                    $unresolved = '';
                }
                $decoded[] = ['token' => $tok, 'meaning' => $meaning];
                $i += $tlen;
                $matched = true;
                break;
            }
        }
        if (!$matched) { $unresolved .= $rest[$i]; $i++; }
    }
    if ($unresolved !== '') { $decoded[] = ['token' => $unresolved, 'meaning' => null]; }

    // Disambiguate "Bdr"/"BDR"/"Brd": genuinely means per-side border WIDTH
    // when followed by a side/unit token (Top/Bottom/Left/Right/Unit/isLinked
    // -- verified: generateBorderShadowAttributes() feeds `${controlName}Bdr_`
    // into generateDimensionsAttributes() with per-side defaults). But when a
    // block author names its WHOLE border+shadow control group "xxxBdr_" (a
    // single arbitrary identifier, e.g. essential-blocks-pro/data-table's own
    // `WRAPPER_BORDER_SHADOW = "wrpBdr_"`), every shadow sub-property
    // generated under that same prefix (spread/blur/hOffset/vOffset/inset/
    // shadowColor/shadowType/shadowTransition) inherits "Bdr_" too -- reading
    // it as "Border width" there produces a nonsense juxtaposition ("Border
    // width: Shadow spread"). Verified via source, not guessed: a real
    // false-positive juxtaposition was reported on wrpBdr_spread/wrpBdr_
    // vOffset and traced to exactly this. Relabel only in that specific case.
    static $sideTokens   = ['Top' => 1, 'Bottom' => 1, 'Left' => 1, 'Right' => 1, 'Unit' => 1, 'isLinked' => 1];
    static $shadowTokens = ['spread' => 1, 'blur' => 1, 'hOffset' => 1, 'vOffset' => 1, 'inset' => 1,
                            'shadowColor' => 1, 'shadowType' => 1, 'shadowTransition' => 1,
                            'VOffset' => 1, 'HOffset' => 1, 'Spread' => 1, 'Blur' => 1, 'Inset' => 1];
    static $skipOver     = ['hover' => 1, 'Hover' => 1, 'hov_' => 1, 'Hv' => 1, 'hv' => 1]; // e.g. Bdr_hoverVOffset
    foreach ($decoded as $idx => $seg) {
        if (!in_array($seg['token'], ['Bdr', 'BDR', 'Brd'], true)) { continue; }
        $j = $idx + 1;
        while (isset($decoded[$j]['token']) && isset($skipOver[$decoded[$j]['token']])) { $j++; }
        $next = $decoded[$j]['token'] ?? null;
        if ($next !== null && isset($shadowTokens[$next]) && !isset($sideTokens[$next])) {
            $decoded[$idx]['meaning'] = 'Border + shadow (group prefix)';
        }
    }

    return [
        'responsive' => $prefix['responsive'],
        'hover'      => $prefix['hover'],
        'decoded'    => $decoded,
    ];
}

/**
 * Hide MOB/TAB/hov_-prefixed variant attributes from the default view,
 * attaching them as `responsive`/`hover` pointers on their verified base
 * attribute instead (their existence and exact key name are STILL fully
 * discoverable — nothing is deleted, just decluttered: this roughly halves
 * the visible attribute count on every EB block, verified across the whole
 * catalog: 18,857 -> 10,236 total occurrences, 10,557 -> 5,761 unique
 * names). A variant with no matching base (~0.1% of cases, verified) stays
 * visible and is flagged `orphaned_variant`, never silently dropped.
 * `$include_variants=true` skips all of this and returns the raw list.
 *
 * Every SURVIVING attribute also gets a `decoded` breakdown attached (see
 * sandbox_editor_gb_decode_attr()).
 */
function sandbox_editor_gb_enrich_attrs(array $attrs, bool $include_variants = false): array
{
    if ($include_variants) {
        $out = [];
        foreach ($attrs as $id => $def) {
            $out[$id] = (is_array($def) ? $def : ['type' => $def]) + ['decoded' => sandbox_editor_gb_decode_attr($id)];
        }
        return $out;
    }

    $idSet = array_flip(array_keys($attrs));
    $out = [];
    foreach ($attrs as $id => $def) {
        $def = is_array($def) ? $def : ['type' => $def];
        $isVariant = strncmp($id, 'hov_', 4) === 0 || strncmp($id, 'MOB', 3) === 0 || strncmp($id, 'TAB', 3) === 0;
        if ($isVariant) {
            $base = sandbox_editor_gb_strip_variant_prefixes($id)['base'];
            if (isset($idSet[$base])) { continue; } // attached to $base's entry below
            $out[$id] = $def + ['decoded' => sandbox_editor_gb_decode_attr($id), 'orphaned_variant' => true];
            continue;
        }

        $entry = $def + ['decoded' => sandbox_editor_gb_decode_attr($id)];
        $mobKey = 'MOB' . $id; $tabKey = 'TAB' . $id; $hovKey = 'hov_' . $id;
        $responsive = array_filter([
            'mobile' => isset($idSet[$mobKey]) ? $mobKey : null,
            'tablet' => isset($idSet[$tabKey]) ? $tabKey : null,
        ]);
        if ($responsive) { $entry['responsive'] = $responsive; }
        if (isset($idSet[$hovKey])) { $entry['hover'] = $hovKey; }
        // nested hover+responsive (e.g. hov_MOBwrpMrg_Top) -- verified 100%
        // match rate when hov_MOB/hov_TAB exist at all, so safe to attach
        // directly on the root base entry alongside the plain ones above.
        $hovResponsive = array_filter([
            'mobile' => isset($idSet['hov_' . $mobKey]) ? 'hov_' . $mobKey : null,
            'tablet' => isset($idSet['hov_' . $tabKey]) ? 'hov_' . $tabKey : null,
        ]);
        if ($hovResponsive) { $entry['hover_responsive'] = $hovResponsive; }
        $out[$id] = $entry;
    }
    return $out;
}

/**
 * Trim a computed {attributes, groups} response so the DEFAULT payload for a
 * large EB block isn't dominated by hundreds of recurring global/style
 * attributes (measured: pro-business-hours' 918 attributes are 715
 * common/global -- 78% of the total). `groups.common` becomes just names +
 * a count instead of full definitions; the top-level `attributes` map is
 * reduced to `groups.content` only (this block's own settings, which is
 * what a caller almost always actually wants first). `$full=true` skips all
 * of this and returns everything unchanged -- the escape hatch.
 *
 * The `tip` field is written to be self-sufficient: an agent that has NEVER
 * read the docs, seeing only this one JSON response, can act on it directly
 * (per instruction -- this same explanation is also in editor-schema-api.md).
 */
function sandbox_editor_gb_trim_response(array $resp, bool $full): array
{
    if ($full || empty($resp['groups'])) { return $resp; }
    $groups = $resp['groups'];
    $commonCount = count($groups['common'] ?? []);
    // `groups.common` is dropped entirely by default (not even names) -- a bare
    // id like "wrpMrg_Top" has zero standalone value without its `decoded`
    // breakdown (the whole reason decoded exists), so a names-only list was
    // decoration, not information. `groups.content` is also dropped: it's the
    // exact same data as `attributes` (which is already content-only by
    // default), so keeping both was pure duplication for no reason -- just
    // `attributes` + a count + a tip.
    $resp['attributes'] = $groups['content'] ?? [];
    unset($resp['groups']);
    $resp['global_attributes_count'] = $commonCount;
    if ($commonCount > 0) {
        $resp['tip'] = "$commonCount recurring global/style attributes (background, border, shadow, "
            . "spacing, typography, alignment, sizing, etc. -- the same kinds of settings found on "
            . "nearly every block, just under a different literal name here) exist but aren't shown -- "
            . "their bare ids have no standalone meaning without a decoded breakdown, so listing just "
            . "names would be noise, not information. Pass full=1 on this same request (same "
            . "builder+name) to get all of them, each with a full definition including `decoded`.";
    }
    return $resp;
}

function sandbox_editor_gb_search_synonyms(): array
{
    return [
        'align' => ['Align'],
        'alignment' => ['Align', 'Alignment', 'Direction', 'Justify', 'VAlign'],
        'background' => ['BG', 'Background', 'Bg', 'BgColor', 'background', 'backgroundColor', 'backgroundSize', 'backgroundType', 'bg', 'bgImage', 'bgImg', 'overlay'],
        'background color' => ['BackgroundColor', 'BgColor', 'backgroundColor'],
        'background image' => ['bgImg'],
        'background position' => ['bgImgPos', 'customPos'],
        'background repeat' => ['bgImgRepeat'],
        'blur' => ['Blur', 'blur', 'fltrBlur'],
        'border' => ['BDR', 'Bdr', 'Border', 'Brd', 'borderColor', 'borderStyle', 'borderType'],
        'border radius' => ['Radius', 'Rds'],
        'border style' => ['BDR', 'Bdr'],
        'border width' => ['Bdr'],
        'bottom' => ['Bottom'],
        'box shadow' => ['BorderShadow'],
        'button' => ['Btn'],
        'color' => ['BgColor', 'Color', 'HColor', 'TextColor'],
        'column' => ['Column'],
        'corner' => ['Radius', 'Rds'],
        'filter' => ['Filters', 'allowFilters', 'filtersTransition', 'fltr'],
        'font' => ['Font', 'FontFamily', 'FontSize', 'FontSource', 'FontStyle', 'FontWeight', 'Typo', 'Typography'],
        'font size' => ['FontSize'],
        'gap' => ['Gap', 'GapIsAuto', 'GapRange', 'GapUnit', 'Gp'],
        'gradient' => ['Gradient', 'gradient', 'gradientColor'],
        'height' => ['Height', 'HeightRange', 'HeightUnit', 'height'],
        'horizontal' => ['Horizontal'],
        'hover' => ['Active', 'H', 'HBdr', 'HRds', 'Hover', 'Hv', 'hov_', 'hover', 'hoverBlur', 'hoverSpread', 'hv'],
        'icon' => ['Icon', 'icn', 'icon'],
        'image' => ['Image', 'Img', 'bgImg', 'image', 'img'],
        'inset' => ['Inset', 'inset'],
        'item' => ['item'],
        'label' => ['label'],
        'left' => ['Left'],
        'letter spacing' => ['LetterSpacing', 'TypoLetterSpacing'],
        'line height' => ['LineHeight', 'TypoLineHeight'],
        'linked' => ['isLinked'],
        'margin' => ['Margin', 'Mrg'],
        'media' => ['Media'],
        'mobile' => ['MOB', 'mob'],
        'offset' => ['HOffset', 'Offset', 'VOffset', 'hOffset', 'vOffset'],
        'opacity' => ['Opacity', 'opacity', 'opacityTransition'],
        'overlay' => ['Overlay', 'isBgOverlay', 'overlay', 'overlayColor', 'overlayType', 'ovl', 'ovl_'],
        'padding' => ['Pad', 'Padding'],
        'position' => ['Position'],
        'radius' => ['Radius', 'Rds'],
        'range' => ['Range'],
        'responsive' => ['TAB'],
        'right' => ['Right'],
        'rounded' => ['Radius', 'Rds'],
        'row' => ['row'],
        'shadow' => ['BorderShadow', 'BrdShd', 'Shadow', 'Shd', 'blur', 'hOffset', 'inset', 'shadow', 'shadowColor', 'shadowTransition', 'shadowType', 'spread', 'vOffset'],
        'size' => ['Size'],
        'spacing' => ['Gap', 'Margin', 'Mrg', 'Pad', 'Padding', 'Space', 'Spacing'],
        'spread' => ['Spread', 'spread'],
        'table' => ['table'],
        'tablet' => ['TAB'],
        'text' => ['Text'],
        'top' => ['Top'],
        'transition' => ['Transition', 'borderTransition', 'radiusTransition', 'shadowTransition', 'transition'],
        'typography' => ['FontFamily', 'FontSize', 'FontWeight', 'LineHeight', 'Typo', 'Typography'],
        'unit' => ['Unit'],
        'vertical' => ['Vertical'],
        'width' => ['Width', 'WidthIsAuto', 'WidthRange', 'WidthUnit', 'width'],
        'wrapper' => ['Wrap', 'Wrp', 'wrapper', 'wrp'],
    ];
}

/**
 * Ranked keyword search across ONE Gutenberg block's attributes — the
 * counterpart to sandbox_editor_search_controls() (Elementor). No `label`/
 * `section`/`tab` metadata exists for Gutenberg attributes, so fields are
 * necessarily thinner: the id itself (weight 100, the only field with real
 * signal), then the markup-mapping hints `source`/`selector`/`attribute`
 * (weight 20 — Gutenberg's rough equivalent of Elementor's `selectors`), then
 * `enum` values (weight 10). Token-AND across query words; each token also
 * expands through sandbox_editor_gb_search_synonyms() so "gap" matches the
 * literal `Gp`/`Gap` substrings EB actually uses. `common` (groups.common)
 * attributes are scored the same as `content` — unlike Elementor there's no
 * core-vs-extension noise problem to penalize for.
 */
function sandbox_editor_gb_search_attrs(array $attrs, string $query, array $groups): array
{
    $q = strtolower(trim($query));
    if ($q === '') { return []; }

    $index = [];
    foreach (($groups['content'] ?? []) as $id => $_) { $index[$id] = 'content'; }
    foreach (($groups['common'] ?? []) as $id => $_)  { $index[$id] = 'common'; }

    // Whole-phrase key (e.g. "border radius") takes priority as a single unit over
    // treating it as two independent tokens.
    $syn    = sandbox_editor_gb_search_synonyms();
    $tokens = isset($syn[$q]) ? [$q] : array_values(array_filter(preg_split('/\s+/', $q)));

    // Per token: the literal word (matched case-INsensitively -- safe, these are
    // real words a human typed, e.g. "gap"/"color"/"border", long enough to not
    // collide) + dictionary tokens (matched case-SENSITIVELY against the ORIGINAL,
    // un-lowercased id -- these are short/abbreviated (2-4 chars, e.g. "Gp", "Bdr",
    // "MOB") and EB's camelCase casing is the only thing that keeps them from
    // colliding with unrelated substrings. Verified bug from a real false positive:
    // case-INsensitive "Gp" matched inside "bgImgPos" (contains "gP", not "Gp") --
    // wrongly surfacing background-image-position attributes on a "gap" search.
    // Case-sensitive matching against the original id excludes that collision
    // while still matching the real target ("MOBclGp_Range" DOES contain "Gp").
    $tokenPlan = [];
    foreach ($tokens as $t) {
        $tokenPlan[] = ['literal' => $t, 'dict' => $syn[$t] ?? []];
    }

    $scored = [];
    foreach ($attrs as $id => $def) {
        $lid = strtolower($id);
        $mapping = strtolower(implode(' ', array_filter([
            (string) ($def['source'] ?? ''), (string) ($def['selector'] ?? ''), (string) ($def['attribute'] ?? ''),
        ])));
        $enum = strtolower(implode(' ', array_map('strval', (array) ($def['enum'] ?? []))));
        // MOB/TAB/hov_ variants are hidden from the id by default (see
        // sandbox_editor_gb_enrich_attrs) and attached as responsive/hover
        // pointers instead -- surface that here so "mobile"/"tablet"/"hover"
        // still finds a base attribute that HAS a hidden variant, even though
        // the literal prefix substring is no longer in $id.
        $variantFlags = trim(implode(' ', [
            !empty($def['responsive']['mobile']) ? 'mobile' : '',
            !empty($def['responsive']['tablet']) ? 'tablet' : '',
            (!empty($def['hover']) || !empty($def['hover_responsive'])) ? 'hover' : '',
        ]));

        $allMatched = true;
        $score = 0;
        foreach ($tokenPlan as $plan) {
            $best = 0;
            $lit  = $plan['literal'];
            foreach ([[$lid, 100], [$mapping, 20], [$enum, 10], [$variantFlags, 10]] as [$hay, $w]) {
                if ($hay === '' || $lit === '') { continue; }
                if ($hay === $lit) { $best = max($best, $w + 50); }
                elseif (strpos($hay, $lit) !== false) { $best = max($best, $w); }
            }
            foreach ($plan['dict'] as $tok) {
                if ($tok === '' || strpos($id, $tok) === false) { continue; } // case-sensitive, original $id
                $best = max($best, ($id === $tok) ? 150 : 100);
            }
            if ($best === 0) { $allMatched = false; break; }
            $score += $best;
        }
        if (!$allMatched) { continue; }

        if ($lid === $q) { $score += 500; }
        elseif (strpos($lid, str_replace(' ', '', $q)) !== false) { $score += 120; }

        $scored[$id] = array_merge($def, [
            'group' => $index[$id] ?? 'unknown',
            'score' => $score,
        ]);
    }

    uasort($scored, fn($a, $b) => $b['score'] <=> $a['score']);
    return $scored;
}

/**
 * Split a flat controls dict into navigable groups.
 *
 * content — flat {ctrl_id => def}: primary controls defining what the widget
 *           shows (text, image, link). Read these first to understand the widget.
 * style   — {section_name => {ctrl_id => def}}: widget-specific appearance
 *           (colors, typography) targeting widget-inner elements.
 * common  — {section_name => {ctrl_id => def}}: Elementor base + EA extension
 *           controls targeting {{WRAPPER}} (outer div). Identical across all
 *           widgets. Key sections: _section_background (background color/image/
 *           gradient), _section_border, _section_box_shadow, section_effects
 *           (entrance animation), section_motion_effects, _section_transform.
 *           To change wrapper background → common._section_background.
 *
 * Structural UI controls (section, tab, raw_html, alert, divider) are omitted —
 * they are editor chrome, not widget settings.
 *
 * @param array $controls     flat {ctrl_id => definition} map
 * @param array $content_ids  ordered list of known content-tab IDs (from catalog)
 */
function sandbox_editor_group_controls(array $controls, array $content_ids = []): array
{
    // Editor chrome — not real settings agents can write.
    static $skip = ['section', 'tab', 'tabs', 'raw_html', 'alert', 'heading', 'divider'];

    $content = [];
    $style   = [];  // section_name  => {ctrl_id => def}
    $common  = [];  // section_name  => {ctrl_id => def}

    $content_set = array_flip($content_ids);
    foreach ($content_ids as $id) {
        if (isset($controls[$id]) && !in_array($controls[$id]['type'] ?? '', $skip, true)) {
            $content[$id] = $controls[$id];
        }
    }

    foreach ($controls as $id => $def) {
        if (isset($content_set[$id])) { continue; }
        if (in_array($def['type'] ?? '', $skip, true)) { continue; }

        $tab     = $def['tab'] ?? null;
        $section = $def['section'] ?? '_root';
        $is_base = strncmp($id, '_', 1) === 0;
        $is_ea   = strncmp($id, 'eael_', 5) === 0;

        if ($is_base || $is_ea || $tab === 'advanced') {
            $common[$section][$id] = $def;
        } elseif ($tab === 'style') {
            $style[$section][$id] = $def;
        } elseif ($tab === 'content') {
            $content[$id] = $def;
        } else {
            $common[$section][$id] = $def;
        }
    }

    return ['content' => $content, 'style' => $style, 'common' => $common];
}

/**
 * Prime a REST context so Elementor v4+ rebuilds control stacks with FULL
 * metadata (labels/tabs/options) AND the Advanced/common tab (padding, margin,
 * background, border, ...). Outside REST, get_controls() returns a stripped set
 * (heading: 623 keys, no _padding/_margin) — primed it returns 879 with them.
 * Idempotent; call before any get_controls() introspection.
 */
function sandbox_editor_elementor_prime_context(): void
{
    try {
        if (!defined('REST_REQUEST')) { define('REST_REQUEST', true); }
        $ref  = new ReflectionClass('Elementor\Core\Frontend\Performance');
        $prop = $ref->getProperty('is_frontend');
        $prop->setAccessible(true);
        $prop->setValue(null, null);
    } catch (\Throwable $_) {}
    if (isset(\Elementor\Plugin::$instance->controls_manager)) {
        \Elementor\Plugin::$instance->controls_manager->clear_stack_cache();
    }
}

/** Active Elementor breakpoints (desktop is the base; e.g. ['mobile','tablet']). */
function sandbox_editor_active_breakpoints(): array
{
    try {
        if (isset(\Elementor\Plugin::$instance->breakpoints)) {
            $keys = array_keys(\Elementor\Plugin::$instance->breakpoints->get_active_breakpoints());
            if ($keys) { return $keys; }
        }
    } catch (\Throwable $_) {}
    return ['mobile', 'tablet'];
}

/**
 * Per-device variant keys for a responsive control, desktop-first in Elementor's
 * large→small order: {desktop: key, tablet: key_tablet, mobile: key_mobile}.
 * Desktop uses the BARE key; each active breakpoint appends _<breakpoint>.
 */
function sandbox_editor_responsive_variants(string $key, array $breakpoints): array
{
    static $order = ['widescreen', 'desktop', 'laptop', 'tablet_extra',
                     'tablet', 'mobile_extra', 'mobile'];
    $out = ['desktop' => $key];
    foreach ($order as $bp) {
        if ($bp === 'desktop') { continue; }
        if (in_array($bp, $breakpoints, true)) { $out[$bp] = $key . '_' . $bp; }
    }
    return $out;
}

/** Human-term → control-token synonyms, so "font size"/"space" resolve. */
function sandbox_editor_search_synonyms(): array
{
    return [
        'space'     => ['padding', 'margin', 'gap', 'spacing'],
        'spacing'   => ['padding', 'margin', 'gap'],
        'dimension' => ['padding', 'margin', 'width', 'height'],
        'dimensions'=> ['padding', 'margin', 'width', 'height'],
        'gutter'    => ['gap', 'padding'],
        'shadow'    => ['box_shadow'],
        'round'     => ['radius'],
        'rounded'   => ['radius'],
        'corner'    => ['radius'],
        'colour'    => ['color'],
        'bg'        => ['background'],
        'font'      => ['typography', 'font_family', 'font_size'],
        'weight'    => ['font_weight'],
        'align'     => ['alignment'],
        'alignment' => ['align'],
        'position'  => ['align'],
        'hidden'    => ['hide'],
        'hide'      => ['hide'],
        'thickness' => ['width', 'border_width'],
        'stroke'    => ['border', 'text_stroke'],
        'opacity'   => ['opacity'],
        'zindex'    => ['z_index'],
        'z-index'   => ['z_index'],
        'overflow'  => ['overflow'],
        'sticky'    => ['sticky'],
        'animation' => ['animation', 'motion_fx'],
        'width'     => ['width', 'content_width'],
        'height'    => ['height', 'min_height'],
        'responsive'=> ['tablet', 'mobile'],
    ];
}

/**
 * Ranked keyword search across a widget/element's controls. Tokenized AND
 * (every query token — or a synonym — must match SOMEWHERE), weighted by field
 * (id > label > section > description > selector) with exact/whole-segment
 * boosts, and core Elementor controls surfaced above extension (`eael_*`) noise.
 *
 * Each match carries: `group` (content|style|common), `section`, `origin`
 * (core|extension), and `score`. Returned highest-score first.
 *
 * Usage: sandbox_editor_schema(['builder'=>'elementor','name'=>'heading','search'=>'font size'])
 */
function sandbox_editor_search_controls(array $controls, string $query, array $groups): array
{
    $q = strtolower(trim($query));
    if ($q === '') { return []; }

    // ctrl_id → {group, section} index from the already-computed groups.
    $index = [];
    foreach (($groups['content'] ?? []) as $id => $_) {
        $index[$id] = ['group' => 'content', 'section' => null];
    }
    foreach (['style', 'common'] as $grp) {
        foreach (($groups[$grp] ?? []) as $section => $ctrls) {
            foreach ($ctrls as $id => $_) {
                $index[$id] = ['group' => $grp, 'section' => $section];
            }
        }
    }

    // Split query into tokens; expand each with synonyms (token OR its aliases).
    $syn    = sandbox_editor_search_synonyms();
    $tokens = array_values(array_filter(preg_split('/\s+/', $q)));
    $tokenAlts = [];
    foreach ($tokens as $t) {
        $alts = [$t];
        // light stem so a longer query token still matches a short id
        if (strlen($t) > 5 && substr($t, -4) === 'ment') { $alts[] = substr($t, 0, -4); } // alignment→align
        if (strlen($t) > 4 && substr($t, -1) === 's')    { $alts[] = substr($t, 0, -1); } // dimensions→dimension
        foreach (array_values($alts) as $a) {              // synonyms for token AND its stems
            if (isset($syn[$a])) { $alts = array_merge($alts, $syn[$a]); }
        }
        $tokenAlts[] = array_values(array_unique($alts));
    }
    $qKey = str_replace(' ', '_', $q); // "font size" → "font_size" for whole-id checks

    // UI chrome — not writable settings; keep out of search results.
    static $chrome = ['section', 'tab', 'tabs', 'raw_html', 'alert', 'heading', 'divider'];

    $scored = [];
    foreach ($controls as $id => $def) {
        if (in_array($def['type'] ?? '', $chrome, true)) { continue; }
        $lid      = strtolower($id);
        $idParts  = explode('_', $lid);
        $section  = strtolower((string) ($def['section'] ?? ($index[$id]['section'] ?? '')));
        $fields   = [
            [$lid,                                             100], // id
            [strtolower((string) ($def['label'] ?? '')),        60], // label
            [$section,                                          40], // section title
            [strtolower((string) ($def['description'] ?? '')),  25], // description
            [strtolower(implode(' ', array_merge(
                array_keys($def['selectors'] ?? []),
                array_values($def['selectors'] ?? [])))),       15], // selectors
        ];

        // Token-AND: every token (or a synonym) must score in some field.
        $allMatched = true;
        $score      = 0;
        foreach ($tokenAlts as $alts) {
            $best = 0;
            foreach ($alts as $alt) {
                foreach ($fields as [$hay, $w]) {
                    if ($hay === '') { continue; }
                    if ($hay === $alt)                    { $best = max($best, $w + 50); }
                    elseif ($w === 100 && in_array($alt, $idParts, true))
                                                          { $best = max($best, $w + 25); } // whole id segment
                    elseif (strpos($hay, $alt) !== false) { $best = max($best, $w); }
                }
            }
            if ($best === 0) { $allMatched = false; break; }
            $score += $best;
        }
        if (!$allMatched) { continue; }

        // Whole-query boosts. Multi-word queries reward the exact human phrase in the
        // label ("text color"→title_color); single-word phrase boosts are skipped —
        // a generic "Color" label would otherwise outrank the semantically-primary control.
        if     ($lid === $q || $lid === $qKey)      { $score += 500; }
        elseif (strpos($lid, $qKey) !== false)      { $score += 120; }
        if (count($tokens) > 1) {
            $lbl = strtolower((string) ($def['label'] ?? ''));
            if     ($lbl === $q)                    { $score += 200; }
            elseif (strpos($lbl, $q) !== false)     { $score += 90; }
        }

        // core vs extension: keep core Elementor above EA/extension noise even when the
        // extension control has strong id matches (penalty > a double id-segment hit).
        // Large penalty: a flat shift only breaks CORE-vs-EXTENSION ties within one
        // widget (where core is wanted); intra-extension order on EA-only widgets is
        // unchanged since every control is penalized equally.
        $isExt  = (strncmp($id, 'eael_', 5) === 0) || (strpos($id, '_eael') !== false);
        if ($isExt) { $score -= 250; }

        $loc = $index[$id] ?? ['group' => 'unknown', 'section' => null];
        $scored[$id] = array_merge($def, $loc, [
            'origin' => $isExt ? 'extension' : 'core',
            'score'  => $score,
        ]);
    }

    // Sort: score desc, then core before extension, then id asc (stable-ish).
    uasort($scored, function ($a, $b) {
        if ($a['score'] !== $b['score']) { return $b['score'] <=> $a['score']; }
        if ($a['origin'] !== $b['origin']) { return $a['origin'] === 'core' ? -1 : 1; }
        return 0;
    });

    return $scored;
}

/** Build an editor-schema response from a catalog entry (source: catalog). */
function sandbox_editor_catalog_response($builder, $name, $cat, $bt = null, $search = null, $include_variants = false, $full = false)
{
    $key  = $builder === 'gutenberg' ? 'attributes' : 'controls';
    $resp = ['builder' => $builder, 'name' => $name, $key => $cat[$key] ?? [],
             'source' => 'catalog'];
    $installed = sandbox_editor_plugin_version($cat['plugin'] ?? null);
    $resp['catalog'] = ['version' => $cat['version'] ?? null, 'installed_version' => $installed];
    if ($installed && !empty($cat['version']) && $installed !== $cat['version']) {
        $resp['version_mismatch'] = true;
    }
    if ($builder === 'gutenberg') {
        // Catalog entries are essential-blocks/* only (the only names that ever
        // fall back to the catalog for gutenberg) -- same abbreviated-attribute
        // naming as the live path, so the same hide/decode treatment applies.
        $attrs  = sandbox_editor_gb_enrich_attrs((array) ($cat[$key] ?? []), $include_variants);
        $groups = sandbox_editor_gb_group_attrs($attrs);
        $resp[$key] = $attrs;
        if ($search !== null && $search !== '') {
            return ['builder' => 'gutenberg', 'name' => $name, 'search' => $search, 'source' => 'catalog',
                    'matches' => sandbox_editor_gb_search_attrs($attrs, $search, $groups)];
        }
        $resp['dynamic']  = $bt ? sandbox_editor_dynamic_flag($name, $bt) : ($cat['dynamic'] ?? null);
        $resp['fidelity'] = ['level' => $cat['coverage'] ?? 'full',
                             'count' => count($cat[$key] ?? [])];
        $resp['groups']   = $groups;
        // title/supports/style_paths: pulled from the LIVE block type when this
        // plugin happens to be active on this instance too (common — the catalog
        // path is chosen for richer counts, not because the plugin is absent).
        // Catalog entries themselves don't carry these yet (a known gap — the
        // catalog-generation pipeline drops title/supports when writing the
        // committed file; would need a regen to backfill for installs where the
        // plugin truly isn't active). title/description fall back further
        // still: the curated block-descriptions.json (real source data, not
        // generated) even when $bt is entirely null (not even stub-registered).
        $resp += sandbox_editor_gb_meta($bt);
        $curated = sandbox_editor_gb_block_descriptions()[$name] ?? [];
        if (empty($resp['title']))       { $resp['title']       = $curated['title'] ?? null; }
        if (empty($resp['description'])) { $resp['description'] = $curated['description'] ?? null; }
        return sandbox_editor_gb_trim_response($resp, $full);
    }
    if ($builder === 'elementor' && !empty($cat['groups'])) {
        if ($search !== null && $search !== '') {
            return array_merge(
                ['builder' => $builder, 'name' => $name, 'search' => $search, 'source' => 'catalog'],
                array_intersect_key($resp, ['catalog' => 1, 'version_mismatch' => 1]),
                ['matches' => sandbox_editor_search_controls($cat[$key] ?? [], $search, $cat['groups'])]
            );
        }
        $resp['groups'] = $cat['groups'];
    }
    return $resp;
}

function sandbox_editor_schema($input)
{
    $builder = (string) ($input['builder'] ?? '');
    $name    = (string) ($input['name'] ?? '');

    if ($builder === 'gutenberg') {
        $reg = WP_Block_Type_Registry::get_instance();
        // Previously silently ignored for Gutenberg (Elementor-only feature) -- a
        // named-block search returned the FULL unfiltered attribute list either way.
        $gb_search = isset($input['search']) ? trim((string) $input['search']) : null;
        // Default: MOB/TAB/hov_-prefixed EB variant attrs are hidden (attached as
        // responsive/hover pointers on their base attr instead) -- pass true to
        // get the raw, full, undecorated list back.
        $gb_include_variants = !empty($input['include_variants']);
        // Default: `groups.common` (the recurring style/global attrs -- often the
        // MAJORITY of a large block's attribute count, e.g. 715 of 918 on
        // pro-business-hours) is trimmed to just names, not full definitions --
        // pass full:true to get everything back. `attributes`/`groups.content`
        // (this block's own settings) are unaffected either way.
        $gb_full = !empty($input['full']);

        // Find a block by TITLE/DESCRIPTION/PURPOSE (not attribute content --
        // that's `search`). Scans every live-registered block (+ any catalog-only
        // names not live-registered) matching title/description/name against
        // the query + its synonym expansion, ranked. Only makes sense with no
        // `name` (finding is how you GET a name to then look up).
        $gb_find = isset($input['find']) ? trim((string) $input['find']) : null;
        if ($gb_find && !$name) {
            $q = strtolower($gb_find);
            $syn = sandbox_editor_find_synonyms();
            $alts = array_unique(array_merge([$q], array_map('strtolower', $syn[$q] ?? [])));
            $onlyEb = !empty($input['eb_only']);
            $limit  = isset($input['limit']) ? max(1, (int) $input['limit']) : 40;
            $descriptions = sandbox_editor_gb_block_descriptions();
            $seen = [];
            $all = [];
            $scoreOne = function ($bn, $title, $desc) use ($alts, $q) {
                $hay = strtolower(trim($title . ' ' . $desc . ' ' . $bn));
                $score = 0;
                foreach ($alts as $alt) {
                    if ($alt === '') { continue; }
                    if (strtolower($title) === $alt) { $score = max($score, 500); }
                    elseif (strpos($hay, $alt) !== false) { $score = max($score, $alt === $q ? 200 : 100); }
                }
                return $score;
            };
            foreach ($reg->get_all_registered() as $bn => $bt) {
                if ($onlyEb && strpos($bn, 'essential-blocks/') !== 0) { continue; }
                $seen[$bn] = true;
                $curated = $descriptions[$bn] ?? [];
                $title = $bt->title ?: ($curated['title'] ?? '');
                $desc  = $bt->description ?: ($curated['description'] ?? '');
                $score = $scoreOne($bn, $title, $desc);
                if ($score > 0) {
                    $all[] = ['name' => $bn, 'title' => $title, 'description' => $desc,
                              'attribute_count' => count((array) $bt->attributes), 'score' => $score];
                }
            }
            foreach (sandbox_editor_catalog_all_names('gutenberg') as $bn) {
                if (isset($seen[$bn])) { continue; }
                if ($onlyEb && strpos($bn, 'essential-blocks/') !== 0) { continue; }
                $curated = $descriptions[$bn] ?? [];
                $title = $curated['title'] ?? '';
                $desc  = $curated['description'] ?? '';
                $score = $scoreOne($bn, $title, $desc);
                if ($score > 0) {
                    $cat = sandbox_editor_catalog_entry('gutenberg', $bn);
                    $all[] = ['name' => $bn, 'title' => $title, 'description' => $desc,
                              'attribute_count' => count($cat['attributes'] ?? []), 'score' => $score, 'source' => 'catalog'];
                }
            }
            usort($all, fn($a, $b) => $b['score'] <=> $a['score']);
            return ['builder' => 'gutenberg', 'find' => $gb_find, 'matches' => array_slice($all, 0, $limit)];
        }

        // spec 011: named EB block -> resolve the FULL attribute set from source, or
        // honestly report reduced fidelity. Non-EB blocks + listings stay unchanged.
        if ($name && strpos($name, 'essential-blocks/') === 0) {
            $bt = $reg->get_registered($name);
            if (!$bt) {
                // spec 012: not registered live (e.g. EB Pro or this block absent on
                // this install) -> serve the catalog if we have it, same as the
                // Elementor path below. Previously returned not_found immediately,
                // silently skipping the catalog fallback for this whole block family.
                $cat = sandbox_editor_catalog_entry('gutenberg', $name);
                if ($cat) {
                    return sandbox_editor_catalog_response('gutenberg', $name, $cat, null, $gb_search, $gb_include_variants, $gb_full);
                }
                return new WP_Error('not_found', "block '$name' not registered");
            }
            $extra_roots = [];
            if (!empty($input['source_root'])) { $extra_roots[] = (string) $input['source_root']; }
            $full = sandbox_editor_eb_resolve($name, $bt, $extra_roots);
            $live_level = $full['fidelity']['level'] ?? 'reduced';
            $live_count = $full ? count($full['attributes']) : 0;
            // spec 012: live preferred when full; else catalog if it's richer.
            if ($live_level !== 'full') {
                $cat = sandbox_editor_catalog_entry('gutenberg', $name);
                if ($cat && count($cat['attributes'] ?? []) > $live_count) {
                    return sandbox_editor_catalog_response('gutenberg', $name, $cat, $bt, $gb_search, $gb_include_variants, $gb_full);
                }
            }
            if ($full !== null) {
                $full['source'] = 'live';
                $full['attributes'] = sandbox_editor_gb_enrich_attrs((array) ($full['attributes'] ?? []), $gb_include_variants);
                $full['groups'] = sandbox_editor_gb_group_attrs($full['attributes']);
                if ($gb_search) {
                    return ['builder' => 'gutenberg', 'name' => $name, 'search' => $gb_search, 'source' => 'live',
                            'matches' => sandbox_editor_gb_search_attrs($full['attributes'], $gb_search, $full['groups'])];
                }
                return sandbox_editor_gb_trim_response($full + sandbox_editor_gb_meta($bt), $gb_full); // level: full | partial
            }
            // No source + no catalog: block.json attributes only, flagged reduced.
            $attrs = sandbox_editor_gb_enrich_attrs(sandbox_editor_gb_attrs((array) $bt->attributes), $gb_include_variants);
            $groups = sandbox_editor_gb_group_attrs($attrs);
            if ($gb_search) {
                return ['builder' => 'gutenberg', 'name' => $name, 'search' => $gb_search, 'source' => 'live',
                        'matches' => sandbox_editor_gb_search_attrs($attrs, $gb_search, $groups)];
            }
            return sandbox_editor_gb_trim_response(['builder' => 'gutenberg', 'name' => $name, 'dynamic' => sandbox_editor_dynamic_flag($name, $bt),
                    'attributes' => $attrs, 'groups' => $groups,
                    'fidelity' => sandbox_editor_eb_fidelity('reduced', count($attrs), null, []),
                    'eb_attribute_fidelity' => 'reduced', 'source' => 'live']
                    + sandbox_editor_gb_meta($bt), $gb_full);
        }

        // Non-EB named blocks + all listings. NOTE: this branch is unreachable for any
        // essential-blocks/* name (that whole family returns above, found-or-not) — so the
        // EB-specific "reduced (block.json attributes only; no src/controls checkout)"
        // wording this used to carry for EVERY non-EB block (core/*, ACF, any 3rd-party
        // block) was simply wrong: block.json IS the complete, authoritative attribute
        // declaration for a properly-registered non-EB block type — there is no
        // "src/controls checkout" concept to be missing for them. Fixed to report 'full'
        // for non-EB blocks; the EB reduced/full distinction only applies within the
        // essential-blocks/* branch above.
        if ($name) {
            $bt = $reg->get_registered($name);
            if (!$bt) {
                return new WP_Error('not_found', "block '$name' not registered");
            }
            $attrs = sandbox_editor_gb_attrs((array) $bt->attributes);
            $groups = sandbox_editor_gb_group_attrs($attrs);
            if ($gb_search) {
                return ['builder' => 'gutenberg', 'name' => $name, 'search' => $gb_search, 'source' => 'live',
                        'matches' => sandbox_editor_gb_search_attrs($attrs, $gb_search, $groups)];
            }
            return sandbox_editor_gb_trim_response(['builder' => 'gutenberg', 'name' => $name, 'dynamic' => sandbox_editor_dynamic_flag($name, $bt),
                    'eb_attribute_fidelity' => 'full', 'attributes' => $attrs,
                    'groups' => $groups]
                    + sandbox_editor_gb_meta($bt), $gb_full);
        }
        // GLOBAL search (no name): "which block has an attribute matching X?" --
        // Gutenberg's counterpart to Elementor's global search below. Was a
        // documented gap (silently returned the full listing instead). Scans every
        // live-registered block (+ any catalog-only names not live-registered, e.g.
        // an EB block absent from this install) and keeps each block's single
        // best-scoring match, ranked -- same shape as the Elementor version.
        if ($gb_search) {
            @set_time_limit(120);
            $onlyEb = !empty($input['eb_only']);
            $limit  = isset($input['limit']) ? max(1, (int) $input['limit']) : 40;
            $all = [];
            $seen = [];
            foreach ($reg->get_all_registered() as $bn => $bt) {
                if ($onlyEb && strpos($bn, 'essential-blocks/') !== 0) { continue; }
                $seen[$bn] = true;
                $liveAttrs = (array) $bt->attributes;
                if (strpos($bn, 'essential-blocks/') === 0) {
                    $cat = sandbox_editor_catalog_entry('gutenberg', $bn);
                    if ($cat && count($cat['attributes'] ?? []) > count($liveAttrs)) {
                        $liveAttrs = (array) $cat['attributes'];
                    }
                }
                $attrs   = sandbox_editor_gb_enrich_attrs($liveAttrs, false);
                $groups  = sandbox_editor_gb_group_attrs($attrs);
                $matches = sandbox_editor_gb_search_attrs($attrs, $gb_search, $groups);
                foreach ($matches as $aid => $m) { // best only, already sorted
                    $all[] = ['name' => $bn, 'attribute' => $aid,
                              'group' => $m['group'] ?? 'unknown', 'score' => $m['score'] ?? 0];
                    break;
                }
            }
            // catalog-only names (EB block/plugin not live-registered on this instance at all)
            foreach (sandbox_editor_catalog_all_names('gutenberg') as $bn) {
                if (isset($seen[$bn])) { continue; }
                if ($onlyEb && strpos($bn, 'essential-blocks/') !== 0) { continue; }
                $cat = sandbox_editor_catalog_entry('gutenberg', $bn);
                if (!$cat) { continue; }
                $attrs   = sandbox_editor_gb_enrich_attrs((array) ($cat['attributes'] ?? []), false);
                $groups  = sandbox_editor_gb_group_attrs($attrs);
                $matches = sandbox_editor_gb_search_attrs($attrs, $gb_search, $groups);
                foreach ($matches as $aid => $m) {
                    $all[] = ['name' => $bn, 'attribute' => $aid,
                              'group' => $m['group'] ?? 'unknown', 'score' => $m['score'] ?? 0, 'source' => 'catalog'];
                    break;
                }
            }
            usort($all, fn($a, $b) => $b['score'] <=> $a['score']);
            return ['builder' => 'gutenberg', 'search' => $gb_search, 'scanned' => count($seen),
                    'matches' => array_slice($all, 0, $limit)];
        }

        // Real bug found comparing this against the per-name path: EB blocks declare
        // only ~3-4 generic attributes in their own block.json (the full set is only
        // ever resolved by sandbox_editor_eb_resolve(), called from the per-name path
        // above, NOT here) -- so this loop's `array_keys((array) $bt->attributes)` was
        // reporting essential-blocks/pro-business-hours as having 4 attributes when it
        // actually has 918 (visible) / 1764 (raw), a >200x undercount for every EB
        // block in any listing. `$fidelity` referenced below was also undefined (a
        // leftover from an earlier edit) -- would return null silently.
        // Fix: for essential-blocks/* names, prefer the catalog's attribute set when
        // it's richer than the live block.json set (same live-vs-catalog preference
        // as the per-name path), and apply the same variant-hiding as the per-name
        // view so the listing's counts are consistent with what a per-name lookup
        // would show -- without paying for full per-block source resolution (parsing
        // every EB block's JS attribute generators) on every listing call.
        $blocks = [];
        foreach ($reg->get_all_registered() as $bn => $bt) {
            if (!empty($input['eb_only']) && strpos($bn, 'essential-blocks/') !== 0) {
                continue;
            }
            $liveAttrs = (array) $bt->attributes;
            $attrSource = 'live';
            if (strpos($bn, 'essential-blocks/') === 0) {
                $cat = sandbox_editor_catalog_entry('gutenberg', $bn);
                if ($cat && count($cat['attributes'] ?? []) > count($liveAttrs)) {
                    $liveAttrs = (array) $cat['attributes'];
                    $attrSource = 'catalog';
                }
                $liveAttrs = sandbox_editor_gb_enrich_attrs($liveAttrs, false);
            }
            $blocks[$bn] = ['dynamic' => sandbox_editor_dynamic_flag($bn, $bt),
                            'attributes' => array_keys($liveAttrs), 'attribute_source' => $attrSource];
        }
        return ['builder' => 'gutenberg', 'count' => count($blocks), 'blocks' => $blocks];
    }

    if ($builder === 'elementor' && !class_exists('\\Elementor\\Plugin')) {
        // Previously fell through to the generic bad_builder error below, which
        // wrongly implied "elementor" was an invalid value for `builder` when the
        // real cause is that Elementor simply isn't installed/active on THIS
        // instance (verified: an instance with only Essential Blocks active hit
        // this path and got a "builder must be gutenberg|elementor" error for a
        // perfectly valid builder=elementor request).
        return new WP_Error('elementor_inactive', 'Elementor is not installed or active on this instance.');
    }

    if ($builder === 'elementor' && class_exists('\\Elementor\\Plugin')) {
        // Elementor v4+ strips label/tab/options during WP init (non-REST context)
        // AND omits the entire Advanced/common tab (padding, margin, background, ...).
        // Prime a REST context so get_controls() rebuilds with FULL metadata.
        sandbox_editor_elementor_prime_context();

        $wm = \Elementor\Plugin::$instance->widgets_manager;
        $em = \Elementor\Plugin::$instance->elements_manager;
        $types = method_exists($wm, 'get_widget_types') ? $wm->get_widget_types() : [];
        $search = isset($input['search']) ? trim((string) $input['search']) : null;

        // Find a widget/element by TITLE/KEYWORDS/PURPOSE (not control content --
        // that's `search`). Elementor has no per-widget description (confirmed
        // earlier -- no get_description() method anywhere in Widget_Base/
        // Element_Base), so keywords is the real text to match against here.
        $find = isset($input['find']) ? trim((string) $input['find']) : null;
        if ($find && !$name) {
            $q = strtolower($find);
            $syn = sandbox_editor_find_synonyms();
            $alts = array_unique(array_merge([$q], array_map('strtolower', $syn[$q] ?? [])));
            $onlyTypes = isset($input['types']) ? (string) $input['types'] : 'all';
            $limit = isset($input['limit']) ? max(1, (int) $input['limit']) : 40;
            $scoreOne = function ($n, $title, $keywords) use ($alts, $q) {
                $hay = strtolower(trim($title . ' ' . implode(' ', (array) $keywords) . ' ' . $n));
                $score = 0;
                foreach ($alts as $alt) {
                    if ($alt === '') { continue; }
                    if (strtolower($title) === $alt) { $score = max($score, 500); }
                    elseif (strpos($hay, $alt) !== false) { $score = max($score, $alt === $q ? 200 : 100); }
                }
                return $score;
            };
            $all = [];
            if ($onlyTypes !== 'elements' && is_array($types)) {
                foreach ($types as $wn => $wobj) {
                    $title = method_exists($wobj, 'get_title') ? $wobj->get_title() : '';
                    $kw    = method_exists($wobj, 'get_keywords') ? $wobj->get_keywords() : [];
                    $score = $scoreOne($wn, $title, $kw);
                    if ($score > 0) { $all[] = ['name' => $wn, 'kind' => 'widget', 'title' => $title, 'keywords' => $kw, 'score' => $score]; }
                }
            }
            if ($onlyTypes !== 'widgets' && $em && method_exists($em, 'get_element_types')) {
                foreach ($em->get_element_types() as $en => $eobj) {
                    $title = method_exists($eobj, 'get_title') ? $eobj->get_title() : '';
                    $kw    = method_exists($eobj, 'get_keywords') ? $eobj->get_keywords() : [];
                    $score = $scoreOne($en, $title, $kw);
                    if ($score > 0) { $all[] = ['name' => $en, 'kind' => 'element', 'title' => $title, 'keywords' => $kw, 'score' => $score]; }
                }
            }
            usort($all, fn($a, $b) => $b['score'] <=> $a['score']);
            return ['builder' => 'elementor', 'find' => $find, 'matches' => array_slice($all, 0, $limit)];
        }

        if ($name) {
            // Widget first; then a structural element type (section|column|container|
            // e-flexbox|e-div-block|...) which lives in the elements_manager registry.
            $obj  = is_array($types) ? ($types[$name] ?? null) : null;
            $kind = 'widget';
            if (!$obj && $em && method_exists($em, 'get_element_types')) {
                $el = $em->get_element_types($name);
                if ($el) { $obj = $el; $kind = 'element'; }
            }
            if (!$obj) {
                // spec 012: not registered live → serve the catalog if we have it
                // (e.g. Elementor Pro/EA widget absent on this install).
                $cat = sandbox_editor_catalog_entry('elementor', $name);
                if ($cat) {
                    return sandbox_editor_catalog_response('elementor', $name, $cat, null, $search);
                }
                return new WP_Error('not_found',
                    "'$name' is not a registered widget or element type (enable it first if EA)");
            }
            $controls = [];
            try {
                foreach ((array) $obj->get_controls() as $cid => $c) {
                    $controls[$cid] = sandbox_editor_el_control_entry($cid, $c);
                }
            } catch (\Throwable $e) {
                return new WP_Error('controls_unavailable', $e->getMessage());
            }
            $groups = sandbox_editor_group_controls($controls);

            // Responsive variant lookup: resolve a control's per-device keys.
            //   editor-schema {name, variants:"typography_font_size"}
            if (isset($input['variants'])) {
                $vk  = (string) $input['variants'];
                if (!isset($controls[$vk])) {
                    return new WP_Error('not_found', "control '$vk' not found on '$name'");
                }
                $isResp = !empty($controls[$vk]['responsive']);
                $bps    = sandbox_editor_active_breakpoints();
                return ['builder' => 'elementor', 'name' => $name, 'kind' => $kind,
                        'control' => $vk, 'responsive' => $isResp,
                        'breakpoints' => $isResp ? $bps : [],
                        'variants' => $isResp
                            ? sandbox_editor_responsive_variants($vk, $bps)
                            : ['desktop' => $vk],
                        'source' => 'live'];
            }

            if ($search !== null && $search !== '') {
                return ['builder' => 'elementor', 'name' => $name, 'kind' => $kind,
                        'search' => $search, 'source' => 'live',
                        'matches' => sandbox_editor_search_controls($controls, $search, $groups)];
            }
            // Separate `responsive` block: active breakpoints + which controls are
            // responsive (so agents don't scan every control for the flag).
            $respControls = [];
            foreach ($controls as $cid => $cdef) {
                if (!empty($cdef['responsive'])) { $respControls[] = $cid; }
            }
            // title/keywords: Elementor's rough equivalent of Gutenberg's block-level
            // title/description (offered earlier, never wired in until this gap-
            // comparison pass) -- verified present on both widgets AND element types
            // (container, section, ...), so safe to call regardless of $kind. There is
            // no per-widget "description" method in Elementor's Widget_Base/
            // Element_Base at all (checked directly: heading/button/form/container all
            // return false for method_exists(..., 'get_description')) -- keywords is
            // the closest real equivalent Elementor actually has.
            $title = method_exists($obj, 'get_title') ? $obj->get_title() : null;
            $keywords = method_exists($obj, 'get_keywords') ? $obj->get_keywords() : [];
            return ['builder' => 'elementor', 'name' => $name, 'kind' => $kind, 'source' => 'live',
                    'title' => $title, 'keywords' => $keywords,
                    'controls' => $controls, 'count' => count($controls),
                    'groups'   => $groups,
                    'responsive' => ['breakpoints' => sandbox_editor_active_breakpoints(),
                                     'controls' => $respControls]];
        }
        $names   = is_array($types) ? array_keys($types) : [];
        $elNames = ($em && method_exists($em, 'get_element_types'))
                   ? array_keys($em->get_element_types()) : [];

        // GLOBAL search (no name): "which widget/element has control X?" Scans every
        // widget + element type, keeps each one's single best-scoring match, returns
        // the top matches across all. Heavier than a per-widget search (instantiates
        // all stacks, ~1s) — pass `types:"widgets"` to skip elements, or a specific
        // `name` when you know the widget. `limit` caps results (default 40).
        if ($search !== null && $search !== '') {
            @set_time_limit(120); // never die mid-scan
            $only  = isset($input['types']) ? (string) $input['types'] : 'all'; // all|widgets|elements
            $limit = isset($input['limit']) ? max(1, (int) $input['limit']) : 40;
            $all = [];
            $scan = function ($host, $hostName, $hostKind) use ($search, &$all) {
                try {
                    $ctrls = [];
                    foreach ((array) $host->get_controls() as $cid => $c) {
                        $ctrls[$cid] = sandbox_editor_el_control_entry($cid, $c);
                    }
                    $groups  = sandbox_editor_group_controls($ctrls);
                    $matches = sandbox_editor_search_controls($ctrls, $search, $groups);
                    foreach ($matches as $id => $m) {                       // best (already sorted)
                        $all[] = ['name' => $hostName, 'kind' => $hostKind, 'control' => $id,
                                  'origin' => $m['origin'], 'score' => $m['score'],
                                  'group' => $m['group'], 'section' => $m['section']];
                        break;
                    }
                } catch (\Throwable $_) {}
            };
            if ($only !== 'elements' && is_array($types)) {
                foreach ($types as $wn => $wobj) { $scan($wobj, $wn, 'widget'); }
            }
            if ($only !== 'widgets' && $em && method_exists($em, 'get_element_types')) {
                foreach ($em->get_element_types() as $en => $eobj) { $scan($eobj, $en, 'element'); }
            }
            usort($all, fn($a, $b) => $b['score'] <=> $a['score']);
            return ['builder' => 'elementor', 'search' => $search, 'source' => 'live',
                    'scanned' => count($all), 'types' => $only,
                    'matches' => array_slice($all, 0, $limit)];
        }

        return ['builder' => 'elementor', 'count' => count($names) + count($elNames),
                'widgets' => $names, 'elements' => $elNames];
    }
    return new WP_Error('bad_builder', 'builder must be gutenberg|elementor');
}
