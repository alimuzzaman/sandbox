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

/* ------------------------------- Schema ----------------------------------- */

function sandbox_editor_schema($input)
{
    $builder = (string) ($input['builder'] ?? '');
    $name    = (string) ($input['name'] ?? '');

    if ($builder === 'gutenberg') {
        $reg = WP_Block_Type_Registry::get_instance();
        // src/controls present => full attribute fidelity; built plugin => block.json only.
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
        $wm = \Elementor\Plugin::$instance->widgets_manager;
        $types = method_exists($wm, 'get_widget_types') ? $wm->get_widget_types() : [];
        if ($name) {
            $w = is_array($types) ? ($types[$name] ?? null) : null;
            if (!$w) {
                return new WP_Error('not_found', "widget '$name' not registered (enable it first if EA)");
            }
            $controls = [];
            try {
                foreach ((array) $w->get_controls() as $cid => $c) {
                    $controls[$cid] = ['type' => $c['type'] ?? null, 'default' => $c['default'] ?? null];
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
