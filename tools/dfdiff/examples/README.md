# dfdiff examples — golden DesignSpec fixtures

Real DesignSpec v1 docs (extract-web.js output) kept as example inputs for the
design-fidelity diff/gate CLI and as reference-of-record for a rebuild.

## flexigency-ref.json

The Templately **flexigency** reference design
(<https://agency.blocks.templately.com/flexigency/>), extracted with
`extract-web.js@4`:

```
sb specextract https://agency.blocks.templately.com/flexigency/ --out flexigency-ref.json
```

- 10 sections · 212 elements · 4 fonts (Inter Tight 400/500/600, Arial) · page 9562px · contentMaxWidth 1240
- 53 images, all loaded; 37 elements carry `text-transform:capitalize`; per-section gradients + decor captured
- Extraction is stable: two independent runs diff to **0 defects** (dLeft max 1px), so this file is a
  trustworthy golden reference, not a one-off capture.
- **Known limitation — section 10 ("Explore Digital Insights") has 15 elements with negative
  `left`.** That section is a horizontally-scrolling blog-card carousel; its slide offset is
  autoplay-driven, independent of page scroll, so it's a moving target at capture time — the same
  class of issue as the nav (SKILL "Header/nav is an inherent approximation"). Don't gate section 10
  on exact `left`/`dLeft`; gate height/content/asset presence instead.

### Use it

Gate a build of flexigency against this reference (no re-extraction of the reference needed):

```
sb specextract <your-build-url> --out build.json   # e.g. --login for a wp-admin preview
sb specdiff tools/dfdiff/examples/flexigency-ref.json build.json   # ranked defect report
sb specgate tools/dfdiff/examples/flexigency-ref.json build.json   # PASS/FAIL done-gate
```

Refresh it if the upstream design changes: re-run the `specextract` command above and commit the diff.
