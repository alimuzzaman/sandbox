---
name: Elementor Gutenberg Convert
description: Convert an Elementor page/post/template to Gutenberg OR Gutenberg to Elementor (both directions), preserving content/structure/widget-block identity, routing the output by source shape (templately_library/elementor_library template -> Templately library item; normal page/post -> normal page/post), and proving fidelity with the design-fidelity-diff spec gate. Use when migrating a design between page builders. Covers the widget<->block mapping table both ways, destination routing via Templately's own TemplateFactory/create_page paths, editor-valid block save markup, and the content-lossless/style-lossy law.
---

# Elementor <-> Gutenberg Convert

Convert a page/post/template **between builders** — Elementor -> Gutenberg OR Gutenberg
-> Elementor — preserving content, structure, and widget/block identity, then PROVE
fidelity with the [[design-fidelity-diff]] gate. This skill owns the *mapping* (which
widget becomes which block, both directions) and the *routing* (where the converted
result lands). It does NOT re-implement the numeric fidelity gate — it hands the source
and the converted page to `sb specextract`/`specdiff`/`specgate` (design-fidelity-diff).

Both directions were validated end-to-end on the FlexiGency hero (see Worked Example).

## The one law that governs every conversion

**Content + structure + block/widget IDENTITY convert losslessly. Engine-specific STYLING
does NOT travel by default.** A conversion is content-lossless and style-lossy: text,
heading levels, button labels, image assets, and the layout tree round-trip cleanly, but
any style that lived in the *source engine's* system — Elementor **kit globals**
(`settings.__globals__`, `--e-global-color-*`), the source **theme's CSS classes** (e.g. a
`.text_highlight` span), or Gutenberg **theme.json** tokens — falls back to the
DESTINATION engine's defaults after conversion (measured: FlexiGency's navy buttons + navy
heading came back as the destination Elementor kit's green/blue). So:

1. **Never claim "converted" from a structural map alone.** Re-apply per-element styling on
   the destination, then run `sb specgate <source-spec> <converted-spec>` — the appearance
   gate (color/font/transform/background) is what catches the style loss. A green button
   where the source was navy is a `specgate` FAIL, not "close enough".
2. **Carry the WP attachment ID, not just the URL.** Media survives both directions *iff*
   you key on the attachment id (EL `image:{id,url}` <-> Guten `{"id":N}` + `wp-image-N`).
   The id drives srcset + the editor's media selection; a bare URL renders but loses both.

---

## Step 1 — Detect the SOURCE shape (two axes)

**Axis A — which builder** (drives the mapping direction):
- Elementor: `get_post_meta($id,'_elementor_edit_mode')==='builder'` AND non-empty
  `_elementor_data`.
- Gutenberg: `has_blocks($post->post_content)` / `parse_blocks()` yields named blocks.

**Axis B — template vs normal** (drives the DESTINATION routing, Step 4):
- **Template** (a reusable library item): `post_type` is `templately_library` OR
  `elementor_library`.
- **Normal**: `post_type` is `page` / `post` / any public CPT.

## Step 2 — Extract the source tree

- **Elementor source** -> `sandbox/elementor-get {post_id}` (tree) + read
  `_elementor_data` for full per-widget `settings`. Modern imports are **Container/flex**
  trees (verify `experiments->is_feature_active('container')`), not legacy Section/Column.
- **Gutenberg source** -> `sandbox/gutenberg-get {post_id}` + `parse_blocks($content)` for
  each block's `attrs` + `innerHTML`/`innerContent`.
- Snapshot the source as a DesignSpec NOW so you can gate later:
  `sb specextract <source-front-url> --out source.json` ([[design-fidelity-diff]]).

## Step 3 — Map, node by node (the tables)

Prefer **portable core blocks <-> core Elementor widgets** so the result has no plugin
dependency. When both WPDeveloper libraries are present, the **Essential Addons (`eael-*`)
<-> Essential Blocks (`essential-blocks/*`) sister components** give the highest-fidelity
map (same team, near-identical controls) — use them when the source used them.

### Elementor -> Gutenberg
| Elementor | Gutenberg (core) | Notes |
|---|---|---|
| `container` (flex) | `core/group` `{layout:{type:"flex",orientation:"vertical\|horizontal"}}` | map `flex_direction`->orientation, `flex_gap`->blockGap, `flex_align_items`->justify |
| `container` (grid) / legacy `section`+`column` | `core/columns` > `core/column` | one column block per source column |
| `heading` (`header_size`) | `core/heading` `{level:N}` | `h5`->`level:5`; `title` HTML (incl. inline `<span>`) copies verbatim into innerHTML |
| `text-editor` | `core/paragraph` | `editor` HTML -> innerHTML |
| `button` | `core/button` inside `core/buttons` | `text`->label; color -> see gotcha 1 |
| `image` | `core/image` `{id:N}` | carry `image.id`; emit `wp-image-N` class + url |
| `icon-list` | `core/list` > `core/list-item` | icons drop unless you use EB `essential-blocks/advanced-list` |
| `eael-*` (EA) | `essential-blocks/*` sister | 1:1 when EB active; else core + custom CSS |

