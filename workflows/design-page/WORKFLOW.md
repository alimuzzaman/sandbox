# Workflow: Design a page through the WP runtime

Run by the **designer** skill. Output: a live, rendering WordPress page built
the way the user asked, plus the URL.

## Inputs

- Page brief (title, layout intent, copy text, specific widgets if named)
- Active project (`./sandbox status` — should be `design-elementor` /
  `design-gutenberg` / a plugin project that includes Elementor)

## Steps

1. **Confirm tools are live.**
   ```
   wp_cli plugin list --status=active
   ```
   Need `elementor` + `essential-addons-for-elementor-lite` (or the Gutenberg
   equivalents). If not, `activate_plugin` them or `./sandbox use
   design-elementor`.

2. **Look for a reusable template** in `runtime/seeds/*.json`. If one fits
   the layout, start from it. Otherwise build the JSON tree from scratch
   following the schema in `skills/designer/SKILL.md`.

3. **Create the page.**
   ```
   wp_rest POST /wp/v2/pages
     body: { "title": "<title>", "status": "publish", "content": "" }
   → note the returned id
   ```

4. **Write the Elementor data.** If the JSON is small, inline it:
   ```
   wp_cli post meta update <id> _elementor_data '<JSON-string>'
   ```
   If large (>30 lines), write it to `runtime/seeds/<title-slug>.json` first
   so future-you can reuse it, then:
   ```
   wp_cli post meta update <id> _elementor_data "$(cat /seeds/<title-slug>.json)"
   ```

5. **Set companion meta.**
   ```
   wp_cli post meta update <id> _elementor_edit_mode builder
   wp_cli post meta update <id> _elementor_template_type wp-page
   wp_cli post meta update <id> _elementor_version 3.0.0
   ```

6. **(Optional) Pick a template.** For full-width designs:
   ```
   wp_cli post meta update <id> _wp_page_template elementor_canvas
   ```

7. **Smoke check.**
   ```
   tail_log 50
   ```
   Should be clean. If there are warnings about a widget, the `widgetType`
   slug is wrong or its settings are malformed.

8. **Report.**
   - Live URL: `http://localhost:8088/?page_id=<id>`
   - Editor:   `http://localhost:8088/wp-admin/post.php?post=<id>&action=elementor`
   - List the sections + widgets created.

9. **Iterate on request.** The user reads the page, says what to change.
   Fetch current data, mutate, write back:
   ```
   wp_cli post meta get <id> _elementor_data > /tmp/d.json
   # edit /tmp/d.json (change widget setting, add/remove element, …)
   wp_cli post meta update <id> _elementor_data "$(cat /tmp/d.json)"
   ```

## Common iteration verbs

| User says | You do |
|---|---|
| "Change the heading to X" | mutate `settings.title` of that widget |
| "Make it 3 columns" | section `settings.structure = "33"`; add a column |
| "Swap the button for an icon-box" | change `widgetType` + reshape `settings` |
| "Duplicate this section" | clone the section dict, give new IDs |
| "Use the testimonial widget instead" | swap `widgetType` to `testimonial` or `eael-testimonials`; map settings |
| "Center everything" | section `settings.content_position = "middle"`; widget `settings.align = "center"` |
| "Add a CTA at the bottom" | append a new section with an `eael-call-to-action` widget |

## Done criteria

- Page renders on its live URL.
- Opens cleanly in the Elementor editor (companion meta correct).
- `tail_log` is clean.
- JSON is saved to `runtime/seeds/` if it's a layout worth reusing.
