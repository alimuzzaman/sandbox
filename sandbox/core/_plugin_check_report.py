from __future__ import annotations
import json


# Renders Plugin Check findings into a single self-contained HTML report — the
# ./sb plugin-check equivalent of Playwright's tests/test-results/html-report/.
# Ported from a working reference implementation (a project-local Node script's
# plugin-check-report.js) — see specs/013-plugin-check/research.md's HTML-report
# decision for what changed and why: (1) the masthead/title use the checked plugin's
# OWN slug/version instead of a hardcoded plugin name, (2) the footer's excluded-
# directories sentence is generated from the actual configured list, (3) the
# decorative headline font is the system sans-serif stack rather than an inlined
# font binary — a cosmetic difference only; the report is still one self-contained
# file with zero external requests either way.


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_report(findings: list[dict], meta: dict) -> str:
    """findings: list of {file, type: 'ERROR'|'WARNING', code, line, column, message}.
    meta: {plugin_slug, plugin_version, checker_version, wp_version, php_version,
    exclude_directories, baseline_total, new_count}. Returns a complete HTML document."""
    errors = [f for f in findings if f.get("type") == "ERROR"]
    warnings = [f for f in findings if f.get("type") == "WARNING"]

    code_counts: dict[str, int] = {}
    for f in findings:
        key = f"{f.get('type')} {f.get('code')}"
        code_counts[key] = code_counts.get(key, 0) + 1
    top_codes = sorted(
        ({"type": k.split(" ", 1)[0], "code": k.split(" ", 1)[1], "n": n}
         for k, n in code_counts.items()),
        key=lambda x: -x["n"],
    )[:14]
    max_code_count = top_codes[0]["n"] if top_codes else 1

    file_stats: dict[str, dict[str, int]] = {}
    for f in findings:
        st = file_stats.setdefault(f.get("file"), {"ERROR": 0, "WARNING": 0})
        st[f.get("type")] = st.get(f.get("type"), 0) + 1
    sorted_files = sorted(
        file_stats.keys(),
        key=lambda p: (-file_stats[p]["ERROR"], -file_stats[p]["WARNING"], p),
    )

    findings_by_file: dict[str, list[dict]] = {}
    for f in findings:
        findings_by_file.setdefault(f.get("file"), []).append(f)
    for items in findings_by_file.values():
        items.sort(key=lambda it: (0 if it.get("type") == "ERROR" else 1, it.get("line") or 0))

    top_codes_html = "".join(
        f"""
        <div class="code-row">
          <span class="code-badge {'err' if c['type'] == 'ERROR' else 'warn'}">{c['type'][0]}</span>
          <span class="code-name">{_esc(c['code'])}</span>
          <span class="code-bar-track"><span class="code-bar {'err' if c['type'] == 'ERROR' else 'warn'}" style="width:{round(c['n'] / max_code_count * 100)}%"></span></span>
          <span class="code-count">{c['n']}</span>
        </div>"""
        for c in top_codes
    )

    file_groups_html_parts = []
    for fpath in sorted_files:
        st = file_stats[fpath]
        items = findings_by_file[fpath]
        rows = "".join(
            f"""
          <tr class="finding-row" data-type="{it.get('type')}">
            <td class="col-sev"><span class="sev-dot {'err' if it.get('type') == 'ERROR' else 'warn'}" title="{it.get('type')}"></span></td>
            <td class="col-line">{it.get('line')}</td>
            <td class="col-code">{_esc(it.get('code'))}</td>
            <td class="col-msg">{_esc(it.get('message'))}</td>
          </tr>"""
            for it in items
        )
        err_chip = f'<span class="chip err">{st["ERROR"]} err</span>' if st["ERROR"] else ""
        warn_chip = f'<span class="chip warn">{st["WARNING"]} warn</span>' if st["WARNING"] else ""
        file_groups_html_parts.append(f"""
      <details class="file-group" data-file="{_esc(fpath.lower())}">
        <summary>
          <span class="file-path">{_esc(fpath)}</span>
          <span class="file-chips">{err_chip}{warn_chip}</span>
        </summary>
        <table class="findings-table">
          <thead><tr><th></th><th>Line</th><th>Rule</th><th>Message</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </details>""")
    file_groups_html = "".join(file_groups_html_parts)

    findings_json = json.dumps([
        {"f": f.get("file"), "t": f.get("type"), "c": f.get("code"),
         "l": f.get("line"), "m": f.get("message")}
        for f in findings
    ])

    gate_ok = meta.get("new_count", 0) == 0
    exclude_dirs = meta.get("exclude_directories") or []
    exclude_note = (
        f"Excludes {', '.join(exclude_dirs)} (configured via sandbox.config.json's "
        f"pluginCheck.excludeDirectories)." if exclude_dirs else
        "No directories excluded for this run."
    )

    return f"""<title>{_esc(meta.get('plugin_slug'))} &mdash; Plugin Check Report</title>
<style>
:root {{
  --bg: #F1F5F4; --surface: #FFFFFF; --surface-2: #E6EEEC; --text: #12201E;
  --text-dim: #4B615D; --border: #D3DEDC; --accent: #0E7C74; --accent-contrast: #FFFFFF;
  --error: #B7402A; --error-bg: #F7E4DF; --warning: #92660A; --warning-bg: #F5EBD3;
  --shadow: 0 1px 2px rgba(18,32,30,0.06), 0 8px 24px -12px rgba(18,32,30,0.18);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #0E1716; --surface: #152220; --surface-2: #1B2C29; --text: #E6F1EE; --text-dim: #93A9A5;
    --border: #29403C; --accent: #4FD1C1; --accent-contrast: #08211D; --error: #FF8064; --error-bg: #3A2018;
    --warning: #EBB652; --warning-bg: #362A11; --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px -12px rgba(0,0,0,0.5);
  }}
}}
:root[data-theme="dark"] {{
  --bg: #0E1716; --surface: #152220; --surface-2: #1B2C29; --text: #E6F1EE; --text-dim: #93A9A5;
  --border: #29403C; --accent: #4FD1C1; --accent-contrast: #08211D; --error: #FF8064; --error-bg: #3A2018;
  --warning: #EBB652; --warning-bg: #362A11; --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px -12px rgba(0,0,0,0.5);
}}
:root[data-theme="light"] {{
  --bg: #F1F5F4; --surface: #FFFFFF; --surface-2: #E6EEEC; --text: #12201E; --text-dim: #4B615D;
  --border: #D3DEDC; --accent: #0E7C74; --accent-contrast: #FFFFFF; --error: #B7402A; --error-bg: #F7E4DF;
  --warning: #92660A; --warning-bg: #F5EBD3; --shadow: 0 1px 2px rgba(18,32,30,0.06), 0 8px 24px -12px rgba(18,32,30,0.18);
}}
* {{ box-sizing: border-box; }}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 15px; line-height: 1.6; -webkit-font-smoothing: antialiased;
}}
.mono {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; }}
.wrap {{ max-width: 1120px; margin: 0 auto; padding: 48px 24px 96px; }}
.masthead {{ display: flex; flex-direction: column; gap: 14px; padding-bottom: 32px; margin-bottom: 32px; border-bottom: 1px solid var(--border); }}
.eyebrow {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; letter-spacing: 0.09em; text-transform: uppercase; color: var(--accent); font-weight: 600; }}
h1 {{ font-family: ui-sans-serif, sans-serif; font-weight: 800; font-size: clamp(2.2rem, 5vw, 3.4rem); line-height: 1.05; letter-spacing: -0.01em; margin: 0; color: var(--text); }}
h1 span {{ color: var(--accent); }}
.masthead-meta {{ display: flex; flex-wrap: wrap; gap: 6px 22px; color: var(--text-dim); font-size: 13px; }}
.masthead-meta .mono {{ color: var(--text); }}
.stat-strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 28px; box-shadow: var(--shadow); }}
.stat-tile {{ background: var(--surface); padding: 20px 20px 18px; display: flex; flex-direction: column; gap: 6px; }}
.stat-tile.gate {{ background: var(--gate-color, var(--accent)); color: var(--accent-contrast); }}
.stat-label {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.75; }}
.stat-value {{ font-weight: 800; font-size: 2.2rem; line-height: 1; font-variant-numeric: tabular-nums; }}
.stat-tile.gate .stat-value {{ font-size: 1.8rem; }}
.stat-sub {{ font-size: 12.5px; color: var(--text-dim); }}
.stat-tile.gate .stat-sub {{ color: var(--accent-contrast); opacity: 0.85; }}
.callout {{ background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; padding: 18px 20px; margin-bottom: 40px; font-size: 14px; color: var(--text-dim); }}
.callout strong {{ color: var(--text); }}
.callout summary {{ cursor: pointer; font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 11.5px; letter-spacing: 0.07em; text-transform: uppercase; color: var(--accent); font-weight: 600; list-style: none; }}
.callout summary::-webkit-details-marker {{ display: none; }}
.callout summary::after {{ content: "  \\25b8 how this gate works"; color: var(--text-dim); text-transform: none; letter-spacing: 0; font-weight: 400; }}
.callout[open] summary::after {{ content: "  \\25be how this gate works"; }}
.callout p {{ margin: 12px 0 0; }}
.callout code {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; background: var(--surface-2); padding: 1px 5px; border-radius: 4px; font-size: 12.5px; color: var(--text); }}
h2 {{ font-weight: 700; font-size: 1.05rem; letter-spacing: 0.04em; text-transform: uppercase; color: var(--text); margin: 0 0 4px; }}
.section-sub {{ color: var(--text-dim); font-size: 13px; margin: 0 0 18px; }}
section {{ margin-bottom: 44px; }}
.code-row {{ display: grid; grid-template-columns: 22px minmax(180px, 1fr) minmax(80px, 220px) 40px; align-items: center; gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--border); }}
.code-row:last-child {{ border-bottom: none; }}
.code-badge {{ width: 20px; height: 20px; border-radius: 5px; display: flex; align-items: center; justify-content: center; font-family: ui-monospace, monospace; font-size: 11px; font-weight: 700; }}
.code-badge.err {{ background: var(--error-bg); color: var(--error); }}
.code-badge.warn {{ background: var(--warning-bg); color: var(--warning); }}
.code-name {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 12.5px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.code-bar-track {{ height: 8px; background: var(--surface-2); border-radius: 4px; overflow: hidden; display: block; }}
.code-bar {{ display: block; height: 100%; border-radius: 4px; }}
.code-bar.err {{ background: var(--error); }}
.code-bar.warn {{ background: var(--warning); }}
.code-count {{ font-variant-numeric: tabular-nums; text-align: right; color: var(--text-dim); font-size: 13px; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; align-items: center; }}
.search-input {{ flex: 1 1 240px; background: var(--surface); border: 1px solid var(--border); border-radius: 7px; padding: 9px 12px; font-size: 14px; color: var(--text); font-family: inherit; }}
.search-input:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.chip-toggle {{ display: flex; gap: 6px; }}
.chip-toggle button {{ background: var(--surface); border: 1px solid var(--border); color: var(--text-dim); padding: 8px 14px; border-radius: 20px; font-size: 12.5px; font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; letter-spacing: 0.02em; cursor: pointer; }}
.chip-toggle button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.chip-toggle button.active {{ background: var(--accent); color: var(--accent-contrast); border-color: var(--accent); }}
.results-note {{ font-size: 12.5px; color: var(--text-dim); margin: 0 0 14px; }}
.file-group {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; overflow: hidden; }}
.file-group[hidden] {{ display: none; }}
.file-group summary {{ list-style: none; cursor: pointer; padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }}
.file-group summary::-webkit-details-marker {{ display: none; }}
.file-group summary::before {{ content: "\\25b8"; color: var(--text-dim); margin-right: 10px; display: inline-block; transition: transform 0.15s ease; }}
.file-group[open] summary::before {{ transform: rotate(90deg); }}
.file-path {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 13px; color: var(--text); word-break: break-all; }}
.file-chips {{ display: flex; gap: 6px; flex-shrink: 0; }}
.chip {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 12px; font-variant-numeric: tabular-nums; }}
.chip.err {{ background: var(--error-bg); color: var(--error); }}
.chip.warn {{ background: var(--warning-bg); color: var(--warning); }}
.findings-table {{ width: 100%; border-collapse: collapse; border-top: 1px solid var(--border); }}
.findings-table thead th {{ text-align: left; font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); padding: 8px 16px 6px; background: var(--surface-2); }}
.findings-table td {{ padding: 8px 16px; border-top: 1px solid var(--border); vertical-align: top; font-size: 13px; }}
.findings-table tr.finding-row[hidden] {{ display: none; }}
.col-sev {{ width: 20px; }}
.sev-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; }}
.sev-dot.err {{ background: var(--error); }}
.sev-dot.warn {{ background: var(--warning); }}
.col-line {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; color: var(--text-dim); font-variant-numeric: tabular-nums; width: 56px; }}
.col-code {{ font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; color: var(--text); width: 260px; }}
.col-msg {{ color: var(--text-dim); }}
footer {{ margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--border); color: var(--text-dim); font-size: 12.5px; }}
@media (max-width: 640px) {{ .code-row {{ grid-template-columns: 20px 1fr 34px; }} .code-bar-track {{ display: none; }} .col-code {{ width: auto; }} }}
</style>
<div class="wrap">
  <header class="masthead">
    <span class="eyebrow">WordPress Plugin Check &middot; Static Analysis</span>
    <h1>{_esc(meta.get('plugin_slug'))}<br><span>Plugin&nbsp;Check&nbsp;Report</span></h1>
    <div class="masthead-meta">
      <span>Plugin <span class="mono">{_esc(meta.get('plugin_slug'))} v{_esc(meta.get('plugin_version'))}</span></span>
      <span>Checker <span class="mono">plugin-check v{_esc(meta.get('checker_version'))}</span></span>
      <span>Environment <span class="mono">WP {_esc(meta.get('wp_version'))} &middot; PHP {_esc(meta.get('php_version'))}</span></span>
      <span>Scope <span class="mono">static checks, distribution files only</span></span>
    </div>
  </header>
  <div class="stat-strip">
    <div class="stat-tile gate" style="--gate-color:{'var(--accent)' if gate_ok else 'var(--error)'}">
      <span class="stat-label">Gate</span>
      <span class="stat-value">{'PASS' if gate_ok else 'FAIL'}</span>
      <span class="stat-sub">{meta.get('new_count', 0)} new error(s) vs. baseline</span>
    </div>
    <div class="stat-tile"><span class="stat-label">Errors</span><span class="stat-value">{len(errors)}</span><span class="stat-sub">{meta.get('baseline_total', 0)} baselined</span></div>
    <div class="stat-tile"><span class="stat-label">Warnings</span><span class="stat-value">{len(warnings)}</span><span class="stat-sub">reported, not gated</span></div>
    <div class="stat-tile"><span class="stat-label">Files flagged</span><span class="stat-value">{len(file_stats)}</span><span class="stat-sub">of the shipped tree</span></div>
    <div class="stat-tile"><span class="stat-label">Rule types</span><span class="stat-value">{len(code_counts)}</span><span class="stat-sub">distinct finding codes</span></div>
  </div>
  <details class="callout">
    <summary>How this gate works</summary>
    <p><strong>Baseline, not flat pass/fail.</strong> {len(errors)} ERROR-level findings already exist. A flat "any ERROR fails" gate would be permanently red for a project with any accepted, deliberate trade-offs. Instead <span class="mono">{_esc((meta.get('baseline_file') or 'the baseline file'))}</span> freezes the current count per <span class="mono">(file, rule)</span> pair. A run only fails on a <strong>new</strong> finding above its baselined count. WARNING-level findings are shown below for visibility but never gate the run.</p>
    <p>Run <span class="mono">./sb plugin-check --update</span> after fixing findings, to tighten the baseline.</p>
  </details>
  <section>
    <h2>Top rule codes</h2>
    <p class="section-sub">Ranked by occurrence, across all flagged files.</p>
    {top_codes_html}
  </section>
  <section>
    <h2>Findings by file</h2>
    <p class="section-sub">Sorted by error count, then warning count. {len(file_stats)} files, {len(findings)} findings total.</p>
    <div class="controls">
      <input class="search-input" id="search" type="text" placeholder="Filter by file, rule, or message..." autocomplete="off">
      <div class="chip-toggle" id="typeToggle">
        <button data-type="all" class="active">All</button>
        <button data-type="ERROR">Errors</button>
        <button data-type="WARNING">Warnings</button>
      </div>
    </div>
    <p class="results-note" id="resultsNote"></p>
    <div id="fileList">
      {file_groups_html}
    </div>
  </section>
  <footer>
    Generated locally via <span class="mono">./sb plugin-check</span> against the WPDeveloper Sandbox &mdash; not a CI workflow. {_esc(exclude_note)}
  </footer>
</div>
<script id="findings-data" type="application/json">{findings_json}</script>
<script>
(function() {{
  var search = document.getElementById('search');
  var toggle = document.getElementById('typeToggle');
  var note = document.getElementById('resultsNote');
  var groups = Array.prototype.slice.call(document.querySelectorAll('.file-group'));
  var activeType = 'all';

  function apply() {{
    var q = search.value.trim().toLowerCase();
    var visibleFiles = 0, visibleFindings = 0;

    groups.forEach(function(g) {{
      var rows = Array.prototype.slice.call(g.querySelectorAll('.finding-row'));
      var fileMatches = g.dataset.file.indexOf(q) !== -1;
      var anyRowVisible = false;

      rows.forEach(function(r) {{
        var typeOk = activeType === 'all' || r.dataset.type === activeType;
        var text = r.textContent.toLowerCase();
        var textOk = q === '' || fileMatches || text.indexOf(q) !== -1;
        var show = typeOk && textOk;
        r.hidden = !show;
        if (show) {{ anyRowVisible = true; visibleFindings++; }}
      }});

      g.hidden = !anyRowVisible;
      if (anyRowVisible) {{
        visibleFiles++;
        if (q !== '') g.open = true;
      }}
    }});

    note.textContent = visibleFiles + ' file(s), ' + visibleFindings + ' finding(s) shown.';
  }}

  search.addEventListener('input', apply);
  toggle.addEventListener('click', function(e) {{
    var btn = e.target.closest('button');
    if (!btn) return;
    toggle.querySelectorAll('button').forEach(function(b) {{ b.classList.remove('active'); }});
    btn.classList.add('active');
    activeType = btn.dataset.type;
    apply();
  }});

  apply();
}})();
</script>
"""
