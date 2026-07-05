/**
 * extract-figma.js — Figma node JSON → DesignSpec v1 (see DESIGNSPEC.md).
 *
 * The THIRD source extractor, parallel to extract-web.js and extract-png.py.
 *
 * THIS ADAPTER IS FOR THE FIGMA **REST** PATH — full node JSON with
 * `absoluteBoundingBox`, `fills`, `style`, `cornerRadius`, `paddingLeft`…:
 *   Figma REST → GET /v1/files/:key/nodes?ids=<id> returns
 *   `{ nodes: { "<id>": { document: <node> } } }` — pass `.document` here.
 *
 * The Figma **desktop / Dev-Mode MCP** path does NOT return this shape:
 * `get_metadata` gives geometry-only XML (id/type/name/x/y/w/h — no fills/style),
 * and styles come from `get_design_context` / `get_variable_defs`. For that path
 * assemble DesignSpec by merging geometry + styles (see DESIGNSPEC.md), not via
 * this function. Point the desktop metadata at ONE page frame, not a whole board.
 *
 * This is a PURE function (no network, no DOM) — run it in node, or paste it
 * into any JS context and feed it the node JSON you already fetched. It emits
 * `fidelity:"full"` and `colorFormat:"hex"` per the DesignSpec rules.
 *
 * Usage:
 *   const { figmaToDesignSpec } = require('./extract-figma.js');
 *   const spec = figmaToDesignSpec(frameNode, { ref: '1:17295', viewport: {w,h} });
 *
 * The FRAME you pass is the page root (the top-level frame of the design node).
 * Its direct children become `sections`; leaf text/image/vector nodes inside a
 * section become that section's `elements`. Coordinates are normalised to the
 * frame origin so `top`/`left` match the document-relative coords web emits.
 */

// --- color: Figma {r,g,b,a in 0..1} → #rrggbb / rgba() ---------------------
function toHex(c, opacity) {
  if (!c) return null;
  const a = (c.a == null ? 1 : c.a) * (opacity == null ? 1 : opacity);
  const h = n => Math.round(n * 255).toString(16).padStart(2, '0');
  const base = '#' + h(c.r) + h(c.g) + h(c.b);
  // Fully opaque → hex; otherwise rgba() so the alpha is not silently dropped.
  return a >= 0.999 ? base : `rgba(${Math.round(c.r*255)},${Math.round(c.g*255)},${Math.round(c.b*255)},${+a.toFixed(3)})`;
}

// First VISIBLE solid fill's color (skips hidden fills, gradients, images).
function solidFill(node) {
  const f = (node.fills || []).find(x => x.visible !== false && x.type === 'SOLID');
  return f ? toHex(f.color, f.opacity) : null;
}

// First VISIBLE image fill → { imageRef, scaleMode }.
function imageFill(node) {
  const f = (node.fills || []).find(x => x.visible !== false && x.type === 'IMAGE');
  return f ? { imageRef: f.imageRef, scaleMode: f.scaleMode } : null;
}

const FIT = { FILL: 'cover', FIT: 'contain', TILE: 'repeat', STRETCH: 'fill' };

// A CSS-ish shorthand from Figma auto-layout padding (0 when not auto-layout).
function padding(node) {
  const t = node.paddingTop|0, r = node.paddingRight|0, b = node.paddingBottom|0, l = node.paddingLeft|0;
  if (!(t||r||b||l)) return '0px';
  return `${t}px ${r}px ${b}px ${l}px`;
}

function border(node) {
  const s = (node.strokes || []).find(x => x.visible !== false && x.type === 'SOLID');
  if (!s) return 'none';
  const w = node.strokeWeight || 1;
  return `${w}px solid ${toHex(s.color, s.opacity)}`;
}

// kind: use the node name as a strong hint (designers name buttons "Button",
// icons "icon/…"), fall back to geometry/type. Every guess is still measured
// against the build in Phase 3 — this only seeds the build.
function classify(node) {
  const name = (node.name || '').toLowerCase();
  if (node.type === 'TEXT') {
    if (/button|cta|btn/.test(name)) return 'button';
    // heading vs body: big or bold display type reads as a heading.
    const sz = node.style && node.style.fontSize || 0;
    const wt = node.style && node.style.fontWeight || 400;
    return (sz >= 24 || (wt >= 600 && sz >= 18)) ? 'heading' : 'text';
  }
  if (/button|cta|btn/.test(name)) return 'button';
  if (/icon|logo/.test(name) || node.type === 'VECTOR' || node.type === 'BOOLEAN_OPERATION') return 'icon';
  if (imageFill(node) || node.type === 'IMAGE') return 'image';
  if (/input|field|search/.test(name)) return 'input';
  return null; // container / decorative — skipped by isLeafElement
}

