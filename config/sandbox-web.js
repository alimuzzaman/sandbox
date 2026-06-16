(function(){"use strict";const r=e=>document.getElementById(e),fe={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},i=e=>String(e).replace(/[&<>"]/g,t=>fe[t]),q=e=>e.charAt(0).toUpperCase()+e.slice(1),l={data:{instances:[],plugins:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1},busy:{},usage:null,paused:!1};async function E(e){return(await fetch(e)).json()}const me=()=>E("/api/instances"),ee=()=>E("/api/usage"),te=(e,t)=>E(`/api/job/${e}?offset=${t}`),ge=e=>E(`/api/snapshots/${e}`);async function M(e){return(await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)})).json()}function he(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="create"&&t.length===1?{page:"create"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const T=()=>he(location.pathname);let W=()=>{};function ve(e){W=e}function m(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),W(T())}function D(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}function ke(){window.addEventListener("popstate",()=>W(T())),document.addEventListener("click",e=>{var a,s;const t=(s=(a=e.target)==null?void 0:a.closest)==null?void 0:s.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),m(n))})}let ye=0;const O={};function we(){return"mcsel"+ ++ye}function H(e,t,n,a,s,o){const d=t.find(k=>k.v===n),c=d?d.label:t[0]?t[0].label:"";O[e]=a;const p=s?"opacity-50 pointer-events-none":"",be=o?"block w-full":"inline-block",xe=o?"w-full":"w-48";return`<div class="relative ${be}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${xe} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${p}">
      <span class="truncate flex-1" data-csel-label>${i(c)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${xe} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(k=>`<button type="button" data-v="${i(k.v)}" data-search="${i(k.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${i(k.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${k.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${i(k.label)}</button>`).join("")}
      </div>
    </div></div>`}function $e(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",ne(e),n.focus())}}function Ce(e,t){var s,o;(s=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||s.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(o=O[e])==null||o.call(O,t)}function ne(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const s=a;s.style.display=(s.dataset.search||"").includes(n)?"":"none"})}function Se(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function y(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function Le(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function g(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const w=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function F(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const $="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function j(e,t,n,a){return`<button class="${$}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function U(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${i(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function J(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const b=e=>(e||0).toLocaleString(),I=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),V=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function Te(){const e=l.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No Claude session data found.</div>`;const t=e.total||{},n=(c,p,be)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${c}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${p}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([c,p])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${c}</span>
      <span class="flex-1 text-neutral-500">${b(V(p))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${I(p.cost)}</span></div>`).join(""),s=Object.entries(e.per_instance||{}).sort((c,p)=>(p[1].cost||0)-(c[1].cost||0)).map(([c,p])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${c==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${i(c)}</span>
      <span class="flex-1 text-neutral-500">${b(V(p))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${I(p.cost)}</span></div>`).join(""),o=e.sessions||[],d=o.map(c=>`
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
    <div class="mt-6">${g("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${s||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${g("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${d}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const je=V;function z(){const e=l.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
    <h1 class="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Give Claude a real WordPress</h1>
    <p class="mt-2 text-[14px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      The sandbox hands Claude a <b>live, disposable WordPress environment</b> to work in — so it
      stops guessing and starts proving. Through the connected tools Claude can run WP-CLI, hit the
      REST API and database, open pages in a real browser, read and edit your plugin's actual code,
      and watch the logs. Each item on the left is one isolated environment Claude can fully control.</p>

    <div class="mt-7 grid grid-cols-3 gap-3">
      ${J("Build","Claude implements features against a running install and verifies them live — not from memory.")}
      ${J("Reproduce","Hand Claude the environment and it reproduces the bug on a real stack — confirmed, not assumed.")}
      ${J("Fix &amp; prove","Reproduce → fix the real code → re-verify in the same environment. Fast, and proven.")}
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
  </div>`}function Ie(e){const t=e.domain||"",n=e.url.startsWith("https://"),a=t.endsWith(".tst");if(n)return`<span class="text-neutral-700 dark:text-neutral-200">${i(t)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;const s=a?`<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${U("./sb secure "+e.name)}
       </div>`:"";return`<span class="text-neutral-700 dark:text-neutral-200">${i(t)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${s}`}function Re(e){var a;const t=l.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show Claude token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Claude usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${b(je(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${I(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No Claude usage attributed to this instance yet.</div>'}function ae(e){if(!e)return z();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${i(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${y()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${i(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=l.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,s=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?y():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?y("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${i(e.name)}</h1>
        ${Le(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${i(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${e.login_url?F(e.login_url,"Login",e.running):F(e.url+"/wp-admin","Admin",e.running)}
      ${F(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${s}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?y():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${g("Overview")}
    ${w("Web server",`<div class="flex items-center gap-2">
        ${H("serverSel",(l.data.servers&&l.data.servers.length?l.data.servers:["apache","nginx","litespeed"]).map(o=>({v:o,label:o})),e.server,o=>window.sb.doServer(e.name,o),!!t||!e.running)}
        ${t==="server"?y():""}
        ${e.running?"":'<span class="text-[11px] text-neutral-400">start the site to switch</span>'}</div>`)}
    ${e.domain?w("Domain",Ie(e)):""}
    ${w("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${w("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${w("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':i(e.project))}
    ${w("Focus plugin",`<div class="flex items-center gap-2">
        ${H("focusSel",[{v:"",label:"— none —"}].concat(l.data.plugins.map(o=>({v:o,label:o}))),e.focus&&e.focus!=="—"?e.focus:"",o=>window.sb.doFocus(e.name,o),!!t)}
        ${t==="focus"||t==="unfocus"?y():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${g("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${j(e.name,"logs","Logs")}
      ${j(e.name,"status","Status")}
      ${j(e.name,"doctor","Doctor")}
      ${j(e.name,"update","Update plugins")}
      <button class="${$}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${$}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${$}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${j(e.name,"xdebug","Xdebug",{state:"status"})}
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
      ${Re(e.name)}
    </div>
  </div>`}function re(){m("/create")}function _e(){return`<div class="max-w-2xl mx-auto px-6 py-8">
    <a href="/" data-link class="inline-flex items-center gap-1 text-[12.5px] text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300">
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Back</a>

    <h1 class="mt-3 text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Create an instance</h1>
    <p class="mt-2 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      Sandbox is per-project: each plugin repo carries its own
      <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">sandbox.config.json</code>,
      and instances are created from the CLI — one per project directory — not from the dashboard.</p>

    <div class="mt-5 rounded border border-brdin dark:border-neutral-700 p-4 bg-app dark:bg-neutral-900">
      <p class="text-[12.5px] text-neutral-600 dark:text-neutral-400 mb-2">In a plugin repo:</p>
      <pre class="text-[13px] leading-relaxed text-neutral-800 dark:text-neutral-200"><code>cd &lt;plugin-repo&gt;
./sb init     # scaffold config, boot an instance, provision the test harness
./sb test     # run its phpunit tests</code></pre>
    </div>

    <p class="mt-4 text-[12.5px] text-neutral-400">The instance appears here once it boots (this list refreshes automatically).</p>

    <div class="mt-7">
      <a href="/" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Back to instances</a>
    </div>
  </div>`}function Be(e,t){const n=t.map(a=>{const s=a.disabled?"opacity-40 pointer-events-none":"",o=a.danger?"text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40":"text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800";return`<button type="button" ${a.disabled?"disabled":""}
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuClose();${a.js}"
      class="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left ${o} ${s}">
      ${a.icon?`<span class="w-3.5 h-3.5 grid place-items-center shrink-0 opacity-70">${a.icon}</span>`:""}
      <span class="flex-1">${i(a.label)}</span></button>`}).join("");return`<span class="relative shrink-0" data-rowmenu="${e}">
    <button type="button" title="More actions" aria-label="More actions"
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuToggle('${e}')"
      class="w-6 h-6 grid place-items-center rounded text-neutral-500 dark:text-neutral-400
      hover:bg-neutral-200 dark:hover:bg-neutral-700 hover:text-neutral-900 dark:hover:text-neutral-100">
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
    </button>
    <div data-rowmenu-pop class="hidden absolute right-0 z-[60] mt-1 min-w-[10rem] py-1
      rounded-lg border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl">
      ${n}</div></span>`}function Ee(e){var n;let t=!1;document.querySelectorAll("[data-rowmenu-pop]").forEach(a=>{const s=a.closest("[data-rowmenu]"),o=!a.classList.contains("hidden");s&&s.dataset.rowmenu===e&&(t=o),a.classList.add("hidden")}),!t&&((n=document.querySelector(`[data-rowmenu="${e}"] [data-rowmenu-pop]`))==null||n.classList.remove("hidden"))}function se(){document.querySelectorAll("[data-rowmenu-pop]").forEach(e=>e.classList.add("hidden"))}function Me(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-rowmenu]")||se()})}function X(){const e=T();return e.page==="instance"?e.name:null}const x={play:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',stop:'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',restart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',admin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',term:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7l4 4-4 4"/><path d="M12 15h6"/></svg>',snapshot:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>',restore:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8"/><path d="M3 3v5h5"/></svg>',trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>'};function De(e){const t=JSON.stringify(e.name),n=[e.running?{label:"Stop",icon:x.stop,js:`sb.act(${t},'stop')`}:{label:"Start",icon:x.play,js:`sb.act(${t},'start')`},{label:"Restart",icon:x.restart,js:`sb.act(${t},'restart')`,disabled:!e.running},{label:"Open admin",icon:x.admin,js:`window.open(${JSON.stringify(e.url+"/wp-admin")},'_blank')`,disabled:!e.running},{label:"Console",icon:x.term,js:`sb.navigate(${JSON.stringify(D(e.name,!0))})`,disabled:!e.running},{label:"Snapshot",icon:x.snapshot,js:`sb.doSnapshot(${t})`},{label:"Restore…",icon:x.restore,js:`sb.doRestore(${t})`}];return e.name!=="main"&&n.push({label:"Delete",icon:x.trash,js:`sb.doDelete(${t})`,danger:!0}),n}function Oe(e){const t=e.name===X(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",s=l.busy[e.name]?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:`<span class="ml-auto transition-opacity ${t?"opacity-100":"opacity-0 group-hover:opacity-100"}">
        ${Be("rm-"+e.name,De(e))}</span>`;return`<a href="${D(e.name)}" data-link class="group w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${i(e.name)}</span>
     ${s}</a>`}function oe(){if(document.querySelector("[data-rowmenu-pop]:not(.hidden)"))return;const e=l.data.instances;r("list").innerHTML=e.map(Oe).join("");const t=e.filter(n=>n.running).length;r("runcount").textContent=t+"/"+e.length,r("footstat").textContent=e.length?t+" of "+e.length+" running":"no instances yet"}let le="";function Ae(e){if(e.page==="instance"){const t=l.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,t.url,t.domain||"",l.busy[t.name]||"",l.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(l.usage?"loaded":"pending"):e.page}function Ne(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!r("modal").classList.contains("hidden"))}function Pe(e){switch(e.page){case"create":return _e();case"usage":return Te();case"instance":{const t=l.data.instances.find(n=>n.name===e.name)||null;return ae(t)}case"home":{const t=l.data.instances[0];return t?ae(t):z()}default:return z()}}function G(e){const t=T(),n=Ae(t);!e&&n===le||!e&&Ne()||(le=n,r("detail").innerHTML=Pe(t))}function Q(){oe(),G(!0)}let A=null;function qe(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${i(e.label||"")}</div>`;if(e.type==="select"){const n=we(),a=e.options||[],s=a.map(d=>({v:d,label:d})),o=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${i(o)}">`+H(n,s,o,d=>{document.getElementById(`${n}_val`).value=d},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${e.key}" data-type="checkbox"
        ${e.value?"checked":""} class="accent-accent w-3.5 h-3.5">
      ${i(e.label||e.key||"")}</label>`;if(e.type==="checklist"){const a=(e.options||[]).map(s=>`
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${e.key}" value="${i(s.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${i(s.label)}</span>
          ${s.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${i(s.desc)}</span>`:""}
        </span></label>`).join("");return`<div data-checklist-group="${e.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${a||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`}const t=e.oninput?` oninput="${i(e.oninput)}"`:"";return`<input data-k="${e.key}" data-field="${i(e.key||"")}"${t}
    placeholder="${i(e.placeholder||"")}" value="${i(e.value||"")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function R(e={}){return new Promise(t=>{A=t,r("mTitle").textContent=e.title||"",r("mDesc").textContent=e.desc||"",r("mFields").innerHTML=(e.fields||[]).map(qe).join("");const n=r("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),r("modal").classList.remove("hidden"),setTimeout(()=>{(r("mFields").querySelector("input,select")||n).focus()},30)})}function N(e){if(r("modal").classList.add("hidden"),A){const t=A;A=null,t(e)}}function We(){const e={};return r("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),r("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function He(){r("mCancel").onclick=()=>N(null),r("mOk").onclick=()=>N(We()),r("modal").addEventListener("keydown",e=>{e.key==="Enter"&&r("mOk").click(),e.key==="Escape"&&N(null)}),r("modal").addEventListener("click",e=>{e.target===r("modal")&&N(null)})}const ie={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function u(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(ie[t]||ie.info),n.textContent=e,r("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let de=async()=>{};function Fe(e){de=e}function Ue(e){r("console").classList.remove("w-0"),r("console").classList.add("w-[26rem]"),r("conTitle").textContent=e,r("conBody").textContent="",r("conInputRow").classList.add("hidden"),r("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function K(){r("console").classList.add("w-0"),r("console").classList.remove("w-[26rem]"),r("conInputRow").classList.add("hidden")}function h(e){const t=r("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function Je(e,t){r("conTitle").textContent=e,r("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function Y(e,t,n){l.paused=!0,Ue(n||"Working…");let a=0,s=!!n;const o=setInterval(async()=>{var c;const d=await te(e,a);!s&&d.status&&(r("conTitle").textContent=d.status.replace(/ [✓✗]$/,""),s=!0),d.chunk?(h(d.chunk),a=(c=d.offset)!=null?c:a):typeof d.offset=="number"&&(a=d.offset),d.done&&(clearInterval(o),l.paused=!1,t&&delete l.busy[t],Je(d.status||"done",d.ok),u(d.status||"done",d.ok?"ok":"err"),await de())},800)}let Z=null;const C=[];let f=-1,_=!1;function P(e){Z=e,r("console").classList.remove("w-0"),r("console").classList.add("w-[26rem]"),r("conTitle").textContent="Terminal — "+e,r("conDot").className="w-2 h-2 rounded-full bg-emerald-500",r("conBody").textContent.trim()||h("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),r("conInputRow").classList.remove("hidden"),setTimeout(()=>r("conInput").focus(),60)}async function Ve(){if(_)return;const e=r("conInput"),t=e.value.trim();if(!t||!Z)return;C.push(t),f=C.length,e.value="",h("› "+t+`
`),_=!0,r("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await M({instance:Z,action:"term",cmd:t})}catch(o){h("error: "+o+`
`),_=!1;return}if(!n.job_id){h((n.output||"failed")+`
`),_=!1;return}let a=0;const s=setInterval(async()=>{var d;const o=await te(n.job_id,a);o.chunk?(h(o.chunk),a=(d=o.offset)!=null?d:a):typeof o.offset=="number"&&(a=o.offset),o.done&&(clearInterval(s),_=!1,h(`
`),r("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function ze(e){const t=r("conInput");e.key==="Enter"?Ve():e.key==="ArrowUp"?(f>0&&(f--,t.value=C[f]||""),e.preventDefault()):e.key==="ArrowDown"&&(f<C.length-1?(f++,t.value=C[f]||""):(f=C.length,t.value=""),e.preventDefault())}function Xe(){r("conClose").onclick=K,r("conInput").addEventListener("keydown",e=>ze(e))}let ce=async()=>{},v=()=>{};function Ge(e){ce=e.refresh,v=e.render}const ue={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function S(e,t,n={}){l.busy[e]=t,v();let a;try{a=await M(Object.assign({instance:e,action:t},n))}catch(s){delete l.busy[e],u("request failed: "+s,"err"),v();return}if(a.job_id){const s=ue[t]?ue[t](e):q(t)+" "+e;u(t.replace("-"," ")+" started…","info"),Y(a.job_id,e,s)}else delete l.busy[e],a.ok?u(q(t)+" "+e+" ✓","ok"):u((a.output||"failed").split(`
`)[0],"err"),await ce()}async function L(e,t,n={}){let a;try{a=await M(Object.assign({instance:e,action:t},n))}catch(s){u("request failed: "+s,"err");return}if(a.job_id){const s={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};Y(a.job_id,null,(s[t]||q(t))+" — "+e)}else u((a.output||"failed").split(`
`)[0],"err")}async function Qe(e,t){t===""?S(e,"unfocus"):t&&S(e,"focus",{slug:t})}async function Ke(e,t){const n=l.data.instances.find(s=>s.name===e);if(!t||n&&n.server===t)return;l.busy[e]="server",v();let a;try{a=await M({instance:e,action:"server",server:t})}catch(s){delete l.busy[e],v(),u("request failed: "+s,"err");return}a.job_id?(u("switching "+e+" → "+t+"…","info"),Y(a.job_id,e,"Switching "+e+" → "+t)):(delete l.busy[e],v(),u((a.output||"failed").split(`
`)[0],"err"))}async function Ye(e){const t=await R({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?S(e,"delete",{confirm:e}):t&&u("name did not match — not deleted","err")}function Ze(e){const n=r("wpArgs").value.trim();if(!n){u("enter a wp-cli command","err");return}L(e,"wp",{args:n})}async function et(e){const t=await R({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&L(e,"snapshot",{name:t.name})}async function tt(e){let t=[];try{t=(await ge(e)).snapshots||[]}catch(a){}if(!t.length){u("no snapshots for "+e,"err");return}const n=await R({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&L(e,"restore",{name:n.name})}async function nt(e){const t=l.data.seeds||[];if(!t.length){u("no WXR files in runtime/seeds/","err");return}const n=await R({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&L(e,"seed",{file:n.file})}function at(e){const t=(r("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){u("type a plugin slug to install","err");return}L(e,"install",{slug:t})}function rt(e){const t=(r("plugQ").value||"").toLowerCase().trim(),n=r("plugResults");if(!t){n.innerHTML="";return}const a=l.data.instances.find(o=>o.name===e),s=(l.data.plugins||[]).filter(o=>o.toLowerCase().includes(t)).slice(0,8);n.innerHTML=s.map(o=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${i(o)}</span>
      ${a&&a.focus===o?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${i(o)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${i(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function st(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function ot(){try{l.usage=await ee()}catch(e){l.usage={available:!1}}v()}async function B(){if(l.paused)return;let e;try{e=await me()}catch(t){return}l.data=e,oe(),G(!1)}async function lt(){m("/usage"),r("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading Claude usage…</div>';try{l.usage=await ee()}catch(e){l.usage={available:!1}}G(!0)}function it(){m("/")}function dt(e){m(D(e))}function pe(){R({title:"How Claude works here",okText:"Got it",desc:`The sandbox gives Claude a live WordPress to act in, so it can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — Claude picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. One MCP server (mcp__sandbox__*) serves every project — each tool takes the project directory and resolves the right environment from the registry. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const ct={navigate:m,goHome:it,selectInstance:dt,showUsage:lt,showHelp:pe,openTerminal:P,doCreate:re,doDelete:Ye,doFocus:Qe,doServer:Ke,doSnapshot:et,doRestore:tt,doSeed:nt,doWp:Ze,doInstall:at,plugFilter:()=>rt(X()),loadUsageThenRender:ot,act:S,op:L,cselToggle:$e,cselPick:Ce,cselFilter:ne,rowMenuToggle:Ee,rowMenuClose:se,consoleClose:K,copyText:st};window.sb=ct;function ut(){Ge({refresh:B,render:Q}),Fe(B),He(),Xe(),Se(),Me(),ke(),r("newBtn").onclick=re,r("startAll").onclick=()=>S("*","start-all"),r("stopAll").onclick=()=>S("*","stop-all"),r("helpBtn").onclick=pe,r("termBtn").onclick=()=>{const t=X()||l.data.instances[0]&&l.data.instances[0].name;if(!t){u("create an instance first","err");return}m(D(t,!0)),P(t)},ve(t=>{Q(),t.page==="instance"&&t.console?P(t.name):K()}),Q();const e=T();e.page==="instance"&&e.console&&P(e.name),bt()}const pt=5e3;function bt(){B(),window.setInterval(()=>{document.visibilityState==="visible"&&B()},pt),document.addEventListener("visibilitychange",()=>{document.visibilityState==="visible"&&B()})}ut()})();
