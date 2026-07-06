#!/usr/bin/env python3
"""dfdiff.py@1 — DesignSpec v1 diff + gate (Phase 3 + Phase 5 of design-fidelity-diff).

Pure data comparison over two DesignSpec v1 JSON docs (schema in
skills/design-fidelity-diff/DESIGNSPEC.md). Reference and build emit the SAME shape
(extract-web.js run on both), so the diff is key-for-key and needs no browser, no
judgement — it is the deterministic spine the skill wraps around the two agent steps
(Build, Fix-by-cause).

Two subcommands:
  diff <ref.json> <build.json>   Phase 3 — full diagnosis, every defect ranked by cause/severity.
  gate <ref.json> <build.json>   Phase 5 — numeric done-gate, PASS/FAIL per hard gate.

Both take --json for the raw machine shape. Exit code: 0 = pass/clean, 1 = defects/fail,
2 = usage/load error. NOT covered here (needs a browser or eyes, stays an agent/human step):
the side-by-side VISION pass and pixel overlay (use `sb pxdiff` / `sb vrdiff`).

Thresholds (from SKILL.md Phase 5):
  content width  : exact (<=1px rounding)      section height : +/-2px
  dLeft          : median ~0, max <=3px        dTop           : median <=3px, residuals >5px named
  assets         : ref_srcs - build_srcs == 0  backgrounds    : every ref gradient/image/decor present
  fonts          : every ref family loaded on the build (needs extract-web.js@3 `fonts` block)
"""
from __future__ import annotations
import argparse
import json
import re
import statistics
import sys
from pathlib import Path

# ---- tolerances (px) --------------------------------------------------------
TOL_WIDTH = 1        # content-width delta treated as exact below this
TOL_HEIGHT = 2       # per-section height gate
TOL_DLEFT = 3        # horizontal drift gate
TOL_DTOP = 3         # vertical drift gate (median)
TOL_DTOP_NAME = 5    # residual dTop above this is named individually
TOL_DIM = 3          # per-element width/height drift note

# cap noisy lists in the human report (never silently — we print the count dropped)
CAP = 12