### Gutenberg -> Elementor
| Gutenberg | Elementor | Notes |
|---|---|---|
| `core/group` (flex) | `container` | orientation->`flex_direction`, blockGap->`flex_gap`, justify->`flex_align_items` |
| `core/columns` | `container` (row) > `container` per `core/column` | |
| `core/heading` `{level}` | `heading` `header_size:"h{level}"` | innerHTML text -> `title` (keeps inline spans) |
| `core/paragraph` | `text-editor` (`editor`) | |
| `core/buttons`/`core/button` | `container` (row) > `button` widgets | |
| `core/image` `{id}` | `image` `{image:{id,url}}` | resolve url via `wp_get_attachment_url(id)` |
| `core/list` | `icon-list` (or `text-editor`) | |
| `essential-blocks/*` (EB) | `eael-*` sister | 1:1 when EA active |

## Step 4 — Build on the destination + ROUTE by source shape

Route the OUTPUT by the Axis-B classification from Step 1, reusing **Templately's own
paths** so companion content / library types behave exactly like a normal Templately import:

- **Source was a TEMPLATE** (`templately_library`/`elementor_library`) -> land a
  **library item**:
  `new TemplateFactory('<dest-platform>')->create($type, ['post_type'=>'templately_library', ...])`
  then `$template->import(['content'=>$destContent, 'import_settings'=>['type'=>$type,...]])`
  (the plugin's `import_in_library` path). Dest platform = `gutenberg` when converting
  EL->Guten, `elementor` when Guten->EL. **`$type` must be a REGISTERED Builder type**
  (`get_template_types()` keys: `header footer single archive post page error page_single
  ...` — there is NO `section`/`block` key). `Platform::resolve_library_type()` maps an API
  `template_type` slug to one of these but is **`protected static`** — NOT callable from
  eval/global scope (gotcha 7); instead read the source's `_templately_template_type` meta,
  map the API `template_type`, or pass a sensible registered key (`page_single`). For a
  Gutenberg dest, `import()` writes the block markup straight to `post_content`; the factory
  sets `_templately_template_platform`/`_templately_template_type` meta for you (verified:
  dest post came out `templately_library`, platform `gutenberg`, type `page_single`).
- **Source was a NORMAL page/post** -> land a **normal `page`/`post`**:
  - EL destination: `Importer\Elementor::create_page($template_data)` (needs
    `set_edit_mode(true)` + a current user), OR build the container tree and
    `Document::save(['elements'=>$tree])`.
  - Guten destination: `wp_insert_post(['post_type'=>'page','post_content'=>$blockMarkup])`
    (or `Platform\Gutenberg::create_page(...)`).

**Writing the destination tree via the sandbox abilities** (portable, no plugin internals):
`sandbox/elementor-insert` / `sandbox/gutenberg-insert`. For Elementor, injecting the whole
container tree via `_elementor_data` + `Document::save` is far fewer calls than per-widget
inserts ([[elementor-page-data-injection-recipe]]).

## Step 5 — Validate in the REAL editor, then GATE

1. **Open the converted page in its editor** (`visit` the edit URL) and read the console.
   A `core/*` conversion that renders on the front end can still be **editor-invalid** —
   see gotcha 1. Fix until zero "Block validation failed".
2. **Re-apply styling** lost per the One Law (colors, typography, backgrounds, radii).
3. **Gate**: `sb specextract <converted-front-url> --out conv.json` then
   `sb specgate source.json conv.json`. The appearance gate FAILing on color/font is the
   expected signal that Step 2's styling re-application is incomplete — chase it there, not
   in the mapping. A green `specgate` + a side-by-side eyeball = done. [[design-fidelity-diff]]

---

## Measured gotchas (from the FlexiGency worked example)

1. **Core static blocks must byte-match their `save()` — an inline style needs its backing
   attribute.** Emitting `<a ... style="color:#091439">` on a `core/button` WITHOUT the
   matching `{"style":{"color":{"text":"#091439"}}}` attr + `has-text-color` class ->
   `Block validation failed ... recovery` in the editor (renders on front end, breaks in
   editor). Encode every style as its block attribute so the serialized markup equals the
   block's `save()` output. When you can't hand-author valid save markup (complex/static EB
   blocks), route through the **EB finalizer** ([[gutenberg-eb]]) instead of raw insert.
2. **Styling doesn't travel — only content/structure/identity do** (the One Law). FlexiGency's
   `__globals__`-referenced button/heading colors + the theme's `.text_highlight` span color
   all reverted to the destination kit's defaults. Re-apply on the destination + gate.
3. **Inline HTML inside a heading round-trips as TEXT but loses its CSS.** EL `heading.title`
   holds raw HTML; `core/heading` holds it in innerHTML. `<span class="text_highlight">`
   survives both ways as markup — but the class's stylesheet does not travel (gotcha 2).
4. **Media localization is PATH-DEPENDENT — the library-import path leaves remote URLs +
   FOREIGN ids.** The normal-page import (`create_page`) side-loads images to the local media
   library (post 51's ids were local + valid). The **library import (`import_in_library` /
   `ElementorImporter::import_in_library`) does NOT** — the source keeps
   `demo.assets.templately.com/...` URLs and attachment ids from the ORIGIN site (e.g. 859,
   875, 987) that don't exist locally. Consequences for the converter (measured, twice
   broken): (a) `wp_get_attachment_url($foreign_id)` returns **false** -> you emit
   `<img src="">` -> broken image + `do_blocks()` renders zero `<img>`. (b) carrying the id
   alone is useless when the id is foreign. FIX: carry BOTH `{id,url}` from the source widget;
   if `get_post($id)` is missing locally, `media_sideload_image($url,$destPost,null,'id')` to
   mint a REAL local id, then write the local id (`{"id":N}` + `wp-image-N`) AND the local
   `url` into the block `src`. Verify via DOM (`do_blocks` emits the `<img src>`), not the
   editor placeholder.
