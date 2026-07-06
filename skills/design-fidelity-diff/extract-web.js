// extract-web.js@4 — canonical DesignSpec v1 web extractor.
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
      let root = document.querySelector('main, .elementor, .site-main, #content') || document.body;
      while (root && root.children.length === 1 && box(root.children[0]).height > 200) root = root.children[0];
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

  const sectionSpec = (sec, i) => {
    const b = box(sec);
    const inner = sec.querySelector('.elementor-container, [class*="container"], [class*="row"]') || sec;
    const cols = [...inner.children].filter(c => box(c).width > 40).map(c => ({ width: r2(box(c).width) }));
    let gap = null;
    if (cols.length > 1) {
      const kids = [...inner.children].filter(c => box(c).width > 40);
      gap = r2(box(kids[1]).left - (box(kids[0]).left + box(kids[0]).width));
      cols.forEach(c => c.gap = gap);
    }
    const els = [...sec.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,button,img,input')]
      .filter(e => !CHROME.has(e.tagName) && box(e).width > 3 && box(e).height > 3 && (e.tagName === 'IMG' || txt(e)))
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

  return {
    designspec: '1.0',
    meta: { source: 'web', ref: location.href, viewport: { w: innerWidth, h: innerHeight },
            colorFormat: 'rgb', fidelity: 'full', tool: 'extract-web.js@4' },
    page: {
      width: r2(box(ROOT).width), height: document.body.scrollHeight,
      background: cs(document.body).backgroundColor,
      contentMaxWidth: sections.length ? r2(box(sections[0].querySelector('.elementor-container,[class*="container"]') || sections[0]).width) : null,
    },
    fonts: Object.values(fontSet),
    sections: secs,
  };
}