# ---- loading / normalisation ------------------------------------------------
def die(msg: str, code: int = 2):
    print(f"dfdiff: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.is_file():
        die(f"not found: {p}")
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        die(f"invalid JSON in {p}: {e}")
    if not isinstance(data, dict) or "sections" not in data:
        die(f"{p} is not a DesignSpec v1 doc (no `sections`)")
    return data


def _norm_text(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()[:40]


def _norm_color(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _fname(s) -> str:
    """Basename of a src/url, query stripped — the stable asset key."""
    return str(s or "").split("/")[-1].split("?")[0]


def elem_key(el: dict) -> str:
    """Stable within-section identity: image -> src filename, else kind:text."""
    if el.get("src") or el.get("kind") == "image":
        return "img:" + _fname(el.get("src"))
    return f"{el.get('kind', 'text')}:{_norm_text(el.get('text'))}"


def _n(v):
    """Best-effort number, else None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- matching ---------------------------------------------------------------
def match_sections(ref_secs: list, build_secs: list):
    """Pair sections by normalised label, falling back to positional order for the
    rest. Returns (pairs, only_ref, only_build) where pairs = [(ref_sec, build_sec)]."""
    b_by_label: dict[str, list] = {}
    for s in build_secs:
        b_by_label.setdefault(_norm_text(s.get("label")), []).append(s)

    pairs, matched_build = [], set()
    unmatched_ref = []
    for rs in ref_secs:
        cands = b_by_label.get(_norm_text(rs.get("label")), [])
        pick = next((c for c in cands if id(c) not in matched_build), None)
        if pick is not None:
            matched_build.add(id(pick))
            pairs.append((rs, pick))
        else:
            unmatched_ref.append(rs)

    # positional fallback for whatever stayed unmatched (order-preserved)
    leftover_build = [s for s in build_secs if id(s) not in matched_build]
    for rs, bs in zip(unmatched_ref, leftover_build):
        matched_build.add(id(bs))
        pairs.append((rs, bs))
    used = {id(rs) for rs, _ in pairs}
    only_ref = [rs for rs in unmatched_ref[len(leftover_build):]] if len(unmatched_ref) > len(leftover_build) else []
    only_build = [s for s in build_secs if id(s) not in matched_build]
    return pairs, only_ref, only_build


def match_elements(ref_els: list, build_els: list):
    """Multiset match by elem_key within a section.
    Returns (pairs, missing, extra)."""
    b_by_key: dict[str, list] = {}
    for e in build_els:
        b_by_key.setdefault(elem_key(e), []).append(e)
    pairs, missing = [], []
    for re_ in ref_els:
        k = elem_key(re_)
        bucket = b_by_key.get(k)
        if bucket:
            pairs.append((re_, bucket.pop(0)))
        else:
            missing.append(re_)
    extra = [e for bucket in b_by_key.values() for e in bucket]
    return pairs, missing, extra


def collect_srcs(spec: dict) -> set:
    out = set()
    for s in spec.get("sections", []):
        for d in s.get("decor", []) or []:
            if d.get("src"):
                out.add(_fname(d["src"]))
        for e in s.get("elements", []) or []:
            if e.get("src"):
                out.add(_fname(e["src"]))
    return out


# ---- diff (Phase 3) ---------------------------------------------------------
# severity weights: systemic first, then missing content, then geometry, then appearance
SEV = {
    "content_width": 100, "font_unloaded": 90, "missing_asset": 85, "section_missing": 88,
    "missing_element": 80, "background_missing": 75, "extra_element": 70,
    "section_height": 60, "element_dleft": 50, "appearance": 45, "element_dtop": 40,
    "element_dim": 35, "box_owner": 55, "appearance_soft": 18,
}
# categorical style diffs that FAIL the appearance gate (box-gating is blind to these).
# appearance_soft (line-height/letter-spacing) is sub-pixel across engines → reported, not gated.
APPEARANCE_GATE_CATS = ("appearance", "box_owner")


def _finding(cat, msg, mag=0.0, where=""):
    return {"category": cat, "severity": SEV.get(cat, 30), "magnitude": round(mag, 1),
            "where": where, "message": msg}


def compute_diff(ref: dict, build: dict) -> dict:
    findings = []

    # (2) content-width parity — systemic
    rw = _n((ref.get("page") or {}).get("contentMaxWidth"))
    bw = _n((build.get("page") or {}).get("contentMaxWidth"))
    if rw is not None and bw is not None:
        d = bw - rw
        if abs(d) > TOL_WIDTH:
            findings.append(_finding("content_width",
                f"content width {bw:.0f} vs reference {rw:.0f} (d={d:+.0f}px) — shifts every left; fix before per-element",
                abs(d), "page"))

    # (1) fonts loaded on the build — a matching family that never loaded is a FALSE PASS
    ref_fams = {(_norm_text(f.get("family")), int(_n(f.get("weight")) or 400))
                for f in ref.get("fonts", []) or []}
    for f in build.get("fonts", []) or []:
        key = (_norm_text(f.get("family")), int(_n(f.get("weight")) or 400))
        if f.get("loaded") is False and (not ref_fams or key in ref_fams):
            findings.append(_finding("font_unloaded",
                f"font '{f.get('family')}' {f.get('weight')} NOT loaded on build (silent fallback) — inject the webfont",
                90, "page"))
    if not build.get("fonts") and ref.get("fonts"):
        findings.append(_finding("font_unloaded",
            "build DesignSpec has no `fonts` block — re-extract with extract-web.js@3 to gate font loading", 1, "page"))

    # (7) asset completeness — every reference src present on the build
    missing_assets = sorted(collect_srcs(ref) - collect_srcs(build))
    for a in missing_assets:
        findings.append(_finding("missing_asset", f"reference asset '{a}' absent from build (renders as a red blob)", 85, a))

    pairs, only_ref, only_build = match_sections(ref.get("sections", []), build.get("sections", []))
    for rs in only_ref:
        findings.append(_finding("section_missing", f"section '{rs.get('label')}' present in reference, absent in build", 88, rs.get("label", "")))
    for bs in only_build:
        findings.append(_finding("section_missing", f"section '{bs.get('label')}' present in build, absent in reference", 70, bs.get("label", "")))

    dtops, dlefts, sec_rows = [], [], []
    for rs, bs in pairs:
        label = rs.get("label") or rs.get("id") or ""
        # (4) per-section height
        rh, bh = _n(rs.get("height")), _n(bs.get("height"))
        dH = (bh - rh) if (rh is not None and bh is not None) else None
        sec_rows.append({"label": label, "ref": rh, "build": bh, "dH": dH})
        if dH is not None and abs(dH) > TOL_HEIGHT:
            findings.append(_finding("section_height",
                f"section '{label}' height {bh:.0f} vs {rh:.0f} (d={dH:+.0f}px)", abs(dH), label))

        # (6) background parity — gradient / image / decor set
        rb, bb = rs.get("bgOwner") or {}, bs.get("bgOwner") or {}
        if rb.get("gradient") and not bb.get("gradient"):
            findings.append(_finding("background_missing", f"section '{label}': reference gradient missing on build (flat-color/white)", 75, label))
        if rb.get("image") and _fname(rb.get("image")) != _fname(bb.get("image")):
            findings.append(_finding("background_missing", f"section '{label}': reference bg image '{_fname(rb.get('image'))}' not on build", 75, label))
        r_decor = {_fname(d.get("src")) for d in (rs.get("decor") or []) if d.get("src")}
        b_decor = {_fname(d.get("src")) for d in (bs.get("decor") or []) if d.get("src")}
        for d in sorted(r_decor - b_decor):
            findings.append(_finding("background_missing", f"section '{label}': decorative layer '{d}' missing on build", 74, label))

        # (5) per-element dTop/dLeft + appearance
        e_pairs, e_missing, e_extra = match_elements(rs.get("elements", []) or [], bs.get("elements", []) or [])
        for em in e_missing:
            findings.append(_finding("missing_element", f"section '{label}': element {elem_key(em)!r} missing on build", 80, label))
        for ex in e_extra:
            findings.append(_finding("extra_element", f"section '{label}': extra element {elem_key(ex)!r} on build (not in reference)", 70, label))
        for re_, be_ in e_pairs:
            rt, bt = _n(re_.get("top")), _n(be_.get("top"))
            rl, bl = _n(re_.get("left")), _n(be_.get("left"))
            rW, bW = _n(re_.get("w")), _n(be_.get("w"))
            rHt, bHt = _n(re_.get("h")), _n(be_.get("h"))
            k = elem_key(re_)
            if rt is not None and bt is not None:
                dtop = bt - rt
                dtops.append(dtop)
                if abs(dtop) > TOL_DTOP_NAME:
                    findings.append(_finding("element_dtop", f"'{label}' {k}: dTop {dtop:+.0f}px", abs(dtop), label))
            if rl is not None and bl is not None:
                dleft = bl - rl
                dlefts.append(dleft)
                if abs(dleft) > TOL_DLEFT:
                    findings.append(_finding("element_dleft", f"'{label}' {k}: dLeft {dleft:+.0f}px (width/align/structure bug, not fonts)", abs(dleft), label))
            if rW is not None and bW is not None and abs(bW - rW) > TOL_DIM:
                findings.append(_finding("element_dim", f"'{label}' {k}: width {bW:.0f} vs {rW:.0f} (d={bW-rW:+.0f})", abs(bW - rW), label))
            if rHt is not None and bHt is not None and abs(bHt - rHt) > TOL_DIM:
                findings.append(_finding("element_dim", f"'{label}' {k}: height {bHt:.0f} vs {rHt:.0f} (d={bHt-rHt:+.0f})", abs(bHt - rHt), label))
            # appearance (computable subset of the appearance gate)
            _appearance(re_, be_, label, k, findings)

    findings.sort(key=lambda f: (f["severity"], f["magnitude"]), reverse=True)
    return {
        "ok": True,
        "summary": {
            "sections_ref": len(ref.get("sections", [])), "sections_build": len(build.get("sections", [])),
            "sections_matched": len(pairs), "defects": len(findings),
            "missing_assets": len(missing_assets),
            "dtop_median": round(statistics.median([abs(x) for x in dtops]), 1) if dtops else None,
            "dtop_max": round(max([abs(x) for x in dtops]), 1) if dtops else None,
            "dleft_median": round(statistics.median([abs(x) for x in dlefts]), 1) if dlefts else None,
            "dleft_max": round(max([abs(x) for x in dlefts]), 1) if dlefts else None,
            "content_width_delta": (round(bw - rw, 1) if (rw is not None and bw is not None) else None),
        },
        "section_heights": sec_rows,
        "findings": findings,
    }


def _appearance(re_: dict, be_: dict, label: str, k: str, findings: list):
    # font/color checks are meaningless on images (they carry only INHERITED text styles) —
    # a build inheriting a different theme font must not read as a per-image appearance defect.
    if (re_.get("kind") == "image") or re_.get("src") or be_.get("src"):
        rb, bb = re_.get("box") or {}, be_.get("box") or {}
        if _norm_color(rb.get("radius")) and _norm_color(rb.get("radius")) != _norm_color(bb.get("radius")):
            findings.append(_finding("appearance", f"'{label}' {k}: border-radius {bb.get('radius')} vs {rb.get('radius')}", 20, label))
        return
    rf, bf = re_.get("font") or {}, be_.get("font") or {}
    # categorical style diffs — box-gating is blind to these; they FAIL the appearance gate
    if rf.get("color") and bf.get("color") and _norm_color(rf["color"]) != _norm_color(bf["color"]):
        findings.append(_finding("appearance", f"'{label}' {k}: color {bf['color']} vs {rf['color']}", 45, label))
    if _norm_text(rf.get("family")) and _norm_text(rf.get("family")) != _norm_text(bf.get("family")):
        findings.append(_finding("appearance", f"'{label}' {k}: font-family '{bf.get('family')}' vs '{rf.get('family')}'", 45, label))
    rw_, bw_ = _n(rf.get("weight")), _n(bf.get("weight"))
    if rw_ is not None and bw_ is not None and rw_ != bw_:
        findings.append(_finding("appearance", f"'{label}' {k}: font-weight {bw_:.0f} vs {rw_:.0f}", 30, label))
    # italic/oblique emphasis — a missing accent style on one run (SKILL appearance corollary)
    rstyle, bstyle = _norm_text(rf.get("style")), _norm_text(bf.get("style"))
    if rstyle and rstyle != "normal" and rstyle != bstyle:
        findings.append(_finding("appearance", f"'{label}' {k}: font-style '{bf.get('style') or 'normal'}' vs '{rf.get('style')}' (missing italic accent?)", 45, label))
    elif rstyle == "normal" and bstyle and bstyle not in ("normal", ""):
        findings.append(_finding("appearance", f"'{label}' {k}: font-style '{bf.get('style')}' vs 'normal' (unexpected italic)", 35, label))
    # text-transform — "With" vs "with" reads as a different word (SKILL appearance corollary)
    rtr, btr = _norm_text(rf.get("transform")), _norm_text(bf.get("transform"))
    if rtr and rtr != btr:
        findings.append(_finding("appearance", f"'{label}' {k}: text-transform '{bf.get('transform') or 'none'}' vs '{rf.get('transform')}'", 40, label))
    # soft style diffs — sub-pixel across engines; REPORTED, never gate-failing (honest floor)
    rls, bls = _norm_color(rf.get("letterSpacing")), _norm_color(bf.get("letterSpacing"))
    if rls and rls != "normal" and rls != bls:
        findings.append(_finding("appearance_soft", f"'{label}' {k}: letter-spacing {bf.get('letterSpacing')} vs {rf.get('letterSpacing')}", 18, label))
    rlh, blh = _n(rf.get("lineHeight")), _n(bf.get("lineHeight"))
    if rlh is not None and blh is not None and abs(rlh - blh) > 2:
        findings.append(_finding("appearance_soft", f"'{label}' {k}: line-height {blh:.0f} vs {rlh:.0f} (d={blh-rlh:+.0f}px)", abs(rlh - blh), label))
    rb, bb = re_.get("box") or {}, be_.get("box") or {}
    if bool(rb.get("bgOwner")) and not bool(bb.get("bgOwner")):
        findings.append(_finding("box_owner", f"'{label}' {k}: reference element OWNS its bg+padding, build does not (Box-Model Owner rule)", 55, label))
    if rb.get("backgroundGradient") and not bb.get("backgroundGradient"):
        findings.append(_finding("appearance", f"'{label}' {k}: element bg gradient missing on build", 45, label))
    if _norm_color(rb.get("radius")) and _norm_color(rb.get("radius")) != _norm_color(bb.get("radius")):
        findings.append(_finding("appearance", f"'{label}' {k}: border-radius {bb.get('radius')} vs {rb.get('radius')}", 20, label))


# ---- gate (Phase 5) ---------------------------------------------------------
def compute_gate(ref: dict, build: dict) -> dict:
    diff = compute_diff(ref, build)
    s = diff["summary"]
    gates = []

    def gate(name, passed, detail):
        gates.append({"gate": name, "pass": bool(passed), "detail": detail})

    cwd = s["content_width_delta"]
    gate("content_width", cwd is not None and abs(cwd) <= TOL_WIDTH,
         f"delta {cwd:+.0f}px (must be <= {TOL_WIDTH})" if cwd is not None else "reference/build contentMaxWidth missing")

    heights_bad = [r for r in diff["section_heights"] if r["dH"] is not None and abs(r["dH"]) > TOL_HEIGHT]
    gate("section_heights", not heights_bad,
         "all sections +/-2px" if not heights_bad
         else "; ".join(f"{r['label']} {r['dH']:+.0f}px" for r in heights_bad[:CAP])
              + (f" (+{len(heights_bad)-CAP} more)" if len(heights_bad) > CAP else ""))

    gate("assets", s["missing_assets"] == 0,
         "all reference assets placed" if s["missing_assets"] == 0 else f"{s['missing_assets']} asset(s) missing")

    bg_bad = [f for f in diff["findings"] if f["category"] == "background_missing"]
    gate("backgrounds", not bg_bad,
         "every reference gradient/image/decor present" if not bg_bad else f"{len(bg_bad)} background layer(s) missing")

    dleft_max = s["dleft_max"]
    gate("dleft", dleft_max is not None and dleft_max <= TOL_DLEFT,
         f"median {s['dleft_median']}px, max {dleft_max}px (max must be <= {TOL_DLEFT})" if dleft_max is not None else "no matched elements")

    dtop_med = s["dtop_median"]
    residuals = sorted([f for f in diff["findings"] if f["category"] == "element_dtop"], key=lambda f: f["magnitude"], reverse=True)
    gate("dtop", dtop_med is not None and dtop_med <= TOL_DTOP,
         f"median {dtop_med}px (must be <= {TOL_DTOP})" + (f"; {len(residuals)} residual(s) >5px" if residuals else "")
         if dtop_med is not None else "no matched elements")

    counts_bad = [f for f in diff["findings"] if f["category"] in ("missing_element", "extra_element", "section_missing")]
    gate("element_counts", not counts_bad,
         "section + element counts match" if not counts_bad else f"{len(counts_bad)} missing/extra section(s)/element(s)")

    unloaded = [f for f in diff["findings"] if f["category"] == "font_unloaded"]
    gate("fonts", not unloaded, "all reference families loaded on build" if not unloaded else f"{len(unloaded)} font issue(s)")

    appearance = [f for f in diff["findings"] if f["category"] in APPEARANCE_GATE_CATS]
    gate("appearance", not appearance,
         "color/font-style/text-transform/weight/box-owner all match"
         if not appearance else f"{len(appearance)} style diff(s) — "
         + "; ".join(f["message"].split(": ", 1)[-1] for f in appearance[:4])
         + (f" (+{len(appearance)-4} more)" if len(appearance) > 4 else ""))

    passed = all(g["pass"] for g in gates)
    return {"ok": True, "pass": passed, "gates": gates, "summary": s, "residual_dtop": residuals[:CAP]}


# ---- rendering --------------------------------------------------------------
def print_diff(d: dict):
    s = d["summary"]
    print(f"sections: ref {s['sections_ref']} · build {s['sections_build']} · matched {s['sections_matched']}")
    cwd = s["content_width_delta"]
    print(f"content-width delta: {cwd:+.0f}px" if cwd is not None else "content-width delta: n/a")
    print(f"dTop  median {s['dtop_median']}px  max {s['dtop_max']}px   |   "
          f"dLeft median {s['dleft_median']}px  max {s['dleft_max']}px")
    print()
    print("per-section height (build vs ref):")
    for r in d["section_heights"]:
        mark = " " if (r["dH"] is not None and abs(r["dH"]) <= TOL_HEIGHT) else "!"
        dh = f"{r['dH']:+.0f}" if r["dH"] is not None else "  ?"
        print(f"  {mark} {str(r['label'])[:38]:<38} {str(int(r['build'])) if r['build'] is not None else '?':>6} / "
              f"{str(int(r['ref'])) if r['ref'] is not None else '?':<6} d={dh}")
    print()
    if not d["findings"]:
        print("no defects — clean diff.")
        return
    print(f"defects ({len(d['findings'])}), most-severe first:")
    by_cat: dict[str, list] = {}
    for f in d["findings"]:
        by_cat.setdefault(f["category"], []).append(f)
    # preserve severity order of first appearance
    seen = []
    for f in d["findings"]:
        if f["category"] not in seen:
            seen.append(f["category"])
    for cat in seen:
        items = by_cat[cat]
        print(f"\n  [{cat}]  ({len(items)})")
        for f in items[:CAP]:
            print(f"    - {f['message']}")
        if len(items) > CAP:
            print(f"    … +{len(items) - CAP} more {cat} (use --json for the full list)")


def print_gate(g: dict):
    for gt in g["gates"]:
        print(f"  [{'PASS' if gt['pass'] else 'FAIL'}]  {gt['gate']:<16} {gt['detail']}")
    if g["residual_dtop"]:
        print("\n  residual dTop > 5px:")
        for f in g["residual_dtop"]:
            print(f"    - {f['message']}")
    print()
    print(f"==> {'PASS' if g['pass'] else 'FAIL'}  (design-fidelity done-gate)")
    if not g["pass"]:
        print("    NOT done. Whole-overlay-red / height drift is systemic — fix width + section heights first (SKILL Phase 4).")


# ---- cli --------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="dfdiff", description="DesignSpec v1 diff + gate (design-fidelity-diff Phase 3/5).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("diff", "Phase 3 — ranked defect report"), ("gate", "Phase 5 — numeric PASS/FAIL done-gate")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("reference", help="reference DesignSpec JSON")
        p.add_argument("build", help="build DesignSpec JSON")
        p.add_argument("--json", action="store_true", help="emit raw JSON instead of a summary")
    args = ap.parse_args(argv)

    ref, build = load(args.reference), load(args.build)
    if args.cmd == "diff":
        res = compute_diff(ref, build)
        print(json.dumps(res, indent=2)) if args.json else print_diff(res)
        return 1 if res["findings"] else 0
    else:
        res = compute_gate(ref, build)
        print(json.dumps(res, indent=2)) if args.json else print_gate(res)
        return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
