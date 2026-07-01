// extract-web.js@1 — canonical DesignSpec v1 web extractor.
// Paste the FUNCTION BODY into Playwright browser_evaluate (run on reference AND build).
// Force-load images first (see SKILL Reference A). Override ROOT if section detection is wrong.
//
//   const ROOT = '.eb-fullwidth-content-wrapper';  // EB demo   (or '.elementor' for a build)
//
// Returns a DesignSpec v1 object (meta.source='web', fidelity='full').

() => {
  const r2 = n => Math.round(n);
  const cs = el => getComputedStyle(el);
  const box = el => el.getBoundingClientRect();
  const num = v => { const n = parseFloat(v); return Number.isFinite(n) ? n : v; };
  const txt = el => (el.textContent || '').replace(/\s+/g, ' ').trim();

  // ---- content root + top-level sections ----------------------------------
  const ROOT = (typeof window.__DS_ROOT === 'string' && document.querySelector(window.__DS_ROOT))
    || (() => {
      let root = document.querySelector('main, .elementor, .site-main, #content') || document.body;
      while (root && root.children.length === 1 && box(root.children[0]).height > 200) root = root.children[0];
      return root;
    })();
  const sections = [...ROOT.children].filter(c => box(c).height > 30);

  // ---- helpers ------------------------------------------------------------
  const CHROME = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT']);
  const isColored = c => c.backgroundColor !== 'rgba(0, 0, 0, 0)' && c.backgroundColor !== 'transparent';

  // the element that OWNS the visible background of a band (largest colored box, else the band)
  const bgOwnerOf = sec => {
    let best = isColored(cs(sec)) ? sec : null, bestArea = best ? box(best).width * box(best).height : 0;
    sec.querySelectorAll('*').forEach(e => {
      const c = cs(e), b = box(e);
      if (isColored(c) && b.width > 80 && b.height > 40) {
        const a = b.width * b.height;
        if (a > bestArea) { best = e; bestArea = a; }
      }
    });
    if (!best) return { background: 'rgba(0,0,0,0)', padding: '0px', radius: '0px' };
    const c = cs(best);
    return { background: c.backgroundColor, padding: c.padding, radius: c.borderRadius };
  };

  // does THIS element own its own visible background + breathing-room padding?
  const ownsBg = el => {
    const c = cs(el);
    return isColored(c) && c.padding !== '0px';
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
    const o = {
      kind: kindOf(el),
      top: r2(b.top + scrollY), left: r2(b.left), w: r2(b.width), h: r2(b.height),
      font: {
        family: c.fontFamily.split(',')[0].replace(/["']/g, ''),
        size: num(c.fontSize), weight: num(c.fontWeight),
        lineHeight: num(c.lineHeight), letterSpacing: c.letterSpacing,
        align: c.textAlign, color: c.color,
      },
      box: {
        background: c.backgroundColor, padding: c.padding, margin: c.margin,
        radius: c.borderRadius,
        border: c.borderStyle === 'none' ? 'none' : `${c.borderWidth} ${c.borderStyle} ${c.borderColor}`,
        bgOwner: ownsBg(el),
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
    // inter-column gap (first two columns)
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
      contentWidth: r2(box(inner).width),
      align: cs(head || sec).textAlign,
      columns: cols,
      elements: els,
    };
  };

  return {
    designspec: '1.0',
    meta: { source: 'web', ref: location.href, viewport: { w: innerWidth, h: innerHeight },
            colorFormat: 'rgb', fidelity: 'full', tool: 'extract-web.js@1' },
    page: {
      width: r2(box(ROOT).width), height: document.body.scrollHeight,
      background: cs(document.body).backgroundColor,
      contentMaxWidth: sections.length ? r2(box(sections[0].querySelector('.elementor-container,[class*="container"]') || sections[0]).width) : null,
    },
    sections: sections.map(sectionSpec),
  };
}
