// extract-web.js@6 — canonical DesignSpec v1 web extractor.
// Paste the FUNCTION BODY into Playwright browser_evaluate (run on reference AND build).
// Force-load images first (see SKILL Reference A). Override ROOT if section detection is wrong.
//
//   window.__DS_ROOT = '.eb-fullwidth-content-wrapper';  // EB demo (or '.elementor' for a build)
//
// Returns a DesignSpec v1 object (meta.source='web', fidelity='full').
// v2: captures FULL backgrounds — color + gradient + image(url) + size/position, on the element
// AND its ::before/::after, plus a per-section `decor` list of decorative background layers
// (the reason flat rebuilds "don't match": EB/Elementor put the real bg on an inner wrapper, a
// pseudo-element, or an absolutely-positioned object PNG — never just backgroundColor).
// v3: adds a top-level `fonts:[{family,weight,loaded}]` block (document.fonts.check per used
// family) so `sb specgate` can gate silent webfont fallback offline. Feed the reference AND
// build JSON to `sb specdiff` / `sb specgate` (tools/dfdiff) for the Phase-3/5 numeric diff.
// v4: font.style (italic) + font.transform (text-transform) so specdiff gates APPEARANCE, not
// just geometry (a hero can box-match to ±2px yet render wrong color/case/italic — SKILL corollary).
// v5: adds `li` to the tag scan + a LEAF-NODE div/span rule (any div/span with zero child
// ELEMENTS and non-trivial text). Some widgets (EAAL/EB pricing tables) render price/feature-list
// content in bare <li>/<div> tags with no semantic wrapper — invisible to the v1-v4 tag list
// (h1-h6,p,a,button,img,input), so a build with COMPLETELY WRONG price/feature text ($99 vs $49,
// "Unlimited calls" vs "Customization Options") produced ZERO missing/extra element findings —
// the diff had no way to see the content existed at all. Verified: this was the actual root cause
// of a content-swap bug that only vision caught, not `sb specdiff`.
// v6: contentMaxWidth is now GEOMETRIC (descend single-child wrapper chains until the width
// stops matching the section), not class-name-based. The old selector
// ('.e-con-inner,.elementor-container,[class*="container"]') only matches Elementor markup — on
// a Gutenberg/EB page nothing matched, so it silently fell back to measuring the OUTER full-width
// section instead of the real content wrapper, reporting a reference's true ~1240px content width
// as ~1280px. Verified: this produced a spurious "content-width delta -40px" finding on a build
// that was already pixel-identical to the reference — an entire investigation chasing a defect
// that didn't exist, because one side of the comparison was measured wrong.
// v7: ROOT auto-detection now EXCLUDES header/footer/nav chrome and picks the TALLEST remaining
// candidate, not the FIRST DOM match. `document.querySelector('main, .elementor, .site-main,
// #content')` returns the first match in document order — but any page whose header/footer are
// built with an Elementor/EB theme-builder template renders THREE separate top-level roots
// (`<header class="elementor …">`, a `<div class="elementor …">` for the body content, `<footer
// class="elementor …">`), all matching `.elementor`, with the header coming FIRST in the DOM.
// Verified on lms.elementor.templately.com/womeninlead/: old logic picked the 93px `<header>` as
// ROOT, so the whole 6479px body content (13 real sections) was invisible to the extractor —
// `sections.length===1` with 13 elements total, when the real page has far more. Fixed by
// filtering candidates to those NOT inside (or themselves) `header/footer/nav/[role=banner|
// contentinfo|navigation]`, then picking the max-height survivor; falls back to the old
// first-match behavior if every candidate is chrome (should not happen in practice).
// v8: the single-child DESCEND loop now ignores near-zero-height siblings (<=30px) when
// deciding whether to keep descending, instead of requiring EXACTLY one child. A standard WP
// block-theme template renders `<!-- wp:post-title /--> <!-- wp:post-content /-->` as TWO
// siblings under the same wrapper — if the page hides the title for a builder-style page (e.g.
// `display:none` on `.wp-block-post-title`, a common "no theme chrome" pattern for a rebuilt
// design), that hidden H1 still counts as a DOM child with 0 height, so the old `children.length
// === 1` check saw 2 children and stopped descending ONE LEVEL TOO EARLY — treating [hidden-H1,
// entry-content] as the final `sections` array (1 "real" section after the height>30 filter)
// instead of continuing into entry-content's own 9 real top-level blocks. Verified while building
// a Gutenberg rebuild: `main`'s single child (a wrapper group) had 2 children (0px H1 + 5327px
// `.entry-content`); the fix descends past the 0px sibling into entry-content, correctly finding
// all 9 top-level sections (nav, hero, 6 body sections, footer) instead of reporting 1.
//
// KNOWN LIMITATION (not yet fixed — flagged for a future v9): `elemSpec()`'s font.color always
// reads `getComputedStyle(el).color` on the OUTER heading/text element. When a rich-text widget
// (EB's "advanced-heading" and similar) wraps ALL visible text in per-run child <span>s for
// word-level color/highlight control, the outer element's own `color` is a fallback that never
// actually renders — verified a heading measuring `color:rgb(17,17,17)` (black) via this method
// while every visible glyph was actually white (a child `<span class="first-title">` with its own
// `color:rgb(255,255,255)`, confirmed via `document.elementFromPoint()` on the glyph pixels).
// This produces a false-positive "wrong color" appearance finding. If you hit a suspicious color
// mismatch on a heading, verify with `elementFromPoint` on an actual glyph before trusting
// `elemSpec`'s reported color; don't "fix" a build that already matches the reference.