5. **`Document::save()` silently no-ops without a current user** — call
   `\Elementor\Plugin::$instance->editor->set_edit_mode(true)` + `wp_set_current_user(1)`,
   then after save regen CSS: `delete_post_meta($id,'_elementor_css')` +
   `\Elementor\Core\Files\CSS\Post::create($id)->update()`. [[elementor-save-needs-current-user]]
6. **Import a real reference via Templately's own dev API** (when the instance is dev-connected,
   `TEMPLATELY_DEV_API` true): search with the GraphQL `packs`/`pages`/`items` operations
   (the op name IS the type), fetch with `itemContent`, then drive
   `Importer\Elementor::get_data()` + `create_page()` directly (the `Platform` wrapper
   re-fetches from its own args, so call the importer). FlexiGency Landing = dev item `6090`.
7. **`ElementorImporter::import_in_library` lands the source in Elementor's NATIVE
   `elementor_library` CPT** (via `Source_Local`), NOT `templately_library`, and leaves the
   `_templately_template_*` meta EMPTY. Axis B still classifies it a *template* (both CPTs
   count). But since there's no Templately type meta to read, pick the dest `$type` from the
   API `template_type` or a registered key. Contrast: the Gutenberg `TemplateFactory` lands in
   `templately_library` WITH the platform/type meta set. The two builders' library homes differ.
8. **Malformed HTML in source rich-text fails core block validation — normalize it.** A source
   heading/text title can hold HTML Elementor tolerates but core's `save()` rewrites — measured:
   a heading title `...business</span></br> growth` (an invalid `</br>` close tag). `core/heading`
   `save()` emits the canonical `<br>`, so the stored markup mismatches -> `Block validation
   failed`. Before emitting, normalize break tags (`</br>`/`<br/>`/`<br />` -> `<br>`) and generally
   sanitize source rich-text to the exact HTML core would serialize (same class as gotcha 1).
9. **EA/dynamic widgets have NO core-block twin — DECOMPOSE, don't drop.** With EB inactive,
   `eael-info-box` -> `core/group{ image + heading(title,tag) + paragraph(text) + button }`,
   `eael-testimonial` -> `group{ image(avatar) + paragraph(quote) + heading(name) + paragraph
   (company) }`, `counter` -> `heading(number+suffix) + paragraph(title)`, `image-gallery` ->
   `core/gallery` of `core/image`. A truly dynamic widget (`form`) has no static equivalent ->
   emit an HONEST placeholder group (heading + note) and surface it, never a silent drop. Read
   the EA content keys first (`eael_infobox_title`/`_text`/`_image`, `eael_testimonial_name`/
   `_description`/`_company_title`, counter `ending_number`/`suffix`/`title`).

---

## Worked example (evidence, on instance `templately-rebuild2`)

NORMAL page -> page branch (hero):
- **51** — `page`, FlexiGency Landing imported as **Elementor** via Templately's own importer
  (Container/flex tree: heading eyebrow, H1 w/ highlight span, 2 buttons, 2 images).
- **54** — `page`, hero **converted EL -> Gutenberg** (core group/heading/buttons/image),
  editor-valid after the gotcha-1 button fix.
- **57** — `page`, hero **converted Gutenberg -> Elementor** (container + heading/button/image
  widgets); content/structure faithful, styling fell back to the dest kit (the One Law).

