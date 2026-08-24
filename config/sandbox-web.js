(function(){"use strict";const i=e=>document.getElementById(e),Ge={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},l=e=>String(e).replace(/[&<>"]/g,t=>Ge[t]),ne=e=>e.charAt(0).toUpperCase()+e.slice(1),r={data:{instances:[],plugins:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1,remotes:[]},busy:{},usage:null,remote:{},remoteBusy:{},sync:{refreshing:!1,lastCompleted:null,error:null},paused:!1};async function W(e){const t=await fetch(e),n=await t.json();if(!t.ok)throw new Error(n.error||`Request failed (${t.status})`);return n}const Ke=()=>W("/api/instances"),ge=()=>W("/api/usage"),Ye=(e,t="fast")=>W(`/api/remote/${encodeURIComponent(e)}${t==="deep"?"?deep=1":""}`),he=(e,t)=>W(`/api/job/${e}?offset=${t}`),Ze=e=>W(`/api/snapshots/${e}`);async function G(e){const t=await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)}),n=await t.json();if(!t.ok&&!n.output)throw new Error(`Request failed (${t.status})`);return n}function et(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="create"&&t.length===1?{page:"create"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="remote"&&t[1]&&t.length===2?{page:"remote",name:decodeURIComponent(t[1])}:t[0]==="remote"&&t[1]&&t[2]==="instance"&&t[3]&&t.length===4?{page:"remote-instance",name:decodeURIComponent(t[1]),instance:decodeURIComponent(t[3])}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const w=()=>et(location.pathname);let ae=()=>{};function tt(e){ae=e}function S(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),ae(w())}function q(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}const F=e=>`/remote/${encodeURIComponent(e)}`,nt=(e,t)=>`${F(e)}/instance/${encodeURIComponent(t)}`;function at(){window.addEventListener("popstate",()=>ae(w())),document.addEventListener("click",e=>{var a,s;const t=(s=(a=e.target)==null?void 0:a.closest)==null?void 0:s.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),S(n))})}let st=0;const K={};function rt(){return"mcsel"+ ++st}function se(e,t,n,a,s,o){const d=t.find(h=>h.v===n),u=d?d.label:t[0]?t[0].label:"";K[e]=a;const b=s?"opacity-50 pointer-events-none":"",k=o?"block w-full":"inline-block",m=o?"w-full":"w-48";return`<div class="relative ${k}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${m} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${b}">
      <span class="truncate flex-1" data-csel-label>${l(u)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${m} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(h=>`<button type="button" data-v="${l(h.v)}" data-search="${l(h.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${l(h.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${h.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${l(h.label)}</button>`).join("")}
      </div>
    </div></div>`}function ot(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",ve(e),n.focus())}}function lt(e,t){var s,o;(s=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||s.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(o=K[e])==null||o.call(K,t)}function ve(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const s=a;s.style.display=(s.dataset.search||"").includes(n)?"":"none"})}function it(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function E(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function dt(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function R(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const D=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function re(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const A="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function U(e,t,n,a){return`<button class="${A}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function oe(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${l(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function le(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const y=e=>(e||0).toLocaleString(),J=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),ie=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function ct(){const e=r.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No supported agent-usage data found.</div>`;const t=e.total||{},n=(u,b,k)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${u}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${b}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([u,b])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${u}</span>
      <span class="flex-1 text-neutral-500">${y(ie(b))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${J(b.cost)}</span></div>`).join(""),s=Object.entries(e.per_instance||{}).sort((u,b)=>(b[1].cost||0)-(u[1].cost||0)).map(([u,b])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${u==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${l(u)}</span>
      <span class="flex-1 text-neutral-500">${y(ie(b))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${J(b.cost)}</span></div>`).join(""),o=e.sessions||[],d=o.map(u=>`
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${u.id}</code>
      <span class="w-16 capitalize text-neutral-500">${u.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(u.instances||[]).join(", ")||"—"}</span>
      <span class="text-neutral-500">${y(u.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${J(u.cost)}</span></div>`).join("");return`<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Agent usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Currently collected from Claude session telemetry; this collector does not yet include Codex. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${n("Total tokens",y(e.tokens))}
      ${n("Estimated cost",J(e.cost))}
      ${n("Sessions",y(o.length)+(o.length>=25?"+":""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${y(t.in)} · out ${y(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${y(t.cw)} · cache read ${y(t.cr)}</div>
    </div>
    <div class="mt-6">${R("By model")}${a||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${R("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${s||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${R("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${d}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const ut=ie;function pt(){const e=r.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
    <h1 class="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Give your coding agent a real WordPress</h1>
    <p class="mt-2 text-[14px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      The sandbox hands Codex, Claude, or another connected agent a <b>live, disposable WordPress environment</b> to work in — so it
      stops guessing and starts proving. Through the connected tools your agent can run WP-CLI, hit the
      REST API and database, open pages in a real browser, read and edit your plugin's actual code,
      and watch the logs. Each item on the left is one isolated environment the agent can fully control.</p>

    <div class="mt-7 grid grid-cols-3 gap-3">
      ${le("Build","Your agent implements features against a running install and verifies them live — not from memory.")}
      ${le("Reproduce","Hand your agent the environment and it reproduces the bug on a real stack — confirmed, not assumed.")}
      ${le("Fix &amp; prove","Reproduce → fix the real code → re-verify in the same environment. Fast, and proven.")}
    </div>

    <div class="mt-7 rounded-lg border border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 p-4">
      <div class="text-[13px] font-medium text-neutral-800 dark:text-neutral-100">How connected agents work here</div>
      <p class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
        Just tell your agent <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">focus &lt;plugin&gt;</code>
        in chat — it picks the right environment, loads your plugin's code + context, and can build,
        debug, and fix it end-to-end. Every environment also exposes its own tools
        (<code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">mcp__sandbox-&lt;name&gt;__*</code>)
        so multiple Codex or Claude sessions can work in parallel without colliding.</p>
      <button onclick="sb.showHelp()" class="mt-2 text-[13px] text-accent dark:text-blue-400 hover:underline">How it works →</button>
    </div>

    <p class="mt-5 text-[12.5px] text-neutral-400">
      It's a real WordPress under the hood — break it, migrate it, throw it away. Snapshot or delete
      anytime; nothing here is precious.</p>

    ${e?`<p class="mt-6 text-[13px] text-neutral-400">${e} environment${e===1?"":"s"} ready — pick one on the left, or hand one to your agent.</p>`:'<button onclick="sb.doCreate()" class="mt-6 px-4 py-2 rounded-full bg-accent text-white text-[13px] font-medium hover:bg-blue-700">Create your first environment</button>'}
  </div>`}function bt(e){const t=e.domain||"",n=e.url.startsWith("https://"),a=t.endsWith(".tst");if(n)return`<span class="text-neutral-700 dark:text-neutral-200">${l(t)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;const s=a?`<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${oe("./sb secure "+e.name)}
       </div>`:"";return`<span class="text-neutral-700 dark:text-neutral-200">${l(t)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${s}`}function xt(e){var a;const t=r.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show agent token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Agent usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${y(ut(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${J(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No agent usage attributed to this instance yet.</div>'}function mt(e){if(!e)return pt();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${l(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${E()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${l(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=r.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,s=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?E():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?E("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${l(e.name)}</h1>
        ${dt(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${l(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${e.login_url?re(e.login_url,"Login",e.running):re(e.url+"/wp-admin","Admin",e.running)}
      ${re(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${s}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?E():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${R("Overview")}
    ${D("Web server",`<div class="flex items-center gap-2">
        ${se("serverSel",(r.data.servers&&r.data.servers.length?r.data.servers:["apache","nginx","litespeed"]).map(o=>({v:o,label:o})),e.server,o=>window.sb.doServer(e.name,o),!!t||!e.running)}
        ${t==="server"?E():""}
        ${e.running?"":'<span class="text-[11px] text-neutral-400">start the site to switch</span>'}</div>`)}
    ${e.domain?D("Domain",bt(e)):""}
    ${D("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${D("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${D("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':l(e.project))}
    ${D("Focus plugin",`<div class="flex items-center gap-2">
        ${se("focusSel",[{v:"",label:"— none —"}].concat(r.data.plugins.map(o=>({v:o,label:o}))),e.focus&&e.focus!=="—"?e.focus:"",o=>window.sb.doFocus(e.name,o),!!t)}
        ${t==="focus"||t==="unfocus"?E():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${R("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${U(e.name,"logs","Logs")}
      ${U(e.name,"status","Status")}
      ${U(e.name,"doctor","Doctor")}
      ${U(e.name,"update","Update plugins")}
      <button class="${A}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${A}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${A}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${U(e.name,"xdebug","Xdebug",{state:"status"})}
    </div>
    <div class="flex gap-2 mt-2.5">
      <span class="px-2 py-1.5 text-[12.5px] font-mono text-neutral-400">wp</span>
      <input id="wpArgs" placeholder="plugin list --status=active"
        onkeydown="if(event.key==='Enter')sb.doWp('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] font-mono focus:border-accent outline-none">
      <button class="${A}" onclick="sb.doWp('${e.name}')">Run</button>
    </div>
  </div>

  <div class="px-6 pb-6">
    ${R("Plugins","Search to install from WordPress.org, or symlink a local source")}
    <div class="flex gap-2">
      <input id="plugQ" placeholder="Search plugins by name or slug…" oninput="sb.plugFilter()"
        onkeydown="if(event.key==='Enter')sb.doInstall('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">
      <button class="${A}" onclick="sb.doInstall('${e.name}')">Install from .org</button>
    </div>
    <div id="plugResults" class="mt-2 flex flex-col gap-1"></div>
  </div>

  <div class="px-6 pb-8">
    ${R("Use with Codex or Claude","Connect a coding-agent session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Codex or Claude in chat (simplest):</div>
      ${oe("focus "+(e.focus&&e.focus!=="—"?e.focus:"<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${oe("mcp__"+e.mcp_server+"__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${e.url}</code>.</div>
      ${xt(e.name)}
    </div>
  </div>`}const c={page:"min-h-full bg-neutral-50 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-50",shell:"max-w-7xl mx-auto px-4 py-5 sm:px-6 sm:py-7 lg:px-8",panel:"rounded-xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-sm",muted:"text-neutral-600 dark:text-neutral-300",quiet:"text-neutral-500 dark:text-neutral-400",label:"text-[11px] font-semibold uppercase tracking-[0.12em] text-neutral-500 dark:text-neutral-400",button:"inline-flex min-h-9 items-center justify-center rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 px-3 py-2 text-[12px] font-medium text-neutral-800 dark:text-neutral-100 hover:bg-neutral-50 dark:hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950",primary:"inline-flex min-h-9 items-center justify-center rounded-lg bg-blue-700 px-3 py-2 text-[12px] font-semibold text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950"};function ke(e,t=!0){return`<span class="host-icon relative flex shrink-0 items-center justify-center rounded-lg bg-white dark:bg-neutral-800 ${e==="all"?"text-blue-600 dark:text-blue-400":e==="local"?"text-emerald-600 dark:text-emerald-400":"text-blue-700 dark:text-blue-300"}">
    <svg aria-hidden="true" class="h-4 w-4" viewBox="0 0 24 24" fill="${e==="all"?"currentColor":"none"}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${e==="all"?'<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>':e==="local"?'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>':'<rect x="4" y="3" width="16" height="8" rx="2"/><rect x="4" y="13" width="16" height="8" rx="2"/><path d="M8 7h.01M8 17h.01"/>'}</svg>
    <span aria-hidden="true" class="host-dot absolute rounded-full border-neutral-100 dark:border-neutral-950 ${e==="all"?"bg-blue-500":t?"bg-emerald-500":"bg-amber-400"}"></span>
  </span>`}function ft(e="all"){const t=r.data.instances[0]?q(r.data.instances[0].name):"/create",n=r.data.remotes.find(m=>m.name===e),a=e==="local"?"local":n?"remote":"all",s=a==="local"?"Local host":(n==null?void 0:n.name)||"All hosts",o=a!=="remote"||!!(n!=null&&n.control_ready),d=a==="all"?"Host overview":a==="local"?"This machine":o?"Remote available":"Remote unavailable",u="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-[13px] hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:hover:bg-neutral-800",b=(m,h,N,P,X,g)=>`<a href="${m}" data-link ${g?'aria-current="page"':""} class="${u} ${g?"bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-100":"text-neutral-700 dark:text-neutral-200"}">
      ${ke(N,P)}<span class="min-w-0 flex-1"><span class="block truncate font-medium">${l(h)}</span><span class="block truncate text-[10px] text-neutral-400">${l(X)}</span></span>
    </a>`,k=r.data.remotes.map(m=>b(F(m.name),m.name,"remote",m.control_ready,m.control_ready?"Remote available":"Remote unavailable",e===m.name)).join("");return`<details class="group relative" id="hostSelector">
    <summary aria-label="Choose host. Current host: ${l(s)}" class="flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-2 text-left hover:bg-neutral-200/60 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:hover:bg-neutral-900">
      ${ke(a,o)}
      <span class="min-w-0 flex-1"><span class="block truncate text-[13px] font-semibold text-neutral-900 dark:text-neutral-50">${l(s)}</span><span class="block truncate text-[10px] text-neutral-400">${l(d)}</span></span>
      <svg aria-hidden="true" class="h-4 w-4 shrink-0 text-neutral-400" viewBox="0 0 24 24" fill="none"><path d="m7 10 5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </summary>
    <nav aria-label="Hosts" class="absolute left-0 right-0 z-50 mt-1 max-h-64 space-y-0.5 overflow-auto rounded-lg border border-neutral-200 bg-white p-2 shadow-xl dark:border-neutral-700 dark:bg-neutral-900">
      ${b("/","All hosts","all",!0,"Host overview",e==="all")}
      ${b(t,"Local host","local",!0,"This machine",e==="local")}${k}
    </nav>
  </details>`}function ye(e,t,n,a,s,o,d,u){return`<a href="${t}" data-link class="${c.panel} group block p-5 hover:border-blue-300 dark:hover:border-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500">
    <div class="flex items-start gap-3">
      <span class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${a?"bg-emerald-500":"bg-amber-400"}"></span>
      <div class="min-w-0 flex-1"><div class="${c.label}">${l(n)}</div>
        <h2 class="mt-1 truncate text-[16px] font-semibold text-neutral-900 dark:text-white">${l(e)}</h2></div>
      <span aria-hidden="true" class="text-neutral-400 group-hover:text-blue-600">→</span>
    </div>
    <div class="mt-5 grid grid-cols-3 gap-3 border-t border-neutral-200 pt-4 dark:border-neutral-800">
      <div><div class="${c.label}">Instances</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${l(s)}</div></div>
      <div><div class="${c.label}">Running</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${l(o)}</div></div>
      <div><div class="${c.label}">RAM</div><div class="mt-1 text-[14px] font-semibold tabular-nums">${l(d)}</div></div>
    </div>
    <p class="mt-4 text-[12px] ${c.quiet}">${l(u)}</p>
  </a>`}function $e(e="all"){return""}function we(){const e=r.data.instances.length,t=r.data.instances.filter(d=>d.running).length,n=r.data.remotes.map(d=>{var P;const u=r.remote[d.name],b=r.remoteBusy[d.name],k=u!=null&&u.instances?String(u.instances.total):b?"…":"—",m=u!=null&&u.instances?String(u.instances.running):b?"…":"—",h=((P=u==null?void 0:u.host)==null?void 0:P.memory_used_percent)==null?"—":`${u.host.memory_used_percent}%`,N=b?"Refreshing host inventory…":u?`${u.evidence_status} ${u.scan_mode||"fast"} evidence`:d.control_ready?"Waiting for first inventory":"Control service unavailable";return ye(d.name,F(d.name),"Remote host",d.control_ready,k,m,h,N)}).join(""),a=r.data.remotes.reduce((d,u)=>{var b,k;return d+(((k=(b=r.remote[u.name])==null?void 0:b.instances)==null?void 0:k.total)||0)},0),s=1+r.data.remotes.length,o=r.sync.refreshing?"Refreshing inventories…":r.sync.lastCompleted?`Updated ${new Date(r.sync.lastCompleted).toLocaleTimeString()}`:"Loading inventories…";return`<div class="${c.page}"><div class="${c.shell} space-y-6">
    <header class="space-y-4"><div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div class="${c.label}">Host control</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">All Sandbox hosts</h1>
      <p class="mt-1 text-[13px] ${c.muted}">One view of local and remote WordPress capacity.</p></div>
      <div class="flex flex-wrap items-center justify-end gap-3"><div role="status" class="text-[12px] ${r.sync.error?"text-red-700 dark:text-red-300":c.quiet}">${l(r.sync.error||o)}</div><button class="${c.button}" onclick="sb.refreshHosts()">Refresh remote hosts</button></div>
    </div></header>
    <section class="${c.panel} grid grid-cols-2 gap-px overflow-hidden bg-neutral-200 p-0 dark:bg-neutral-800 sm:grid-cols-4">
      ${[["Hosts",s],["Known instances",e+a],["Local running",t],["Remote hosts",r.data.remotes.length]].map(([d,u])=>`<div class="bg-white p-4 dark:bg-neutral-900"><div class="${c.label}">${d}</div><div class="mt-1 text-[24px] font-semibold tabular-nums">${u}</div></div>`).join("")}
    </section>
    <section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Available hosts</h2><span class="text-[11px] ${c.quiet}">Select a host to inspect its instances</span></div>
      <div class="grid gap-4 lg:grid-cols-2">${ye("Local host",r.data.instances[0]?q(r.data.instances[0].name):"/create","This machine",!0,String(e),String(t),"—","Open local instances and lifecycle controls")}${n}</div>
    </section>
  </div></div>`}const Se={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function x(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(Se[t]||Se.info),n.textContent=e,i("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let Y=null;function gt(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${l(e.label||"")}</div>`;if(e.type==="select"){const n=rt(),a=e.options||[],s=a.map(d=>({v:d,label:d})),o=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${l(o)}">`+se(n,s,o,d=>{document.getElementById(`${n}_val`).value=d},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${e.key}" data-type="checkbox"
        ${e.value?"checked":""} class="accent-accent w-3.5 h-3.5">
      ${l(e.label||e.key||"")}</label>`;if(e.type==="checklist"){const a=(e.options||[]).map(s=>`
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${e.key}" value="${l(s.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${l(s.label)}</span>
          ${s.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${l(s.desc)}</span>`:""}
        </span></label>`).join("");return`<div data-checklist-group="${e.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${a||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`}const t=e.oninput?` oninput="${l(e.oninput)}"`:"";return`<input data-k="${e.key}" data-field="${l(e.key||"")}"${t}
    placeholder="${l(e.placeholder||"")}" value="${l(e.value||"")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function z(e={}){return new Promise(t=>{Y=t,i("mTitle").textContent=e.title||"",i("mDesc").textContent=e.desc||"",i("mFields").innerHTML=(e.fields||[]).map(gt).join("");const n=i("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),i("modal").classList.remove("hidden"),setTimeout(()=>{(i("mFields").querySelector("input,select")||n).focus()},30)})}function Z(e){if(i("modal").classList.add("hidden"),Y){const t=Y;Y=null,t(e)}}function ht(){const e={};return i("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),i("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function vt(){i("mCancel").onclick=()=>Z(null),i("mOk").onclick=()=>Z(ht()),i("modal").addEventListener("keydown",e=>{e.key==="Enter"&&i("mOk").click(),e.key==="Escape"&&Z(null)}),i("modal").addEventListener("click",e=>{e.target===i("modal")&&Z(null)})}let Ce=async()=>{};function kt(e){Ce=e}function yt(e){i("console").classList.remove("w-0"),i("console").classList.add("w-[26rem]"),i("conTitle").textContent=e,i("conBody").textContent="",i("conInputRow").classList.add("hidden"),i("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function de(){i("console").classList.add("w-0"),i("console").classList.remove("w-[26rem]"),i("conInputRow").classList.add("hidden")}function L(e){const t=i("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function $t(e,t){i("conTitle").textContent=e,i("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function ce(e,t,n){r.paused=!0,yt(n||"Working…");let a=0,s=!!n;const o=setInterval(async()=>{var u;const d=await he(e,a);!s&&d.status&&(i("conTitle").textContent=d.status.replace(/ [✓✗]$/,""),s=!0),d.chunk?(L(d.chunk),a=(u=d.offset)!=null?u:a):typeof d.offset=="number"&&(a=d.offset),d.done&&(clearInterval(o),r.paused=!1,t&&delete r.busy[t],$t(d.status||"done",d.ok),x(d.status||"done",d.ok?"ok":"err"),await Ce())},800)}let ue=null;const H=[];let C=-1,V=!1;function ee(e){ue=e,i("console").classList.remove("w-0"),i("console").classList.add("w-[26rem]"),i("conTitle").textContent="Terminal — "+e,i("conDot").className="w-2 h-2 rounded-full bg-emerald-500",i("conBody").textContent.trim()||L("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),i("conInputRow").classList.remove("hidden"),setTimeout(()=>i("conInput").focus(),60)}async function wt(){if(V)return;const e=i("conInput"),t=e.value.trim();if(!t||!ue)return;H.push(t),C=H.length,e.value="",L("› "+t+`
`),V=!0,i("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await G({instance:ue,action:"term",cmd:t})}catch(o){L("error: "+o+`
`),V=!1;return}if(!n.job_id){L((n.output||"failed")+`
`),V=!1;return}let a=0;const s=setInterval(async()=>{var d;const o=await he(n.job_id,a);o.chunk?(L(o.chunk),a=(d=o.offset)!=null?d:a):typeof o.offset=="number"&&(a=o.offset),o.done&&(clearInterval(s),V=!1,L(`
`),i("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function St(e){const t=i("conInput");e.key==="Enter"?wt():e.key==="ArrowUp"?(C>0&&(C--,t.value=H[C]||""),e.preventDefault()):e.key==="ArrowDown"&&(C<H.length-1?(C++,t.value=H[C]||""):(C=H.length,t.value=""),e.preventDefault())}function Ct(){i("conClose").onclick=de,i("conInput").addEventListener("keydown",e=>St(e))}let _e=async()=>{},T=()=>{};function _t(e){_e=e.refresh,T=e.render}const je={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function I(e,t,n={}){r.busy[e]=t,T();let a;try{a=await G(Object.assign({instance:e,action:t},n))}catch(s){delete r.busy[e],x("request failed: "+s,"err"),T();return}if(a.job_id){const s=je[t]?je[t](e):ne(t)+" "+e;x(t.replace("-"," ")+" started…","info"),ce(a.job_id,e,s)}else delete r.busy[e],a.ok?x(ne(t)+" "+e+" ✓","ok"):x((a.output||"failed").split(`
`)[0],"err"),await _e()}async function O(e,t,n={}){let a;try{a=await G(Object.assign({instance:e,action:t},n))}catch(s){x("request failed: "+s,"err");return}if(a.job_id){const s={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};ce(a.job_id,null,(s[t]||ne(t))+" — "+e)}else x((a.output||"failed").split(`
`)[0],"err")}async function jt(e,t){t===""?I(e,"unfocus"):t&&I(e,"focus",{slug:t})}async function Rt(e,t){const n=r.data.instances.find(s=>s.name===e);if(!t||n&&n.server===t)return;r.busy[e]="server",T();let a;try{a=await G({instance:e,action:"server",server:t})}catch(s){delete r.busy[e],T(),x("request failed: "+s,"err");return}a.job_id?(x("switching "+e+" → "+t+"…","info"),ce(a.job_id,e,"Switching "+e+" → "+t)):(delete r.busy[e],T(),x((a.output||"failed").split(`
`)[0],"err"))}async function Lt(e){const t=await z({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?I(e,"delete",{confirm:e}):t&&x("name did not match — not deleted","err")}function Tt(e){const n=i("wpArgs").value.trim();if(!n){x("enter a wp-cli command","err");return}O(e,"wp",{args:n})}async function It(e){const t=await z({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&O(e,"snapshot",{name:t.name})}async function Mt(e){let t=[];try{t=(await Ze(e)).snapshots||[]}catch(a){}if(!t.length){x("no snapshots for "+e,"err");return}const n=await z({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&O(e,"restore",{name:n.name})}async function Bt(e){const t=r.data.seeds||[];if(!t.length){x("no WXR files in runtime/seeds/","err");return}const n=await z({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&O(e,"seed",{file:n.file})}function Pt(e){const t=(i("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){x("type a plugin slug to install","err");return}O(e,"install",{slug:t})}function qt(e){const t=(i("plugQ").value||"").toLowerCase().trim(),n=i("plugResults");if(!t){n.innerHTML="";return}const a=r.data.instances.find(o=>o.name===e),s=(r.data.plugins||[]).filter(o=>o.toLowerCase().includes(t)).slice(0,8);n.innerHTML=s.map(o=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${l(o)}</span>
      ${a&&a.focus===o?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${l(o)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${l(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function Et(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function Dt(){try{r.usage=await ge()}catch(e){r.usage={available:!1}}T()}function Re(){S("/create")}async function At(){var t;const e=await((t=window.sandboxDesktop)==null?void 0:t.chooseProjectDirectory());e&&(i("createProject").value=e)}function Le(){const e=document.getElementById("chooseProject");!e||!window.sandboxDesktop||(e.hidden=!1,e.addEventListener("click",()=>void At()))}function Ht(){const e=i("createProject").value.trim(),t=i("createLabel").value.trim().toLowerCase();if(!e.startsWith("/")){x("enter an absolute local project path","err");return}if(t&&!/^[a-z0-9][a-z0-9_-]{0,30}$/.test(t)){x("label must use a-z, 0-9, _ or -","err");return}I(t||"new instance","create",{project_dir:e,label:t})}function Ot(){return`<div class="max-w-2xl mx-auto px-6 py-8">
    <a href="/" data-link class="inline-flex items-center gap-1 text-[12.5px] text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300">
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Back</a>

    <h1 class="mt-3 text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Create an instance</h1>
    <p class="mt-2 text-[13px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      Create a local instance from an existing project directory. Each plugin repo carries its own
      <code class="px-1 rounded bg-neutral-200 dark:bg-neutral-800">sandbox.config.json</code>,
      and the resolved directory becomes the instance identity.</p>

    <div class="mt-5 space-y-4 rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <div><label for="createProject" class="block text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">Local project directory</label>
      <p class="mt-1 text-[11px] text-neutral-500 dark:text-neutral-400">Existing absolute path on this machine. It is validated again by the local-only dashboard service.</p>
      <div class="mt-2 flex gap-2"><input id="createProject" autocomplete="off" placeholder="/Users/you/Sites/plugin" class="min-w-0 flex-1 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-[13px] text-neutral-900 outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-200 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100">
      <button id="chooseProject" hidden type="button" class="rounded-lg border border-neutral-300 px-3 py-2 text-[13px] font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-800">Choose folder</button></div></div>
      <div><label for="createLabel" class="block text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">Instance label <span class="font-normal text-neutral-400">optional</span></label>
      <input id="createLabel" autocomplete="off" maxlength="31" placeholder="review" class="mt-2 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-[13px] text-neutral-900 outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-200 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"></div>
      <div class="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Creates on the local host only. Remote creation is unavailable until remote lifecycle operations have a service-backed API.</div>
      <button onclick="sb.submitCreate()" class="rounded-lg bg-blue-700 px-4 py-2 text-[13px] font-semibold text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500">Create local instance</button>
    </div>

    <p class="mt-4 text-[12.5px] text-neutral-500 dark:text-neutral-400">Creation runs as a background job. Progress opens in the activity panel.</p>

    <div class="mt-7">
      <a href="/" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Back to instances</a>
    </div>
  </div>`}const $=e=>{if(e==null)return"unknown";const t=["B","KiB","MiB","GiB","TiB"];let n=Math.max(0,e),a=0;for(;n>=1024&&a<t.length-1;)n/=1024,a++;return`${n.toFixed(a>1?1:0)} ${t[a]}`},f=e=>e==null||!Number.isFinite(e)?"unknown":String(e),v=(e,t,n="")=>`<div class="${c.panel} p-4">
  <div class="${c.label}">${l(e)}</div>
  <div class="mt-1 text-[20px] font-semibold tabular-nums text-neutral-900 dark:text-white">${l(t)}</div>
  ${n?`<div class="mt-1 text-[11px] ${c.quiet}">${l(n)}</div>`:""}</div>`;function pe(e,t,n,a){const s=t.length?t.map(o=>`<tr class="border-b border-brd/70 dark:border-brd-dark/70 last:border-0">${n.map(([,,d])=>`<td class="py-2.5 pr-4 text-[12px] text-neutral-600 dark:text-neutral-300">${l(d(o))}</td>`).join("")}</tr>`).join(""):`<tr><td colspan="${n.length}" class="py-4 text-[12px] text-neutral-400">${l(a)}</td></tr>`;return`<section class="${c.panel} p-4"><div class="flex items-center justify-between gap-3 mb-3"><h2 class="text-[13px] font-semibold">${l(e)}</h2><span class="text-[11px] ${c.quiet}">${t.length} observed</span></div><div class="overflow-x-auto"><table class="w-full text-left"><thead><tr>${n.map(([o,d])=>`<th scope="col" class="pb-2 pr-4 ${c.label}">${l(d||o)}</th>`).join("")}</tr></thead><tbody>${s}</tbody></table></div></section>`}function Te(e,t,n){var De,Ae,He,Oe,Ne,We,Fe,Ue,Je,ze,Ve,Qe,Xe;if(!t)return`<div class="${c.page}"><div class="${c.shell}">${$e(e)}<div class="py-16 text-center text-neutral-500 dark:text-neutral-400 text-[13px]">Loading ${l(e)}…</div></div></div>`;const a=t.instances||{total:0,running:0,stopped:0,rows:[]},s=t.host||{},o=t.storage,d=t.evidence_status==="complete"?"text-emerald-600":t.evidence_status==="partial"?"text-amber-600":"text-rose-600",u=s.disk_total_bytes?Math.min(100,Math.max(0,(s.disk_used_bytes||0)*100/s.disk_total_bytes)):0,b=t.per_instance_usage||[],k=a.rows.map(p=>({...b.find(on=>on.name===p.name),name:p.name,running:p.running})),m=((De=t.process_view)==null?void 0:De.apps)||[],h=((Ae=t.process_view)==null?void 0:Ae.processes)||[],N=((He=t.containers)==null?void 0:He.rows)||[],P=JSON.stringify(e),X=!!r.remoteBusy[e],g=n?b.find(p=>p.name===n):void 0,j=n?a.rows.find(p=>p.name===n):void 0,rn=n?`<section class="${c.panel} p-5"><div class="flex items-start justify-between gap-4"><div><div class="${c.label}">Remote instance · read only</div><h2 class="mt-1 text-[20px] font-semibold">${l(n)}</h2><p class="mt-1 text-[12px] ${c.quiet}">${l((j==null?void 0:j.project)||"Project unavailable")} · ${l((j==null?void 0:j.server)||"server unknown")}</p></div><a href="${F(e)}" data-link class="${c.button}">Back to host</a></div><div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">${v("State",j?j.running?"running":"stopped":"unknown")}${v("RAM",$(g==null?void 0:g.memory_used_bytes))}${v("CPU",`${f(g==null?void 0:g.cpu_percent)}%`)}${v("Containers",f(g==null?void 0:g.container_count),String((g==null?void 0:g.attribution_status)||"unknown"))}</div><p class="mt-4 text-[12px] ${c.quiet}">Remote lifecycle controls are not exposed by this inventory endpoint yet. Resource values are container-attributed evidence.</p></section>`:"";return`<div class="${c.page}"><div class="${c.shell} space-y-6"><header class="space-y-4">${$e(e)}<div class="flex flex-col gap-4 md:flex-row md:items-end"><div class="flex-1"><div class="${c.label}">Remote host</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">${l(e)}</h1><p class="mt-1 text-[12px] ${c.muted}">Authenticated inventory · ${l(t.scan_mode||"fast")} snapshot</p></div><div class="flex flex-wrap items-center gap-2"><span class="mr-1 text-[12px] font-semibold ${d}">${X?"refreshing":l(t.evidence_status)}</span><button ${X?"disabled":""} class="${c.button}" onclick="sb.refreshRemote(${P})">Quick refresh</button><button ${X?"disabled":""} class="${c.primary}" onclick="sb.refreshRemote(${P},true)">Rebuild attribution</button></div></div></header>${(Oe=t.partial_reasons)!=null&&Oe.length?`<div role="status" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Partial evidence: ${l(t.partial_reasons.join(", "))}. Unknown values are intentionally not treated as zero.</div>`:""}${rn}<section class="${c.panel} p-5"><div class="flex items-end justify-between gap-3"><div><div class="${c.label}">Host disk capacity</div><div class="mt-1 text-[18px] font-semibold">${$(s.disk_used_bytes)} used <span class="font-normal ${c.quiet}">of ${$(s.disk_total_bytes)}</span></div></div><div class="text-right text-[12px] ${c.quiet}">${$(s.disk_free_bytes)} free</div></div><div class="mt-4 h-3 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"><div class="h-full bg-blue-700" style="width:${u}%"></div></div><div class="mt-2 text-[11px] ${c.quiet}">Container values overlap host capacity and are not additive.</div></section><div class="grid grid-cols-2 gap-3 lg:grid-cols-4">${v("Hosted instances",String(a.total),`${a.running} running · ${a.stopped} stopped`)}${v("RAM used",s.memory_used_percent==null?"unknown":`${s.memory_used_percent}%`,`${f(s.memory_used_mb)} of ${f(s.memory_total_mb)} MiB`)}${v("Load 1m",f(s.load_1m),"point-in-time host sample")}${v("Active jobs",f((Ne=t.jobs)==null?void 0:Ne.active),`${f((We=t.jobs)==null?void 0:We.queued)} queued`)}${v("Containers",f(N.length),((Fe=t.containers)==null?void 0:Fe.status)||"unavailable")}${v("Storage attribution",(o==null?void 0:o.attribution_status)||"unknown",(o==null?void 0:o.status)||"unavailable")}${v("Unattributed",String((Je=(Ue=t.unattributed_containers)==null?void 0:Ue.length)!=null?Je:"unknown"),"containers without a confident match")}${v("Disk pressure",`${u.toFixed(1)}%`,"capacity-backed")}</div><section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Hosted instances</h2><span class="text-[11px] ${c.quiet}">Select an instance for its read-only resource view</span></div><div class="overflow-x-auto ${c.panel}"><table class="w-full text-left"><thead><tr>${["Instance","State","RAM","CPU","Evidence"].map(p=>`<th class="p-3 ${c.label}">${p}</th>`).join("")}</tr></thead><tbody>${k.length?k.map(p=>`<tr class="border-t border-neutral-200 dark:border-neutral-800"><td class="p-3 text-[12px] font-medium"><a data-link class="text-blue-700 hover:underline dark:text-blue-300" href="${nt(e,String(p.name))}">${l(String(p.name))}</a></td><td class="p-3 text-[12px]">${p.running?"running":"stopped"}</td><td class="p-3 text-[12px] tabular-nums">${l($(p.memory_used_bytes))}</td><td class="p-3 text-[12px] tabular-nums">${l(f(p.cpu_percent))}%</td><td class="p-3 text-[12px] ${c.quiet}">${l(String(p.attribution_status||"unattributed"))}</td></tr>`).join(""):`<tr><td colspan="5" class="p-4 text-[12px] ${c.quiet}">No hosted instances reported.</td></tr>`}</tbody></table></div></section><div class="grid gap-4 xl:grid-cols-3">${pe("Apps",m,[["name","App",p=>String(p.name||"unknown")],["process_count","Processes",p=>f(p.process_count)],["rss_bytes","RSS",p=>$(p.rss_bytes)],["cpu_percent","CPU avg",p=>`${f(p.cpu_percent)}%`]],((ze=t.process_view)==null?void 0:ze.status)||"Process view unavailable")}${pe("Processes",h.slice(0,50),[["pid","PID",p=>f(p.pid)],["name","Name",p=>String(p.name||"unknown")],["rss_bytes","RSS",p=>$(p.rss_bytes)],["cpu_percent","CPU avg",p=>`${f(p.cpu_percent)}%`]],"No process rows")}${pe("Containers",N,[["name","Container",p=>String(p.name||"unknown")],["memory_used_bytes","Memory",p=>$(p.memory_used_bytes)],["memory_percent","Memory %",p=>`${f(p.memory_percent)}%`],["cpu_percent","CPU",p=>`${f(p.cpu_percent)}%`]],((Ve=t.containers)==null?void 0:Ve.status)||"Container view unavailable")}</div><section class="${c.panel} p-4"><h2 class="text-[13px] font-semibold">Storage evidence</h2><p class="mt-1 text-[12px] ${c.muted}">${l((o==null?void 0:o.status)||"unavailable")} · attribution ${l((o==null?void 0:o.attribution_status)||"unknown")} · used ${l($((Qe=o==null?void 0:o.capacity)==null?void 0:Qe.used_bytes))} · available ${l($((Xe=o==null?void 0:o.capacity)==null?void 0:Xe.available_bytes))}</p><p class="mt-2 text-[11px] ${c.quiet}">Deep scans are bounded and may remain partial. Unknown bytes are not cleanup authority.</p></section></div></div>`}function Nt(e,t){const n=t.map(a=>{const s=a.disabled?"opacity-40 pointer-events-none":"",o=a.danger?"text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40":"text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800";return`<button type="button" ${a.disabled?"disabled":""}
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuClose();${a.js}"
      class="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left ${o} ${s}">
      ${a.icon?`<span class="w-3.5 h-3.5 grid place-items-center shrink-0 opacity-70">${a.icon}</span>`:""}
      <span class="flex-1">${l(a.label)}</span></button>`}).join("");return`<span class="relative shrink-0" data-rowmenu="${e}">
    <button type="button" title="More actions" aria-label="More actions"
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuToggle('${e}')"
      class="w-6 h-6 grid place-items-center rounded text-neutral-500 dark:text-neutral-400
      hover:bg-neutral-200 dark:hover:bg-neutral-700 hover:text-neutral-900 dark:hover:text-neutral-100">
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
    </button>
    <div data-rowmenu-pop class="hidden absolute right-0 z-[60] mt-1 min-w-[10rem] py-1
      rounded-lg border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl">
      ${n}</div></span>`}function Wt(e){var n;let t=!1;document.querySelectorAll("[data-rowmenu-pop]").forEach(a=>{const s=a.closest("[data-rowmenu]"),o=!a.classList.contains("hidden");s&&s.dataset.rowmenu===e&&(t=o),a.classList.add("hidden")}),!t&&((n=document.querySelector(`[data-rowmenu="${e}"] [data-rowmenu-pop]`))==null||n.classList.remove("hidden"))}function Ie(){document.querySelectorAll("[data-rowmenu-pop]").forEach(e=>e.classList.add("hidden"))}function Ft(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-rowmenu]")||Ie()})}function be(){const e=w();return e.page==="instance"?e.name:null}const _={play:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',stop:'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',restart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',admin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',term:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7l4 4-4 4"/><path d="M12 15h6"/></svg>',snapshot:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>',restore:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8"/><path d="M3 3v5h5"/></svg>',trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>'};function Ut(e){const t=JSON.stringify(e.name),n=[e.running?{label:"Stop",icon:_.stop,js:`sb.act(${t},'stop')`}:{label:"Start",icon:_.play,js:`sb.act(${t},'start')`},{label:"Restart",icon:_.restart,js:`sb.act(${t},'restart')`,disabled:!e.running},{label:"Open admin",icon:_.admin,js:`window.open(${JSON.stringify(e.url+"/wp-admin")},'_blank')`,disabled:!e.running},{label:"Console",icon:_.term,js:`sb.navigate(${JSON.stringify(q(e.name,!0))})`,disabled:!e.running},{label:"Snapshot",icon:_.snapshot,js:`sb.doSnapshot(${t})`},{label:"Restore…",icon:_.restore,js:`sb.doRestore(${t})`}];return n.push({label:"Delete",icon:_.trash,js:`sb.doDelete(${t})`,danger:!0}),n}function Jt(e){const t=e.name===be(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",s=r.busy[e.name]?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:`<span class="ml-auto transition-opacity ${t?"opacity-100":"opacity-0 group-hover:opacity-100"}">
        ${Nt("rm-"+e.name,Ut(e))}</span>`;return`<a href="${q(e.name)}" data-link class="group w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${l(e.name)}</span>
     ${s}</a>`}function Me(e=!1){if(document.querySelector("[data-rowmenu-pop]:not(.hidden)")||!e&&document.querySelector("#hostSelector[open]"))return;const t=r.data.instances,n=w(),a=n.page==="instance"||n.page==="create"?"local":n.page==="remote"||n.page==="remote-instance"?n.name:"all";i("hostSelectorSlot").innerHTML=ft(a),i("list").innerHTML=t.map(Jt).join("");const s=t.filter(o=>o.running).length;i("runcount").textContent=s+"/"+t.length,i("footstat").textContent=t.length?s+" of "+t.length+" running":"no instances yet"}let Be="";function zt(e){if(e.page==="instance"){const t=r.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,t.url,t.domain||"",r.busy[t.name]||"",r.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(r.usage?"loaded":"pending"):e.page==="remote"||e.page==="remote-instance"?e.page+":"+e.name+":"+(e.page==="remote-instance"?e.instance:"")+":"+!!r.remoteBusy[e.name]+":"+JSON.stringify(r.remote[e.name]||null):e.page==="home"?"home:"+r.sync.refreshing+":"+r.sync.lastCompleted+":"+r.sync.error+":"+JSON.stringify(r.data.remotes)+":"+JSON.stringify(r.remote):e.page}function Vt(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!i("modal").classList.contains("hidden"))}function Qt(e){switch(e.page){case"create":return Ot();case"usage":return ct();case"remote":return Te(e.name,r.remote[e.name]);case"remote-instance":return Te(e.name,r.remote[e.name],e.instance);case"instance":{const t=r.data.instances.find(n=>n.name===e.name)||null;return mt(t)}case"home":return we();default:return we()}}function M(e){const t=w(),n=zt(t);!e&&n===Be||!e&&Vt()||(Be=n,i("detail").innerHTML=Qt(t))}function xe(){Me(!0),M(!0)}let Q=null,me=!1;const te=new Map;function fe(e,t="fast"){const n=te.get(e);if(n&&t==="fast")return n;const s=(async()=>{n&&await n,r.remoteBusy[e]=!0,M(!1);try{r.remote[e]=await Ye(e,t)}finally{delete r.remoteBusy[e]}})().finally(()=>{te.get(e)===s&&te.delete(e)});return te.set(e,s),s}async function Xt(){if(!r.paused){r.sync.refreshing=!0,r.sync.error=null,M(!1);try{r.data=await Ke();const e=w();(e.page==="remote"||e.page==="remote-instance")&&await fe(e.name),r.sync.lastCompleted=Date.now()}catch(e){r.sync.error=e instanceof Error?e.message:"Refresh failed"}finally{r.sync.refreshing=!1,Me(),M(!1)}}}function B(e=!1){return Q?(e&&(me=!0),Q):(Q=Xt().finally(()=>{Q=null,me&&(me=!1,B())}),Q)}async function Gt(){S("/usage"),i("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading agent usage…</div>';try{r.usage=await ge()}catch(e){r.usage={available:!1}}M(!0)}function Kt(){S("/")}function Yt(e){S(q(e))}function Zt(e){S(F(e))}async function en(e,t=!1){try{await fe(e,t?"deep":"fast"),M(!0)}catch(n){x("remote inventory refresh failed","err")}}async function tn(){const e=r.data.remotes.filter(a=>a.control_ready).map(a=>a.name),n=(await Promise.allSettled(e.map(a=>fe(a)))).filter(a=>a.status==="rejected").length;n&&x(`${n} host ${n===1?"refresh":"refreshes"} failed`,"err"),M(!0)}function Pe(){z({title:"How AI agents work here",okText:"Got it",desc:`The sandbox gives Codex, Claude, and other connected agents a live WordPress to act in, so they can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — the agent picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. One MCP server (mcp__sandbox__*) serves every project — each tool takes the project directory and resolves the right environment from the registry. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const nn={navigate:S,goHome:Kt,selectInstance:Yt,selectRemote:Zt,refreshRemote:en,refreshHosts:tn,showUsage:Gt,showHelp:Pe,openTerminal:ee,submitCreate:Ht,doCreate:Re,doDelete:Lt,doFocus:jt,doServer:Rt,doSnapshot:It,doRestore:Mt,doSeed:Bt,doWp:Tt,doInstall:Pt,plugFilter:()=>qt(be()),loadUsageThenRender:Dt,act:I,op:O,cselToggle:ot,cselPick:lt,cselFilter:ve,rowMenuToggle:Wt,rowMenuClose:Ie,consoleClose:de,copyText:Et};window.sb=nn;function an(){_t({refresh:()=>B(!0),render:xe}),kt(()=>B(!0)),vt(),Ct(),it(),Ft(),at(),i("newBtn").onclick=Re,i("startAll").onclick=()=>I("*","start-all"),i("stopAll").onclick=()=>I("*","stop-all"),i("helpBtn").onclick=Pe,i("termBtn").onclick=()=>{const t=be()||r.data.instances[0]&&r.data.instances[0].name;if(!t){x("create an instance first","err");return}S(q(t,!0)),ee(t)},tt(t=>{xe(),t.page==="create"&&Le(),(t.page==="remote"||t.page==="remote-instance"||t.page==="home")&&B(),t.page==="instance"&&t.console?ee(t.name):de()}),xe(),w().page==="create"&&Le();const e=w();e.page==="instance"&&e.console&&ee(e.name),sn()}const qe=3e4;let Ee=0;function sn(){const e=()=>{window.clearTimeout(Ee),Ee=window.setTimeout(async()=>{document.visibilityState==="visible"&&await B(),e()},qe)};B().finally(e),document.addEventListener("visibilitychange",()=>{document.visibilityState==="visible"&&(!r.sync.lastCompleted||Date.now()-r.sync.lastCompleted>qe)&&B()})}an()})();
