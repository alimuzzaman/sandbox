(function(){"use strict";const s=e=>document.getElementById(e),xe={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},i=e=>String(e).replace(/[&<>"]/g,t=>xe[t]),W=e=>e.charAt(0).toUpperCase()+e.slice(1),l={data:{instances:[],plugins:[],projects:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1},busy:{},usage:null,paused:!1};async function E(e){return(await fetch(e)).json()}const me=()=>E("/api/instances"),Y=()=>E("/api/usage"),Z=(e,t)=>E(`/api/job/${e}?offset=${t}`),fe=e=>E(`/api/snapshots/${e}`);async function q(e){return(await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)})).json()}function ge(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const S=()=>ge(location.pathname);let H=()=>{};function ke(e){H=e}function f(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),H(S())}function R(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}function he(){window.addEventListener("popstate",()=>H(S())),document.addEventListener("click",e=>{var a,o;const t=(o=(a=e.target)==null?void 0:a.closest)==null?void 0:o.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),f(n))})}let ve=0;const j={};function ye(){return"mcsel"+ ++ve}function ee(e,t,n,a,o,r){const d=t.find(p=>p.v===n),c=d?d.label:t[0]?t[0].label:"";j[e]=a;const u=o?"opacity-50 pointer-events-none":"",F=r?"block w-full":"inline-block",P=r?"w-full":"w-48";return`<div class="relative ${F}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${P} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${u}">
      <span class="truncate flex-1" data-csel-label>${i(c)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${P} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(p=>`<button type="button" data-v="${i(p.v)}" data-search="${i(p.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${i(p.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${p.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${i(p.label)}</button>`).join("")}
      </div>
    </div></div>`}function $e(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",te(e),n.focus())}}function we(e,t){var o,r;(o=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||o.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(r=j[e])==null||r.call(j,t)}function te(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const o=a;o.style.display=(o.dataset.search||"").includes(n)?"":"none"})}function Ce(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function T(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function Se(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function g(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const v=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function ne(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const y="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function L(e,t,n,a){return`<button class="${y}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function ae(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${i(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function U(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const x=e=>(e||0).toLocaleString(),_=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),M=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function Te(){const e=l.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No Claude session data found.</div>`;const t=e.total||{},n=(c,u,F)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${c}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${u}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([c,u])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${c}</span>
      <span class="flex-1 text-neutral-500">${x(M(u))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${_(u.cost)}</span></div>`).join(""),o=Object.entries(e.per_instance||{}).sort((c,u)=>(u[1].cost||0)-(c[1].cost||0)).map(([c,u])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${c==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${i(c)}</span>
      <span class="flex-1 text-neutral-500">${x(M(u))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${_(u.cost)}</span></div>`).join(""),r=e.sessions||[],d=r.map(c=>`
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${c.id}</code>
      <span class="w-16 capitalize text-neutral-500">${c.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(c.instances||[]).join(", ")||"—"}</span>
      <span class="text-neutral-500">${x(c.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${_(c.cost)}</span></div>`).join("");return`<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Claude usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Across all sandbox Claude sessions. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${n("Total tokens",x(e.tokens))}
      ${n("Estimated cost",_(e.cost))}
      ${n("Sessions",x(r.length)+(r.length>=25?"+":""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${x(t.in)} · out ${x(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${x(t.cw)} · cache read ${x(t.cr)}</div>
    </div>
    <div class="mt-6">${g("By model")}${a||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${g("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${o||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${g("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${d}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const Le=M;function J(){const e=l.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
    <h1 class="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Give Claude a real WordPress</h1>
    <p class="mt-2 text-[14px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      The sandbox hands Claude a <b>live, disposable WordPress environment</b> to work in — so it
      stops guessing and starts proving. Through the connected tools Claude can run WP-CLI, hit the
      REST API and database, open pages in a real browser, read and edit your plugin's actual code,
      and watch the logs. Each item on the left is one isolated environment Claude can fully control.</p>

    <div class="mt-7 grid grid-cols-3 gap-3">
      ${U("Build","Claude implements features against a running install and verifies them live — not from memory.")}
      ${U("Reproduce","Hand Claude the environment and it reproduces the bug on a real stack — confirmed, not assumed.")}
      ${U("Fix &amp; prove","Reproduce → fix the real code → re-verify in the same environment. Fast, and proven.")}
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
  </div>`}function _e(e){var a;const t=l.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show Claude token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Claude usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${x(Le(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${_(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No Claude usage attributed to this instance yet.</div>'}function se(e){if(!e)return J();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${i(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${T()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${i(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=l.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,o=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?T():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?T("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${i(e.name)}</h1>
        ${Se(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${i(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${ne(e.url+"/wp-admin","Admin",e.running)}
      ${ne(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${o}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?T():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${g("Overview")}
    ${v("Web server",`<span class="px-2.5 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800 text-[12.5px]">${i(e.server)}</span>`)}
    ${e.domain?v("Domain",`<span class="text-neutral-700 dark:text-neutral-200">${i(e.domain)}</span>
        <span class="text-[11px] text-neutral-400">→ 127.0.0.1</span>`):""}
    ${v("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${v("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${v("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':i(e.project))}
    ${v("Focus plugin",`<div class="flex items-center gap-2">
        ${ee("focusSel",[{v:"",label:"— none —"}].concat(l.data.plugins.map(r=>({v:r,label:r}))),e.focus&&e.focus!=="—"?e.focus:"",r=>window.sb.doFocus(e.name,r),!!t)}
        ${t==="focus"||t==="unfocus"?T():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${g("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${L(e.name,"logs","Logs")}
      ${L(e.name,"status","Status")}
      ${L(e.name,"doctor","Doctor")}
      ${L(e.name,"update","Update plugins")}
      <button class="${y}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${y}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${y}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${L(e.name,"xdebug","Xdebug",{state:"status"})}
    </div>
    <div class="flex gap-2 mt-2.5">
      <span class="px-2 py-1.5 text-[12.5px] font-mono text-neutral-400">wp</span>
      <input id="wpArgs" placeholder="plugin list --status=active"
        onkeydown="if(event.key==='Enter')sb.doWp('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] font-mono focus:border-accent outline-none">
      <button class="${y}" onclick="sb.doWp('${e.name}')">Run</button>
    </div>
  </div>

  <div class="px-6 pb-6">
    ${g("Plugins","Search to install from WordPress.org, or symlink a local source")}
    <div class="flex gap-2">
      <input id="plugQ" placeholder="Search plugins by name or slug…" oninput="sb.plugFilter()"
        onkeydown="if(event.key==='Enter')sb.doInstall('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">
      <button class="${y}" onclick="sb.doInstall('${e.name}')">Install from .org</button>
    </div>
    <div id="plugResults" class="mt-2 flex flex-col gap-1"></div>
  </div>

  <div class="px-6 pb-8">
    ${g("Use with Claude","Connect a Claude session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Claude in chat (simplest):</div>
      ${ae("focus "+(e.focus&&e.focus!=="—"?e.focus:"<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${ae("mcp__"+e.mcp_server+"__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${e.url}</code>.</div>
      ${_e(e.name)}
    </div>
  </div>`}function z(){const e=S();return e.page==="instance"?e.name:null}function Ie(e){const t=e.name===z(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",a=l.busy[e.name];return`<a href="${R(e.name)}" data-link class="w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${i(e.name)}</span>
     ${a?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:""}</a>`}function re(){const e=l.data.instances;s("list").innerHTML=e.map(Ie).join("");const t=e.filter(n=>n.running).length;s("runcount").textContent=t+"/"+e.length,s("footstat").textContent=e.length?t+" of "+e.length+" running":"no instances yet"}let oe="";function Ee(e){if(e.page==="instance"){const t=l.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,l.busy[t.name]||"",l.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(l.usage?"loaded":"pending"):e.page}function Re(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!s("modal").classList.contains("hidden"))}function je(e){switch(e.page){case"usage":return Te();case"instance":{const t=l.data.instances.find(n=>n.name===e.name)||null;return se(t)}case"home":{const t=l.data.instances[0];return t?se(t):J()}default:return J()}}function V(e){const t=S(),n=Ee(t);!e&&n===oe||!e&&Re()||(oe=n,s("detail").innerHTML=je(t))}function X(){re(),V(!0)}let D=null;function De(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${i(e.label||"")}</div>`;if(e.type==="select"){const n=ye(),a=e.options||[],o=a.map(d=>({v:d,label:d})),r=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${i(r)}">`+ee(n,o,r,d=>{document.getElementById(`${n}_val`).value=d},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${e.key}" data-type="checkbox"
        ${e.value?"checked":""} class="accent-accent w-3.5 h-3.5">
      ${i(e.label||e.key||"")}</label>`;if(e.type==="checklist"){const a=(e.options||[]).map(o=>`
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${e.key}" value="${i(o.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${i(o.label)}</span>
          ${o.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${i(o.desc)}</span>`:""}
        </span></label>`).join("");return`<div data-checklist-group="${e.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${a||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`}const t=e.oninput?` oninput="${i(e.oninput)}"`:"";return`<input data-k="${e.key}" data-field="${i(e.key||"")}"${t}
    placeholder="${i(e.placeholder||"")}" value="${i(e.value||"")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function $(e={}){return new Promise(t=>{D=t,s("mTitle").textContent=e.title||"",s("mDesc").textContent=e.desc||"",s("mFields").innerHTML=(e.fields||[]).map(De).join("");const n=s("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),s("modal").classList.remove("hidden"),setTimeout(()=>{(s("mFields").querySelector("input,select")||n).focus()},30)})}function N(e){if(s("modal").classList.add("hidden"),D){const t=D;D=null,t(e)}}function Ne(){const e={};return s("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),s("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function Be(){s("mCancel").onclick=()=>N(null),s("mOk").onclick=()=>N(Ne()),s("modal").addEventListener("keydown",e=>{e.key==="Enter"&&s("mOk").click(),e.key==="Escape"&&N(null)}),s("modal").addEventListener("click",e=>{e.target===s("modal")&&N(null)})}const le={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function b(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(le[t]||le.info),n.textContent=e,s("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let de=async()=>{};function Oe(e){de=e}function Ae(e){s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent=e,s("conBody").textContent="",s("conInputRow").classList.add("hidden"),s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function G(){s("console").classList.add("w-0"),s("console").classList.remove("w-[26rem]"),s("conInputRow").classList.add("hidden")}function k(e){const t=s("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function Fe(e,t){s("conTitle").textContent=e,s("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function ie(e,t,n){l.paused=!0,Ae(n||"Working…");let a=0,o=!!n;const r=setInterval(async()=>{var c;const d=await Z(e,a);!o&&d.status&&(s("conTitle").textContent=d.status.replace(/ [✓✗]$/,""),o=!0),d.chunk?(k(d.chunk),a=(c=d.offset)!=null?c:a):typeof d.offset=="number"&&(a=d.offset),d.done&&(clearInterval(r),l.paused=!1,t&&delete l.busy[t],Fe(d.status||"done",d.ok),b(d.status||"done",d.ok?"ok":"err"),await de())},800)}let Q=null;const w=[];let m=-1,I=!1;function B(e){Q=e,s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent="Terminal — "+e,s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",s("conBody").textContent.trim()||k("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),s("conInputRow").classList.remove("hidden"),setTimeout(()=>s("conInput").focus(),60)}async function Pe(){if(I)return;const e=s("conInput"),t=e.value.trim();if(!t||!Q)return;w.push(t),m=w.length,e.value="",k("› "+t+`
`),I=!0,s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await q({instance:Q,action:"term",cmd:t})}catch(r){k("error: "+r+`
`),I=!1;return}if(!n.job_id){k((n.output||"failed")+`
`),I=!1;return}let a=0;const o=setInterval(async()=>{var d;const r=await Z(n.job_id,a);r.chunk?(k(r.chunk),a=(d=r.offset)!=null?d:a):typeof r.offset=="number"&&(a=r.offset),r.done&&(clearInterval(o),I=!1,k(`
`),s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function We(e){const t=s("conInput");e.key==="Enter"?Pe():e.key==="ArrowUp"?(m>0&&(m--,t.value=w[m]||""),e.preventDefault()):e.key==="ArrowDown"&&(m<w.length-1?(m++,t.value=w[m]||""):(m=w.length,t.value=""),e.preventDefault())}function qe(){s("conClose").onclick=G,s("conInput").addEventListener("keydown",e=>We(e))}let ce=async()=>{},O=()=>{};function He(e){ce=e.refresh,O=e.render}const ue={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function h(e,t,n={}){l.busy[e]=t,O();let a;try{a=await q(Object.assign({instance:e,action:t},n))}catch(o){delete l.busy[e],b("request failed: "+o,"err"),O();return}if(a.job_id){const o=ue[t]?ue[t](e):W(t)+" "+e;b(t.replace("-"," ")+" started…","info"),ie(a.job_id,e,o)}else delete l.busy[e],a.ok?b(W(t)+" "+e+" ✓","ok"):b((a.output||"failed").split(`
`)[0],"err"),await ce()}async function C(e,t,n={}){let a;try{a=await q(Object.assign({instance:e,action:t},n))}catch(o){b("request failed: "+o,"err");return}if(a.job_id){const o={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};ie(a.job_id,null,(o[t]||W(t))+" — "+e)}else b((a.output||"failed").split(`
`)[0],"err")}async function Ue(e,t){t===""?h(e,"unfocus"):t&&h(e,"focus",{slug:t})}async function Me(e){const t=await $({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?h(e,"delete",{confirm:e}):t&&b("name did not match — not deleted","err")}function Je(e){const n=s("wpArgs").value.trim();if(!n){b("enter a wp-cli command","err");return}C(e,"wp",{args:n})}async function ze(e){const t=await $({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&C(e,"snapshot",{name:t.name})}async function Ve(e){let t=[];try{t=(await fe(e)).snapshots||[]}catch(a){}if(!t.length){b("no snapshots for "+e,"err");return}const n=await $({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&C(e,"restore",{name:n.name})}async function Xe(e){const t=l.data.seeds||[];if(!t.length){b("no WXR files in runtime/seeds/","err");return}const n=await $({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&C(e,"seed",{file:n.file})}function Ge(e){const t=(s("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){b("type a plugin slug to install","err");return}C(e,"install",{slug:t})}function Qe(e){const t=(s("plugQ").value||"").toLowerCase().trim(),n=s("plugResults");if(!t){n.innerHTML="";return}const a=l.data.instances.find(r=>r.name===e),o=(l.data.plugins||[]).filter(r=>r.toLowerCase().includes(t)).slice(0,8);n.innerHTML=o.map(r=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${i(r)}</span>
      ${a&&a.focus===r?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${i(r)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${i(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function Ke(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function Ye(){try{l.usage=await Y()}catch(e){l.usage={available:!1}}O()}let K=!1;function Ze(e){if(K)return;const t=document.querySelector('#mFields [data-field="domain"]');if(!t)return;const n=(e.value||"").trim().toLowerCase().replace(/[^a-z0-9-]/g,"-").replace(/^-+|-+$/g,"");t.value=n?n+".sb":""}function et(){K=!0}async function pe(){K=!1;const t=l.data.domains_ready?"Name it, pick a web server, then optionally add plugins and demo content. The domain fills in from the name.":"Name it, pick a web server, then optionally add plugins and demo content. Tip: run `./sb domains setup` once for trusted no-port HTTPS.",n=(l.data.projects||[]).map(p=>({value:p.name,label:p.name,desc:p.description||(p.plugins||[]).join(", ")})),a=["none",...l.data.seeds||[]],o=[{type:"label",label:"Basics"},{key:"name",placeholder:"name (a-z, 0-9, -)",oninput:"sb.syncDomainFromName(this)"},{key:"server",type:"select",options:l.data.servers},{key:"domain",placeholder:"domain — defaults to <name>.sb",oninput:"sb.domainEdited()"},{type:"label",label:"Plugins (optional)"},{key:"plugins",type:"checklist",options:n},{type:"label",label:"Content & options (optional)"},{key:"seed",type:"select",options:a},{key:"site_title",placeholder:"site title — defaults to “Sandbox <name>”"},{key:"theme",placeholder:"theme slug (optional, e.g. astra)"},{key:"wp_debug",type:"checkbox",label:"Enable WP_DEBUG"}],r=await $({title:"New instance",okText:"Create",desc:t,fields:o});if(!r||!r.name)return;const d=String(r.name).trim(),c=String(r.domain||"").trim().toLowerCase(),u=String(r.seed||""),F=u&&u!=="none"?u:"",P=r.plugins||[];l.data.instances.find(p=>p.name===d)||l.data.instances.push({name:d,running:!1,pending:!0,server:String(r.server),url:"",mcp_server:"sandbox-"+d,project:"—",focus:"—",domain:c,wordpress_port:"",mailpit_port:""}),l.busy[d]="create",f(R(d)),h(d,"create",{name:d,server:r.server,domain:c,plugins:P,seed:F,site_title:String(r.site_title||"").trim(),theme:String(r.theme||"").trim(),wp_debug:!!r.wp_debug})}async function A(){if(l.paused)return;let e;try{e=await me()}catch(t){return}l.data=e,re(),V(!1)}async function tt(){f("/usage"),s("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading Claude usage…</div>';try{l.usage=await Y()}catch(e){l.usage={available:!1}}V(!0)}function nt(){f("/")}function at(e){f(R(e))}function be(){$({title:"How Claude works here",okText:"Got it",desc:`The sandbox gives Claude a live WordPress to act in, so it can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — Claude picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. Each environment also has its own tool namespace (mcp__sandbox__* = main, mcp__sandbox-<name>__* = that one) so parallel sessions never collide. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const st={navigate:f,goHome:nt,selectInstance:at,showUsage:tt,showHelp:be,openTerminal:B,doCreate:pe,doDelete:Me,doFocus:Ue,doSnapshot:ze,doRestore:Ve,doSeed:Xe,doWp:Je,doInstall:Ge,plugFilter:()=>Qe(z()),loadUsageThenRender:Ye,act:h,op:C,syncDomainFromName:Ze,domainEdited:et,cselToggle:$e,cselPick:we,cselFilter:te,consoleClose:G,copyText:Ke};window.sb=st;function rt(){He({refresh:A,render:X}),Oe(A),Be(),qe(),Ce(),he(),s("newBtn").onclick=pe,s("startAll").onclick=()=>h("*","start-all"),s("stopAll").onclick=()=>h("*","stop-all"),s("helpBtn").onclick=be,s("termBtn").onclick=()=>{const t=z()||l.data.instances[0]&&l.data.instances[0].name;if(!t){b("create an instance first","err");return}f(R(t,!0)),B(t)},ke(t=>{X(),t.page==="instance"&&t.console?B(t.name):G()}),X();const e=S();e.page==="instance"&&e.console&&B(e.name),A(),setInterval(A,2e3)}rt()})();
