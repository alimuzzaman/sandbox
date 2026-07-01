# DesignSpec v1 — the extraction standard

One canonical JSON shape for a reference design, **source-agnostic** (web / png / figma) and
emitted identically by the BUILD so a reference↔build diff is key-for-key. This replaces the
ad-hoc per-session shapes (`{vw,bodyH,sections}` vs `[{k,top,left,w,h,fs,fw,ff}]` vs …) that
drifted every run and made diffing fragile.

## Rules
1. **Same shape from every source and from the build.** Web, PNG, Figma adapters and the build
   extractor all emit DesignSpec v1. Diff = compare two DesignSpec docs.
2. **Normalized keys, no abbreviations.** `font.size` not `fs`; `font.family` not `ff`.
3. **Box-model OWNER is explicit.** Every colored element records whether IT owns its
   background + the padding that gives breathing room (the "Contact us" bug). `box.bgOwner`.
4. **Honest fidelity.** Each spec + element carries `fidelity: full|partial|low`. Web/Figma =
   full; PNG = low (raster can't yield exact padding/margins/line-height).
5. **Units in px, numbers not strings.** Colors as `rgb()/rgba()` (web) or hex (figma), stated in `meta`.

## Schema (informal)
```jsonc
{
  "designspec": "1.0",
  "meta": {
    "source": "web|png|figma",
    "ref": "<url | file path | figma node id>",
    "viewport": {"w": 1280, "h": 900},     // capture width (match ref↔build!)
    "colorFormat": "rgb|hex",
    "fidelity": "full|partial|low",
    "tool": "extract-web.js@1 | extract-png.py@1 | figma-adapter@1"
  },
  "page": {
    "width": 1280, "height": 5924,
    "background": "rgb(255,255,255)",
    "contentMaxWidth": 1240            // centered content cap, or null if full-bleed
  },
  "sections": [{
    "id": "s1",
    "label": "Powerful Features To…",  // landmark heading/text (stable key for diff)
    "top": 178, "height": 294,
    "bgOwner": {                        // the element that CARRIES the background
      "background": "rgb(255,252,237)",
      "padding": "36px 40px 45px 92px",
      "radius": "16px"
    },
    "contentWidth": 1240, "align": "center",
    "columns": [{"width": 660, "gap": 24}],   // immediate layout children + inter-col gap
    "elements": [{
      "kind": "heading|text|button|image|icon|input",
      "text": "Transform Customer Support",   // OR:
      "src": "chataibot-featured-image-001.png",
      "top": 497, "left": 112, "w": 581, "h": 372,
      "font": {"family":"Archivo","size":36,"weight":600,"lineHeight":43.2,
               "letterSpacing":"normal","align":"left","color":"rgb(17,17,17)"},
      "box": {"background":"rgba(0,0,0,0)","padding":"0px","margin":"0px 0px 20px",
              "radius":"0px","border":"none","bgOwner": false},
      "image": {"naturalW":1272,"naturalH":1213,"objectFit":"cover","filter":"none"},
      "states": {"hover": {"background":"…","color":"…","filter":"…"}},  // optional
      "fidelity": "full"
    }]
  }]
}
```

## Source adapters
- **Web → `extract-web.js`** (Playwright `browser_evaluate`). Computed styles from the live DOM;
  `fidelity: full`. THE canonical extractor — run it on the reference AND the build. Override the
  top-level `ROOT` selector when auto-detect picks the wrong content root (EB pages:
  `.eb-fullwidth-content-wrapper`; Elementor: `.elementor`).
- **PNG → `extract-png.py`** (raster hints) + a vision pass. The script yields what a raster can
  give: `page` dims, per-band boundaries (row-luminance deltas) and each band's dominant
  background — as sections with `fidelity: low` and empty `elements`. Then the VISION step (you,
  reading the PNG) fills each section's `elements` (text, kind, approx font size/weight/color,
  approx box) — every field marked `fidelity: low` and every number prefixed "≈". NEVER present
  PNG-derived numbers as exact; they seed the build, then Phase-3 web-diff of the BUILD against
  the real reference (if available) is what proves fidelity. If the reference only exists as a
  PNG, say so and cap the done-gate at "visually matches", not ±2px.
- **Figma → `figma-adapter` (later)** REST `/v1/files/:key/nodes` → map nodes to DesignSpec v1
  (`absoluteBoundingBox`→top/left/w/h, `fills`→background/color, `style`→font.*,
  `cornerRadius`→radius, `paddingLeft…`→box.padding, `effects`→shadow). Richest source,
  `fidelity: full`, `colorFormat: hex`. Needs a Figma token + file key.

## Diff contract
Because reference and build emit the SAME shape, the diff is mechanical:
- **section diff**: match by `label`, compare `height` (±2px) and `bgOwner.{background,padding,radius}`.
- **element diff**: match by `text|src` within a section, compare `top/left/w/h` (dLeft~0, dTop cumulative → fix heights first) and `font.*` / `box.*`.
- **box-owner check**: any element with a background must have `bgOwner:true` on BOTH sides.
See SKILL.md Phase 3–5 for the numeric gate.
