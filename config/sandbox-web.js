(function(){"use strict";const s=e=>document.getElementById(e),xe={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},d=e=>String(e).replace(/[&<>"]/g,t=>xe[t]),F=e=>e.charAt(0).toUpperCase()+e.slice(1),l={data:{instances:[],plugins:[],projects:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1},busy:{},usage:null,paused:!1};async function j(e){return(await fetch(e)).json()}const me=()=>j("/api/instances"),Z=()=>j("/api/usage"),ee=(e,t)=>j(`/api/job/${e}?offset=${t}`),fe=e=>j(`/api/snapshots/${e}`);async function q(e){return(await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)})).json()}function ge(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const T=()=>ge(location.pathname);let H=()=>{};function ve(e){H=e}function f(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),H(T())}function D(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}function ke(){window.addEventListener("popstate",()=>H(T())),document.addEventListener("click",e=>{var a,r;const t=(r=(a=e.target)==null?void 0:a.closest)==null?void 0:r.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),f(n))})}let he=0;const N={};function ye(){return"mcsel"+ ++he}function te(e,t,n,a,r,o){const i=t.find(h=>h.v===n),c=i?i.label:t[0]?t[0].label:"";N[e]=a;const p=r?"opacity-50 pointer-events-none":"",W=o?"block w-full":"inline-block",x=o?"w-full":"w-48";return`<div class="relative ${W}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${x} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${p}">
      <span class="truncate flex-1" data-csel-label>${d(c)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${x} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(h=>`<button type="button" data-v="${d(h.v)}" data-search="${d(h.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${d(h.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${h.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${d(h.label)}</button>`).join("")}
      </div>
    </div></div>`}function $e(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",ne(e),n.focus())}}function we(e,t){var r,o;(r=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||r.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(o=N[e])==null||o.call(N,t)}function ne(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const r=a;r.style.display=(r.dataset.search||"").includes(n)?"":"none"})}function Se(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function L(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function Ce(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function g(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const y=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function ae(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const $="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function _(e,t,n,a){return`<button class="${$}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function U(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${d(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function M(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const b=e=>(e||0).toLocaleString(),I=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),J=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function Te(){const e=l.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No Claude session data found.</div>`;const t=e.total||{},n=(c,p,W)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${c}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${p}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([c,p])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${c}</span>
      <span class="flex-1 text-neutral-500">${b(J(p))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${I(p.cost)}</span></div>`).join(""),r=Object.entries(e.per_instance||{}).sort((c,p)=>(p[1].cost||0)-(c[1].cost||0)).map(([c,p])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${c==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${d(c)}</span>
      <span class="flex-1 text-neutral-500">${b(J(p))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${I(p.cost)}</span></div>`).join(""),o=e.sessions||[],i=o.map(c=>`
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${c.id}</code>
      <span class="w-16 capitalize text-neutral-500">${c.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(c.instances||[]).join(", ")||"—"}</span>
      <span class="text-neutral-500">${b(c.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${I(c.cost)}</span></div>`).join("");return`<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Claude usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Across all sandbox Claude sessions. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${n("Total tokens",b(e.tokens))}
      ${n("Estimated cost",I(e.cost))}
      ${n("Sessions",b(o.length)+(o.length>=25?"+":""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${b(t.in)} · out ${b(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${b(t.cw)} · cache read ${b(t.cr)}</div>
    </div>
    <div class="mt-6">${g("By model")}${a||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${g("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${r||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${g("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${i}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const Le=J;function z(){const e=l.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
    <h1 class="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Give Claude a real WordPress</h1>
    <p class="mt-2 text-[14px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      The sandbox hands Claude a <b>live, disposable WordPress environment</b> to work in — so it
      stops guessing and starts proving. Through the connected tools Claude can run WP-CLI, hit the
      REST API and database, open pages in a real browser, read and edit your plugin's actual code,
      and watch the logs. Each item on the left is one isolated environment Claude can fully control.</p>

    <div class="mt-7 grid grid-cols-3 gap-3">
      ${M("Build","Claude implements features against a running install and verifies them live — not from memory.")}
      ${M("Reproduce","Hand Claude the environment and it reproduces the bug on a real stack — confirmed, not assumed.")}
      ${M("Fix &amp; prove","Reproduce → fix the real code → re-verify in the same environment. Fast, and proven.")}
    </div>

    <div class="mt-7 rounded-lg border border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 p-4">
      <div class="text-[13px] font-medium text-neutral-800 dark:text-neutral-100">How Claude works here</div>
      <p class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
        Just tell Claude <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">focus &lt;plugin&gt;</code>
        in chat — it picks the right environment, loads your plugin's code + context, and can build,
        debug, and fix it end-to-end. Every environment also exposes its own tools
        (<code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">mcp__sandbox-&lt;name&gt;__*</code>)
        so multiple Claude sessions can work in parallel without colliding.</p>
      <button onclick="sb.showHelp()" class="mt-2 text-[13px] text-accent dark:text-blue-400 hover:underline">How it works →</button>
    </div>

    <p class="mt-5 text-[12.5px] text-neutral-400">
      It's a real WordPress under the hood — break it, migrate it, throw it away. Snapshot or delete
      anytime; nothing here is precious.</p>

    ${e?`<p class="mt-6 text-[13px] text-neutral-400">${e} environment${e===1?"":"s"} ready — pick one on the left, or hand one to Claude.</p>`:'<button onclick="sb.doCreate()" class="mt-6 px-4 py-2 rounded-full bg-accent text-white text-[13px] font-medium hover:bg-blue-700">Create your first environment</button>'}
  </div>`}function _e(e){const t=e.domain||"",n=e.url.startsWith("https://"),a=t.endsWith(".sb");if(n)return`<span class="text-neutral-700 dark:text-neutral-200">${d(t)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;const r=a?`<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${U("./sb secure "+e.name)}
       </div>`:"";return`<span class="text-neutral-700 dark:text-neutral-200">${d(t)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${r}`}function Ie(e){var a;const t=l.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show Claude token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Claude usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${b(Le(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${I(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No Claude usage attributed to this instance yet.</div>'}function se(e){if(!e)return z();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${d(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${L()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${d(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=l.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,r=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?L():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?L("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${d(e.name)}</h1>
        ${Ce(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${d(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${ae(e.url+"/wp-admin","Admin",e.running)}
      ${ae(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${r}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?L():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${g("Overview")}
    ${y("Web server",`<span class="px-2.5 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800 text-[12.5px]">${d(e.server)}</span>`)}
    ${e.domain?y("Domain",_e(e)):""}
    ${y("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${y("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${y("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':d(e.project))}
    ${y("Focus plugin",`<div class="flex items-center gap-2">
        ${te("focusSel",[{v:"",label:"— none —"}].concat(l.data.plugins.map(o=>({v:o,label:o}))),e.focus&&e.focus!=="—"?e.focus:"",o=>window.sb.doFocus(e.name,o),!!t)}
        ${t==="focus"||t==="unfocus"?L():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${g("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${_(e.name,"logs","Logs")}
      ${_(e.name,"status","Status")}
      ${_(e.name,"doctor","Doctor")}
      ${_(e.name,"update","Update plugins")}
      <button class="${$}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${$}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${$}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${_(e.name,"xdebug","Xdebug",{state:"status"})}
    </div>
    <div class="flex gap-2 mt-2.5">
      <span class="px-2 py-1.5 text-[12.5px] font-mono text-neutral-400">wp</span>
      <input id="wpArgs" placeholder="plugin list --status=active"
        onkeydown="if(event.key==='Enter')sb.doWp('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] font-mono focus:border-accent outline-none">
      <button class="${$}" onclick="sb.doWp('${e.name}')">Run</button>
    </div>
  </div>

  <div class="px-6 pb-6">
    ${g("Plugins","Search to install from WordPress.org, or symlink a local source")}
    <div class="flex gap-2">
      <input id="plugQ" placeholder="Search plugins by name or slug…" oninput="sb.plugFilter()"
        onkeydown="if(event.key==='Enter')sb.doInstall('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">
      <button class="${$}" onclick="sb.doInstall('${e.name}')">Install from .org</button>
    </div>
    <div id="plugResults" class="mt-2 flex flex-col gap-1"></div>
  </div>

  <div class="px-6 pb-8">
    ${g("Use with Claude","Connect a Claude session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Claude in chat (simplest):</div>
      ${U("focus "+(e.focus&&e.focus!=="—"?e.focus:"<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${U("mcp__"+e.mcp_server+"__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${e.url}</code>.</div>
      ${Ie(e.name)}
    </div>
  </div>`}function V(){const e=T();return e.page==="instance"?e.name:null}function Ee(e){const t=e.name===V(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",a=l.busy[e.name];return`<a href="${D(e.name)}" data-link class="w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${d(e.name)}</span>
     ${a?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:""}</a>`}function re(){const e=l.data.instances;s("list").innerHTML=e.map(Ee).join("");const t=e.filter(n=>n.running).length;s("runcount").textContent=t+"/"+e.length,s("footstat").textContent=e.length?t+" of "+e.length+" running":"no instances yet"}let oe="";function Re(e){if(e.page==="instance"){const t=l.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,t.url,t.domain||"",l.busy[t.name]||"",l.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(l.usage?"loaded":"pending"):e.page}function je(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!s("modal").classList.contains("hidden"))}function De(e){switch(e.page){case"usage":return Te();case"instance":{const t=l.data.instances.find(n=>n.name===e.name)||null;return se(t)}case"home":{const t=l.data.instances[0];return t?se(t):z()}default:return z()}}function X(e){const t=T(),n=Re(t);!e&&n===oe||!e&&je()||(oe=n,s("detail").innerHTML=De(t))}function G(){re(),X(!0)}let B=null;function Ne(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${d(e.label||"")}</div>`;if(e.type==="select"){const n=ye(),a=e.options||[],r=a.map(i=>({v:i,label:i})),o=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${d(o)}">`+te(n,r,o,i=>{document.getElementById(`${n}_val`).value=i},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${e.key}" data-type="checkbox"
        ${e.value?"checked":""} class="accent-accent w-3.5 h-3.5">
      ${d(e.label||e.key||"")}</label>`;if(e.type==="checklist"){const a=(e.options||[]).map(r=>`
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${e.key}" value="${d(r.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${d(r.label)}</span>
          ${r.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${d(r.desc)}</span>`:""}
        </span></label>`).join("");return`<div data-checklist-group="${e.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${a||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`}const t=e.oninput?` oninput="${d(e.oninput)}"`:"";return`<input data-k="${e.key}" data-field="${d(e.key||"")}"${t}
    placeholder="${d(e.placeholder||"")}" value="${d(e.value||"")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function w(e={}){return new Promise(t=>{B=t,s("mTitle").textContent=e.title||"",s("mDesc").textContent=e.desc||"",s("mFields").innerHTML=(e.fields||[]).map(Ne).join("");const n=s("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),s("modal").classList.remove("hidden"),setTimeout(()=>{(s("mFields").querySelector("input,select")||n).focus()},30)})}function O(e){if(s("modal").classList.add("hidden"),B){const t=B;B=null,t(e)}}function Be(){const e={};return s("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),s("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function Oe(){s("mCancel").onclick=()=>O(null),s("mOk").onclick=()=>O(Be()),s("modal").addEventListener("keydown",e=>{e.key==="Enter"&&s("mOk").click(),e.key==="Escape"&&O(null)}),s("modal").addEventListener("click",e=>{e.target===s("modal")&&O(null)})}const le={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function u(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(le[t]||le.info),n.textContent=e,s("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let de=async()=>{};function Pe(e){de=e}function Ae(e){s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent=e,s("conBody").textContent="",s("conInputRow").classList.add("hidden"),s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function Q(){s("console").classList.add("w-0"),s("console").classList.remove("w-[26rem]"),s("conInputRow").classList.add("hidden")}function v(e){const t=s("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function We(e,t){s("conTitle").textContent=e,s("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function ie(e,t,n){l.paused=!0,Ae(n||"Working…");let a=0,r=!!n;const o=setInterval(async()=>{var c;const i=await ee(e,a);!r&&i.status&&(s("conTitle").textContent=i.status.replace(/ [✓✗]$/,""),r=!0),i.chunk?(v(i.chunk),a=(c=i.offset)!=null?c:a):typeof i.offset=="number"&&(a=i.offset),i.done&&(clearInterval(o),l.paused=!1,t&&delete l.busy[t],We(i.status||"done",i.ok),u(i.status||"done",i.ok?"ok":"err"),await de())},800)}let K=null;const S=[];let m=-1,E=!1;function P(e){K=e,s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent="Terminal — "+e,s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",s("conBody").textContent.trim()||v("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),s("conInputRow").classList.remove("hidden"),setTimeout(()=>s("conInput").focus(),60)}async function Fe(){if(E)return;const e=s("conInput"),t=e.value.trim();if(!t||!K)return;S.push(t),m=S.length,e.value="",v("› "+t+`
`),E=!0,s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await q({instance:K,action:"term",cmd:t})}catch(o){v("error: "+o+`
`),E=!1;return}if(!n.job_id){v((n.output||"failed")+`
`),E=!1;return}let a=0;const r=setInterval(async()=>{var i;const o=await ee(n.job_id,a);o.chunk?(v(o.chunk),a=(i=o.offset)!=null?i:a):typeof o.offset=="number"&&(a=o.offset),o.done&&(clearInterval(r),E=!1,v(`
`),s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function qe(e){const t=s("conInput");e.key==="Enter"?Fe():e.key==="ArrowUp"?(m>0&&(m--,t.value=S[m]||""),e.preventDefault()):e.key==="ArrowDown"&&(m<S.length-1?(m++,t.value=S[m]||""):(m=S.length,t.value=""),e.preventDefault())}function He(){s("conClose").onclick=Q,s("conInput").addEventListener("keydown",e=>qe(e))}let ce=async()=>{},A=()=>{};function Ue(e){ce=e.refresh,A=e.render}const ue={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function k(e,t,n={}){l.busy[e]=t,A();let a;try{a=await q(Object.assign({instance:e,action:t},n))}catch(r){delete l.busy[e],u("request failed: "+r,"err"),A();return}if(a.job_id){const r=ue[t]?ue[t](e):F(t)+" "+e;u(t.replace("-"," ")+" started…","info"),ie(a.job_id,e,r)}else delete l.busy[e],a.ok?u(F(t)+" "+e+" ✓","ok"):u((a.output||"failed").split(`
`)[0],"err"),await ce()}async function C(e,t,n={}){let a;try{a=await q(Object.assign({instance:e,action:t},n))}catch(r){u("request failed: "+r,"err");return}if(a.job_id){const r={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};ie(a.job_id,null,(r[t]||F(t))+" — "+e)}else u((a.output||"failed").split(`
`)[0],"err")}async function Me(e,t){t===""?k(e,"unfocus"):t&&k(e,"focus",{slug:t})}async function Je(e){const t=await w({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?k(e,"delete",{confirm:e}):t&&u("name did not match — not deleted","err")}function ze(e){const n=s("wpArgs").value.trim();if(!n){u("enter a wp-cli command","err");return}C(e,"wp",{args:n})}async function Ve(e){const t=await w({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&C(e,"snapshot",{name:t.name})}async function Xe(e){let t=[];try{t=(await fe(e)).snapshots||[]}catch(a){}if(!t.length){u("no snapshots for "+e,"err");return}const n=await w({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&C(e,"restore",{name:n.name})}async function Ge(e){const t=l.data.seeds||[];if(!t.length){u("no WXR files in runtime/seeds/","err");return}const n=await w({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&C(e,"seed",{file:n.file})}function Qe(e){const t=(s("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){u("type a plugin slug to install","err");return}C(e,"install",{slug:t})}function Ke(e){const t=(s("plugQ").value||"").toLowerCase().trim(),n=s("plugResults");if(!t){n.innerHTML="";return}const a=l.data.instances.find(o=>o.name===e),r=(l.data.plugins||[]).filter(o=>o.toLowerCase().includes(t)).slice(0,8);n.innerHTML=r.map(o=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${d(o)}</span>
      ${a&&a.focus===o?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${d(o)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${d(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function Ye(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function Ze(){try{l.usage=await Z()}catch(e){l.usage={available:!1}}A()}let Y=!1;function et(e){if(Y)return;const t=document.querySelector('#mFields [data-field="domain"]');if(!t)return;const n=(e.value||"").trim().toLowerCase().replace(/[^a-z0-9-]/g,"-").replace(/^-+|-+$/g,"");t.value=n?n+".sb":""}function tt(){Y=!0}async function pe(){Y=!1;const e="Name it, pick a web server, then optionally add plugins and demo content. It'll serve at a clean http://<name>.sb (no port). Want HTTPS? run `./sb secure <name>` after.",t=(l.data.projects||[]).map(x=>({value:x.name,label:x.name,desc:x.description||(x.plugins||[]).join(", ")})),n=["none",...l.data.seeds||[]],a=[{type:"label",label:"Basics"},{key:"name",placeholder:"name (a-z, 0-9, -)",oninput:"sb.syncDomainFromName(this)"},{key:"server",type:"select",options:l.data.servers},{key:"domain",placeholder:"domain — defaults to <name>.sb",oninput:"sb.domainEdited()"},{type:"label",label:"Plugins (optional)"},{key:"plugins",type:"checklist",options:t},{type:"label",label:"Content & options (optional)"},{key:"seed",type:"select",options:n},{key:"site_title",placeholder:"site title — defaults to “Sandbox <name>”"},{key:"theme",placeholder:"theme slug (optional, e.g. astra)"},{key:"wp_debug",type:"checkbox",label:"Enable WP_DEBUG"}],r=await w({title:"New instance",okText:"Create",desc:e,fields:a});if(!r||!r.name)return;const o=String(r.name).trim(),i=String(r.domain||"").trim().toLowerCase(),c=String(r.seed||""),p=c&&c!=="none"?c:"",W=r.plugins||[];l.data.instances.find(x=>x.name===o)||l.data.instances.push({name:o,running:!1,pending:!0,server:String(r.server),url:"",mcp_server:"sandbox-"+o,project:"—",focus:"—",domain:i,wordpress_port:"",mailpit_port:""}),l.busy[o]="create",f(D(o)),k(o,"create",{name:o,server:r.server,domain:i,plugins:W,seed:p,site_title:String(r.site_title||"").trim(),theme:String(r.theme||"").trim(),wp_debug:!!r.wp_debug})}async function R(){if(l.paused)return;let e;try{e=await me()}catch(t){return}l.data=e,re(),X(!1)}async function nt(){f("/usage"),s("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading Claude usage…</div>';try{l.usage=await Z()}catch(e){l.usage={available:!1}}X(!0)}function at(){f("/")}function st(e){f(D(e))}function be(){w({title:"How Claude works here",okText:"Got it",desc:`The sandbox gives Claude a live WordPress to act in, so it can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — Claude picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. Each environment also has its own tool namespace (mcp__sandbox__* = main, mcp__sandbox-<name>__* = that one) so parallel sessions never collide. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const rt={navigate:f,goHome:at,selectInstance:st,showUsage:nt,showHelp:be,openTerminal:P,doCreate:pe,doDelete:Je,doFocus:Me,doSnapshot:Ve,doRestore:Xe,doSeed:Ge,doWp:ze,doInstall:Qe,plugFilter:()=>Ke(V()),loadUsageThenRender:Ze,act:k,op:C,syncDomainFromName:et,domainEdited:tt,cselToggle:$e,cselPick:we,cselFilter:ne,consoleClose:Q,copyText:Ye};window.sb=rt;function ot(){Ue({refresh:R,render:G}),Pe(R),Oe(),He(),Se(),ke(),s("newBtn").onclick=pe,s("startAll").onclick=()=>k("*","start-all"),s("stopAll").onclick=()=>k("*","stop-all"),s("helpBtn").onclick=be,s("termBtn").onclick=()=>{const t=V()||l.data.instances[0]&&l.data.instances[0].name;if(!t){u("create an instance first","err");return}f(D(t,!0)),P(t)},ve(t=>{G(),t.page==="instance"&&t.console?P(t.name):Q()}),G();const e=T();e.page==="instance"&&e.console&&P(e.name),dt()}const lt=5e3;function dt(){R(),window.setInterval(()=>{document.visibilityState==="visible"&&R()},lt),document.addEventListener("visibilitychange",()=>{document.visibilityState==="visible"&&R()})}ot()})();