TEMPLATE -> library branch (CTA section, exercises text-editor + icon-list + 3 images):
- **59** — `elementor_library`, FlexiGency CTA imported via `import_in_library` (empty
  Templately meta, remote demo image URLs w/ foreign ids — gotchas 4 & 7).
- **62** — `templately_library`, **converted EL template -> Gutenberg library item** via
  `TemplateFactory('gutenberg')->create('page_single',...)->import(...)`
  (heading->h4, text-editor->paragraph, 3 images, icon-list->list); editor-valid, platform
  meta `gutenberg`. Images required explicit `media_sideload_image` localization (gotcha 4).

FULL 10-section page (EL -> Gutenberg, the real-scale run):
- **69** — `page`, entire FlexiGency Landing (10 sections, 76 EL containers, depth 5)
  **converted EL -> Gutenberg** with a single recursive walk (container->group preserving
  nesting/orientation). Output: 87 groups, 41 images (0 empty src — all localized), 33
  headings, 29 paragraphs, 12 buttons, 2 lists, 1 gallery. EA widgets decomposed per gotcha 9
  (`eael-info-box`x12, `eael-testimonial`x6, `counter`x2, `image-gallery`, `form`->placeholder).
  Editor-valid after normalizing one `</br>` (gotcha 8). Content/structure faithful; theme
  (not the EL kit) styles it — the One Law.

---

## Reference — the converter cores (both directions)

Read the source tree, map per the tables, build on the destination. These ran on the
worked example (run via `wp_eval_live`).

**Gutenberg -> Elementor** (parse blocks -> EL container tree -> `Document::save`):
```php
wp_set_current_user(1); \Elementor\Plugin::$instance->editor->set_edit_mode(true);
$hex = fn() => substr(bin2hex(random_bytes(4)),0,7);
$inner = function($b){ $h=''; foreach($b['innerContent'] as $c) if(is_string($c)) $h.=$c;
  return preg_match('/<(h[1-6]|a)[^>]*>(.*?)<\/\1>/s',$h,$m)?$m[2]:trim(wp_strip_all_tags($h)); };
$W=[]; $walk=function($bs) use(&$walk,&$W,$hex,$inner){ foreach($bs as $b){ switch($b['blockName']){
  case 'core/heading': $s=['title'=>$inner($b),'header_size'=>'h'.($b['attrs']['level']??2)];
    if(($b['attrs']['textAlign']??'')!=='') $s['align']=$b['attrs']['textAlign'];
    $W[]=['id'=>$hex(),'elType'=>'widget','widgetType'=>'heading','settings'=>$s,'elements'=>[]]; break;
  case 'core/button': $s=['text'=>$inner($b)];
    if(isset($b['attrs']['style']['color']['text'])) $s['button_text_color']=$b['attrs']['style']['color']['text'];
    $W[]=['id'=>$hex(),'elType'=>'widget','widgetType'=>'button','settings'=>$s,'elements'=>[]]; break;
  case 'core/image': $id=$b['attrs']['id']??0;
    $W[]=['id'=>$hex(),'elType'=>'widget','widgetType'=>'image','settings'=>['image'=>['id'=>$id,'url'=>wp_get_attachment_url($id)]],'elements'=>[]]; break;
} if(!empty($b['innerBlocks'])) $walk($b['innerBlocks']); } };
$walk(parse_blocks(get_post($SRC)->post_content));
$container=['id'=>$hex(),'elType'=>'container','settings'=>['content_width'=>'boxed','flex_direction'=>'column','flex_align_items'=>'center','flex_gap'=>['size'=>20,'unit'=>'px']],'elements'=>$W];
$new=wp_insert_post(['post_type'=>'page','post_status'=>'publish','post_title'=>'Converted to Elementor']);
update_post_meta($new,'_elementor_edit_mode','builder'); update_post_meta($new,'_wp_page_template','elementor_canvas');
$doc=\Elementor\Plugin::$instance->documents->get($new); $doc->save(['elements'=>[$container]]);
delete_post_meta($new,'_elementor_css'); \Elementor\Core\Files\CSS\Post::create($new)->update();
```

**Elementor -> Gutenberg**: read `_elementor_data`, walk widgets, emit block markup. Emit
each style as its block ATTRIBUTE (gotcha 1), e.g. a custom-color button:
`<!-- wp:button {"style":{"color":{"text":"#091439"}},"className":"is-style-outline"} -->`
`<div class="wp-block-button is-style-outline"><a class="wp-block-button__link has-text-color wp-element-button" style="color:#091439">Book a Call</a></div>`
`<!-- /wp:button -->`. Then `wp_insert_post(['post_content'=>$markup])` and open the editor to
confirm zero validation failures.
