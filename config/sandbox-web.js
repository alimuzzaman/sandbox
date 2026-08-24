(function(){"use strict";const s=e=>document.getElementById(e),De={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},o=e=>String(e).replace(/[&<>"]/g,t=>De[t]),z=e=>e.charAt(0).toUpperCase()+e.slice(1),i={data:{instances:[],plugins:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1,remotes:[]},busy:{},usage:null,remote:{},paused:!1};async function M(e){return(await fetch(e)).json()}const Ae=()=>M("/api/instances"),ie=()=>M("/api/usage"),X=(e,t="fast")=>M(`/api/remote/${encodeURIComponent(e)}${t==="deep"?"?deep=1":""}`),de=(e,t)=>M(`/api/job/${e}?offset=${t}`),Ne=e=>M(`/api/snapshots/${e}`);async function q(e){return(await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)})).json()}function Oe(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="create"&&t.length===1?{page:"create"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="remote"&&t[1]&&t.length===2?{page:"remote",name:decodeURIComponent(t[1])}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const y=()=>Oe(location.pathname);let G=()=>{};function qe(e){G=e}function h(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),G(y())}function H(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}const ce=e=>`/remote/${encodeURIComponent(e)}`;function He(){window.addEventListener("popstate",()=>G(y())),document.addEventListener("click",e=>{var a,r;const t=(r=(a=e.target)==null?void 0:a.closest)==null?void 0:r.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),h(n))})}let Ue=0;const U={};function Fe(){return"mcsel"+ ++Ue}function Q(e,t,n,a,r,l){const d=t.find(g=>g.v===n),u=d?d.label:t[0]?t[0].label:"";U[e]=a;const x=r?"opacity-50 pointer-events-none":"",V=l?"block w-full":"inline-block",O=l?"w-full":"w-48";return`<div class="relative ${V}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${O} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${x}">
      <span class="truncate flex-1" data-csel-label>${o(u)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${O} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(g=>`<button type="button" data-v="${o(g.v)}" data-search="${o(g.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${o(g.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${g.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${o(g.label)}</button>`).join("")}
      </div>
    </div></div>`}function We(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",ue(e),n.focus())}}function Je(e,t){var r,l;(r=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||r.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(l=U[e])==null||l.call(U,t)}function ue(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const r=a;r.style.display=(r.dataset.search||"").includes(n)?"":"none"})}function Ve(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function _(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function ze(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function w(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const L=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function K(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const j="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function B(e,t,n,a){return`<button class="${j}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function Y(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${o(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function Z(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const m=e=>(e||0).toLocaleString(),E=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),ee=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function Xe(){const e=i.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No Claude session data found.</div>`;const t=e.total||{},n=(u,x,V)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${u}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${x}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([u,x])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${u}</span>
      <span class="flex-1 text-neutral-500">${m(ee(x))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${E(x.cost)}</span></div>`).join(""),r=Object.entries(e.per_instance||{}).sort((u,x)=>(x[1].cost||0)-(u[1].cost||0)).map(([u,x])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${u==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${o(u)}</span>
      <span class="flex-1 text-neutral-500">${m(ee(x))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${E(x.cost)}</span></div>`).join(""),l=e.sessions||[],d=l.map(u=>`
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${u.id}</code>
      <span class="w-16 capitalize text-neutral-500">${u.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(u.instances||[]).join(", ")||"—"}</span>
      <span class="text-neutral-500">${m(u.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${E(u.cost)}</span></div>`).join("");return`<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Claude usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Across all sandbox Claude sessions. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${n("Total tokens",m(e.tokens))}
      ${n("Estimated cost",E(e.cost))}
      ${n("Sessions",m(l.length)+(l.length>=25?"+":""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${m(t.in)} · out ${m(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${m(t.cw)} · cache read ${m(t.cr)}</div>
    </div>
    <div class="mt-6">${w("By model")}${a||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${w("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${r||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${w("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${d}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const Ge=ee;function te(){const e=i.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
    <h1 class="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Give Claude a real WordPress</h1>
    <p class="mt-2 text-[14px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      The sandbox hands Claude a <b>live, disposable WordPress environment</b> to work in — so it
      stops guessing and starts proving. Through the connected tools Claude can run WP-CLI, hit the
      REST API and database, open pages in a real browser, read and edit your plugin's actual code,
      and watch the logs. Each item on the left is one isolated environment Claude can fully control.</p>

    <div class="mt-7 grid grid-cols-3 gap-3">
      ${Z("Build","Claude implements features against a running install and verifies them live — not from memory.")}
      ${Z("Reproduce","Hand Claude the environment and it reproduces the bug on a real stack — confirmed, not assumed.")}
      ${Z("Fix &amp; prove","Reproduce → fix the real code → re-verify in the same environment. Fast, and proven.")}
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
  </div>`}function Qe(e){const t=e.domain||"",n=e.url.startsWith("https://"),a=t.endsWith(".tst");if(n)return`<span class="text-neutral-700 dark:text-neutral-200">${o(t)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;const r=a?`<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${Y("./sb secure "+e.name)}
       </div>`:"";return`<span class="text-neutral-700 dark:text-neutral-200">${o(t)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${r}`}function Ke(e){var a;const t=i.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show Claude token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Claude usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${m(Ge(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${E(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No Claude usage attributed to this instance yet.</div>'}function pe(e){if(!e)return te();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${o(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${_()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${o(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=i.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,r=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?_():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?_("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${o(e.name)}</h1>
        ${ze(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${o(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${e.login_url?K(e.login_url,"Login",e.running):K(e.url+"/wp-admin","Admin",e.running)}
      ${K(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${r}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?_():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${w("Overview")}
    ${L("Web server",`<div class="flex items-center gap-2">
        ${Q("serverSel",(i.data.servers&&i.data.servers.length?i.data.servers:["apache","nginx","litespeed"]).map(l=>({v:l,label:l})),e.server,l=>window.sb.doServer(e.name,l),!!t||!e.running)}
        ${t==="server"?_():""}
        ${e.running?"":'<span class="text-[11px] text-neutral-400">start the site to switch</span>'}</div>`)}
    ${e.domain?L("Domain",Qe(e)):""}
    ${L("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${L("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${L("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':o(e.project))}
    ${L("Focus plugin",`<div class="flex items-center gap-2">
        ${Q("focusSel",[{v:"",label:"— none —"}].concat(i.data.plugins.map(l=>({v:l,label:l}))),e.focus&&e.focus!=="—"?e.focus:"",l=>window.sb.doFocus(e.name,l),!!t)}
        ${t==="focus"||t==="unfocus"?_():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${w("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${B(e.name,"logs","Logs")}
      ${B(e.name,"status","Status")}
      ${B(e.name,"doctor","Doctor")}
      ${B(e.name,"update","Update plugins")}
      <button class="${j}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${j}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${j}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${B(e.name,"xdebug","Xdebug",{state:"status"})}
    </div>
    <div class="flex gap-2 mt-2.5">
      <span class="px-2 py-1.5 text-[12.5px] font-mono text-neutral-400">wp</span>
      <input id="wpArgs" placeholder="plugin list --status=active"
        onkeydown="if(event.key==='Enter')sb.doWp('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] font-mono focus:border-accent outline-none">
      <button class="${j}" onclick="sb.doWp('${e.name}')">Run</button>
    </div>
  </div>

  <div class="px-6 pb-6">
    ${w("Plugins","Search to install from WordPress.org, or symlink a local source")}
    <div class="flex gap-2">
      <input id="plugQ" placeholder="Search plugins by name or slug…" oninput="sb.plugFilter()"
        onkeydown="if(event.key==='Enter')sb.doInstall('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">
      <button class="${j}" onclick="sb.doInstall('${e.name}')">Install from .org</button>
    </div>
    <div id="plugResults" class="mt-2 flex flex-col gap-1"></div>
  </div>

  <div class="px-6 pb-8">
    ${w("Use with Claude","Connect a Claude session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Claude in chat (simplest):</div>
      ${Y("focus "+(e.focus&&e.focus!=="—"?e.focus:"<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${Y("mcp__"+e.mcp_server+"__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${e.url}</code>.</div>
      ${Ke(e.name)}
    </div>
  </div>`}function be(){h("/create")}function Ye(){return`<div class="max-w-2xl mx-auto px-6 py-8">
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
  </div>`}const f=e=>{if(e==null)return"unknown";const t=["B","KiB","MiB","GiB","TiB"];let n=Math.max(0,e),a=0;for(;n>=1024&&a<t.length-1;)n/=1024,a++;return`${n.toFixed(a>1?1:0)} ${t[a]}`},b=e=>e==null||!Number.isFinite(e)?"unknown":String(e),v=(e,t,n="")=>`<div class="rounded-xl border border-brd dark:border-brd-dark bg-white/70 dark:bg-neutral-900/40 p-4">
  <div class="text-[10px] uppercase tracking-[0.16em] text-neutral-400">${o(e)}</div>
  <div class="mt-1 text-xl font-semibold tabular-nums text-neutral-900 dark:text-neutral-50">${o(t)}</div>
  ${n?`<div class="mt-1 text-[11px] text-neutral-400">${o(n)}</div>`:""}</div>`;function ne(e,t,n,a){const r=t.length?t.map(l=>`<tr class="border-b border-brd/70 dark:border-brd-dark/70 last:border-0">${n.map(([,,d])=>`<td class="py-2.5 pr-4 text-[12px] text-neutral-600 dark:text-neutral-300">${o(d(l))}</td>`).join("")}</tr>`).join(""):`<tr><td colspan="${n.length}" class="py-4 text-[12px] text-neutral-400">${o(a)}</td></tr>`;return`<section class="rounded-xl border border-brd dark:border-brd-dark bg-white/50 dark:bg-neutral-900/20 p-4"><div class="flex items-center justify-between gap-3 mb-2"><h2 class="text-[13px] font-semibold">${o(e)}</h2><span class="text-[11px] text-neutral-400">${t.length} observed</span></div><div class="overflow-x-auto"><table class="w-full text-left"><thead><tr>${n.map(([l,d])=>`<th scope="col" class="pb-2 pr-4 text-[10px] uppercase tracking-[0.12em] text-neutral-400">${o(d||l)}</th>`).join("")}</tr></thead><tbody>${r}</tbody></table></div></section>`}function Ze(e,t){var ye,we,Se,Ce,_e,Le,je,Te,Re,Ie,Me,Be,Ee;if(!t)return`<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading ${o(e)}…</div>`;const n=t.instances||{total:0,running:0,stopped:0,rows:[]},a=t.host||{},r=t.storage,l=t.evidence_status==="complete"?"text-emerald-600":t.evidence_status==="partial"?"text-amber-600":"text-rose-600",d=a.disk_total_bytes?Math.min(100,Math.max(0,(a.disk_used_bytes||0)*100/a.disk_total_bytes)):0,u=t.per_instance_usage||[],x=((ye=t.process_view)==null?void 0:ye.apps)||[],V=((we=t.process_view)==null?void 0:we.processes)||[],O=((Se=t.containers)==null?void 0:Se.rows)||[],g=JSON.stringify(e);return`<div class="max-w-7xl mx-auto p-5 md:p-7 space-y-6"><header class="flex flex-col md:flex-row md:items-start gap-4"><div class="flex-1"><div class="text-[10px] uppercase tracking-[0.2em] text-teal-600 dark:text-teal-400">Storage atlas</div><h1 class="mt-1 text-2xl font-semibold tracking-tight">${o(e)}</h1><p class="mt-1 text-[12px] text-neutral-500">Authenticated control-plane inventory · ${o(t.scan_mode||"fast")} snapshot</p></div><div class="flex items-center gap-2"><span class="text-[12px] font-medium ${l}">${o(t.evidence_status)}</span><button class="px-3 py-2 rounded-lg border border-brd dark:border-brd-dark text-[12px] hover:bg-neutral-100 dark:hover:bg-neutral-800" onclick="sb.refreshRemote(${g})">Quick refresh</button><button class="px-3 py-2 rounded-lg bg-teal-700 text-white text-[12px] hover:bg-teal-800" onclick="sb.refreshRemote(${g},true)">Rebuild attribution</button></div></header>${(Ce=t.partial_reasons)!=null&&Ce.length?`<div role="status" class="rounded-xl border border-amber-200 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 text-[12px] text-amber-800 dark:text-amber-200">Partial evidence: ${o(t.partial_reasons.join(", "))}. Unknown values are intentionally not treated as zero.</div>`:""}<section class="rounded-2xl border border-brd dark:border-brd-dark bg-paper/60 dark:bg-neutral-900/30 p-5"><div class="flex items-end justify-between gap-3"><div><div class="text-[10px] uppercase tracking-[0.16em] text-neutral-400">Capacity rail</div><div class="mt-1 text-lg font-semibold">${f(a.disk_used_bytes)} used <span class="text-neutral-400 font-normal">of ${f(a.disk_total_bytes)}</span></div></div><div class="text-right text-[12px] text-neutral-500">${f(a.disk_free_bytes)} free</div></div><div class="mt-4 h-3 rounded-full bg-neutral-200 dark:bg-neutral-800 overflow-hidden"><div class="h-full bg-teal-600" style="width:${d}%"></div></div><div class="mt-2 text-[11px] text-neutral-400">Solid rail is host capacity. Logical Docker/container values below overlap it and are not additive.</div></section><div class="grid grid-cols-2 lg:grid-cols-4 gap-3">${v("Hosted instances",String(n.total),`${n.running} running · ${n.stopped} stopped`)}${v("RAM used",a.memory_used_percent==null?"unknown":`${a.memory_used_percent}%`,`${b(a.memory_used_mb)} of ${b(a.memory_total_mb)} MiB`)}${v("Load 1m",b(a.load_1m),"point-in-time host sample")}${v("Active jobs",b((_e=t.jobs)==null?void 0:_e.active),`${b((Le=t.jobs)==null?void 0:Le.queued)} queued`)}${v("Containers",b(O.length),((je=t.containers)==null?void 0:je.status)||"unavailable")}${v("Storage attribution",(r==null?void 0:r.attribution_status)||"unknown",(r==null?void 0:r.status)||"unavailable")}${v("Unattributed",String((Re=(Te=t.unattributed_containers)==null?void 0:Te.length)!=null?Re:"unknown"),"container rows without a confident instance match")}${v("Disk pressure",`${d.toFixed(1)}%`,"capacity-backed")}</div><section><div class="flex items-center justify-between mb-2"><h2 class="text-[14px] font-semibold">Hosted instances and usage</h2><span class="text-[11px] text-neutral-400">container attribution is heuristic</span></div><div class="overflow-x-auto rounded-xl border border-brd dark:border-brd-dark"><table class="w-full text-left"><thead><tr><th class="p-3 text-[10px] uppercase tracking-[0.12em] text-neutral-400">Instance</th><th class="p-3 text-[10px] uppercase tracking-[0.12em] text-neutral-400">State</th><th class="p-3 text-[10px] uppercase tracking-[0.12em] text-neutral-400">RAM</th><th class="p-3 text-[10px] uppercase tracking-[0.12em] text-neutral-400">CPU</th><th class="p-3 text-[10px] uppercase tracking-[0.12em] text-neutral-400">Evidence</th></tr></thead><tbody>${u.length?u.map(c=>{var Pe;return`<tr class="border-t border-brd/70 dark:border-brd-dark/70"><td class="p-3 text-[12px] font-medium">${o(String(c.name||"unknown"))}</td><td class="p-3 text-[12px]">${o((Pe=n.rows.find(Nt=>Nt.name===c.name))!=null&&Pe.running?"running":"stopped")}</td><td class="p-3 text-[12px] tabular-nums">${o(f(c.memory_used_bytes))}</td><td class="p-3 text-[12px] tabular-nums">${o(b(c.cpu_percent))}%</td><td class="p-3 text-[12px] text-neutral-500">${o(String(c.attribution_status||"unknown"))}</td></tr>`}).join(""):'<tr><td colspan="5" class="p-4 text-[12px] text-neutral-400">No per-instance usage evidence.</td></tr>'}</tbody></table></div></section><div class="grid xl:grid-cols-3 gap-4">${ne("Apps",x,[["name","App",c=>String(c.name||"unknown")],["process_count","Processes",c=>b(c.process_count)],["rss_bytes","RSS",c=>f(c.rss_bytes)],["cpu_percent","CPU avg",c=>`${b(c.cpu_percent)}%`]],((Ie=t.process_view)==null?void 0:Ie.status)||"Process view unavailable")}${ne("Processes",V.slice(0,50),[["pid","PID",c=>b(c.pid)],["name","Name",c=>String(c.name||"unknown")],["rss_bytes","RSS",c=>f(c.rss_bytes)],["cpu_percent","CPU avg",c=>`${b(c.cpu_percent)}%`]],"No process rows")}${ne("Containers",O,[["name","Container",c=>String(c.name||"unknown")],["memory_used_bytes","Memory",c=>f(c.memory_used_bytes)],["memory_percent","Memory %",c=>`${b(c.memory_percent)}%`],["cpu_percent","CPU",c=>`${b(c.cpu_percent)}%`]],((Me=t.containers)==null?void 0:Me.status)||"Container view unavailable")}</div><section class="rounded-xl border border-brd dark:border-brd-dark bg-white/50 dark:bg-neutral-900/20 p-4"><h2 class="text-[13px] font-semibold">Storage evidence</h2><p class="mt-1 text-[12px] text-neutral-500">${o((r==null?void 0:r.status)||"unavailable")} · attribution ${o((r==null?void 0:r.attribution_status)||"unknown")} · used ${o(f((Be=r==null?void 0:r.capacity)==null?void 0:Be.used_bytes))} · available ${o(f((Ee=r==null?void 0:r.capacity)==null?void 0:Ee.available_bytes))}</p><p class="mt-2 text-[11px] text-neutral-400">Deep scans are bounded and may remain partial. Rebuild attribution to refresh the remote directory cache; do not interpret unknown bytes as safe cleanup.</p></section></div>`}function et(e,t){const n=t.map(a=>{const r=a.disabled?"opacity-40 pointer-events-none":"",l=a.danger?"text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40":"text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800";return`<button type="button" ${a.disabled?"disabled":""}
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuClose();${a.js}"
      class="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left ${l} ${r}">
      ${a.icon?`<span class="w-3.5 h-3.5 grid place-items-center shrink-0 opacity-70">${a.icon}</span>`:""}
      <span class="flex-1">${o(a.label)}</span></button>`}).join("");return`<span class="relative shrink-0" data-rowmenu="${e}">
    <button type="button" title="More actions" aria-label="More actions"
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuToggle('${e}')"
      class="w-6 h-6 grid place-items-center rounded text-neutral-500 dark:text-neutral-400
      hover:bg-neutral-200 dark:hover:bg-neutral-700 hover:text-neutral-900 dark:hover:text-neutral-100">
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
    </button>
    <div data-rowmenu-pop class="hidden absolute right-0 z-[60] mt-1 min-w-[10rem] py-1
      rounded-lg border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl">
      ${n}</div></span>`}function tt(e){var n;let t=!1;document.querySelectorAll("[data-rowmenu-pop]").forEach(a=>{const r=a.closest("[data-rowmenu]"),l=!a.classList.contains("hidden");r&&r.dataset.rowmenu===e&&(t=l),a.classList.add("hidden")}),!t&&((n=document.querySelector(`[data-rowmenu="${e}"] [data-rowmenu-pop]`))==null||n.classList.remove("hidden"))}function xe(){document.querySelectorAll("[data-rowmenu-pop]").forEach(e=>e.classList.add("hidden"))}function nt(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-rowmenu]")||xe()})}function ae(){const e=y();return e.page==="instance"?e.name:null}const k={play:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',stop:'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',restart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',admin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',term:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7l4 4-4 4"/><path d="M12 15h6"/></svg>',snapshot:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>',restore:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8"/><path d="M3 3v5h5"/></svg>',trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>'};function at(e){const t=JSON.stringify(e.name),n=[e.running?{label:"Stop",icon:k.stop,js:`sb.act(${t},'stop')`}:{label:"Start",icon:k.play,js:`sb.act(${t},'start')`},{label:"Restart",icon:k.restart,js:`sb.act(${t},'restart')`,disabled:!e.running},{label:"Open admin",icon:k.admin,js:`window.open(${JSON.stringify(e.url+"/wp-admin")},'_blank')`,disabled:!e.running},{label:"Console",icon:k.term,js:`sb.navigate(${JSON.stringify(H(e.name,!0))})`,disabled:!e.running},{label:"Snapshot",icon:k.snapshot,js:`sb.doSnapshot(${t})`},{label:"Restore…",icon:k.restore,js:`sb.doRestore(${t})`}];return n.push({label:"Delete",icon:k.trash,js:`sb.doDelete(${t})`,danger:!0}),n}function rt(e){const t=e.name===ae(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",r=i.busy[e.name]?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:`<span class="ml-auto transition-opacity ${t?"opacity-100":"opacity-0 group-hover:opacity-100"}">
        ${et("rm-"+e.name,at(e))}</span>`;return`<a href="${H(e.name)}" data-link class="group w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${o(e.name)}</span>
     ${r}</a>`}function me(){if(document.querySelector("[data-rowmenu-pop]:not(.hidden)"))return;const e=i.data.instances;s("list").innerHTML=e.map(rt).join("");const t=y();s("remoteList").innerHTML=i.data.remotes.map(a=>`<a href="${ce(a.name)}" data-link class="w-full px-3 py-2 rounded flex items-center gap-2 text-[13px] ${t.page==="remote"&&t.name===a.name?"bg-white dark:bg-neutral-800 shadow-sm font-medium":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}"><span class="w-2 h-2 rounded-full ${a.control_ready?"bg-blue-500":"bg-neutral-400"}"></span><span class="truncate">${o(a.name)}</span></a>`).join("");const n=e.filter(a=>a.running).length;s("runcount").textContent=n+"/"+e.length,s("footstat").textContent=e.length?n+" of "+e.length+" running":"no instances yet"}let ge="";function st(e){if(e.page==="instance"){const t=i.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,t.url,t.domain||"",i.busy[t.name]||"",i.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(i.usage?"loaded":"pending"):e.page==="remote"?"remote:"+e.name+":"+JSON.stringify(i.remote[e.name]||null):e.page}function ot(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!s("modal").classList.contains("hidden"))}function lt(e){switch(e.page){case"create":return Ye();case"usage":return Xe();case"remote":return Ze(e.name,i.remote[e.name]);case"instance":{const t=i.data.instances.find(n=>n.name===e.name)||null;return pe(t)}case"home":{const t=i.data.instances[0];return t?pe(t):te()}default:return te()}}function P(e){const t=y(),n=st(t);!e&&n===ge||!e&&ot()||(ge=n,s("detail").innerHTML=lt(t))}function re(){me(),P(!0)}let F=null;function it(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${o(e.label||"")}</div>`;if(e.type==="select"){const n=Fe(),a=e.options||[],r=a.map(d=>({v:d,label:d})),l=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${o(l)}">`+Q(n,r,l,d=>{document.getElementById(`${n}_val`).value=d},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${e.key}" data-type="checkbox"
        ${e.value?"checked":""} class="accent-accent w-3.5 h-3.5">
      ${o(e.label||e.key||"")}</label>`;if(e.type==="checklist"){const a=(e.options||[]).map(r=>`
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${e.key}" value="${o(r.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${o(r.label)}</span>
          ${r.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${o(r.desc)}</span>`:""}
        </span></label>`).join("");return`<div data-checklist-group="${e.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${a||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`}const t=e.oninput?` oninput="${o(e.oninput)}"`:"";return`<input data-k="${e.key}" data-field="${o(e.key||"")}"${t}
    placeholder="${o(e.placeholder||"")}" value="${o(e.value||"")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function D(e={}){return new Promise(t=>{F=t,s("mTitle").textContent=e.title||"",s("mDesc").textContent=e.desc||"",s("mFields").innerHTML=(e.fields||[]).map(it).join("");const n=s("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),s("modal").classList.remove("hidden"),setTimeout(()=>{(s("mFields").querySelector("input,select")||n).focus()},30)})}function W(e){if(s("modal").classList.add("hidden"),F){const t=F;F=null,t(e)}}function dt(){const e={};return s("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),s("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function ct(){s("mCancel").onclick=()=>W(null),s("mOk").onclick=()=>W(dt()),s("modal").addEventListener("keydown",e=>{e.key==="Enter"&&s("mOk").click(),e.key==="Escape"&&W(null)}),s("modal").addEventListener("click",e=>{e.target===s("modal")&&W(null)})}const fe={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function p(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(fe[t]||fe.info),n.textContent=e,s("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let he=async()=>{};function ut(e){he=e}function pt(e){s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent=e,s("conBody").textContent="",s("conInputRow").classList.add("hidden"),s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function se(){s("console").classList.add("w-0"),s("console").classList.remove("w-[26rem]"),s("conInputRow").classList.add("hidden")}function S(e){const t=s("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function bt(e,t){s("conTitle").textContent=e,s("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function oe(e,t,n){i.paused=!0,pt(n||"Working…");let a=0,r=!!n;const l=setInterval(async()=>{var u;const d=await de(e,a);!r&&d.status&&(s("conTitle").textContent=d.status.replace(/ [✓✗]$/,""),r=!0),d.chunk?(S(d.chunk),a=(u=d.offset)!=null?u:a):typeof d.offset=="number"&&(a=d.offset),d.done&&(clearInterval(l),i.paused=!1,t&&delete i.busy[t],bt(d.status||"done",d.ok),p(d.status||"done",d.ok?"ok":"err"),await he())},800)}let le=null;const T=[];let $=-1,A=!1;function J(e){le=e,s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent="Terminal — "+e,s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",s("conBody").textContent.trim()||S("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),s("conInputRow").classList.remove("hidden"),setTimeout(()=>s("conInput").focus(),60)}async function xt(){if(A)return;const e=s("conInput"),t=e.value.trim();if(!t||!le)return;T.push(t),$=T.length,e.value="",S("› "+t+`
`),A=!0,s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await q({instance:le,action:"term",cmd:t})}catch(l){S("error: "+l+`
`),A=!1;return}if(!n.job_id){S((n.output||"failed")+`
`),A=!1;return}let a=0;const r=setInterval(async()=>{var d;const l=await de(n.job_id,a);l.chunk?(S(l.chunk),a=(d=l.offset)!=null?d:a):typeof l.offset=="number"&&(a=l.offset),l.done&&(clearInterval(r),A=!1,S(`
`),s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function mt(e){const t=s("conInput");e.key==="Enter"?xt():e.key==="ArrowUp"?($>0&&($--,t.value=T[$]||""),e.preventDefault()):e.key==="ArrowDown"&&($<T.length-1?($++,t.value=T[$]||""):($=T.length,t.value=""),e.preventDefault())}function gt(){s("conClose").onclick=se,s("conInput").addEventListener("keydown",e=>mt(e))}let ve=async()=>{},C=()=>{};function ft(e){ve=e.refresh,C=e.render}const ke={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function R(e,t,n={}){i.busy[e]=t,C();let a;try{a=await q(Object.assign({instance:e,action:t},n))}catch(r){delete i.busy[e],p("request failed: "+r,"err"),C();return}if(a.job_id){const r=ke[t]?ke[t](e):z(t)+" "+e;p(t.replace("-"," ")+" started…","info"),oe(a.job_id,e,r)}else delete i.busy[e],a.ok?p(z(t)+" "+e+" ✓","ok"):p((a.output||"failed").split(`
`)[0],"err"),await ve()}async function I(e,t,n={}){let a;try{a=await q(Object.assign({instance:e,action:t},n))}catch(r){p("request failed: "+r,"err");return}if(a.job_id){const r={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};oe(a.job_id,null,(r[t]||z(t))+" — "+e)}else p((a.output||"failed").split(`
`)[0],"err")}async function ht(e,t){t===""?R(e,"unfocus"):t&&R(e,"focus",{slug:t})}async function vt(e,t){const n=i.data.instances.find(r=>r.name===e);if(!t||n&&n.server===t)return;i.busy[e]="server",C();let a;try{a=await q({instance:e,action:"server",server:t})}catch(r){delete i.busy[e],C(),p("request failed: "+r,"err");return}a.job_id?(p("switching "+e+" → "+t+"…","info"),oe(a.job_id,e,"Switching "+e+" → "+t)):(delete i.busy[e],C(),p((a.output||"failed").split(`
`)[0],"err"))}async function kt(e){const t=await D({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?R(e,"delete",{confirm:e}):t&&p("name did not match — not deleted","err")}function $t(e){const n=s("wpArgs").value.trim();if(!n){p("enter a wp-cli command","err");return}I(e,"wp",{args:n})}async function yt(e){const t=await D({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&I(e,"snapshot",{name:t.name})}async function wt(e){let t=[];try{t=(await Ne(e)).snapshots||[]}catch(a){}if(!t.length){p("no snapshots for "+e,"err");return}const n=await D({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&I(e,"restore",{name:n.name})}async function St(e){const t=i.data.seeds||[];if(!t.length){p("no WXR files in runtime/seeds/","err");return}const n=await D({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&I(e,"seed",{file:n.file})}function Ct(e){const t=(s("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){p("type a plugin slug to install","err");return}I(e,"install",{slug:t})}function _t(e){const t=(s("plugQ").value||"").toLowerCase().trim(),n=s("plugResults");if(!t){n.innerHTML="";return}const a=i.data.instances.find(l=>l.name===e),r=(i.data.plugins||[]).filter(l=>l.toLowerCase().includes(t)).slice(0,8);n.innerHTML=r.map(l=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${o(l)}</span>
      ${a&&a.focus===l?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${o(l)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${o(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function Lt(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function jt(){try{i.usage=await ie()}catch(e){i.usage={available:!1}}C()}async function N(){if(i.paused)return;let e;try{e=await Ae()}catch(n){return}i.data=e;const t=y();if(t.page==="remote")try{i.remote[t.name]=await X(t.name)}catch(n){}me(),P(!1)}async function Tt(){h("/usage"),s("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading Claude usage…</div>';try{i.usage=await ie()}catch(e){i.usage={available:!1}}P(!0)}function Rt(){h("/")}function It(e){h(H(e))}function Mt(e){h(ce(e))}async function Bt(e,t=!1){try{i.remote[e]=await X(e,t?"deep":"fast"),P(!0)}catch(n){p("remote inventory refresh failed","err")}}function $e(){D({title:"How Claude works here",okText:"Got it",desc:`The sandbox gives Claude a live WordPress to act in, so it can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — Claude picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. One MCP server (mcp__sandbox__*) serves every project — each tool takes the project directory and resolves the right environment from the registry. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const Et={navigate:h,goHome:Rt,selectInstance:It,selectRemote:Mt,refreshRemote:Bt,showUsage:Tt,showHelp:$e,openTerminal:J,doCreate:be,doDelete:kt,doFocus:ht,doServer:vt,doSnapshot:yt,doRestore:wt,doSeed:St,doWp:$t,doInstall:Ct,plugFilter:()=>_t(ae()),loadUsageThenRender:jt,act:R,op:I,cselToggle:We,cselPick:Je,cselFilter:ue,rowMenuToggle:tt,rowMenuClose:xe,consoleClose:se,copyText:Lt};window.sb=Et;function Pt(){ft({refresh:N,render:re}),ut(N),ct(),gt(),Ve(),nt(),He(),s("newBtn").onclick=be,s("startAll").onclick=()=>R("*","start-all"),s("stopAll").onclick=()=>R("*","stop-all"),s("helpBtn").onclick=$e,s("termBtn").onclick=()=>{const t=ae()||i.data.instances[0]&&i.data.instances[0].name;if(!t){p("create an instance first","err");return}h(H(t,!0)),J(t)},qe(t=>{re(),t.page==="remote"&&X(t.name).then(n=>{i.remote[t.name]=n,P(!0)}).catch(()=>{}),t.page==="instance"&&t.console?J(t.name):se()}),re();const e=y();e.page==="instance"&&e.console&&J(e.name),At()}const Dt=5e3;function At(){N(),window.setInterval(()=>{document.visibilityState==="visible"&&N()},Dt),document.addEventListener("visibilitychange",()=>{document.visibilityState==="visible"&&N()})}Pt()})();
