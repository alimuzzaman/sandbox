<?php
/**
 * Sandbox editor-authoring helpers + abilities (spec 005).
 *
 * Gutenberg/EB: parse_blocks → mutate → serialize_blocks (unique blockId).
 * Elementor/EA: build the element tree (7-hex ids) → Document::save(['elements'=>…]).
 * Plus editor-schema introspection. Registered as sandbox/* abilities on the
 * spec-003 Abilities layer; also callable directly (host-side verification).
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

/* ----------------------------- Gutenberg / EB ----------------------------- */

/**
 * Insert a Gutenberg block at the end of a post's content.
 * @param array $input {post_id:int, name:string, attributes?:array, inner_html?:string}
 * @return array|WP_Error
 */
function sandbox_editor_gutenberg_insert($input)
{
    $post_id = (int) ($input['post_id'] ?? 0);
    $name    = (string) ($input['name'] ?? '');
    if (!$post_id || !$name) {
        return new WP_Error('bad_input', 'post_id and name are required');
    }
    if (!get_post($post_id)) {
        return new WP_Error('not_found', "post $post_id not found");
    }
    $attrs = (array) ($input['attributes'] ?? []);
    // EB (and most builder blocks) key per-block CSS/state off a unique blockId.
    if (!isset($attrs['blockId'])) {
        $attrs['blockId'] = 'sb-' . sandbox_editor_hexid();
    }
    $block = [
        'blockName'    => $name,
        'attrs'        => $attrs,
        'innerBlocks'  => [],
        'innerHTML'    => (string) ($input['inner_html'] ?? ''),
        'innerContent' => [(string) ($input['inner_html'] ?? '')],
    ];
    $post = get_post($post_id);
    $blocks = parse_blocks($post->post_content);
    $blocks[] = $block;
    $content = serialize_blocks($blocks);
    $r = wp_update_post(['ID' => $post_id, 'post_content' => $content], true);
    if (is_wp_error($r)) {
        return $r;
    }
    return ['post_id' => $post_id, 'inserted' => $name, 'blockId' => $attrs['blockId'],
            'block_count' => count($blocks)];
}

/** Return the parsed-block tree of a post (compact). */
function sandbox_editor_gutenberg_get($input)
{
    $post = get_post((int) ($input['post_id'] ?? 0));
    if (!$post) {
        return new WP_Error('not_found', 'post not found');
    }
    $out = [];
    foreach (parse_blocks($post->post_content) as $b) {
        if (!$b['blockName']) {
            continue;
        }
        $out[] = ['name' => $b['blockName'], 'blockId' => $b['attrs']['blockId'] ?? null,
                  'attr_keys' => array_keys($b['attrs'])];
    }
    return ['post_id' => $post->ID, 'blocks' => $out];
}

/* ----------------------------- Elementor / EA ----------------------------- */

/**
 * Insert an Elementor widget into a page (wraps it in section>column).
 * @param array $input {post_id:int, widget:string, settings?:array}
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
    // Enable the EA widget if it's an eael-* one and not yet registered.
    $settings = (array) ($input['settings'] ?? []);
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
    $doc = \Elementor\Plugin::$instance->documents->get($post_id);
    if (!$doc) {
        return new WP_Error('no_doc', "no Elementor document for post $post_id");
    }
    $tree = $doc->get_elements_data();
    $tree[] = $node;
    $ok = $doc->save(['elements' => $tree]);
    update_post_meta($post_id, '_elementor_edit_mode', 'builder');
    // verify the widget node survived (unregistered widgets are silently dropped)
    $survived = false;
    foreach ($doc->get_elements_data() as $sec) {
        array_walk_recursive($sec, function ($v, $k) use (&$survived, $widget) {
            if ($k === 'widgetType' && $v === $widget) {
                $survived = true;
            }
        });
    }
    return ['post_id' => $post_id, 'widget' => $widget, 'saved' => (bool) $ok,
            'widget_survived' => $survived];
}

/* ------------------------------- Schema ----------------------------------- */

function sandbox_editor_schema($input)
{
    $builder = (string) ($input['builder'] ?? '');
    if ($builder === 'gutenberg') {
        $reg = WP_Block_Type_Registry::get_instance();
        $blocks = [];
        foreach ($reg->get_all_registered() as $bn => $bt) {
            if (strpos($bn, 'essential-blocks/') !== 0 && !empty($input['eb_only'])) {
                continue;
            }
            $blocks[$bn] = ['dynamic' => (bool) $bt->render_callback,
                            'attributes' => array_keys((array) $bt->attributes)];
        }
        return ['builder' => 'gutenberg', 'count' => count($blocks), 'blocks' => $blocks];
    }
    if ($builder === 'elementor' && class_exists('\\Elementor\\Plugin')) {
        $wm = \Elementor\Plugin::$instance->widgets_manager;
        $types = method_exists($wm, 'get_widget_types') ? $wm->get_widget_types() : [];
        $names = is_array($types) ? array_keys($types) : [];
        return ['builder' => 'elementor', 'count' => count($names), 'widgets' => $names];
    }
    return new WP_Error('bad_builder', 'builder must be gutenberg|elementor');
}