// A node counts as a spec element if it renders content: text, an image fill,
// a vector/icon, or a named button/input. Pure layout frames are traversed
// (their children become elements) but are not themselves elements.
function isElement(node) {
  return classify(node) != null;
}

function fontOf(node) {
  const s = node.style;
  if (!s) return undefined;
  return {
    family: s.fontFamily,
    size: s.fontSize,
    weight: s.fontWeight,
    lineHeight: s.lineHeightPx != null ? +s.lineHeightPx.toFixed(1) : undefined,
    letterSpacing: s.letterSpacing ? `${+s.letterSpacing.toFixed(2)}px` : 'normal',
    align: (s.textAlignHorizontal || 'LEFT').toLowerCase(),
    color: solidFill(node),
  };
}

function boxOf(node) {
  const bg = solidFill(node);
  const pad = padding(node);
  // bgOwner: this node BOTH carries a background AND gives its content breathing
  // room (padding) — the box-model-owner check from Law 2 / the "Contact us" bug.
  const bgOwner = !!bg && pad !== '0px';
  return {
    background: bg || 'rgba(0,0,0,0)',
    padding: pad,
    margin: '0px', // Figma has no margin; spacing comes from auto-layout gap/position
    radius: (node.cornerRadius || 0) + 'px',
    border: border(node),
    bgOwner,
  };
}

function elementOf(node, origin) {
  const bb = node.absoluteBoundingBox || {};
  const kind = classify(node);
  const el = {
    kind,
    top: Math.round((bb.y || 0) - origin.y),
    left: Math.round((bb.x || 0) - origin.x),
    w: Math.round(bb.width || 0),
    h: Math.round(bb.height || 0),
    box: boxOf(node),
    fidelity: 'full',
  };
  if (node.type === 'TEXT') { el.text = node.characters || ''; el.font = fontOf(node); }
  const img = imageFill(node);
  if (img) {
    el.src = img.imageRef; // Figma image hash; resolve to a real asset URL separately
    el.image = { naturalW: null, naturalH: null, objectFit: FIT[img.scaleMode] || 'cover', filter: 'none' };
  }
  return el;
}

// Walk a section subtree and collect its leaf elements (skips the section node
// itself; descends through layout frames).
function collectElements(node, origin, out, depth) {
  for (const child of node.children || []) {
    if (child.visible === false) continue;
    if (isElement(child)) out.push(elementOf(child, origin));
    // still descend: a button frame can wrap a TEXT + icon we also want, and a
    // layout frame holds the real content.
    if (child.children && depth < 8) collectElements(child, origin, out, depth + 1);
  }
  return out;
}

// A stable, human landmark for diffing: first text node's characters, else name.
function sectionLabel(node) {
  const stack = [...(node.children || [])];
  while (stack.length) {
    const n = stack.shift();
    if (n.type === 'TEXT' && n.characters) return n.characters.replace(/\s+/g, ' ').trim().slice(0, 40);
    if (n.children) stack.push(...n.children);
  }
  return node.name || 'section';
}

function figmaToDesignSpec(frame, meta = {}) {
  const bb = frame.absoluteBoundingBox || { x: 0, y: 0, width: 0, height: 0 };
  const origin = { x: bb.x, y: bb.y };
  const pageBg = solidFill(frame)
    || (frame.backgroundColor ? toHex(frame.backgroundColor) : null)
    || (frame.backgrounds && frame.backgrounds[0] && toHex(frame.backgrounds[0].color, frame.backgrounds[0].opacity))
    || 'rgba(0,0,0,0)';

  const sections = (frame.children || [])
    .filter(c => c.visible !== false)
    .map((c, i) => {
      const cb = c.absoluteBoundingBox || {};
      return {
        id: 's' + (i + 1),
        label: sectionLabel(c),
        top: Math.round((cb.y || 0) - origin.y),
        height: Math.round(cb.height || 0),
        bgOwner: {
          background: solidFill(c) || 'rgba(0,0,0,0)',
          padding: padding(c),
          radius: (c.cornerRadius || 0) + 'px',
        },
        contentWidth: Math.round(cb.width || 0),
        align: 'center',
        columns: [], // auto-layout children + gap can be filled here if needed
        elements: collectElements(c, origin, [], 0),
      };
    });

  return {
    designspec: '1.0',
    meta: {
      source: 'figma',
      ref: meta.ref || frame.id || '',
      viewport: meta.viewport || { w: Math.round(bb.width), h: Math.round(bb.height) },
      colorFormat: 'hex',
      fidelity: 'full',
      tool: 'extract-figma.js@1',
    },
    page: {
      width: Math.round(bb.width),
      height: Math.round(bb.height),
      background: pageBg,
      contentMaxWidth: null, // Figma frames are fixed-width; no centered cap to infer
    },
    sections,
  };
}

if (typeof module !== 'undefined') module.exports = { figmaToDesignSpec, toHex };
