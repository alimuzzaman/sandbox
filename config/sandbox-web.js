(function(){"use strict";const s=e=>document.getElementById(e),$e={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},d=e=>String(e).replace(/[&<>"]/g,t=>$e[t]),z=e=>e.charAt(0).toUpperCase()+e.slice(1),l={data:{instances:[],plugins:[],projects:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1},busy:{},usage:null,paused:!1};async function P(e){return(await fetch(e)).json()}const ye=()=>P("/api/instances"),le=()=>P("/api/usage"),de=(e,t)=>P(`/api/job/${e}?offset=${t}`),we=e=>P(`/api/snapshots/${e}`);async function W(e){return(await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)})).json()}function Ce(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="create"&&t.length===1?{page:"create"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const E=()=>Ce(location.pathname);let X=()=>{};function Se(e){X=e}function g(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),X(E())}function B(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}function Le(){window.addEventListener("popstate",()=>X(E())),document.addEventListener("click",e=>{var a,r;const t=(r=(a=e.target)==null?void 0:a.closest)==null?void 0:r.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),g(n))})}let Te=0;const q={};function G(){return"mcsel"+ ++Te}function R(e,t,n,a,r,o){const i=t.find(c=>c.v===n),u=i?i.label:t[0]?t[0].label:"";q[e]=a;const x=r?"opacity-50 pointer-events-none":"",k=o?"block w-full":"inline-block",V=o?"w-full":"w-48";return`<div class="relative ${k}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${V} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${x}">
      <span class="truncate flex-1" data-csel-label>${d(u)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${V} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(c=>`<button type="button" data-v="${d(c.v)}" data-search="${d(c.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${d(c.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${c.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${d(c.label)}</button>`).join("")}
      </div>
    </div></div>`}function je(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",ie(e),n.focus())}}function _e(e,t){var r,o;(r=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||r.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(o=q[e])==null||o.call(q,t)}function ie(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const r=a;r.style.display=(r.dataset.search||"").includes(n)?"":"none"})}function Ie(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function C(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function Ee(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function m(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const S=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function ce(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const L="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function D(e,t,n,a){return`<button class="${L}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function Q(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${d(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function K(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const f=e=>(e||0).toLocaleString(),M=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),Y=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function Be(){const e=l.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No Claude session data found.</div>`;const t=e.total||{},n=(u,x,k)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${u}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${x}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([u,x])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${u}</span>
      <span class="flex-1 text-neutral-500">${f(Y(x))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${M(x.cost)}</span></div>`).join(""),r=Object.entries(e.per_instance||{}).sort((u,x)=>(x[1].cost||0)-(u[1].cost||0)).map(([u,x])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${u==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${d(u)}</span>
      <span class="flex-1 text-neutral-500">${f(Y(x))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${M(x.cost)}</span></div>`).join(""),o=e.sessions||[],i=o.map(u=>`
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${u.id}</code>
      <span class="w-16 capitalize text-neutral-500">${u.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(u.instances||[]).join(", ")||"—"}</span>
      <span class="text-neutral-500">${f(u.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${M(u.cost)}</span></div>`).join("");return`<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Claude usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Across all sandbox Claude sessions. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${n("Total tokens",f(e.tokens))}
      ${n("Estimated cost",M(e.cost))}
      ${n("Sessions",f(o.length)+(o.length>=25?"+":""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${f(t.in)} · out ${f(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${f(t.cw)} · cache read ${f(t.cr)}</div>
    </div>
    <div class="mt-6">${m("By model")}${a||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${m("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${r||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${m("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${i}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const Re=Y;function Z(){const e=l.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
    <h1 class="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Give Claude a real WordPress</h1>
    <p class="mt-2 text-[14px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      The sandbox hands Claude a <b>live, disposable WordPress environment</b> to work in — so it
      stops guessing and starts proving. Through the connected tools Claude can run WP-CLI, hit the
      REST API and database, open pages in a real browser, read and edit your plugin's actual code,
      and watch the logs. Each item on the left is one isolated environment Claude can fully control.</p>

    <div class="mt-7 grid grid-cols-3 gap-3">
      ${K("Build","Claude implements features against a running install and verifies them live — not from memory.")}
      ${K("Reproduce","Hand Claude the environment and it reproduces the bug on a real stack — confirmed, not assumed.")}
      ${K("Fix &amp; prove","Reproduce → fix the real code → re-verify in the same environment. Fast, and proven.")}
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
  </div>`}function De(e){const t=e.domain||"",n=e.url.startsWith("https://"),a=t.endsWith(".sb");if(n)return`<span class="text-neutral-700 dark:text-neutral-200">${d(t)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;const r=a?`<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${Q("./sb secure "+e.name)}
       </div>`:"";return`<span class="text-neutral-700 dark:text-neutral-200">${d(t)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${r}`}function Me(e){var a;const t=l.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show Claude token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Claude usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${f(Re(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${M(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No Claude usage attributed to this instance yet.</div>'}function ue(e){if(!e)return Z();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${d(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${C()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${d(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=l.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,r=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?C():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?C("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${d(e.name)}</h1>
        ${Ee(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${d(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${ce(e.url+"/wp-admin","Admin",e.running)}
      ${ce(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${r}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?C():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${m("Overview")}
    ${S("Web server",`<div class="flex items-center gap-2">
        ${R("serverSel",(l.data.servers&&l.data.servers.length?l.data.servers:["apache","nginx","litespeed"]).map(o=>({v:o,label:o})),e.server,o=>window.sb.doServer(e.name,o),!!t||!e.running)}
        ${t==="server"?C():""}
        ${e.running?"":'<span class="text-[11px] text-neutral-400">start the site to switch</span>'}</div>`)}
    ${e.domain?S("Domain",De(e)):""}
    ${S("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${S("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${S("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':d(e.project))}
    ${S("Focus plugin",`<div class="flex items-center gap-2">
        ${R("focusSel",[{v:"",label:"— none —"}].concat(l.data.plugins.map(o=>({v:o,label:o}))),e.focus&&e.focus!=="—"?e.focus:"",o=>window.sb.doFocus(e.name,o),!!t)}
        ${t==="focus"||t==="unfocus"?C():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${m("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${D(e.name,"logs","Logs")}
      ${D(e.name,"status","Status")}
      ${D(e.name,"doctor","Doctor")}
      ${D(e.name,"update","Update plugins")}
      <button class="${L}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${L}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${L}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${D(e.name,"xdebug","Xdebug",{state:"status"})}
    </div>
    <div class="flex gap-2 mt-2.5">
      <span class="px-2 py-1.5 text-[12.5px] font-mono text-neutral-400">wp</span>
      <input id="wpArgs" placeholder="plugin list --status=active"
        onkeydown="if(event.key==='Enter')sb.doWp('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] font-mono focus:border-accent outline-none">
      <button class="${L}" onclick="sb.doWp('${e.name}')">Run</button>
    </div>
  </div>

  <div class="px-6 pb-6">
    ${m("Plugins","Search to install from WordPress.org, or symlink a local source")}
    <div class="flex gap-2">
      <input id="plugQ" placeholder="Search plugins by name or slug…" oninput="sb.plugFilter()"
        onkeydown="if(event.key==='Enter')sb.doInstall('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">
      <button class="${L}" onclick="sb.doInstall('${e.name}')">Install from .org</button>
    </div>
    <div id="plugResults" class="mt-2 flex flex-col gap-1"></div>
  </div>

  <div class="px-6 pb-8">
    ${m("Use with Claude","Connect a Claude session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Claude in chat (simplest):</div>
      ${Q("focus "+(e.focus&&e.focus!=="—"?e.focus:"<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${Q("mcp__"+e.mcp_server+"__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${e.url}</code>.</div>
      ${Me(e.name)}
    </div>
  </div>`}const pe={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function b(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(pe[t]||pe.info),n.textContent=e,s("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let F=null;function Oe(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${d(e.label||"")}</div>`;if(e.type==="select"){const n=G(),a=e.options||[],r=a.map(i=>({v:i,label:i})),o=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${d(o)}">`+R(n,r,o,i=>{document.getElementById(`${n}_val`).value=i},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
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
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function O(e={}){return new Promise(t=>{F=t,s("mTitle").textContent=e.title||"",s("mDesc").textContent=e.desc||"",s("mFields").innerHTML=(e.fields||[]).map(Oe).join("");const n=s("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),s("modal").classList.remove("hidden"),setTimeout(()=>{(s("mFields").querySelector("input,select")||n).focus()},30)})}function H(e){if(s("modal").classList.add("hidden"),F){const t=F;F=null,t(e)}}function Ne(){const e={};return s("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),s("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function Ae(){s("mCancel").onclick=()=>H(null),s("mOk").onclick=()=>H(Ne()),s("modal").addEventListener("keydown",e=>{e.key==="Enter"&&s("mOk").click(),e.key==="Escape"&&H(null)}),s("modal").addEventListener("click",e=>{e.target===s("modal")&&H(null)})}let be=async()=>{};function Pe(e){be=e}function We(e){s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent=e,s("conBody").textContent="",s("conInputRow").classList.add("hidden"),s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function ee(){s("console").classList.add("w-0"),s("console").classList.remove("w-[26rem]"),s("conInputRow").classList.add("hidden")}function $(e){const t=s("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function qe(e,t){s("conTitle").textContent=e,s("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function te(e,t,n){l.paused=!0,We(n||"Working…");let a=0,r=!!n;const o=setInterval(async()=>{var u;const i=await de(e,a);!r&&i.status&&(s("conTitle").textContent=i.status.replace(/ [✓✗]$/,""),r=!0),i.chunk?($(i.chunk),a=(u=i.offset)!=null?u:a):typeof i.offset=="number"&&(a=i.offset),i.done&&(clearInterval(o),l.paused=!1,t&&delete l.busy[t],qe(i.status||"done",i.ok),b(i.status||"done",i.ok?"ok":"err"),await be())},800)}let ne=null;const T=[];let v=-1,N=!1;function U(e){ne=e,s("console").classList.remove("w-0"),s("console").classList.add("w-[26rem]"),s("conTitle").textContent="Terminal — "+e,s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",s("conBody").textContent.trim()||$("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),s("conInputRow").classList.remove("hidden"),setTimeout(()=>s("conInput").focus(),60)}async function Fe(){if(N)return;const e=s("conInput"),t=e.value.trim();if(!t||!ne)return;T.push(t),v=T.length,e.value="",$("› "+t+`
`),N=!0,s("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await W({instance:ne,action:"term",cmd:t})}catch(o){$("error: "+o+`
`),N=!1;return}if(!n.job_id){$((n.output||"failed")+`
`),N=!1;return}let a=0;const r=setInterval(async()=>{var i;const o=await de(n.job_id,a);o.chunk?($(o.chunk),a=(i=o.offset)!=null?i:a):typeof o.offset=="number"&&(a=o.offset),o.done&&(clearInterval(r),N=!1,$(`
`),s("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function He(e){const t=s("conInput");e.key==="Enter"?Fe():e.key==="ArrowUp"?(v>0&&(v--,t.value=T[v]||""),e.preventDefault()):e.key==="ArrowDown"&&(v<T.length-1?(v++,t.value=T[v]||""):(v=T.length,t.value=""),e.preventDefault())}function Ue(){s("conClose").onclick=ee,s("conInput").addEventListener("keydown",e=>He(e))}let xe=async()=>{},y=()=>{};function Je(e){xe=e.refresh,y=e.render}const me={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function w(e,t,n={}){l.busy[e]=t,y();let a;try{a=await W(Object.assign({instance:e,action:t},n))}catch(r){delete l.busy[e],b("request failed: "+r,"err"),y();return}if(a.job_id){const r=me[t]?me[t](e):z(t)+" "+e;b(t.replace("-"," ")+" started…","info"),te(a.job_id,e,r)}else delete l.busy[e],a.ok?b(z(t)+" "+e+" ✓","ok"):b((a.output||"failed").split(`
`)[0],"err"),await xe()}async function j(e,t,n={}){let a;try{a=await W(Object.assign({instance:e,action:t},n))}catch(r){b("request failed: "+r,"err");return}if(a.job_id){const r={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};te(a.job_id,null,(r[t]||z(t))+" — "+e)}else b((a.output||"failed").split(`
`)[0],"err")}async function Ve(e,t){t===""?w(e,"unfocus"):t&&w(e,"focus",{slug:t})}async function ze(e,t){const n=l.data.instances.find(r=>r.name===e);if(!t||n&&n.server===t)return;l.busy[e]="server",y();let a;try{a=await W({instance:e,action:"server",server:t})}catch(r){delete l.busy[e],y(),b("request failed: "+r,"err");return}a.job_id?(b("switching "+e+" → "+t+"…","info"),te(a.job_id,e,"Switching "+e+" → "+t)):(delete l.busy[e],y(),b((a.output||"failed").split(`
`)[0],"err"))}async function Xe(e){const t=await O({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?w(e,"delete",{confirm:e}):t&&b("name did not match — not deleted","err")}function Ge(e){const n=s("wpArgs").value.trim();if(!n){b("enter a wp-cli command","err");return}j(e,"wp",{args:n})}async function Qe(e){const t=await O({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&j(e,"snapshot",{name:t.name})}async function Ke(e){let t=[];try{t=(await we(e)).snapshots||[]}catch(a){}if(!t.length){b("no snapshots for "+e,"err");return}const n=await O({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&j(e,"restore",{name:n.name})}async function Ye(e){const t=l.data.seeds||[];if(!t.length){b("no WXR files in runtime/seeds/","err");return}const n=await O({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&j(e,"seed",{file:n.file})}function Ze(e){const t=(s("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){b("type a plugin slug to install","err");return}j(e,"install",{slug:t})}function et(e){const t=(s("plugQ").value||"").toLowerCase().trim(),n=s("plugResults");if(!t){n.innerHTML="";return}const a=l.data.instances.find(o=>o.name===e),r=(l.data.plugins||[]).filter(o=>o.toLowerCase().includes(t)).slice(0,8);n.innerHTML=r.map(o=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${d(o)}</span>
      ${a&&a.focus===o?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${d(o)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${d(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function tt(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function nt(){try{l.usage=await le()}catch(e){l.usage={available:!1}}y()}let ae=!1;const p={name:"cr_name",domain:"cr_domain",server:"cr_server",seed:"cr_seed",title:"cr_title",theme:"cr_theme",debug:"cr_debug",plugins:"cr_plugins"};function fe(){ae=!1,g("/create")}function at(e){if(ae)return;const t=document.getElementById(p.domain);if(!t)return;const n=(e.value||"").trim().toLowerCase().replace(/[^a-z0-9-]/g,"-").replace(/^-+|-+$/g,"");t.value=n?n+".sb":""}function st(){ae=!0}const J="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none";function _(e,t,n){return`<label class="block">
    <span class="block text-[12px] font-medium text-neutral-600 dark:text-neutral-400 mb-1">${d(e)}</span>
    ${t}
    
  </label>`}function rt(){const e=G(),t=(l.data.servers||[]).map(c=>({v:c,label:c})),n=l.data.servers[0]||"",a=`<input type="hidden" id="${p.server}" value="${d(n)}">`+R(e,t,n,c=>{document.getElementById(p.server).value=c},!1,!0),r=G(),i=["none",...l.data.seeds||[]].map(c=>({v:c,label:c})),u=`<input type="hidden" id="${p.seed}" value="none">`+R(r,i,"none",c=>{document.getElementById(p.seed).value=c},!1,!0),k=(l.data.projects||[]).map(c=>({value:c.name,label:c.name,desc:c.description||(c.plugins||[]).join(", ")})).map(c=>`
    <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
      hover:bg-neutral-100 dark:hover:bg-neutral-800">
      <input type="checkbox" data-plugin value="${d(c.value)}" class="accent-accent w-3.5 h-3.5 mt-0.5">
      <span class="flex-1 min-w-0">
        <span class="text-neutral-800 dark:text-neutral-200">${d(c.label)}</span>
        ${c.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${d(c.desc)}</span>`:""}
      </span></label>`).join(""),V=`<div id="${p.plugins}" class="flex flex-col gap-0.5 max-h-56 overflow-y-auto
    rounded border border-brdin dark:border-neutral-700 p-1">${k||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`;return`<div class="max-w-2xl mx-auto px-6 py-8">
    <a href="/" data-link class="inline-flex items-center gap-1 text-[12.5px] text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300">
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Back</a>

    <h1 class="mt-3 text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">New instance</h1>
    <p class="mt-1.5 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      Name it, pick a web server, then optionally add plugins and demo content. It'll serve at a
      clean <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">http://&lt;name&gt;.sb</code>
      (no port). Want HTTPS? run <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">./sb secure &lt;name&gt;</code> after.</p>

    <div class="mt-6">
      ${m("Basics")}
      <div class="flex flex-col gap-3.5">
        ${_("Name",`<input id="${p.name}" oninput="sb.syncDomainFromName(this)"
          placeholder="name (a-z, 0-9, -)" class="${J}">`)}
        ${_("Web server",a)}
        ${_("Domain",`<input id="${p.domain}" oninput="sb.domainEdited()"
          placeholder="domain — defaults to <name>.sb" class="${J}">`)}
      </div>
    </div>

    <div class="mt-7">
      ${m("Plugins","optional")}
      ${V}
    </div>

    <div class="mt-7">
      ${m("Content & options","optional")}
      <div class="flex flex-col gap-3.5">
        ${_("Demo content",u)}
        ${_("Site title",`<input id="${p.title}"
          placeholder="defaults to “Sandbox <name>”" class="${J}">`)}
        ${_("Theme",`<input id="${p.theme}"
          placeholder="theme slug (optional, e.g. astra)" class="${J}">`)}
        <label class="flex items-center gap-2 text-[13px] text-neutral-700 dark:text-neutral-300 cursor-pointer select-none">
          <input type="checkbox" id="${p.debug}" class="accent-accent w-3.5 h-3.5"> Enable WP_DEBUG</label>
      </div>
    </div>

    <div class="mt-8 flex items-center gap-2">
      <button onclick="sb.submitCreate()" class="px-4 py-2 rounded-full bg-accent text-white text-[13px] font-medium hover:bg-blue-700">Create instance</button>
      <a href="/" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Cancel</a>
    </div>
  </div>`}function I(e){var t;return(((t=document.getElementById(e))==null?void 0:t.value)||"").trim()}function ot(){var u,x;const e=I(p.name);if(!e){(u=document.getElementById(p.name))==null||u.focus();return}const t=I(p.server),n=I(p.domain).toLowerCase(),a=I(p.seed),r=a&&a!=="none"?a:"",o=[...document.querySelectorAll(`#${p.plugins} input[data-plugin]:checked`)].map(k=>k.value),i=((x=document.getElementById(p.debug))==null?void 0:x.checked)||!1;l.data.instances.find(k=>k.name===e)||l.data.instances.push({name:e,running:!1,pending:!0,server:t,url:"",mcp_server:"sandbox-"+e,project:"—",focus:"—",domain:n,wordpress_port:"",mailpit_port:""}),l.busy[e]="create",g(B(e)),w(e,"create",{name:e,server:t,domain:n,plugins:o,seed:r,site_title:I(p.title),theme:I(p.theme),wp_debug:i})}function lt(e,t){const n=t.map(a=>{const r=a.disabled?"opacity-40 pointer-events-none":"",o=a.danger?"text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40":"text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800";return`<button type="button" ${a.disabled?"disabled":""}
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuClose();${a.js}"
      class="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left ${o} ${r}">
      ${a.icon?`<span class="w-3.5 h-3.5 grid place-items-center shrink-0 opacity-70">${a.icon}</span>`:""}
      <span class="flex-1">${d(a.label)}</span></button>`}).join("");return`<span class="relative shrink-0" data-rowmenu="${e}">
    <button type="button" title="More actions" aria-label="More actions"
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuToggle('${e}')"
      class="w-6 h-6 grid place-items-center rounded text-neutral-500 dark:text-neutral-400
      hover:bg-neutral-200 dark:hover:bg-neutral-700 hover:text-neutral-900 dark:hover:text-neutral-100">
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
    </button>
    <div data-rowmenu-pop class="hidden absolute right-0 z-[60] mt-1 min-w-[10rem] py-1
      rounded-lg border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl">
      ${n}</div></span>`}function dt(e){var n;let t=!1;document.querySelectorAll("[data-rowmenu-pop]").forEach(a=>{const r=a.closest("[data-rowmenu]"),o=!a.classList.contains("hidden");r&&r.dataset.rowmenu===e&&(t=o),a.classList.add("hidden")}),!t&&((n=document.querySelector(`[data-rowmenu="${e}"] [data-rowmenu-pop]`))==null||n.classList.remove("hidden"))}function ge(){document.querySelectorAll("[data-rowmenu-pop]").forEach(e=>e.classList.add("hidden"))}function it(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-rowmenu]")||ge()})}function se(){const e=E();return e.page==="instance"?e.name:null}const h={play:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',stop:'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',restart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',admin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',term:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7l4 4-4 4"/><path d="M12 15h6"/></svg>',snapshot:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>',restore:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8"/><path d="M3 3v5h5"/></svg>',trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>'};function ct(e){const t=JSON.stringify(e.name),n=[e.running?{label:"Stop",icon:h.stop,js:`sb.act(${t},'stop')`}:{label:"Start",icon:h.play,js:`sb.act(${t},'start')`},{label:"Restart",icon:h.restart,js:`sb.act(${t},'restart')`,disabled:!e.running},{label:"Open admin",icon:h.admin,js:`window.open(${JSON.stringify(e.url+"/wp-admin")},'_blank')`,disabled:!e.running},{label:"Console",icon:h.term,js:`sb.navigate(${JSON.stringify(B(e.name,!0))})`,disabled:!e.running},{label:"Snapshot",icon:h.snapshot,js:`sb.doSnapshot(${t})`},{label:"Restore…",icon:h.restore,js:`sb.doRestore(${t})`}];return e.name!=="main"&&n.push({label:"Delete",icon:h.trash,js:`sb.doDelete(${t})`,danger:!0}),n}function ut(e){const t=e.name===se(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",r=l.busy[e.name]?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:`<span class="ml-auto transition-opacity ${t?"opacity-100":"opacity-0 group-hover:opacity-100"}">
        ${lt("rm-"+e.name,ct(e))}</span>`;return`<a href="${B(e.name)}" data-link class="group w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${d(e.name)}</span>
     ${r}</a>`}function ve(){if(document.querySelector("[data-rowmenu-pop]:not(.hidden)"))return;const e=l.data.instances;s("list").innerHTML=e.map(ut).join("");const t=e.filter(n=>n.running).length;s("runcount").textContent=t+"/"+e.length,s("footstat").textContent=e.length?t+" of "+e.length+" running":"no instances yet"}let he="";function pt(e){if(e.page==="instance"){const t=l.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,t.url,t.domain||"",l.busy[t.name]||"",l.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(l.usage?"loaded":"pending"):e.page}function bt(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!s("modal").classList.contains("hidden"))}function xt(e){switch(e.page){case"create":return rt();case"usage":return Be();case"instance":{const t=l.data.instances.find(n=>n.name===e.name)||null;return ue(t)}case"home":{const t=l.data.instances[0];return t?ue(t):Z()}default:return Z()}}function re(e){const t=E(),n=pt(t);!e&&n===he||!e&&bt()||(he=n,s("detail").innerHTML=xt(t))}function oe(){ve(),re(!0)}async function A(){if(l.paused)return;let e;try{e=await ye()}catch(t){return}l.data=e,ve(),re(!1)}async function mt(){g("/usage"),s("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading Claude usage…</div>';try{l.usage=await le()}catch(e){l.usage={available:!1}}re(!0)}function ft(){g("/")}function gt(e){g(B(e))}function ke(){O({title:"How Claude works here",okText:"Got it",desc:`The sandbox gives Claude a live WordPress to act in, so it can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — Claude picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. Each environment also has its own tool namespace (mcp__sandbox__* = main, mcp__sandbox-<name>__* = that one) so parallel sessions never collide. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const vt={navigate:g,goHome:ft,selectInstance:gt,showUsage:mt,showHelp:ke,openTerminal:U,doCreate:fe,submitCreate:ot,doDelete:Xe,doFocus:Ve,doServer:ze,doSnapshot:Qe,doRestore:Ke,doSeed:Ye,doWp:Ge,doInstall:Ze,plugFilter:()=>et(se()),loadUsageThenRender:nt,act:w,op:j,syncDomainFromName:at,domainEdited:st,cselToggle:je,cselPick:_e,cselFilter:ie,rowMenuToggle:dt,rowMenuClose:ge,consoleClose:ee,copyText:tt};window.sb=vt;function ht(){Je({refresh:A,render:oe}),Pe(A),Ae(),Ue(),Ie(),it(),Le(),s("newBtn").onclick=fe,s("startAll").onclick=()=>w("*","start-all"),s("stopAll").onclick=()=>w("*","stop-all"),s("helpBtn").onclick=ke,s("termBtn").onclick=()=>{const t=se()||l.data.instances[0]&&l.data.instances[0].name;if(!t){b("create an instance first","err");return}g(B(t,!0)),U(t)},Se(t=>{oe(),t.page==="instance"&&t.console?U(t.name):ee()}),oe();const e=E();e.page==="instance"&&e.console&&U(e.name),$t()}const kt=5e3;function $t(){A(),window.setInterval(()=>{document.visibilityState==="visible"&&A()},kt),document.addEventListener("visibilitychange",()=>{document.visibilityState==="visible"&&A()})}ht()})();
