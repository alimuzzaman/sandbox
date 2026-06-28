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
        'dynamic' => (bool) $block_type->render_callback,
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

/** Extract rich metadata from one Elementor control definition (spec 012 ext).
 *  Returns: type, label, default, section, tab, selectors (css map), options (key→label). */
function sandbox_editor_el_control_entry(array $c): array
{
    $e = [
        'type'    => $c['type'] ?? null,
        'label'   => isset($c['label']) ? (string) $c['label'] : null,
        'default' => $c['default'] ?? null,
        'section' => $c['section'] ?? null,
        'tab'     => $c['tab'] ?? null,
    ];
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
            ['controls' => $controls, 'count' => count($controls)]
        );
    }

    return $entry;
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

/** Build an editor-schema response from a catalog entry (source: catalog). */
function sandbox_editor_catalog_response($builder, $name, $cat, $bt = null)
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
        $resp['dynamic']  = $bt ? (bool) $bt->render_callback : ($cat['dynamic'] ?? null);
        $resp['fidelity'] = ['level' => $cat['coverage'] ?? 'full',
                             'count' => count($cat[$key] ?? [])];
    }
    return $resp;
}

function sandbox_editor_schema($input)
{
    $builder = (string) ($input['builder'] ?? '');
    $name    = (string) ($input['name'] ?? '');

    if ($builder === 'gutenberg') {
        $reg = WP_Block_Type_Registry::get_instance();

        // spec 011: named EB block -> resolve the FULL attribute set from source, or
        // honestly report reduced fidelity. Non-EB blocks + listings stay unchanged.
        if ($name && strpos($name, 'essential-blocks/') === 0) {
            $bt = $reg->get_registered($name);
            if (!$bt) {
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
                    return sandbox_editor_catalog_response('gutenberg', $name, $cat, $bt);
                }
            }
            if ($full !== null) {
                $full['source'] = 'live';
                return $full; // level: full | partial
            }
            // No source + no catalog: block.json attributes only, flagged reduced.
            $attrs = [];
            foreach ((array) $bt->attributes as $k => $def) {
                $attrs[$k] = ['type' => $def['type'] ?? null, 'default' => $def['default'] ?? null];
            }
            return ['builder' => 'gutenberg', 'name' => $name, 'dynamic' => (bool) $bt->render_callback,
                    'attributes' => $attrs,
                    'fidelity' => sandbox_editor_eb_fidelity('reduced', count($attrs), null, []),
                    'eb_attribute_fidelity' => 'reduced', 'source' => 'live'];
        }

        // Pre-feature behavior, byte-for-byte, for non-EB named blocks + all listings.
        $eb_full   = is_dir(WP_PLUGIN_DIR . '/essential-blocks/src/controls');
        $fidelity  = $eb_full ? 'full (src/controls)' : 'reduced (block.json attributes only; no src/controls checkout)';
        if ($name) {
            $bt = $reg->get_registered($name);
            if (!$bt) {
                return new WP_Error('not_found', "block '$name' not registered");
            }
            $attrs = [];
            foreach ((array) $bt->attributes as $k => $def) {
                $attrs[$k] = ['type' => $def['type'] ?? null, 'default' => $def['default'] ?? null];
            }
            return ['builder' => 'gutenberg', 'name' => $name, 'dynamic' => (bool) $bt->render_callback,
                    'eb_attribute_fidelity' => $fidelity, 'attributes' => $attrs];
        }
        $blocks = [];
        foreach ($reg->get_all_registered() as $bn => $bt) {
            if (!empty($input['eb_only']) && strpos($bn, 'essential-blocks/') !== 0) {
                continue;
            }
            $blocks[$bn] = ['dynamic' => (bool) $bt->render_callback,
                            'attributes' => array_keys((array) $bt->attributes)];
        }
        return ['builder' => 'gutenberg', 'count' => count($blocks),
                'eb_attribute_fidelity' => $fidelity, 'blocks' => $blocks];
    }

    if ($builder === 'elementor' && class_exists('\\Elementor\\Plugin')) {
        // Elementor v4+ strips label/tab/options during WP init (non-REST context).
        // Reset the Performance static flag + clear control stacks so get_controls()
        // rebuilds with full metadata in this REST request context.
        try {
            if (!defined('REST_REQUEST')) { define('REST_REQUEST', true); }
            $perf_ref = new ReflectionClass('Elementor\Core\Frontend\Performance');
            $perf_prop = $perf_ref->getProperty('is_frontend');
            $perf_prop->setAccessible(true);
            $perf_prop->setValue(null, null);
        } catch (\Throwable $_) {}
        \Elementor\Plugin::$instance->controls_manager->clear_stack_cache();

        $wm = \Elementor\Plugin::$instance->widgets_manager;
        $types = method_exists($wm, 'get_widget_types') ? $wm->get_widget_types() : [];
        if ($name) {
            $w = is_array($types) ? ($types[$name] ?? null) : null;
            if (!$w) {
                // spec 012: not registered live → serve the catalog if we have it
                // (e.g. Elementor Pro/EA widget absent on this install).
                $cat = sandbox_editor_catalog_entry('elementor', $name);
                if ($cat) {
                    return sandbox_editor_catalog_response('elementor', $name, $cat);
                }
                return new WP_Error('not_found', "widget '$name' not registered (enable it first if EA)");
            }
            $controls = [];
            try {
                foreach ((array) $w->get_controls() as $cid => $c) {
                    $controls[$cid] = sandbox_editor_el_control_entry($c);
                }
            } catch (\Throwable $e) {
                return new WP_Error('controls_unavailable', $e->getMessage());
            }
            return ['builder' => 'elementor', 'name' => $name, 'controls' => $controls];
        }
        $names = is_array($types) ? array_keys($types) : [];
        return ['builder' => 'elementor', 'count' => count($names), 'widgets' => $names];
    }
    return new WP_Error('bad_builder', 'builder must be gutenberg|elementor');
}