() => {
  const r2 = n => Math.round(n);
  const cs = (el, p) => getComputedStyle(el, p || null);
  const box = el => el.getBoundingClientRect();
  const num = v => { const n = parseFloat(v); return Number.isFinite(n) ? n : v; };
  const txt = el => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const fnUrl = s => s.replace(/^url\(["']?/, '').replace(/["']?\)$/, '').split('/').pop().split('?')[0];

  // ---- background parsing (the v2 core) -----------------------------------
  // Split a computed background-image into url() filenames + gradient presence (robust to
  // nested parens from rgb()/gradients — matches url(...) globally, flags any *-gradient().
  const parseBgImage = bi => {
    if (!bi || bi === 'none') return { images: [], hasGradient: false, raw: null };
    const images = (bi.match(/url\([^)]*\)/g) || []).map(fnUrl);
    return { images, hasGradient: /gradient\(/.test(bi), raw: bi.slice(0, 140) };
  };
  const colorVisible = c => c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent';
  // full background descriptor for an element+pseudo, or null if nothing paints
  const bgOf = (el, p) => {
    const c = cs(el, p);
    const { images, hasGradient, raw } = parseBgImage(c.backgroundImage);
    const hasColor = colorVisible(c.backgroundColor);
    if (!hasColor && !hasGradient && !images.length) return null;
    return {
      color: hasColor ? c.backgroundColor : null,
      gradient: hasGradient ? raw : null,
      image: images[0] || null,
      size: c.backgroundSize, position: c.backgroundPosition, repeat: c.backgroundRepeat,
    };
  };
  const hasVisibleBg = el => bgOf(el, '') || bgOf(el, '::before') || bgOf(el, '::after');

  // ---- content root + top-level sections ----------------------------------
  const ROOT = (typeof window.__DS_ROOT === 'string' && document.querySelector(window.__DS_ROOT))
    || (() => {
      const SEL = 'main, .elementor, .site-main, #content';
      const isChrome = el => el.matches('header, footer, nav, [role="banner"], [role="contentinfo"], [role="navigation"]')
        || !!el.closest('header, footer, nav, [role="banner"], [role="contentinfo"], [role="navigation"]');
      const all = [...document.querySelectorAll(SEL)];
      const nonChrome = all.filter(el => !isChrome(el));
      let root = (nonChrome.length ? nonChrome : all)
        .reduce((best, el) => (!best || box(el).height > box(best).height) ? el : best, null)
        || document.body;
      // Descend past wrapper levels that have exactly one SUBSTANTIAL child (>30px — the same
      // threshold the section filter below uses), ignoring near-zero-height siblings (a hidden
      // post-title, a collapsed spacer) so a real single content wrapper one level down isn't
      // missed just because a invisible sibling technically exists in the DOM.
      for (let guard = 0; guard < 10; guard++) {
        const kids = [...root.children];
        const substantial = kids.filter(c => box(c).height > 30);
        if (substantial.length !== 1) break;
        if (box(substantial[0]).height <= 200) break;
        root = substantial[0];
      }
      return root;
    })();
  const sections = [...ROOT.children].filter(c => box(c).height > 30);

  const CHROME = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT']);

  // the element that OWNS the visible background of a band: the largest box (or pseudo) that
  // paints ANY background (color OR gradient OR image), else the band itself.
  const bgOwnerOf = sec => {
    let best = null, bestArea = -1, bestBg = null;
    const consider = (el) => {
      const bg = hasVisibleBg(el); if (!bg) return;
      const b = box(el); const a = b.width * b.height;
      if (b.width > 80 && b.height > 40 && a > bestArea) { best = el; bestArea = a; bestBg = bg; }
    };
    consider(sec);
    sec.querySelectorAll('*').forEach(consider);
    if (!best) return { background: 'rgba(0,0,0,0)', gradient: null, image: null, padding: '0px', radius: '0px' };
    const c = cs(best);
    return { background: bestBg.color || 'rgba(0,0,0,0)', gradient: bestBg.gradient, image: bestBg.image,
             backgroundSize: bestBg.size, backgroundPosition: bestBg.position,
             padding: c.padding, radius: c.borderRadius };
  };

  // decorative background layers: any element/pseudo carrying a background-IMAGE or a gradient
  // that isn't the main bgOwner (object PNGs, mesh gradients, grid patterns, absolute <img>).
  const decorOf = sec => {
    const out = []; const seen = new Set();
    const push = (el, p, bg) => {
      const b = box(el); if (b.width < 32 || b.height < 32) return;
      const k = (bg.image || bg.gradient || '') + b.width + 'x' + b.height + (p || '');
      if (seen.has(k)) return; seen.add(k);
      out.push({ src: bg.image, gradient: !!bg.gradient, pseudo: p || null,
                 position: cs(el).position, top: r2(b.top + scrollY), left: r2(b.left),
                 w: r2(b.width), h: r2(b.height) });
    };
    const scan = el => { for (const p of ['', '::before', '::after']) { const bg = bgOf(el, p);
      if (bg && (bg.image || bg.gradient)) push(el, p, bg); } };
    scan(sec); sec.querySelectorAll('*').forEach(scan);
    // absolutely-positioned decorative flow images
    sec.querySelectorAll('img').forEach(im => { const c = cs(im);
      if (c.position === 'absolute' || c.position === 'fixed') {
        const b = box(im); if (b.width > 20) out.push({ src: (im.currentSrc || im.src).split('/').pop(),
          gradient: false, pseudo: null, position: c.position, top: r2(b.top + scrollY),
          left: r2(b.left), w: r2(b.width), h: r2(b.height) }); } });
    return out.slice(0, 24);
  };

  const kindOf = el => {
    const t = el.tagName;
    if (t === 'IMG' || t === 'SVG') return 'image';
    if (t === 'A' || t === 'BUTTON') return 'button';
    if (/^H[1-6]$/.test(t)) return 'heading';
    if (t === 'INPUT' || t === 'TEXTAREA') return 'input';
    if (el.querySelector && el.querySelector('svg') && txt(el).length < 3) return 'icon';
    return 'text';
  };

  const elemSpec = el => {
    const b = box(el), c = cs(el), isImg = el.tagName === 'IMG';
    const ownBg = bgOf(el, '');
    const o = {
      kind: kindOf(el),
      top: r2(b.top + scrollY), left: r2(b.left), w: r2(b.width), h: r2(b.height),
      font: {
        family: c.fontFamily.split(',')[0].replace(/["']/g, ''),
        size: num(c.fontSize), weight: num(c.fontWeight),
        style: c.fontStyle, transform: c.textTransform,
        lineHeight: num(c.lineHeight), letterSpacing: c.letterSpacing,
        align: c.textAlign, color: c.color,
      },
      box: {
        background: c.backgroundColor,
        backgroundImage: ownBg && ownBg.image ? ownBg.image : null,
        backgroundGradient: ownBg && ownBg.gradient ? ownBg.gradient : null,
        padding: c.padding, margin: c.margin, radius: c.borderRadius,
        border: c.borderStyle === 'none' ? 'none' : `${c.borderWidth} ${c.borderStyle} ${c.borderColor}`,
        bgOwner: !!(ownBg && (ownBg.color || ownBg.gradient || ownBg.image) && c.padding !== '0px'),
      },
      fidelity: 'full',
    };
    if (isImg) {
      o.src = (el.currentSrc || el.src || '').split('/').pop();
      o.image = { naturalW: el.naturalWidth, naturalH: el.naturalHeight, objectFit: c.objectFit, filter: c.filter };
    } else {
      o.text = txt(el).slice(0, 80);
    }
    return o;
  };

  // v6: GEOMETRIC content-wrapper finder (see the `contentMaxWidth` note near `page` below) —
  // descend single-child wrapper chains from `sec` until width stops matching the section, no
  // class-name guessing. Used for BOTH the page-level `contentMaxWidth` and each section's own
  // `contentWidth`/`columns` — the old class-based selector
  // ('.elementor-container, [class*="container"], [class*="row"]') never matched Gutenberg/EB
  // markup, silently falling back to the section itself and over-reporting content width by the
  // side-padding amount on every non-Elementor page.
  // v9 fix: the loop used to unconditionally do `node = kids[0]` BEFORE checking
  // `kids.length > 1`, so whenever the current level had MULTIPLE substantial children (a real
  // multi-column row — e.g. a nav bar's [logo, nav-links] or a hero's [text-column, image-column])
  // it still descended one wrong level into just the FIRST column, then stopped — returning that
  // narrow single column's width as "content width" instead of the multi-column row's own width
  // (the actual content wrapper). Verified: a nav bar (logo ~174px + links ~700px, row ~1170px)
  // measured contentMaxWidth as 174 (the logo's own width) instead of 1170. Fixed: only descend
  // when there is EXACTLY ONE substantial child; stop immediately (without moving `node`) at 0 or
  // ≥2 substantial children, since ≥2 means the CURRENT node is already the content wrapper (the
  // narrowing already happened at the parent), and 0 means there's nothing further to descend into.
  const contentNodeOf = sec => {
    const secW = box(sec).width;
    let node = sec;
    for (let i = 0; i < 10; i++) {
      const kids = [...node.children].filter(c => box(c).width > 40 && box(c).height > 10);
      if (kids.length !== 1) break;
      const next = kids[0];
      node = next;
      if (box(next).width < secW - 4) break;
    }
    return node;
  };

  const sectionSpec = (sec, i) => {
    const b = box(sec);
    const inner = contentNodeOf(sec);
    const cols = [...inner.children].filter(c => box(c).width > 40).map(c => ({ width: r2(box(c).width) }));
    let gap = null;
    if (cols.length > 1) {
      const kids = [...inner.children].filter(c => box(c).width > 40);
      gap = r2(box(kids[1]).left - (box(kids[0]).left + box(kids[0]).width));
      cols.forEach(c => c.gap = gap);
    }
    // v5: some widgets (EAAL/EB pricing tables, badges, counters) render meaningful text in a
    // bare <div>/<span> with NO semantic tag at all (a price like "$49/month" in
    // <div class="eael-pricing-tag">) — invisible to the tag-based scan below, which made
    // wrong CONTENT (not just wrong style) in these fields structurally undetectable by the
    // diff. Catch them with a LEAF-NODE rule: any div/span with NO child ELEMENTS (so it can't
    // be a structural wrapper) and non-trivial own text — EXCLUDING one already inside a
    // tag-matched ancestor (h1-h6/p/a/button/li), which would otherwise double-count a button's
    // own inner label span as a second, spuriously "missing" text element.
    const TAG_MATCHED = 'h1,h2,h3,h4,h5,h6,p,a,button,li';
    const leafTextNodes = [...sec.querySelectorAll('div,span')]
      .filter(e => e.children.length === 0 && txt(e).length > 0 && box(e).width > 3 && box(e).height > 3
        && !e.closest(TAG_MATCHED));
    const seen = new Set();
    const els = [...sec.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,button,img,input,li'), ...leafTextNodes]
      .filter(e => {
        if (seen.has(e)) return false;
        seen.add(e);
        return !CHROME.has(e.tagName) && box(e).width > 3 && box(e).height > 3 && (e.tagName === 'IMG' || txt(e));
      })
      .map(elemSpec);
    const head = sec.querySelector('h1,h2,h3,h4,h5,h6');
    return {
      id: 's' + (i + 1),
      label: (head ? txt(head) : txt(sec)).slice(0, 50),
      top: r2(b.top + scrollY), height: r2(b.height),
      bgOwner: bgOwnerOf(sec),
      decor: decorOf(sec),           // <- v2: decorative bg layers (object PNGs, gradients, patterns)
      contentWidth: r2(box(inner).width),
      align: cs(head || sec).textAlign,
      columns: cols,
      elements: els,
    };
  };

  const secs = sections.map(sectionSpec);

  // v3: per-(family,weight) webfont load status. A control can NAME a font without LOADING it
  // (silent fallback) — a matching font-family with document.fonts.check()===false is a FALSE
  // PASS. Capture it here so `dfdiff`/`sb specgate` can gate font loading offline (Phase 3 #1).
  const fontSet = {};
  secs.forEach(s => (s.elements || []).forEach(e => {
    if (e.kind === 'image' || !e.font || !e.font.family) return;
    const fam = e.font.family, w = num(e.font.weight) || 400, k = fam + '|' + w;
    if (!fontSet[k]) {
      let loaded = null;
      try { loaded = document.fonts.check(w + ' 16px "' + fam + '"'); } catch (_) {}
      fontSet[k] = { family: fam, weight: w, loaded };
    }
  }));

  // v5: contentMaxWidth is now GEOMETRIC, not class-name-based. The old selector
  // ('.e-con-inner,.elementor-container,[class*="container"]') only matches Elementor's own
  // markup — on a Gutenberg/Essential-Blocks page NOTHING matches, so it silently fell back to
  // `sections[0]` itself (the full-width OUTER wrapper), reporting the reference's real ~1240px
  // content width as ~1280px. VERIFIED: this produced a spurious "content-width delta -40px"
  // finding on a build that was already pixel-identical to the reference at 1240px true width —
  // wasted an entire investigation chasing a defect that didn't exist. Fix: descend single-child
  // wrapper chains from the section root as long as they stay full section-width; stop at (and
  // measure) the first node that's either narrower than the section or has multiple wide
  // children — that is the real content-capping wrapper, regardless of the engine's class names.

  return {
    designspec: '1.0',
    meta: { source: 'web', ref: location.href, viewport: { w: innerWidth, h: innerHeight },
            colorFormat: 'rgb', fidelity: 'full', tool: 'extract-web.js@6' },
    page: {
      width: r2(box(ROOT).width), height: document.body.scrollHeight,
      background: cs(document.body).backgroundColor,
      contentMaxWidth: sections.length ? r2(box(contentNodeOf(sections[0])).width) : null,
    },
    fonts: Object.values(fontSet),
    sections: secs,
  };
}
