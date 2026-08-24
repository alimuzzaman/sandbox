(function(){"use strict";const i=e=>document.getElementById(e),Qe={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},l=e=>String(e).replace(/[&<>"]/g,t=>Qe[t]),ne=e=>e.charAt(0).toUpperCase()+e.slice(1),r={data:{instances:[],plugins:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1,remotes:[]},busy:{},usage:null,remote:{},remoteBusy:{},sync:{refreshing:!1,lastCompleted:null,error:null},paused:!1};async function H(e){const t=await fetch(e),n=await t.json();if(!t.ok)throw new Error(n.error||`Request failed (${t.status})`);return n}const Xe=()=>H("/api/instances"),ve=()=>H("/api/usage"),Ge=(e,t="fast")=>H(`/api/remote/${encodeURIComponent(e)}${t==="deep"?"?deep=1":""}`),he=(e,t)=>H(`/api/job/${e}?offset=${t}`),Ke=e=>H(`/api/snapshots/${e}`);async function Q(e){const t=await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)}),n=await t.json();if(!t.ok&&!n.output)throw new Error(`Request failed (${t.status})`);return n}function Ye(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="create"&&t.length===1?{page:"create"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="remote"&&t[1]&&t.length===2?{page:"remote",name:decodeURIComponent(t[1])}:t[0]==="remote"&&t[1]&&t[2]==="instance"&&t[3]&&t.length===4?{page:"remote-instance",name:decodeURIComponent(t[1]),instance:decodeURIComponent(t[3])}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const _=()=>Ye(location.pathname);let ae=()=>{};function Ze(e){ae=e}function y(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),ae(_())}function M(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}const P=e=>`/remote/${encodeURIComponent(e)}`,et=(e,t)=>`${P(e)}/instance/${encodeURIComponent(t)}`;function tt(){window.addEventListener("popstate",()=>ae(_())),document.addEventListener("click",e=>{var a,o;const t=(o=(a=e.target)==null?void 0:a.closest)==null?void 0:o.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),y(n))})}let nt=0;const X={};function at(){return"mcsel"+ ++nt}function re(e,t,n,a,o,s){const c=t.find(v=>v.v===n),u=c?c.label:t[0]?t[0].label:"";X[e]=a;const b=o?"opacity-50 pointer-events-none":"",$=s?"block w-full":"inline-block",N=s?"w-full":"w-48";return`<div class="relative ${$}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${N} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${b}">
      <span class="truncate flex-1" data-csel-label>${l(u)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${N} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(v=>`<button type="button" data-v="${l(v.v)}" data-search="${l(v.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${l(v.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${v.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${l(v.label)}</button>`).join("")}
      </div>
    </div></div>`}function rt(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",ke(e),n.focus())}}function st(e,t){var o,s;(o=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||o.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(s=X[e])==null||s.call(X,t)}function ke(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const o=a;o.style.display=(o.dataset.search||"").includes(n)?"":"none"})}function ot(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function q(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function lt(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function j(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const E=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function se(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const A="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function W(e,t,n,a){return`<button class="${A}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function oe(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${l(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function le(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const h=e=>(e||0).toLocaleString(),F=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),ie=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function it(){const e=r.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No supported agent-usage data found.</div>`;const t=e.total||{},n=(u,b,$)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${u}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${b}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([u,b])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${u}</span>
      <span class="flex-1 text-neutral-500">${h(ie(b))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${F(b.cost)}</span></div>`).join(""),o=Object.entries(e.per_instance||{}).sort((u,b)=>(b[1].cost||0)-(u[1].cost||0)).map(([u,b])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${u==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${l(u)}</span>
      <span class="flex-1 text-neutral-500">${h(ie(b))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${F(b.cost)}</span></div>`).join(""),s=e.sessions||[],c=s.map(u=>`
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${u.id}</code>
      <span class="w-16 capitalize text-neutral-500">${u.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(u.instances||[]).join(", ")||"—"}</span>
      <span class="text-neutral-500">${h(u.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${F(u.cost)}</span></div>`).join("");return`<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Agent usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Currently collected from Claude session telemetry; this collector does not yet include Codex. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${n("Total tokens",h(e.tokens))}
      ${n("Estimated cost",F(e.cost))}
      ${n("Sessions",h(s.length)+(s.length>=25?"+":""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${h(t.in)} · out ${h(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${h(t.cw)} · cache read ${h(t.cr)}</div>
    </div>
    <div class="mt-6">${j("By model")}${a||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${j("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${o||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${j("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${c}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const dt=ie;function ct(){const e=r.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
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
  </div>`}function ut(e){const t=e.domain||"",n=e.url.startsWith("https://"),a=t.endsWith(".tst");if(n)return`<span class="text-neutral-700 dark:text-neutral-200">${l(t)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;const o=a?`<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${oe("./sb secure "+e.name)}
       </div>`:"";return`<span class="text-neutral-700 dark:text-neutral-200">${l(t)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${o}`}function pt(e){var a;const t=r.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show agent token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Agent usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${h(dt(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${F(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No agent usage attributed to this instance yet.</div>'}function bt(e){if(!e)return ct();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${l(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${q()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${l(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=r.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,o=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?q():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?q("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${l(e.name)}</h1>
        ${lt(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${l(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${e.login_url?se(e.login_url,"Login",e.running):se(e.url+"/wp-admin","Admin",e.running)}
      ${se(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${o}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?q():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${j("Overview")}
    ${E("Web server",`<div class="flex items-center gap-2">
        ${re("serverSel",(r.data.servers&&r.data.servers.length?r.data.servers:["apache","nginx","litespeed"]).map(s=>({v:s,label:s})),e.server,s=>window.sb.doServer(e.name,s),!!t||!e.running)}
        ${t==="server"?q():""}
        ${e.running?"":'<span class="text-[11px] text-neutral-400">start the site to switch</span>'}</div>`)}
    ${e.domain?E("Domain",ut(e)):""}
    ${E("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${E("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${E("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':l(e.project))}
    ${E("Focus plugin",`<div class="flex items-center gap-2">
        ${re("focusSel",[{v:"",label:"— none —"}].concat(r.data.plugins.map(s=>({v:s,label:s}))),e.focus&&e.focus!=="—"?e.focus:"",s=>window.sb.doFocus(e.name,s),!!t)}
        ${t==="focus"||t==="unfocus"?q():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${j("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${W(e.name,"logs","Logs")}
      ${W(e.name,"status","Status")}
      ${W(e.name,"doctor","Doctor")}
      ${W(e.name,"update","Update plugins")}
      <button class="${A}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${A}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${A}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${W(e.name,"xdebug","Xdebug",{state:"status"})}
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
    ${j("Plugins","Search to install from WordPress.org, or symlink a local source")}
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
    ${j("Use with Codex or Claude","Connect a coding-agent session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Codex or Claude in chat (simplest):</div>
      ${oe("focus "+(e.focus&&e.focus!=="—"?e.focus:"<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${oe("mcp__"+e.mcp_server+"__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${e.url}</code>.</div>
      ${pt(e.name)}
    </div>
  </div>`}const d={page:"min-h-full bg-neutral-50 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-50",shell:"max-w-7xl mx-auto px-4 py-5 sm:px-6 sm:py-7 lg:px-8",panel:"rounded-xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-sm",muted:"text-neutral-600 dark:text-neutral-300",quiet:"text-neutral-500 dark:text-neutral-400",label:"text-[11px] font-semibold uppercase tracking-[0.12em] text-neutral-500 dark:text-neutral-400",button:"inline-flex min-h-9 items-center justify-center rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 px-3 py-2 text-[12px] font-medium text-neutral-800 dark:text-neutral-100 hover:bg-neutral-50 dark:hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950",primary:"inline-flex min-h-9 items-center justify-center rounded-lg bg-blue-700 px-3 py-2 text-[12px] font-semibold text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950"};function $e(e,t,n,a,o,s,c,u){return`<a href="${t}" data-link class="${d.panel} group block p-5 hover:border-blue-300 dark:hover:border-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500">
    <div class="flex items-start gap-3">
      <span class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${a?"bg-emerald-500":"bg-amber-400"}"></span>
      <div class="min-w-0 flex-1"><div class="${d.label}">${l(n)}</div>
        <h2 class="mt-1 truncate text-[16px] font-semibold text-neutral-900 dark:text-white">${l(e)}</h2></div>
      <span aria-hidden="true" class="text-neutral-400 group-hover:text-blue-600">→</span>
    </div>
    <div class="mt-5 grid grid-cols-3 gap-3 border-t border-neutral-200 pt-4 dark:border-neutral-800">
      <div><div class="${d.label}">Instances</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${l(o)}</div></div>
      <div><div class="${d.label}">Running</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${l(s)}</div></div>
      <div><div class="${d.label}">RAM</div><div class="mt-1 text-[14px] font-semibold tabular-nums">${l(c)}</div></div>
    </div>
    <p class="mt-4 text-[12px] ${d.quiet}">${l(u)}</p>
  </a>`}function G(e="all"){const t=r.data.remotes.map(a=>`<a href="${P(a.name)}" data-link class="shrink-0 rounded-lg border px-3 py-2 text-[12px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${e===a.name?"border-blue-600 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-100":"border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"}"><span class="mr-2 inline-block h-2 w-2 rounded-full ${a.control_ready?"bg-emerald-500":"bg-amber-400"}"></span>${l(a.name)}</a>`).join(""),n=r.data.instances[0]?M(r.data.instances[0].name):"/create";return`<nav aria-label="Host selector" class="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
    <a href="/" data-link class="shrink-0 rounded-lg border px-3 py-2 text-[12px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${e==="all"?"border-blue-600 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-100":"border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"}"><span class="mr-2 inline-block h-2 w-2 rounded-full bg-blue-500"></span>All hosts</a>
    <a href="${n}" data-link class="shrink-0 rounded-lg border px-3 py-2 text-[12px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${e==="local"?"border-blue-600 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-100":"border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-200"}"><span class="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-500"></span>Local host</a>${t}
  </nav>`}function ye(){const e=r.data.instances.length,t=r.data.instances.filter(c=>c.running).length,n=r.data.remotes.map(c=>{var z;const u=r.remote[c.name],b=r.remoteBusy[c.name],$=u!=null&&u.instances?String(u.instances.total):b?"…":"—",N=u!=null&&u.instances?String(u.instances.running):b?"…":"—",v=((z=u==null?void 0:u.host)==null?void 0:z.memory_used_percent)==null?"—":`${u.host.memory_used_percent}%`,te=b?"Refreshing host inventory…":u?`${u.evidence_status} ${u.scan_mode||"fast"} evidence`:c.control_ready?"Waiting for first inventory":"Control service unavailable";return $e(c.name,P(c.name),"Remote host",c.control_ready,$,N,v,te)}).join(""),a=r.data.remotes.reduce((c,u)=>{var b,$;return c+((($=(b=r.remote[u.name])==null?void 0:b.instances)==null?void 0:$.total)||0)},0),o=1+r.data.remotes.length,s=r.sync.refreshing?"Refreshing inventories…":r.sync.lastCompleted?`Updated ${new Date(r.sync.lastCompleted).toLocaleTimeString()}`:"Loading inventories…";return`<div class="${d.page}"><div class="${d.shell} space-y-6">
    <header class="space-y-4"><div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div class="${d.label}">Host control</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">All Sandbox hosts</h1>
      <p class="mt-1 text-[13px] ${d.muted}">One view of local and remote WordPress capacity.</p></div>
      <div class="flex flex-wrap items-center justify-end gap-3"><div role="status" class="text-[12px] ${r.sync.error?"text-red-700 dark:text-red-300":d.quiet}">${l(r.sync.error||s)}</div><button class="${d.button}" onclick="sb.refreshHosts()">Refresh remote hosts</button></div>
    </div>${G("all")}</header>
    <section class="${d.panel} grid grid-cols-2 gap-px overflow-hidden bg-neutral-200 p-0 dark:bg-neutral-800 sm:grid-cols-4">
      ${[["Hosts",o],["Known instances",e+a],["Local running",t],["Remote hosts",r.data.remotes.length]].map(([c,u])=>`<div class="bg-white p-4 dark:bg-neutral-900"><div class="${d.label}">${c}</div><div class="mt-1 text-[24px] font-semibold tabular-nums">${u}</div></div>`).join("")}
    </section>
    <section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Available hosts</h2><span class="text-[11px] ${d.quiet}">Select a host to inspect its instances</span></div>
      <div class="grid gap-4 lg:grid-cols-2">${$e("Local host",r.data.instances[0]?M(r.data.instances[0].name):"/create","This machine",!0,String(e),String(t),"—","Open local instances and lifecycle controls")}${n}</div>
    </section>
  </div></div>`}const we={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function x(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(we[t]||we.info),n.textContent=e,i("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let K=null;function xt(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${l(e.label||"")}</div>`;if(e.type==="select"){const n=at(),a=e.options||[],o=a.map(c=>({v:c,label:c})),s=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${l(s)}">`+re(n,o,s,c=>{document.getElementById(`${n}_val`).value=c},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${e.key}" data-type="checkbox"
        ${e.value?"checked":""} class="accent-accent w-3.5 h-3.5">
      ${l(e.label||e.key||"")}</label>`;if(e.type==="checklist"){const a=(e.options||[]).map(o=>`
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${e.key}" value="${l(o.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${l(o.label)}</span>
          ${o.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${l(o.desc)}</span>`:""}
        </span></label>`).join("");return`<div data-checklist-group="${e.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${a||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`}const t=e.oninput?` oninput="${l(e.oninput)}"`:"";return`<input data-k="${e.key}" data-field="${l(e.key||"")}"${t}
    placeholder="${l(e.placeholder||"")}" value="${l(e.value||"")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function U(e={}){return new Promise(t=>{K=t,i("mTitle").textContent=e.title||"",i("mDesc").textContent=e.desc||"",i("mFields").innerHTML=(e.fields||[]).map(xt).join("");const n=i("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),i("modal").classList.remove("hidden"),setTimeout(()=>{(i("mFields").querySelector("input,select")||n).focus()},30)})}function Y(e){if(i("modal").classList.add("hidden"),K){const t=K;K=null,t(e)}}function mt(){const e={};return i("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),i("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function ft(){i("mCancel").onclick=()=>Y(null),i("mOk").onclick=()=>Y(mt()),i("modal").addEventListener("keydown",e=>{e.key==="Enter"&&i("mOk").click(),e.key==="Escape"&&Y(null)}),i("modal").addEventListener("click",e=>{e.target===i("modal")&&Y(null)})}let Se=async()=>{};function gt(e){Se=e}function vt(e){i("console").classList.remove("w-0"),i("console").classList.add("w-[26rem]"),i("conTitle").textContent=e,i("conBody").textContent="",i("conInputRow").classList.add("hidden"),i("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function de(){i("console").classList.add("w-0"),i("console").classList.remove("w-[26rem]"),i("conInputRow").classList.add("hidden")}function R(e){const t=i("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function ht(e,t){i("conTitle").textContent=e,i("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function ce(e,t,n){r.paused=!0,vt(n||"Working…");let a=0,o=!!n;const s=setInterval(async()=>{var u;const c=await he(e,a);!o&&c.status&&(i("conTitle").textContent=c.status.replace(/ [✓✗]$/,""),o=!0),c.chunk?(R(c.chunk),a=(u=c.offset)!=null?u:a):typeof c.offset=="number"&&(a=c.offset),c.done&&(clearInterval(s),r.paused=!1,t&&delete r.busy[t],ht(c.status||"done",c.ok),x(c.status||"done",c.ok?"ok":"err"),await Se())},800)}let ue=null;const D=[];let w=-1,J=!1;function Z(e){ue=e,i("console").classList.remove("w-0"),i("console").classList.add("w-[26rem]"),i("conTitle").textContent="Terminal — "+e,i("conDot").className="w-2 h-2 rounded-full bg-emerald-500",i("conBody").textContent.trim()||R("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),i("conInputRow").classList.remove("hidden"),setTimeout(()=>i("conInput").focus(),60)}async function kt(){if(J)return;const e=i("conInput"),t=e.value.trim();if(!t||!ue)return;D.push(t),w=D.length,e.value="",R("› "+t+`
`),J=!0,i("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await Q({instance:ue,action:"term",cmd:t})}catch(s){R("error: "+s+`
`),J=!1;return}if(!n.job_id){R((n.output||"failed")+`
`),J=!1;return}let a=0;const o=setInterval(async()=>{var c;const s=await he(n.job_id,a);s.chunk?(R(s.chunk),a=(c=s.offset)!=null?c:a):typeof s.offset=="number"&&(a=s.offset),s.done&&(clearInterval(o),J=!1,R(`
`),i("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function $t(e){const t=i("conInput");e.key==="Enter"?kt():e.key==="ArrowUp"?(w>0&&(w--,t.value=D[w]||""),e.preventDefault()):e.key==="ArrowDown"&&(w<D.length-1?(w++,t.value=D[w]||""):(w=D.length,t.value=""),e.preventDefault())}function yt(){i("conClose").onclick=de,i("conInput").addEventListener("keydown",e=>$t(e))}let Ce=async()=>{},L=()=>{};function wt(e){Ce=e.refresh,L=e.render}const _e={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function T(e,t,n={}){r.busy[e]=t,L();let a;try{a=await Q(Object.assign({instance:e,action:t},n))}catch(o){delete r.busy[e],x("request failed: "+o,"err"),L();return}if(a.job_id){const o=_e[t]?_e[t](e):ne(t)+" "+e;x(t.replace("-"," ")+" started…","info"),ce(a.job_id,e,o)}else delete r.busy[e],a.ok?x(ne(t)+" "+e+" ✓","ok"):x((a.output||"failed").split(`
`)[0],"err"),await Ce()}async function O(e,t,n={}){let a;try{a=await Q(Object.assign({instance:e,action:t},n))}catch(o){x("request failed: "+o,"err");return}if(a.job_id){const o={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};ce(a.job_id,null,(o[t]||ne(t))+" — "+e)}else x((a.output||"failed").split(`
`)[0],"err")}async function St(e,t){t===""?T(e,"unfocus"):t&&T(e,"focus",{slug:t})}async function Ct(e,t){const n=r.data.instances.find(o=>o.name===e);if(!t||n&&n.server===t)return;r.busy[e]="server",L();let a;try{a=await Q({instance:e,action:"server",server:t})}catch(o){delete r.busy[e],L(),x("request failed: "+o,"err");return}a.job_id?(x("switching "+e+" → "+t+"…","info"),ce(a.job_id,e,"Switching "+e+" → "+t)):(delete r.busy[e],L(),x((a.output||"failed").split(`
`)[0],"err"))}async function _t(e){const t=await U({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?T(e,"delete",{confirm:e}):t&&x("name did not match — not deleted","err")}function jt(e){const n=i("wpArgs").value.trim();if(!n){x("enter a wp-cli command","err");return}O(e,"wp",{args:n})}async function Rt(e){const t=await U({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&O(e,"snapshot",{name:t.name})}async function Lt(e){let t=[];try{t=(await Ke(e)).snapshots||[]}catch(a){}if(!t.length){x("no snapshots for "+e,"err");return}const n=await U({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&O(e,"restore",{name:n.name})}async function Tt(e){const t=r.data.seeds||[];if(!t.length){x("no WXR files in runtime/seeds/","err");return}const n=await U({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&O(e,"seed",{file:n.file})}function It(e){const t=(i("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){x("type a plugin slug to install","err");return}O(e,"install",{slug:t})}function Bt(e){const t=(i("plugQ").value||"").toLowerCase().trim(),n=i("plugResults");if(!t){n.innerHTML="";return}const a=r.data.instances.find(s=>s.name===e),o=(r.data.plugins||[]).filter(s=>s.toLowerCase().includes(t)).slice(0,8);n.innerHTML=o.map(s=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${l(s)}</span>
      ${a&&a.focus===s?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${l(s)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${l(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function Mt(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function Pt(){try{r.usage=await ve()}catch(e){r.usage={available:!1}}L()}function je(){y("/create")}function qt(){const e=i("createProject").value.trim(),t=i("createLabel").value.trim().toLowerCase();if(!e.startsWith("/")){x("enter an absolute local project path","err");return}if(t&&!/^[a-z0-9][a-z0-9_-]{0,30}$/.test(t)){x("label must use a-z, 0-9, _ or -","err");return}T(t||"new instance","create",{project_dir:e,label:t})}function Et(){return`<div class="max-w-2xl mx-auto px-6 py-8">
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
      <input id="createProject" autocomplete="off" placeholder="/Users/you/Sites/plugin" class="mt-2 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-[13px] text-neutral-900 outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-200 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"></div>
      <div><label for="createLabel" class="block text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">Instance label <span class="font-normal text-neutral-400">optional</span></label>
      <input id="createLabel" autocomplete="off" maxlength="31" placeholder="review" class="mt-2 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-[13px] text-neutral-900 outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-200 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"></div>
      <div class="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Creates on the local host only. Remote creation is unavailable until remote lifecycle operations have a service-backed API.</div>
      <button onclick="sb.submitCreate()" class="rounded-lg bg-blue-700 px-4 py-2 text-[13px] font-semibold text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500">Create local instance</button>
    </div>

    <p class="mt-4 text-[12.5px] text-neutral-500 dark:text-neutral-400">Creation runs as a background job. Progress opens in the activity panel.</p>

    <div class="mt-7">
      <a href="/" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Back to instances</a>
    </div>
  </div>`}const k=e=>{if(e==null)return"unknown";const t=["B","KiB","MiB","GiB","TiB"];let n=Math.max(0,e),a=0;for(;n>=1024&&a<t.length-1;)n/=1024,a++;return`${n.toFixed(a>1?1:0)} ${t[a]}`},m=e=>e==null||!Number.isFinite(e)?"unknown":String(e),f=(e,t,n="")=>`<div class="${d.panel} p-4">
  <div class="${d.label}">${l(e)}</div>
  <div class="mt-1 text-[20px] font-semibold tabular-nums text-neutral-900 dark:text-white">${l(t)}</div>
  ${n?`<div class="mt-1 text-[11px] ${d.quiet}">${l(n)}</div>`:""}</div>`;function pe(e,t,n,a){const o=t.length?t.map(s=>`<tr class="border-b border-brd/70 dark:border-brd-dark/70 last:border-0">${n.map(([,,c])=>`<td class="py-2.5 pr-4 text-[12px] text-neutral-600 dark:text-neutral-300">${l(c(s))}</td>`).join("")}</tr>`).join(""):`<tr><td colspan="${n.length}" class="py-4 text-[12px] text-neutral-400">${l(a)}</td></tr>`;return`<section class="${d.panel} p-4"><div class="flex items-center justify-between gap-3 mb-3"><h2 class="text-[13px] font-semibold">${l(e)}</h2><span class="text-[11px] ${d.quiet}">${t.length} observed</span></div><div class="overflow-x-auto"><table class="w-full text-left"><thead><tr>${n.map(([s,c])=>`<th scope="col" class="pb-2 pr-4 ${d.label}">${l(c||s)}</th>`).join("")}</tr></thead><tbody>${o}</tbody></table></div></section>`}function Re(e,t,n){var qe,Ee,Ae,De,Oe,Ne,He,We,Fe,Ue,Je,Ve,ze;if(!t)return`<div class="${d.page}"><div class="${d.shell}">${G(e)}<div class="py-16 text-center text-neutral-500 dark:text-neutral-400 text-[13px]">Loading ${l(e)}…</div></div></div>`;const a=t.instances||{total:0,running:0,stopped:0,rows:[]},o=t.host||{},s=t.storage,c=t.evidence_status==="complete"?"text-emerald-600":t.evidence_status==="partial"?"text-amber-600":"text-rose-600",u=o.disk_total_bytes?Math.min(100,Math.max(0,(o.disk_used_bytes||0)*100/o.disk_total_bytes)):0,b=t.per_instance_usage||[],$=a.rows.map(p=>({...b.find(nn=>nn.name===p.name),name:p.name,running:p.running})),N=((qe=t.process_view)==null?void 0:qe.apps)||[],v=((Ee=t.process_view)==null?void 0:Ee.processes)||[],te=((Ae=t.containers)==null?void 0:Ae.rows)||[],z=JSON.stringify(e),ge=!!r.remoteBusy[e],g=n?b.find(p=>p.name===n):void 0,C=n?a.rows.find(p=>p.name===n):void 0,tn=n?`<section class="${d.panel} p-5"><div class="flex items-start justify-between gap-4"><div><div class="${d.label}">Remote instance · read only</div><h2 class="mt-1 text-[20px] font-semibold">${l(n)}</h2><p class="mt-1 text-[12px] ${d.quiet}">${l((C==null?void 0:C.project)||"Project unavailable")} · ${l((C==null?void 0:C.server)||"server unknown")}</p></div><a href="${P(e)}" data-link class="${d.button}">Back to host</a></div><div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">${f("State",C?C.running?"running":"stopped":"unknown")}${f("RAM",k(g==null?void 0:g.memory_used_bytes))}${f("CPU",`${m(g==null?void 0:g.cpu_percent)}%`)}${f("Containers",m(g==null?void 0:g.container_count),String((g==null?void 0:g.attribution_status)||"unknown"))}</div><p class="mt-4 text-[12px] ${d.quiet}">Remote lifecycle controls are not exposed by this inventory endpoint yet. Resource values are container-attributed evidence.</p></section>`:"";return`<div class="${d.page}"><div class="${d.shell} space-y-6"><header class="space-y-4">${G(e)}<div class="flex flex-col gap-4 md:flex-row md:items-end"><div class="flex-1"><div class="${d.label}">Remote host</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">${l(e)}</h1><p class="mt-1 text-[12px] ${d.muted}">Authenticated inventory · ${l(t.scan_mode||"fast")} snapshot</p></div><div class="flex flex-wrap items-center gap-2"><span class="mr-1 text-[12px] font-semibold ${c}">${ge?"refreshing":l(t.evidence_status)}</span><button ${ge?"disabled":""} class="${d.button}" onclick="sb.refreshRemote(${z})">Quick refresh</button><button ${ge?"disabled":""} class="${d.primary}" onclick="sb.refreshRemote(${z},true)">Rebuild attribution</button></div></div></header>${(De=t.partial_reasons)!=null&&De.length?`<div role="status" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Partial evidence: ${l(t.partial_reasons.join(", "))}. Unknown values are intentionally not treated as zero.</div>`:""}${tn}<section class="${d.panel} p-5"><div class="flex items-end justify-between gap-3"><div><div class="${d.label}">Host disk capacity</div><div class="mt-1 text-[18px] font-semibold">${k(o.disk_used_bytes)} used <span class="font-normal ${d.quiet}">of ${k(o.disk_total_bytes)}</span></div></div><div class="text-right text-[12px] ${d.quiet}">${k(o.disk_free_bytes)} free</div></div><div class="mt-4 h-3 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"><div class="h-full bg-blue-700" style="width:${u}%"></div></div><div class="mt-2 text-[11px] ${d.quiet}">Container values overlap host capacity and are not additive.</div></section><div class="grid grid-cols-2 gap-3 lg:grid-cols-4">${f("Hosted instances",String(a.total),`${a.running} running · ${a.stopped} stopped`)}${f("RAM used",o.memory_used_percent==null?"unknown":`${o.memory_used_percent}%`,`${m(o.memory_used_mb)} of ${m(o.memory_total_mb)} MiB`)}${f("Load 1m",m(o.load_1m),"point-in-time host sample")}${f("Active jobs",m((Oe=t.jobs)==null?void 0:Oe.active),`${m((Ne=t.jobs)==null?void 0:Ne.queued)} queued`)}${f("Containers",m(te.length),((He=t.containers)==null?void 0:He.status)||"unavailable")}${f("Storage attribution",(s==null?void 0:s.attribution_status)||"unknown",(s==null?void 0:s.status)||"unavailable")}${f("Unattributed",String((Fe=(We=t.unattributed_containers)==null?void 0:We.length)!=null?Fe:"unknown"),"containers without a confident match")}${f("Disk pressure",`${u.toFixed(1)}%`,"capacity-backed")}</div><section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Hosted instances</h2><span class="text-[11px] ${d.quiet}">Select an instance for its read-only resource view</span></div><div class="overflow-x-auto ${d.panel}"><table class="w-full text-left"><thead><tr>${["Instance","State","RAM","CPU","Evidence"].map(p=>`<th class="p-3 ${d.label}">${p}</th>`).join("")}</tr></thead><tbody>${$.length?$.map(p=>`<tr class="border-t border-neutral-200 dark:border-neutral-800"><td class="p-3 text-[12px] font-medium"><a data-link class="text-blue-700 hover:underline dark:text-blue-300" href="${et(e,String(p.name))}">${l(String(p.name))}</a></td><td class="p-3 text-[12px]">${p.running?"running":"stopped"}</td><td class="p-3 text-[12px] tabular-nums">${l(k(p.memory_used_bytes))}</td><td class="p-3 text-[12px] tabular-nums">${l(m(p.cpu_percent))}%</td><td class="p-3 text-[12px] ${d.quiet}">${l(String(p.attribution_status||"unattributed"))}</td></tr>`).join(""):`<tr><td colspan="5" class="p-4 text-[12px] ${d.quiet}">No hosted instances reported.</td></tr>`}</tbody></table></div></section><div class="grid gap-4 xl:grid-cols-3">${pe("Apps",N,[["name","App",p=>String(p.name||"unknown")],["process_count","Processes",p=>m(p.process_count)],["rss_bytes","RSS",p=>k(p.rss_bytes)],["cpu_percent","CPU avg",p=>`${m(p.cpu_percent)}%`]],((Ue=t.process_view)==null?void 0:Ue.status)||"Process view unavailable")}${pe("Processes",v.slice(0,50),[["pid","PID",p=>m(p.pid)],["name","Name",p=>String(p.name||"unknown")],["rss_bytes","RSS",p=>k(p.rss_bytes)],["cpu_percent","CPU avg",p=>`${m(p.cpu_percent)}%`]],"No process rows")}${pe("Containers",te,[["name","Container",p=>String(p.name||"unknown")],["memory_used_bytes","Memory",p=>k(p.memory_used_bytes)],["memory_percent","Memory %",p=>`${m(p.memory_percent)}%`],["cpu_percent","CPU",p=>`${m(p.cpu_percent)}%`]],((Je=t.containers)==null?void 0:Je.status)||"Container view unavailable")}</div><section class="${d.panel} p-4"><h2 class="text-[13px] font-semibold">Storage evidence</h2><p class="mt-1 text-[12px] ${d.muted}">${l((s==null?void 0:s.status)||"unavailable")} · attribution ${l((s==null?void 0:s.attribution_status)||"unknown")} · used ${l(k((Ve=s==null?void 0:s.capacity)==null?void 0:Ve.used_bytes))} · available ${l(k((ze=s==null?void 0:s.capacity)==null?void 0:ze.available_bytes))}</p><p class="mt-2 text-[11px] ${d.quiet}">Deep scans are bounded and may remain partial. Unknown bytes are not cleanup authority.</p></section></div></div>`}function At(e,t){const n=t.map(a=>{const o=a.disabled?"opacity-40 pointer-events-none":"",s=a.danger?"text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40":"text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800";return`<button type="button" ${a.disabled?"disabled":""}
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuClose();${a.js}"
      class="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left ${s} ${o}">
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
      ${n}</div></span>`}function Dt(e){var n;let t=!1;document.querySelectorAll("[data-rowmenu-pop]").forEach(a=>{const o=a.closest("[data-rowmenu]"),s=!a.classList.contains("hidden");o&&o.dataset.rowmenu===e&&(t=s),a.classList.add("hidden")}),!t&&((n=document.querySelector(`[data-rowmenu="${e}"] [data-rowmenu-pop]`))==null||n.classList.remove("hidden"))}function Le(){document.querySelectorAll("[data-rowmenu-pop]").forEach(e=>e.classList.add("hidden"))}function Ot(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-rowmenu]")||Le()})}function be(){const e=_();return e.page==="instance"?e.name:null}const S={play:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',stop:'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',restart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',admin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',term:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7l4 4-4 4"/><path d="M12 15h6"/></svg>',snapshot:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>',restore:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8"/><path d="M3 3v5h5"/></svg>',trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>'};function Nt(e){const t=JSON.stringify(e.name),n=[e.running?{label:"Stop",icon:S.stop,js:`sb.act(${t},'stop')`}:{label:"Start",icon:S.play,js:`sb.act(${t},'start')`},{label:"Restart",icon:S.restart,js:`sb.act(${t},'restart')`,disabled:!e.running},{label:"Open admin",icon:S.admin,js:`window.open(${JSON.stringify(e.url+"/wp-admin")},'_blank')`,disabled:!e.running},{label:"Console",icon:S.term,js:`sb.navigate(${JSON.stringify(M(e.name,!0))})`,disabled:!e.running},{label:"Snapshot",icon:S.snapshot,js:`sb.doSnapshot(${t})`},{label:"Restore…",icon:S.restore,js:`sb.doRestore(${t})`}];return n.push({label:"Delete",icon:S.trash,js:`sb.doDelete(${t})`,danger:!0}),n}function Ht(e){const t=e.name===be(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",o=r.busy[e.name]?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:`<span class="ml-auto transition-opacity ${t?"opacity-100":"opacity-0 group-hover:opacity-100"}">
        ${At("rm-"+e.name,Nt(e))}</span>`;return`<a href="${M(e.name)}" data-link class="group w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${l(e.name)}</span>
     ${o}</a>`}function Te(){if(document.querySelector("[data-rowmenu-pop]:not(.hidden)"))return;const e=r.data.instances;i("list").innerHTML=e.map(Ht).join("");const t=_();i("remoteList").innerHTML=r.data.remotes.map(a=>`<a href="${P(a.name)}" data-link class="w-full px-3 py-2 rounded flex items-center gap-2 text-[13px] ${t.page==="remote"&&t.name===a.name?"bg-white dark:bg-neutral-800 shadow-sm font-medium":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}"><span class="w-2 h-2 rounded-full ${a.control_ready?"bg-blue-500":"bg-neutral-400"}"></span><span class="truncate">${l(a.name)}</span></a>`).join("");const n=e.filter(a=>a.running).length;i("runcount").textContent=n+"/"+e.length,i("footstat").textContent=e.length?n+" of "+e.length+" running":"no instances yet"}let Ie="";function Wt(e){if(e.page==="instance"){const t=r.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,t.url,t.domain||"",r.busy[t.name]||"",r.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(r.usage?"loaded":"pending"):e.page==="remote"||e.page==="remote-instance"?e.page+":"+e.name+":"+(e.page==="remote-instance"?e.instance:"")+":"+!!r.remoteBusy[e.name]+":"+JSON.stringify(r.remote[e.name]||null):e.page==="home"?"home:"+r.sync.refreshing+":"+r.sync.lastCompleted+":"+r.sync.error+":"+JSON.stringify(r.data.remotes)+":"+JSON.stringify(r.remote):e.page}function Ft(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!i("modal").classList.contains("hidden"))}function Ut(e){const t=n=>`<div class="min-h-full bg-neutral-50 dark:bg-neutral-950"><div class="max-w-7xl mx-auto px-4 pt-5 sm:px-6 lg:px-8">${G("local")}</div>${n}</div>`;switch(e.page){case"create":return t(Et());case"usage":return it();case"remote":return Re(e.name,r.remote[e.name]);case"remote-instance":return Re(e.name,r.remote[e.name],e.instance);case"instance":{const n=r.data.instances.find(a=>a.name===e.name)||null;return t(bt(n))}case"home":return ye();default:return ye()}}function I(e){const t=_(),n=Wt(t);!e&&n===Ie||!e&&Ft()||(Ie=n,i("detail").innerHTML=Ut(t))}function xe(){Te(),I(!0)}let V=null,me=!1;const ee=new Map;function fe(e,t="fast"){const n=ee.get(e);if(n&&t==="fast")return n;const o=(async()=>{n&&await n,r.remoteBusy[e]=!0,I(!1);try{r.remote[e]=await Ge(e,t)}finally{delete r.remoteBusy[e]}})().finally(()=>{ee.get(e)===o&&ee.delete(e)});return ee.set(e,o),o}async function Jt(){if(!r.paused){r.sync.refreshing=!0,r.sync.error=null,I(!1);try{r.data=await Xe();const e=_();(e.page==="remote"||e.page==="remote-instance")&&await fe(e.name),r.sync.lastCompleted=Date.now()}catch(e){r.sync.error=e instanceof Error?e.message:"Refresh failed"}finally{r.sync.refreshing=!1,Te(),I(!1)}}}function B(e=!1){return V?(e&&(me=!0),V):(V=Jt().finally(()=>{V=null,me&&(me=!1,B())}),V)}async function Vt(){y("/usage"),i("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading agent usage…</div>';try{r.usage=await ve()}catch(e){r.usage={available:!1}}I(!0)}function zt(){y("/")}function Qt(e){y(M(e))}function Xt(e){y(P(e))}async function Gt(e,t=!1){try{await fe(e,t?"deep":"fast"),I(!0)}catch(n){x("remote inventory refresh failed","err")}}async function Kt(){const e=r.data.remotes.filter(a=>a.control_ready).map(a=>a.name),n=(await Promise.allSettled(e.map(a=>fe(a)))).filter(a=>a.status==="rejected").length;n&&x(`${n} host ${n===1?"refresh":"refreshes"} failed`,"err"),I(!0)}function Be(){U({title:"How AI agents work here",okText:"Got it",desc:`The sandbox gives Codex, Claude, and other connected agents a live WordPress to act in, so they can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — the agent picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. One MCP server (mcp__sandbox__*) serves every project — each tool takes the project directory and resolves the right environment from the registry. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const Yt={navigate:y,goHome:zt,selectInstance:Qt,selectRemote:Xt,refreshRemote:Gt,refreshHosts:Kt,showUsage:Vt,showHelp:Be,openTerminal:Z,submitCreate:qt,doCreate:je,doDelete:_t,doFocus:St,doServer:Ct,doSnapshot:Rt,doRestore:Lt,doSeed:Tt,doWp:jt,doInstall:It,plugFilter:()=>Bt(be()),loadUsageThenRender:Pt,act:T,op:O,cselToggle:rt,cselPick:st,cselFilter:ke,rowMenuToggle:Dt,rowMenuClose:Le,consoleClose:de,copyText:Mt};window.sb=Yt;function Zt(){wt({refresh:()=>B(!0),render:xe}),gt(()=>B(!0)),ft(),yt(),ot(),Ot(),tt(),i("newBtn").onclick=je,i("startAll").onclick=()=>T("*","start-all"),i("stopAll").onclick=()=>T("*","stop-all"),i("helpBtn").onclick=Be,i("termBtn").onclick=()=>{const t=be()||r.data.instances[0]&&r.data.instances[0].name;if(!t){x("create an instance first","err");return}y(M(t,!0)),Z(t)},Ze(t=>{xe(),(t.page==="remote"||t.page==="remote-instance"||t.page==="home")&&B(),t.page==="instance"&&t.console?Z(t.name):de()}),xe();const e=_();e.page==="instance"&&e.console&&Z(e.name),en()}const Me=3e4;let Pe=0;function en(){const e=()=>{window.clearTimeout(Pe),Pe=window.setTimeout(async()=>{document.visibilityState==="visible"&&await B(),e()},Me)};B().finally(e),document.addEventListener("visibilitychange",()=>{document.visibilityState==="visible"&&(!r.sync.lastCompleted||Date.now()-r.sync.lastCompleted>Me)&&B()})}Zt()})();
