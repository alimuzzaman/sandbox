(function(){"use strict";const i=e=>document.getElementById(e),st={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"},l=e=>String(e).replace(/[&<>"]/g,t=>st[t]),re=e=>e.charAt(0).toUpperCase()+e.slice(1),o={data:{instances:[],plugins:[],seeds:[],servers:["apache","nginx","litespeed"],domains_ready:!1,remotes:[]},busy:{},usage:null,remote:{},remoteBusy:{},sync:{refreshing:!1,lastCompleted:null,error:null},paused:!1};async function U(e){const t=await fetch(e),n=await t.json();if(!t.ok)throw new Error(n.error||`Request failed (${t.status})`);return n}const rt=()=>U("/api/instances"),$e=()=>U("/api/usage"),ot=(e,t="fast")=>U(`/api/remote/${encodeURIComponent(e)}${t==="deep"?"?deep=1":""}`),ye=(e,t)=>U(`/api/job/${e}?offset=${t}`),lt=e=>U(`/api/snapshots/${e}`);async function G(e){const t=await fetch("/api/action",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(e)}),n=await t.json();if(!t.ok&&!n.output)throw new Error(`Request failed (${t.status})`);return n}function it(e){const t=e.replace(/\/+$/,"").split("/").filter(Boolean);return t.length===0?{page:"home"}:t[0]==="create"&&t.length===1?{page:"create"}:t[0]==="host"&&t[1]==="local"&&t.length===2?{page:"local-host"}:t[0]==="usage"&&t.length===1?{page:"usage"}:t[0]==="remote"&&t[1]&&t.length===2?{page:"remote",name:decodeURIComponent(t[1])}:t[0]==="remote"&&t[1]&&t[2]==="instance"&&t[3]&&t.length===4?{page:"remote-instance",name:decodeURIComponent(t[1]),instance:decodeURIComponent(t[3])}:t[0]==="instance"&&t[1]?{page:"instance",name:decodeURIComponent(t[1]),console:t[2]==="console"}:{page:"notfound"}}const $=()=>it(location.pathname);function K(e=$()){return e.page==="remote"||e.page==="remote-instance"?{kind:"remote",name:e.name}:e.page==="local-host"||e.page==="instance"||e.page==="create"||e.page==="usage"?{kind:"local"}:{kind:"all"}}let oe=()=>{};function dt(e){oe=e}function _(e,t=!1){e!==location.pathname&&(t?history.replaceState({},"",e):history.pushState({},"",e)),oe($())}function W(e,t=!1){const n=`/instance/${encodeURIComponent(e)}`;return t?`${n}/console`:n}const Y=()=>"/host/local",F=e=>`/remote/${encodeURIComponent(e)}`,we=(e,t)=>`${F(e)}/instance/${encodeURIComponent(t)}`;function ct(){window.addEventListener("popstate",()=>oe($())),document.addEventListener("click",e=>{var a,r;const t=(r=(a=e.target)==null?void 0:a.closest)==null?void 0:r.call(a,"a[data-link]");if(!t)return;const n=t.getAttribute("href")||"";n.startsWith("/")&&(e.preventDefault(),_(n))})}let ut=0;const Z={};function pt(){return"mcsel"+ ++ut}function le(e,t,n,a,r,s){const c=t.find(x=>x.v===n),u=c?c.label:t[0]?t[0].label:"";Z[e]=a;const b=r?"opacity-50 pointer-events-none":"",f=s?"block w-full":"inline-block",y=s?"w-full":"w-48";return`<div class="relative ${f}" data-csel="${e}">
    <button type="button" onclick="sb.cselToggle('${e}')"
      class="${y} flex items-center gap-2 px-2.5 py-1.5 rounded border border-brdin
      dark:border-neutral-700 bg-app dark:bg-neutral-900 text-[13px] text-left ${b}">
      <span class="truncate flex-1" data-csel-label>${l(u)}</span>
      <svg class="w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </button>
    <div data-csel-pop class="hidden absolute z-[60] mt-1 ${y} max-h-64 overflow-auto rounded-lg
      border border-brd dark:border-neutral-700 bg-app dark:bg-card-dark shadow-xl py-1">
      ${t.length>8?`<input data-csel-search oninput="sb.cselFilter('${e}')" placeholder="Search…"
        class="w-[calc(100%-12px)] mx-1.5 mb-1 px-2 py-1 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] outline-none focus:border-accent">`:""}
      <div data-csel-list>
        ${t.map(x=>`<button type="button" data-v="${l(x.v)}" data-search="${l(x.label.toLowerCase())}"
          onclick="sb.cselPick('${e}','${l(x.v)}')"
          class="w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-100 dark:hover:bg-neutral-800
          ${x.v===n?"text-accent dark:text-blue-400 font-medium":"text-neutral-700 dark:text-neutral-300"}">
          ${l(x.label)}</button>`).join("")}
      </div>
    </div></div>`}function bt(e){document.querySelectorAll("[data-csel-pop]").forEach(n=>{const a=n.closest("[data-csel]");a&&a.dataset.csel!==e&&n.classList.add("hidden")});const t=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`);if(t&&(t.classList.toggle("hidden"),!t.classList.contains("hidden"))){const n=t.querySelector("[data-csel-search]");n&&(n.value="",Se(e),n.focus())}}function xt(e,t){var r,s;(r=document.querySelector(`[data-csel="${e}"] [data-csel-pop]`))==null||r.classList.add("hidden");const n=document.querySelector(`[data-csel="${e}"] [data-csel-label]`),a=document.querySelector(`[data-csel="${e}"] [data-v="${CSS.escape(t)}"]`);n&&a&&(n.textContent=a.textContent.trim()),(s=Z[e])==null||s.call(Z,t)}function Se(e){const t=document.querySelector(`[data-csel="${e}"] [data-csel-search]`),n=((t==null?void 0:t.value)||"").toLowerCase();document.querySelectorAll(`[data-csel="${e}"] [data-csel-list] button`).forEach(a=>{const r=a;r.style.display=(r.dataset.search||"").includes(n)?"":"none"})}function mt(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-csel]")||document.querySelectorAll("[data-csel-pop]").forEach(a=>a.classList.add("hidden"))})}function A(e){return`<svg class="spin w-3.5 h-3.5 ${e==="white"?"text-white":"text-neutral-400"}"
    viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3"
    stroke-dasharray="42" stroke-linecap="round"/></svg>`}function ft(e){const t="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border";return e?`<span class="${t} bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>running</span>`:`<span class="${t} bg-neutral-100 text-neutral-500 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-400 dark:border-neutral-700"><span class="w-1.5 h-1.5 rounded-full bg-neutral-400"></span>stopped</span>`}function T(e,t){return`<div class="mb-2.5 mt-1">
    <div class="text-[12px] font-semibold text-neutral-800 dark:text-neutral-100">${e}</div>
    ${t?`<div class="text-[12px] text-neutral-400">${t}</div>`:""}</div>`}const D=(e,t)=>`<div class="flex items-center py-3 border-b border-brd dark:border-brd-dark/70">
    <div class="w-40 shrink-0 text-[13px] text-neutral-500 dark:text-neutral-400">${e}</div>
    <div class="text-[13.5px] text-neutral-800 dark:text-neutral-200">${t}</div></div>`;function ie(e,t,n){const a="px-4 py-1.5 rounded-full border text-[13px]";return n?`<a href="${e}" target="_blank" class="${a} border-brd dark:border-neutral-700
      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t}</a>`:`<span title="Start the site first" class="${a} border-brd/60 dark:border-neutral-800
    text-neutral-300 dark:text-neutral-600 cursor-default">${t}</span>`}const E="px-2.5 py-1.5 rounded border border-brd dark:border-neutral-700 text-[12.5px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800";function J(e,t,n,a){return`<button class="${E}" onclick='sb.op(${JSON.stringify(e)},${JSON.stringify(t)},${JSON.stringify(a||{})})'>${n}</button>`}function de(e){return`<div class="flex items-center gap-2 bg-app dark:bg-neutral-950 border border-brd dark:border-neutral-800 rounded px-2.5 py-1.5">
    <code class="flex-1 text-[12px] text-neutral-700 dark:text-neutral-200 truncate">${l(e)}</code>
    <button onclick='sb.copyText(${JSON.stringify(e)}, this)' class="text-[11px] text-accent dark:text-blue-400 hover:underline shrink-0">copy</button></div>`}function ce(e,t){return`<div class="rounded-lg border border-brd dark:border-brd-dark p-3.5">
    <div class="text-[12px] font-semibold uppercase tracking-wide text-accent dark:text-blue-400">${e}</div>
    <div class="mt-1 text-[13px] text-neutral-600 dark:text-neutral-300 leading-snug">${t}</div></div>`}const S=e=>(e||0).toLocaleString(),z=e=>"$"+(e||0).toLocaleString(void 0,{minimumFractionDigits:2,maximumFractionDigits:2}),ue=e=>(e.in||0)+(e.out||0)+(e.cw||0)+(e.cr||0);function gt(){const e=o.usage;if(!e||!e.available)return`<div class="px-6 py-12 text-center text-neutral-400 text-[14px]">
      No supported agent-usage data found.</div>`;const t=e.total||{},n=(u,b,f)=>`<div class="rounded-lg border border-brd dark:border-brd-dark p-4">
      <div class="text-[12px] text-neutral-400">${u}</div>
      <div class="mt-1 text-[20px] font-semibold text-neutral-900 dark:text-neutral-50">${b}</div>
      </div>`,a=Object.entries(e.by_model||{}).map(([u,b])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-24 capitalize text-neutral-700 dark:text-neutral-300">${u}</span>
      <span class="flex-1 text-neutral-500">${S(ue(b))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${z(b.cost)}</span></div>`).join(""),r=Object.entries(e.per_instance||{}).sort((u,b)=>(b[1].cost||0)-(u[1].cost||0)).map(([u,b])=>`
    <div class="flex items-center py-2 border-b border-brd dark:border-brd-dark/70 text-[13px]">
      <span class="w-32 truncate ${u==="unattributed"?"text-neutral-400 italic":"text-neutral-700 dark:text-neutral-300"}">${l(u)}</span>
      <span class="flex-1 text-neutral-500">${S(ue(b))} tokens</span>
      <span class="text-neutral-700 dark:text-neutral-200">${z(b.cost)}</span></div>`).join(""),s=e.sessions||[],c=s.map(u=>`
    <div class="flex items-center gap-3 py-1.5 text-[12.5px] border-b border-brd dark:border-brd-dark/60">
      <code class="text-neutral-400">${u.id}</code>
      <span class="w-16 capitalize text-neutral-500">${u.model}</span>
      <span class="flex-1 text-neutral-400 truncate">${(u.instances||[]).join(", ")||"—"}</span>
      <span class="text-neutral-500">${S(u.tokens)}</span>
      <span class="w-16 text-right text-neutral-700 dark:text-neutral-200">${z(u.cost)}</span></div>`).join("");return`<div class="px-6 py-6 max-w-3xl">
    <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50">Agent usage</h1>
    <p class="mt-1 text-[12.5px] text-neutral-400">Local host telemetry only. Currently collected from Claude sessions; this collector does not yet include Codex. Cost is <b>estimated</b> from public per-token prices.</p>
    <div class="mt-5 grid grid-cols-3 gap-3">
      ${n("Total tokens",S(e.tokens))}
      ${n("Estimated cost",z(e.cost))}
      ${n("Sessions",S(s.length)+(s.length>=25?"+":""))}
    </div>
    <div class="mt-5 grid grid-cols-2 gap-3 text-[12px] text-neutral-400">
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">in ${S(t.in)} · out ${S(t.out)}</div>
      <div class="rounded-lg border border-brd dark:border-brd-dark p-3">cache write ${S(t.cw)} · cache read ${S(t.cr)}</div>
    </div>
    <div class="mt-6">${T("By model")}${a||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${T("By instance","Best-effort — attributed by which mcp__sandbox-… tools each session used")}${r||'<div class="text-[13px] text-neutral-400">—</div>'}</div>
    <div class="mt-6">${T("Recent sessions")}
      <div class="flex items-center gap-3 py-1 text-[11px] uppercase tracking-wide text-neutral-400 border-b border-brd dark:border-brd-dark">
        <span>id</span><span class="w-16">model</span><span class="flex-1">instances</span><span>tokens</span><span class="w-16 text-right">cost</span></div>
      ${c}</div>
    <button onclick="sb.showUsage()" class="mt-5 text-[13px] text-accent dark:text-blue-400 hover:underline">↻ Refresh</button>
  </div>`}const ht=ue;function vt(){const e=o.data.instances.length;return`<div class="max-w-2xl mx-auto px-6 py-12">
    <h1 class="text-[22px] font-semibold text-neutral-900 dark:text-neutral-50">Give your coding agent a real WordPress</h1>
    <p class="mt-2 text-[14px] text-neutral-600 dark:text-neutral-300 leading-relaxed">
      The sandbox hands Codex, Claude, or another connected agent a <b>live, disposable WordPress environment</b> to work in — so it
      stops guessing and starts proving. Through the connected tools your agent can run WP-CLI, hit the
      REST API and database, open pages in a real browser, read and edit your plugin's actual code,
      and watch the logs. Each item on the left is one isolated environment the agent can fully control.</p>

    <div class="mt-7 grid grid-cols-3 gap-3">
      ${ce("Build","Your agent implements features against a running install and verifies them live — not from memory.")}
      ${ce("Reproduce","Hand your agent the environment and it reproduces the bug on a real stack — confirmed, not assumed.")}
      ${ce("Fix &amp; prove","Reproduce → fix the real code → re-verify in the same environment. Fast, and proven.")}
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
  </div>`}function kt(e){const t=e.domain||"",n=e.url.startsWith("https://"),a=t.endsWith(".tst");if(n)return`<span class="text-neutral-700 dark:text-neutral-200">${l(t)}</span>
      <span class="inline-flex items-center gap-1 ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">
        <span>🔒</span> https (trusted)</span>`;const r=a?`<div class="mt-1.5">
         <div class="text-[11.5px] text-neutral-500 dark:text-neutral-400 mb-1">
           🔒 Want HTTPS? Run this once in your terminal:</div>
         ${de("./sb secure "+e.name)}
       </div>`:"";return`<span class="text-neutral-700 dark:text-neutral-200">${l(t)}</span>
    <span class="text-[11px] text-neutral-400 ml-1">http (no cert)</span>${r}`}function $t(e){var a;const t=o.usage;if(!t||!t.available)return`<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1">
      <button onclick="sb.loadUsageThenRender()" class="text-accent dark:text-blue-400 hover:underline">Show agent token usage →</button></div>`;const n=(a=t.per_instance)==null?void 0:a[e];return n?`<div class="pt-2 border-t border-brd dark:border-brd-dark/60 mt-1 flex items-center gap-3">
    <span class="text-neutral-600 dark:text-neutral-300">Agent usage (est.):</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${S(ht(n))} tokens</span>
    <span class="text-neutral-700 dark:text-neutral-200 font-medium">${z(n.cost)}</span>
    <button onclick="sb.showUsage()" class="ml-auto text-accent dark:text-blue-400 hover:underline">details →</button></div>`:'<div class="pt-1 border-t border-brd dark:border-brd-dark/60 mt-1 text-neutral-400">No agent usage attributed to this instance yet.</div>'}function yt(e){if(!e)return vt();if(e.pending)return`<div class="px-6 pt-8">
    <div class="flex items-center gap-3">
      <h1 class="text-[24px] font-semibold text-neutral-900 dark:text-neutral-50">${l(e.name)}</h1>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full
        bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900/60">
        ${A()} creating</span>
    </div>
    <p class="mt-3 text-[13px] text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-md">
      Setting up the <b>${l(e.server)}</b> stack — pulling images, installing WordPress,
      and wiring it up. Live progress is in the Activity panel on the right; this can take a
      minute on first run.</p></div>`;const t=o.busy[e.name],n="text-accent dark:text-blue-400 hover:underline",a=e.mailpit_port,r=e.running?`<button onclick="sb.act('${e.name}','stop')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full border
        border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px] font-medium
        hover:bg-red-50 dark:hover:bg-red-950/40 flex items-center gap-2">
        ${t==="stop"?A():'<span class="w-2 h-2 rounded-sm bg-red-500"></span>'}Stop site</button>`:`<button onclick="sb.act('${e.name}','start')" ${t?"disabled":""} class="px-4 py-1.5 rounded-full
        bg-accent text-white text-[13px] font-medium hover:bg-blue-700
        flex items-center gap-2">${t==="start"?A("white"):"▶"} Start site</button>`;return`
  <div class="px-6 pt-5 pb-3.5 border-b border-brd dark:border-brd-dark flex items-start gap-4">
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="text-[19px] font-semibold text-neutral-900 dark:text-neutral-50 truncate">${l(e.name)}</h1>
        ${ft(e.running)}
      </div>
      <div class="mt-0.5 text-[12px] text-neutral-400 flex items-center gap-1">
        ${e.url} · <code>${l(e.mcp_server)}</code></div>
    </div>
    <div class="ml-auto flex items-center gap-2">
      ${e.login_url?ie(e.login_url,"Login",e.running):ie(e.url+"/wp-admin","Admin",e.running)}
      ${ie(e.url,"View site",e.running)}
    </div>
  </div>
  <div class="px-6 py-3.5 flex items-center gap-2">
    ${r}
    <button onclick="sb.act('${e.name}','restart')" ${!e.running||t?"disabled":""} class="px-4 py-1.5 rounded-full border
      border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300
      hover:bg-neutral-50 dark:hover:bg-neutral-800 flex items-center gap-2">
      ${t==="restart"?A():""}Restart</button>
    ${e.name==="main"?"":`<button onclick="sb.doDelete('${e.name}')" ${t?"disabled":""} class="ml-auto px-4 py-1.5
      rounded-full border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 text-[13px]
      hover:bg-red-50 dark:hover:bg-red-950/40">Delete</button>`}
  </div>
  <div class="px-6 pb-2">
    ${T("Overview")}
    ${D("Web server",`<div class="flex items-center gap-2">
        ${le("serverSel",(o.data.servers&&o.data.servers.length?o.data.servers:["apache","nginx","litespeed"]).map(s=>({v:s,label:s})),e.server,s=>window.sb.doServer(e.name,s),!!t||!e.running)}
        ${t==="server"?A():""}
        ${e.running?"":'<span class="text-[11px] text-neutral-400">start the site to switch</span>'}</div>`)}
    ${e.domain?D("Domain",kt(e)):""}
    ${D("Site address",e.running?`<a href="${e.url}" target="_blank" class="${n}">${e.url}</a>`:`<span class="text-neutral-400">${e.url} <span class="text-[11px]">(stopped)</span></span>`)}
    ${D("Test inbox",e.running?`<a href="http://localhost:${a}" target="_blank" class="${n}">Mailpit · :${a}</a>`:`<span class="text-neutral-400">Mailpit · :${a} <span class="text-[11px]">(start the site first)</span></span>`)}
    ${D("Active project",e.project==="—"?'<span class="text-neutral-400">none</span>':l(e.project))}
    ${D("Focus plugin",`<div class="flex items-center gap-2">
        ${le("focusSel",[{v:"",label:"— none —"}].concat(o.data.plugins.map(s=>({v:s,label:s}))),e.focus&&e.focus!=="—"?e.focus:"",s=>window.sb.doFocus(e.name,s),!!t)}
        ${t==="focus"||t==="unfocus"?A():""}</div>`)}
  </div>

  <div class="px-6 pb-6">
    ${T("Tools","Run maintenance commands — output streams below")}
    <div class="flex flex-wrap gap-1.5">
      ${J(e.name,"logs","Logs")}
      ${J(e.name,"status","Status")}
      ${J(e.name,"doctor","Doctor")}
      ${J(e.name,"update","Update plugins")}
      <button class="${E}" onclick="sb.doSnapshot('${e.name}')">Snapshot</button>
      <button class="${E}" onclick="sb.doRestore('${e.name}')">Restore…</button>
      <button class="${E}" onclick="sb.doSeed('${e.name}')">Seed…</button>
      ${J(e.name,"xdebug","Xdebug",{state:"status"})}
    </div>
    <div class="flex gap-2 mt-2.5">
      <span class="px-2 py-1.5 text-[12.5px] font-mono text-neutral-400">wp</span>
      <input id="wpArgs" placeholder="plugin list --status=active"
        onkeydown="if(event.key==='Enter')sb.doWp('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[12.5px] font-mono focus:border-accent outline-none">
      <button class="${E}" onclick="sb.doWp('${e.name}')">Run</button>
    </div>
  </div>

  <div class="px-6 pb-6">
    ${T("Plugins","Search to install from WordPress.org, or symlink a local source")}
    <div class="flex gap-2">
      <input id="plugQ" placeholder="Search plugins by name or slug…" oninput="sb.plugFilter()"
        onkeydown="if(event.key==='Enter')sb.doInstall('${e.name}')"
        class="flex-1 min-w-0 px-2.5 py-1.5 rounded border border-brdin dark:border-neutral-700
        bg-app dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">
      <button class="${E}" onclick="sb.doInstall('${e.name}')">Install from .org</button>
    </div>
    <div id="plugResults" class="mt-2 flex flex-col gap-1"></div>
  </div>

  <div class="px-6 pb-8">
    ${T("Use with Codex or Claude","Connect a coding-agent session to this exact site")}
    <div class="rounded-lg border border-brd dark:border-brd-dark bg-neutral-50 dark:bg-neutral-900/50 p-3.5 text-[12.5px] space-y-2">
      <div class="text-neutral-600 dark:text-neutral-300">Tell Codex or Claude in chat (simplest):</div>
      ${de("focus "+(e.focus&&e.focus!=="—"?e.focus:"<plugin>"))}
      <div class="text-neutral-600 dark:text-neutral-300">Or call this site's tools directly in a session:</div>
      ${de("mcp__"+e.mcp_server+"__*")}
      <div class="text-neutral-500 dark:text-neutral-400">Everything (admin, REST, wp-cli) targets <code>${e.url}</code>.</div>
      ${$t(e.name)}
    </div>
  </div>`}const d={page:"min-h-full bg-neutral-50 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-50",shell:"max-w-7xl mx-auto px-4 py-5 sm:px-6 sm:py-7 lg:px-8",panel:"rounded-xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-sm",muted:"text-neutral-600 dark:text-neutral-300",quiet:"text-neutral-500 dark:text-neutral-400",label:"text-[11px] font-semibold uppercase tracking-[0.12em] text-neutral-500 dark:text-neutral-400",button:"inline-flex min-h-9 items-center justify-center rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 px-3 py-2 text-[12px] font-medium text-neutral-800 dark:text-neutral-100 hover:bg-neutral-50 dark:hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950",primary:"inline-flex min-h-9 items-center justify-center rounded-lg bg-blue-700 px-3 py-2 text-[12px] font-semibold text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950"};function Ce(e,t=!0){return`<span class="host-icon relative flex shrink-0 items-center justify-center rounded-lg bg-white dark:bg-neutral-800 ${e==="all"?"text-blue-600 dark:text-blue-400":e==="local"?"text-emerald-600 dark:text-emerald-400":"text-blue-700 dark:text-blue-300"}">
    <svg aria-hidden="true" class="h-4 w-4" viewBox="0 0 24 24" fill="${e==="all"?"currentColor":"none"}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${e==="all"?'<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>':e==="local"?'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>':'<rect x="4" y="3" width="16" height="8" rx="2"/><rect x="4" y="13" width="16" height="8" rx="2"/><path d="M8 7h.01M8 17h.01"/>'}</svg>
    <span aria-hidden="true" class="host-dot absolute rounded-full border-neutral-100 dark:border-neutral-950 ${e==="all"?"bg-blue-500":t?"bg-emerald-500":"bg-amber-400"}"></span>
  </span>`}function wt(e="all"){const t=o.data.remotes.find(f=>f.name===e),n=e==="local"?"local":t?"remote":"all",a=n==="local"?"Local host":(t==null?void 0:t.name)||"All hosts",r=n!=="remote"||!!(t!=null&&t.control_ready),s=n==="all"?"Host overview":n==="local"?"This machine":r?"Remote available":"Remote unavailable",c="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-[13px] hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:hover:bg-neutral-800",u=(f,y,x,v,w,O)=>`<a href="${f}" data-link ${O?'aria-current="page"':""} class="${c} ${O?"bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-100":"text-neutral-700 dark:text-neutral-200"}">
      ${Ce(x,v)}<span class="min-w-0 flex-1"><span class="block truncate font-medium">${l(y)}</span><span class="block truncate text-[10px] text-neutral-400">${l(w)}</span></span>
    </a>`,b=o.data.remotes.map(f=>u(F(f.name),f.name,"remote",f.control_ready,f.control_ready?"Remote available":"Remote unavailable",e===f.name)).join("");return`<details class="group relative" id="hostSelector">
    <summary aria-label="Choose host. Current host: ${l(a)}" class="flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-2 text-left hover:bg-neutral-200/60 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:hover:bg-neutral-900">
      ${Ce(n,r)}
      <span class="min-w-0 flex-1"><span class="block truncate text-[13px] font-semibold text-neutral-900 dark:text-neutral-50">${l(a)}</span><span class="block truncate text-[10px] text-neutral-400">${l(s)}</span></span>
      <svg aria-hidden="true" class="h-4 w-4 shrink-0 text-neutral-400" viewBox="0 0 24 24" fill="none"><path d="m7 10 5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </summary>
    <nav aria-label="Hosts" class="absolute left-0 right-0 z-50 mt-1 max-h-64 space-y-0.5 overflow-auto rounded-lg border border-neutral-200 bg-white p-2 shadow-xl dark:border-neutral-700 dark:bg-neutral-900">
      ${u("/","All hosts","all",!0,"Host overview",e==="all")}
      ${u(Y(),"Local host","local",!0,"This machine",e==="local")}${b}
    </nav>
  </details>`}function _e(e,t,n,a,r,s,c,u){return`<a href="${t}" data-link class="${d.panel} group block p-5 hover:border-blue-300 dark:hover:border-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500">
    <div class="flex items-start gap-3">
      <span class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${a?"bg-emerald-500":"bg-amber-400"}"></span>
      <div class="min-w-0 flex-1"><div class="${d.label}">${l(n)}</div>
        <h2 class="mt-1 truncate text-[16px] font-semibold text-neutral-900 dark:text-white">${l(e)}</h2></div>
      <span aria-hidden="true" class="text-neutral-400 group-hover:text-blue-600">→</span>
    </div>
    <div class="mt-5 grid grid-cols-3 gap-3 border-t border-neutral-200 pt-4 dark:border-neutral-800">
      <div><div class="${d.label}">Instances</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${l(r)}</div></div>
      <div><div class="${d.label}">Running</div><div class="mt-1 text-[18px] font-semibold tabular-nums">${l(s)}</div></div>
      <div><div class="${d.label}">RAM</div><div class="mt-1 text-[14px] font-semibold tabular-nums">${l(c)}</div></div>
    </div>
    <p class="mt-4 text-[12px] ${d.quiet}">${l(u)}</p>
  </a>`}function je(e="all"){return""}function Le(){const e=o.data.instances.length,t=o.data.instances.filter(c=>c.running).length,n=o.data.remotes.map(c=>{var w;const u=o.remote[c.name],b=o.remoteBusy[c.name],f=u!=null&&u.instances?String(u.instances.total):b?"…":"—",y=u!=null&&u.instances?String(u.instances.running):b?"…":"—",x=((w=u==null?void 0:u.host)==null?void 0:w.memory_used_percent)==null?"—":`${u.host.memory_used_percent}%`,v=b?"Refreshing host inventory…":u?`${u.evidence_status} ${u.scan_mode||"fast"} evidence`:c.control_ready?"Waiting for first inventory":"Control service unavailable";return _e(c.name,F(c.name),"Remote host",c.control_ready,f,y,x,v)}).join(""),a=o.data.remotes.reduce((c,u)=>{var b,f;return c+(((f=(b=o.remote[u.name])==null?void 0:b.instances)==null?void 0:f.total)||0)},0),r=1+o.data.remotes.length,s=o.sync.refreshing?"Refreshing inventories…":o.sync.lastCompleted?`Updated ${new Date(o.sync.lastCompleted).toLocaleTimeString()}`:"Loading inventories…";return`<div class="${d.page}"><div class="${d.shell} space-y-6">
    <header class="space-y-4"><div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div><div class="${d.label}">Host control</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">All Sandbox hosts</h1>
      <p class="mt-1 text-[13px] ${d.muted}">One view of local and remote WordPress capacity.</p></div>
      <div class="flex flex-wrap items-center justify-end gap-3"><div role="status" class="text-[12px] ${o.sync.error?"text-red-700 dark:text-red-300":d.quiet}">${l(o.sync.error||s)}</div><button class="${d.button}" onclick="sb.refreshHosts()">Refresh remote hosts</button></div>
    </div></header>
    <section class="${d.panel} grid grid-cols-2 gap-px overflow-hidden bg-neutral-200 p-0 dark:bg-neutral-800 sm:grid-cols-4">
      ${[["Hosts",r],["Known instances",e+a],["Local running",t],["Remote hosts",o.data.remotes.length]].map(([c,u])=>`<div class="bg-white p-4 dark:bg-neutral-900"><div class="${d.label}">${c}</div><div class="mt-1 text-[24px] font-semibold tabular-nums">${u}</div></div>`).join("")}
    </section>
    <section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Available hosts</h2><span class="text-[11px] ${d.quiet}">Select a host to inspect its instances</span></div>
      <div class="grid gap-4 lg:grid-cols-2">${_e("Local host",Y(),"This machine",!0,String(e),String(t),"—","Open local instances and lifecycle controls")}${n}</div>
    </section>
  </div></div>`}function St(){const e=o.data.instances,t=e.filter(s=>s.running).length,n=e.filter(s=>s.pending).length,a=Math.max(0,e.length-t-n),r=e.length?e.map(s=>`<a href="${W(s.name)}" data-link class="flex items-center gap-3 border-t border-neutral-200 px-4 py-3 first:border-t-0 hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500 dark:border-neutral-800 dark:hover:bg-neutral-800/50">
    <span class="h-2 w-2 shrink-0 rounded-full ${s.running?"bg-emerald-500":s.pending?"bg-amber-400":"bg-neutral-300 dark:bg-neutral-600"}"></span>
    <span class="min-w-0 flex-1"><span class="block truncate text-[13px] font-medium text-neutral-900 dark:text-white">${l(s.name)}</span><span class="block truncate text-[11px] ${d.quiet}">${l(s.project||"Local project")} · ${l(s.server||"server unknown")}</span></span>
    <span class="text-[11px] ${d.quiet}">${s.pending?"pending":s.running?"running":"stopped"}</span><span aria-hidden="true" class="text-neutral-400">→</span>
  </a>`).join(""):`<div class="p-6 text-center text-[13px] ${d.quiet}">No local instances yet. Use New instance to create one from a local project.</div>`;return`<div class="${d.page}"><div class="${d.shell} space-y-6">
    <header><div class="${d.label}">Local host</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">This machine</h1><p class="mt-1 text-[13px] ${d.muted}">Local Sandbox instances and lifecycle controls.</p></header>
    <section class="${d.panel} grid grid-cols-2 gap-px overflow-hidden bg-neutral-200 p-0 dark:bg-neutral-800 sm:grid-cols-4">
      ${[["Instances",e.length],["Running",t],["Stopped",a],["Pending",n]].map(([s,c])=>`<div class="bg-white p-4 dark:bg-neutral-900"><div class="${d.label}">${s}</div><div class="mt-1 text-[24px] font-semibold tabular-nums">${c}</div></div>`).join("")}
    </section>
    <section><div class="mb-3 flex items-center justify-between gap-3"><h2 class="text-[14px] font-semibold">Local instances</h2><a href="/create" data-link class="${d.primary}">New instance</a></div><div class="${d.panel} overflow-hidden">${r}</div></section>
  </div></div>`}const Re={ok:"bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",err:"bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",info:"bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark"};function m(e,t="info"){const n=document.createElement("div");n.className="pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs "+(Re[t]||Re.info),n.textContent=e,i("toasts").appendChild(n),setTimeout(()=>{n.style.opacity="0",setTimeout(()=>n.remove(),220)},2600)}let ee=null;function Ct(e){if(e.type==="label")return`<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${l(e.label||"")}</div>`;if(e.type==="select"){const n=pt(),a=e.options||[],r=a.map(c=>({v:c,label:c})),s=e.value||a[0]||"";return`<input type="hidden" data-k="${e.key}" id="${n}_val" value="${l(s)}">`+le(n,r,s,c=>{document.getElementById(`${n}_val`).value=c},!1,!0)}if(e.type==="checkbox")return`<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${e.key}" data-type="checkbox"
        ${e.value?"checked":""} class="accent-accent w-3.5 h-3.5">
      ${l(e.label||e.key||"")}</label>`;if(e.type==="checklist"){const a=(e.options||[]).map(r=>`
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${e.key}" value="${l(r.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${l(r.label)}</span>
          ${r.desc?`<span class="block text-[11.5px] text-neutral-400 truncate">${l(r.desc)}</span>`:""}
        </span></label>`).join("");return`<div data-checklist-group="${e.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${a||'<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>'}</div>`}const t=e.oninput?` oninput="${l(e.oninput)}"`:"";return`<input data-k="${e.key}" data-field="${l(e.key||"")}"${t}
    placeholder="${l(e.placeholder||"")}" value="${l(e.value||"")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`}function V(e={}){return new Promise(t=>{ee=t,i("mTitle").textContent=e.title||"",i("mDesc").textContent=e.desc||"",i("mFields").innerHTML=(e.fields||[]).map(Ct).join("");const n=i("mOk");n.textContent=e.okText||"Confirm",n.className="px-3 py-1.5 rounded text-[13px] text-white border "+(e.danger?"bg-red-600 border-red-600 hover:bg-red-700":"bg-accent border-accent hover:bg-blue-700"),i("modal").classList.remove("hidden"),setTimeout(()=>{(i("mFields").querySelector("input,select")||n).focus()},30)})}function te(e){if(i("modal").classList.add("hidden"),ee){const t=ee;ee=null,t(e)}}function _t(){const e={};return i("mFields").querySelectorAll("[data-k]").forEach(t=>{t.dataset.type==="checkbox"?e[t.dataset.k]=t.checked:e[t.dataset.k]=(t.value||"").trim()}),i("mFields").querySelectorAll("[data-checklist-group]").forEach(t=>{const n=t.dataset.checklistGroup;e[n]=[...t.querySelectorAll("input[type=checkbox]:checked")].map(a=>a.value)}),e}function jt(){i("mCancel").onclick=()=>te(null),i("mOk").onclick=()=>te(_t()),i("modal").addEventListener("keydown",e=>{e.key==="Enter"&&i("mOk").click(),e.key==="Escape"&&te(null)}),i("modal").addEventListener("click",e=>{e.target===i("modal")&&te(null)})}let Te=async()=>{};function Lt(e){Te=e}function Rt(e){i("console").classList.remove("w-0"),i("console").classList.add("w-[26rem]"),i("conTitle").textContent=e,i("conBody").textContent="",i("conInputRow").classList.add("hidden"),i("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"}function pe(){i("console").classList.add("w-0"),i("console").classList.remove("w-[26rem]"),i("conInputRow").classList.add("hidden")}function B(e){const t=i("conBody");t.textContent+=e,t.scrollTop=t.scrollHeight}function Tt(e,t){i("conTitle").textContent=e,i("conDot").className="w-2 h-2 rounded-full "+(t?"bg-emerald-500":"bg-red-500")}function be(e,t,n){o.paused=!0,Rt(n||"Working…");let a=0,r=!!n;const s=setInterval(async()=>{var u;const c=await ye(e,a);!r&&c.status&&(i("conTitle").textContent=c.status.replace(/ [✓✗]$/,""),r=!0),c.chunk?(B(c.chunk),a=(u=c.offset)!=null?u:a):typeof c.offset=="number"&&(a=c.offset),c.done&&(clearInterval(s),o.paused=!1,t&&delete o.busy[t],Tt(c.status||"done",c.ok),m(c.status||"done",c.ok?"ok":"err"),await Te())},800)}let xe=null;const N=[];let j=-1,Q=!1;function ne(e){xe=e,i("console").classList.remove("w-0"),i("console").classList.add("w-[26rem]"),i("conTitle").textContent="Terminal — "+e,i("conDot").className="w-2 h-2 rounded-full bg-emerald-500",i("conBody").textContent.trim()||B("Terminal for "+e+` — runs inside the container.
Try: wp plugin list · wp option get siteurl · ls wp-content/plugins

`),i("conInputRow").classList.remove("hidden"),setTimeout(()=>i("conInput").focus(),60)}async function Bt(){if(Q)return;const e=i("conInput"),t=e.value.trim();if(!t||!xe)return;N.push(t),j=N.length,e.value="",B("› "+t+`
`),Q=!0,i("conDot").className="w-2 h-2 rounded-full bg-amber-400 animate-pulse";let n;try{n=await G({instance:xe,action:"term",cmd:t})}catch(s){B("error: "+s+`
`),Q=!1;return}if(!n.job_id){B((n.output||"failed")+`
`),Q=!1;return}let a=0;const r=setInterval(async()=>{var c;const s=await ye(n.job_id,a);s.chunk?(B(s.chunk),a=(c=s.offset)!=null?c:a):typeof s.offset=="number"&&(a=s.offset),s.done&&(clearInterval(r),Q=!1,B(`
`),i("conDot").className="w-2 h-2 rounded-full bg-emerald-500",e.focus())},500)}function It(e){const t=i("conInput");e.key==="Enter"?Bt():e.key==="ArrowUp"?(j>0&&(j--,t.value=N[j]||""),e.preventDefault()):e.key==="ArrowDown"&&(j<N.length-1?(j++,t.value=N[j]||""):(j=N.length,t.value=""),e.preventDefault())}function Mt(){i("conClose").onclick=pe,i("conInput").addEventListener("keydown",e=>It(e))}let Be=async()=>{},I=()=>{};function Pt(e){Be=e.refresh,I=e.render}const Ie={create:e=>"Creating "+e,delete:e=>"Deleting "+e,"start-all":()=>"Starting all sites","stop-all":()=>"Stopping all sites"};async function M(e,t,n={}){o.busy[e]=t,I();let a;try{a=await G(Object.assign({instance:e,action:t},n))}catch(r){delete o.busy[e],m("request failed: "+r,"err"),I();return}if(a.job_id){const r=Ie[t]?Ie[t](e):re(t)+" "+e;m(t.replace("-"," ")+" started…","info"),be(a.job_id,e,r)}else delete o.busy[e],a.ok?m(re(t)+" "+e+" ✓","ok"):m((a.output||"failed").split(`
`)[0],"err"),await Be()}async function H(e,t,n={}){let a;try{a=await G(Object.assign({instance:e,action:t},n))}catch(r){m("request failed: "+r,"err");return}if(a.job_id){const r={logs:"Logs",status:"Status",doctor:"Doctor",update:"Updating plugins",snapshot:"Snapshot",restore:"Restoring",seed:"Importing content",xdebug:"Xdebug",install:"Installing "+(n.slug||"plugin"),wp:"wp "+(n.args||"")};be(a.job_id,null,(r[t]||re(t))+" — "+e)}else m((a.output||"failed").split(`
`)[0],"err")}async function qt(e,t){t===""?M(e,"unfocus"):t&&M(e,"focus",{slug:t})}async function At(e,t){const n=o.data.instances.find(r=>r.name===e);if(!t||n&&n.server===t)return;o.busy[e]="server",I();let a;try{a=await G({instance:e,action:"server",server:t})}catch(r){delete o.busy[e],I(),m("request failed: "+r,"err");return}a.job_id?(m("switching "+e+" → "+t+"…","info"),be(a.job_id,e,"Switching "+e+" → "+t)):(delete o.busy[e],I(),m((a.output||"failed").split(`
`)[0],"err"))}async function Dt(e){const t=await V({title:"Delete "+e,danger:!0,okText:"Delete",desc:"Stops + removes the stack, DB volume, and files. Type the name to confirm.",fields:[{key:"confirm",placeholder:e}]});t&&t.confirm===e?M(e,"delete",{confirm:e}):t&&m("name did not match — not deleted","err")}function Et(e){const n=i("wpArgs").value.trim();if(!n){m("enter a wp-cli command","err");return}H(e,"wp",{args:n})}async function Nt(e){const t=await V({title:"Snapshot "+e,okText:"Save",desc:"Save the current DB + uploads under this name.",fields:[{key:"name",placeholder:"snapshot name"}]});t&&t.name&&H(e,"snapshot",{name:t.name})}async function Ht(e){let t=[];try{t=(await lt(e)).snapshots||[]}catch(a){}if(!t.length){m("no snapshots for "+e,"err");return}const n=await V({title:"Restore "+e,danger:!0,okText:"Restore",desc:"Overwrites the current DB + uploads with the chosen snapshot.",fields:[{key:"name",type:"select",options:t}]});n&&n.name&&H(e,"restore",{name:n.name})}async function Ot(e){const t=o.data.seeds||[];if(!t.length){m("no WXR files in runtime/seeds/","err");return}const n=await V({title:"Seed "+e,okText:"Import",desc:"Import a WXR content file into this instance.",fields:[{key:"file",type:"select",options:t}]});n&&n.file&&H(e,"seed",{file:n.file})}function Ut(e){const t=(i("plugQ").value||"").trim().toLowerCase().replace(/\s+/g,"-");if(!t){m("type a plugin slug to install","err");return}H(e,"install",{slug:t})}function Wt(e){const t=(i("plugQ").value||"").toLowerCase().trim(),n=i("plugResults");if(!t){n.innerHTML="";return}const a=o.data.instances.find(s=>s.name===e),r=(o.data.plugins||[]).filter(s=>s.toLowerCase().includes(t)).slice(0,8);n.innerHTML=r.map(s=>`
    <div class="flex items-center gap-2 px-2.5 py-1.5 rounded border border-brd dark:border-neutral-800
      text-[13px]">
      <span class="flex-1 text-neutral-700 dark:text-neutral-300">${l(s)}</span>
      ${a&&a.focus===s?'<span class="text-[11px] text-emerald-500">focused</span>':`<button class="text-[12px] text-accent dark:text-blue-400 hover:underline"
            onclick="sb.doFocus('${a?a.name:""}','${l(s)}')">Symlink + focus</button>`}
    </div>`).join("")||`<div class="text-[12px] text-neutral-400 px-1">No local source matches “${l(t)}”.
        Press Install to fetch it from WordPress.org.</div>`}function Ft(e,t){navigator.clipboard.writeText(e).then(()=>{const n=t.textContent;t.textContent="copied",setTimeout(()=>{t.textContent=n},1200)})}async function Jt(){try{o.usage=await $e()}catch(e){o.usage={available:!1}}I()}function Me(){if(K($()).kind!=="local"){m("New instance is available only for the local host","err");return}_("/create")}async function zt(){var t;const e=await((t=window.sandboxDesktop)==null?void 0:t.chooseProjectDirectory());e&&(i("createProject").value=e)}function Pe(){const e=document.getElementById("chooseProject");!e||!window.sandboxDesktop||(e.hidden=!1,e.addEventListener("click",()=>void zt()))}function Vt(){const e=i("createProject").value.trim(),t=i("createLabel").value.trim().toLowerCase();if(!e.startsWith("/")){m("enter an absolute local project path","err");return}if(t&&!/^[a-z0-9][a-z0-9_-]{0,30}$/.test(t)){m("label must use a-z, 0-9, _ or -","err");return}M(t||"new instance","create",{project_dir:e,label:t})}function Qt(){return`<div class="max-w-2xl mx-auto px-6 py-8">
    <a href="${Y()}" data-link class="inline-flex items-center gap-1 text-[12.5px] text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300">
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
      <a href="${Y()}" data-link class="px-4 py-2 rounded-full border border-brd dark:border-neutral-700 text-[13px] text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">Back to instances</a>
    </div>
  </div>`}const C=e=>{if(e==null)return"unknown";const t=["B","KiB","MiB","GiB","TiB"];let n=Math.max(0,e),a=0;for(;n>=1024&&a<t.length-1;)n/=1024,a++;return`${n.toFixed(a>1?1:0)} ${t[a]}`},g=e=>e==null||!Number.isFinite(e)?"unknown":String(e),h=(e,t,n="")=>`<div class="${d.panel} p-4">
  <div class="${d.label}">${l(e)}</div>
  <div class="mt-1 text-[20px] font-semibold tabular-nums text-neutral-900 dark:text-white">${l(t)}</div>
  ${n?`<div class="mt-1 text-[11px] ${d.quiet}">${l(n)}</div>`:""}</div>`;function me(e,t,n,a){const r=t.length?t.map(s=>`<tr class="border-b border-brd/70 dark:border-brd-dark/70 last:border-0">${n.map(([,,c])=>`<td class="py-2.5 pr-4 text-[12px] text-neutral-600 dark:text-neutral-300">${l(c(s))}</td>`).join("")}</tr>`).join(""):`<tr><td colspan="${n.length}" class="py-4 text-[12px] text-neutral-400">${l(a)}</td></tr>`;return`<section class="${d.panel} p-4"><div class="flex items-center justify-between gap-3 mb-3"><h2 class="text-[13px] font-semibold">${l(e)}</h2><span class="text-[11px] ${d.quiet}">${t.length} observed</span></div><div class="overflow-x-auto"><table class="w-full text-left"><thead><tr>${n.map(([s,c])=>`<th scope="col" class="pb-2 pr-4 ${d.label}">${l(c||s)}</th>`).join("")}</tr></thead><tbody>${r}</tbody></table></div></section>`}function qe(e,t,n){var Je,ze,Ve,Qe,Xe,Ge,Ke,Ye,Ze,et,tt,nt,at;if(!t)return`<div class="${d.page}"><div class="${d.shell}">${je(e)}<div class="py-16 text-center text-neutral-500 dark:text-neutral-400 text-[13px]">Loading ${l(e)}…</div></div></div>`;const a=t.instances||{total:0,running:0,stopped:0,rows:[]},r=t.host||{},s=t.storage,c=t.evidence_status==="complete"?"text-emerald-600":t.evidence_status==="partial"?"text-amber-600":"text-rose-600",u=r.disk_total_bytes?Math.min(100,Math.max(0,(r.disk_used_bytes||0)*100/r.disk_total_bytes)):0,b=t.per_instance_usage||[],f=a.rows.map(p=>({...b.find(mn=>mn.name===p.name),name:p.name,running:p.running})),y=((Je=t.process_view)==null?void 0:Je.apps)||[],x=((ze=t.process_view)==null?void 0:ze.processes)||[],v=((Ve=t.containers)==null?void 0:Ve.rows)||[],w=JSON.stringify(e),O=!!o.remoteBusy[e],k=n?b.find(p=>p.name===n):void 0,R=n?a.rows.find(p=>p.name===n):void 0,xn=n?`<section class="${d.panel} p-5"><div class="flex items-start justify-between gap-4"><div><div class="${d.label}">Remote instance · read only</div><h2 class="mt-1 text-[20px] font-semibold">${l(n)}</h2><p class="mt-1 text-[12px] ${d.quiet}">${l((R==null?void 0:R.project)||"Project unavailable")} · ${l((R==null?void 0:R.server)||"server unknown")}</p></div><a href="${F(e)}" data-link class="${d.button}">Back to host</a></div><div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">${h("State",R?R.running?"running":"stopped":"unknown")}${h("RAM",C(k==null?void 0:k.memory_used_bytes))}${h("CPU",`${g(k==null?void 0:k.cpu_percent)}%`)}${h("Containers",g(k==null?void 0:k.container_count),String((k==null?void 0:k.attribution_status)||"unknown"))}</div><p class="mt-4 text-[12px] ${d.quiet}">Remote lifecycle controls are not exposed by this inventory endpoint yet. Resource values are container-attributed evidence.</p></section>`:"";return`<div class="${d.page}"><div class="${d.shell} space-y-6"><header class="space-y-4">${je(e)}<div class="flex flex-col gap-4 md:flex-row md:items-end"><div class="flex-1"><div class="${d.label}">Remote host</div><h1 class="mt-1 text-[26px] font-semibold tracking-tight">${l(e)}</h1><p class="mt-1 text-[12px] ${d.muted}">Authenticated inventory · ${l(t.scan_mode||"fast")} snapshot</p></div><div class="flex flex-wrap items-center gap-2"><span class="mr-1 text-[12px] font-semibold ${c}">${O?"refreshing":l(t.evidence_status)}</span><button ${O?"disabled":""} class="${d.button}" onclick="sb.refreshRemote(${w})">Quick refresh</button><button ${O?"disabled":""} class="${d.primary}" onclick="sb.refreshRemote(${w},true)">Rebuild attribution</button></div></div></header>${(Qe=t.partial_reasons)!=null&&Qe.length?`<div role="status" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-[12px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Partial evidence: ${l(t.partial_reasons.join(", "))}. Unknown values are intentionally not treated as zero.</div>`:""}${xn}<section class="${d.panel} p-5"><div class="flex items-end justify-between gap-3"><div><div class="${d.label}">Host disk capacity</div><div class="mt-1 text-[18px] font-semibold">${C(r.disk_used_bytes)} used <span class="font-normal ${d.quiet}">of ${C(r.disk_total_bytes)}</span></div></div><div class="text-right text-[12px] ${d.quiet}">${C(r.disk_free_bytes)} free</div></div><div class="mt-4 h-3 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"><div class="h-full bg-blue-700" style="width:${u}%"></div></div><div class="mt-2 text-[11px] ${d.quiet}">Container values overlap host capacity and are not additive.</div></section><div class="grid grid-cols-2 gap-3 lg:grid-cols-4">${h("Hosted instances",String(a.total),`${a.running} running · ${a.stopped} stopped`)}${h("RAM used",r.memory_used_percent==null?"unknown":`${r.memory_used_percent}%`,`${g(r.memory_used_mb)} of ${g(r.memory_total_mb)} MiB`)}${h("Load 1m",g(r.load_1m),"point-in-time host sample")}${h("Active jobs",g((Xe=t.jobs)==null?void 0:Xe.active),`${g((Ge=t.jobs)==null?void 0:Ge.queued)} queued`)}${h("Containers",g(v.length),((Ke=t.containers)==null?void 0:Ke.status)||"unavailable")}${h("Storage attribution",(s==null?void 0:s.attribution_status)||"unknown",(s==null?void 0:s.status)||"unavailable")}${h("Unattributed",String((Ze=(Ye=t.unattributed_containers)==null?void 0:Ye.length)!=null?Ze:"unknown"),"containers without a confident match")}${h("Disk pressure",`${u.toFixed(1)}%`,"capacity-backed")}</div><section><div class="mb-3 flex items-center justify-between"><h2 class="text-[14px] font-semibold">Hosted instances</h2><span class="text-[11px] ${d.quiet}">Select an instance for its read-only resource view</span></div><div class="overflow-x-auto ${d.panel}"><table class="w-full text-left"><thead><tr>${["Instance","State","RAM","CPU","Evidence"].map(p=>`<th class="p-3 ${d.label}">${p}</th>`).join("")}</tr></thead><tbody>${f.length?f.map(p=>`<tr class="border-t border-neutral-200 dark:border-neutral-800"><td class="p-3 text-[12px] font-medium"><a data-link class="text-blue-700 hover:underline dark:text-blue-300" href="${we(e,String(p.name))}">${l(String(p.name))}</a></td><td class="p-3 text-[12px]">${p.running?"running":"stopped"}</td><td class="p-3 text-[12px] tabular-nums">${l(C(p.memory_used_bytes))}</td><td class="p-3 text-[12px] tabular-nums">${l(g(p.cpu_percent))}%</td><td class="p-3 text-[12px] ${d.quiet}">${l(String(p.attribution_status||"unattributed"))}</td></tr>`).join(""):`<tr><td colspan="5" class="p-4 text-[12px] ${d.quiet}">No hosted instances reported.</td></tr>`}</tbody></table></div></section><div class="grid gap-4 xl:grid-cols-3">${me("Apps",y,[["name","App",p=>String(p.name||"unknown")],["process_count","Processes",p=>g(p.process_count)],["rss_bytes","RSS",p=>C(p.rss_bytes)],["cpu_percent","CPU avg",p=>`${g(p.cpu_percent)}%`]],((et=t.process_view)==null?void 0:et.status)||"Process view unavailable")}${me("Processes",x.slice(0,50),[["pid","PID",p=>g(p.pid)],["name","Name",p=>String(p.name||"unknown")],["rss_bytes","RSS",p=>C(p.rss_bytes)],["cpu_percent","CPU avg",p=>`${g(p.cpu_percent)}%`]],"No process rows")}${me("Containers",v,[["name","Container",p=>String(p.name||"unknown")],["memory_used_bytes","Memory",p=>C(p.memory_used_bytes)],["memory_percent","Memory %",p=>`${g(p.memory_percent)}%`],["cpu_percent","CPU",p=>`${g(p.cpu_percent)}%`]],((tt=t.containers)==null?void 0:tt.status)||"Container view unavailable")}</div><section class="${d.panel} p-4"><h2 class="text-[13px] font-semibold">Storage evidence</h2><p class="mt-1 text-[12px] ${d.muted}">${l((s==null?void 0:s.status)||"unavailable")} · attribution ${l((s==null?void 0:s.attribution_status)||"unknown")} · used ${l(C((nt=s==null?void 0:s.capacity)==null?void 0:nt.used_bytes))} · available ${l(C((at=s==null?void 0:s.capacity)==null?void 0:at.available_bytes))}</p><p class="mt-2 text-[11px] ${d.quiet}">Deep scans are bounded and may remain partial. Unknown bytes are not cleanup authority.</p></section></div></div>`}function Xt(e,t){const n=t.map(a=>{const r=a.disabled?"opacity-40 pointer-events-none":"",s=a.danger?"text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40":"text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800";return`<button type="button" ${a.disabled?"disabled":""}
      onclick="event.preventDefault();event.stopPropagation();sb.rowMenuClose();${a.js}"
      class="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left ${s} ${r}">
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
      ${n}</div></span>`}function Gt(e){var n;let t=!1;document.querySelectorAll("[data-rowmenu-pop]").forEach(a=>{const r=a.closest("[data-rowmenu]"),s=!a.classList.contains("hidden");r&&r.dataset.rowmenu===e&&(t=s),a.classList.add("hidden")}),!t&&((n=document.querySelector(`[data-rowmenu="${e}"] [data-rowmenu-pop]`))==null||n.classList.remove("hidden"))}function Ae(){document.querySelectorAll("[data-rowmenu-pop]").forEach(e=>e.classList.add("hidden"))}function Kt(){document.addEventListener("click",e=>{var t,n;(n=(t=e.target)==null?void 0:t.closest)!=null&&n.call(t,"[data-rowmenu]")||Ae()})}function fe(){const e=$();return e.page==="instance"?e.name:null}const L={play:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',stop:'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',restart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',admin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',term:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7l4 4-4 4"/><path d="M12 15h6"/></svg>',snapshot:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>',restore:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.7 3L3 8"/><path d="M3 3v5h5"/></svg>',trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>'};function Yt(e){const t=JSON.stringify(e.name),n=[e.running?{label:"Stop",icon:L.stop,js:`sb.act(${t},'stop')`}:{label:"Start",icon:L.play,js:`sb.act(${t},'start')`},{label:"Restart",icon:L.restart,js:`sb.act(${t},'restart')`,disabled:!e.running},{label:"Open admin",icon:L.admin,js:`window.open(${JSON.stringify(e.url+"/wp-admin")},'_blank')`,disabled:!e.running},{label:"Console",icon:L.term,js:`sb.navigate(${JSON.stringify(W(e.name,!0))})`,disabled:!e.running},{label:"Snapshot",icon:L.snapshot,js:`sb.doSnapshot(${t})`},{label:"Restore…",icon:L.restore,js:`sb.doRestore(${t})`}];return n.push({label:"Delete",icon:L.trash,js:`sb.doDelete(${t})`,danger:!0}),n}function De(e){const t=e.name===fe(),n=e.running?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600",r=o.busy[e.name]?`<svg class="spin ml-auto w-3.5 h-3.5 text-neutral-400 shrink-0" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" stroke-dasharray="42" stroke-linecap="round"/></svg>`:`<span class="ml-auto transition-opacity ${t?"opacity-100":"opacity-0 group-hover:opacity-100"}">
        ${Xt("rm-"+e.name,Yt(e))}</span>`;return`<a href="${W(e.name)}" data-link class="group w-full text-left px-3 py-2 rounded
     flex items-center gap-2.5 text-[13.5px] ${t?"bg-white dark:bg-neutral-800 shadow-sm":"hover:bg-neutral-100 dark:hover:bg-neutral-800/60"}">
     <span class="w-2 h-2 rounded-full ${n} shrink-0"></span>
     <span class="truncate ${t?"font-medium text-neutral-900 dark:text-neutral-50":"text-neutral-700 dark:text-neutral-300"}">${l(e.name)}</span>
     ${r}</a>`}function Zt(e,t,n){const a=$(),r=a.page==="remote-instance"&&a.name===e&&a.instance===t;return`<a href="${we(e,t)}" data-link class="group flex w-full items-center gap-2.5 rounded px-3 py-2 text-left text-[13.5px] ${r?"bg-white font-medium text-neutral-900 shadow-sm dark:bg-neutral-800 dark:text-neutral-50":"text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800/60"}">
    <span class="h-2 w-2 shrink-0 rounded-full ${n?"bg-emerald-500":"bg-neutral-300 dark:bg-neutral-600"}"></span><span class="truncate">${l(t)}</span><span class="ml-auto text-[10px] text-neutral-400">remote</span></a>`}const Ee=e=>`<div class="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-neutral-400">${l(e)}</div>`;function Ne(e){var a;const t=o.remote[e];if(!t)return`<div class="px-3 py-3 text-[11px] text-neutral-400">${o.remoteBusy[e]?"Loading remote inventory…":"Remote inventory not loaded"}</div>`;const n=((a=t.instances)==null?void 0:a.rows)||[];return n.length?n.map(r=>Zt(e,r.name,r.running)).join(""):'<div class="px-3 py-3 text-[11px] text-neutral-400">No remote instances reported</div>'}function en(e){const t="Available only for the local host";for(const a of["newBtn","termBtn","startAll","stopAll"]){const r=i(a);r.disabled=!e,r.title=e?"":t,r.classList.toggle("opacity-40",!e),r.classList.toggle("cursor-not-allowed",!e)}const n=i("usageBtn");n.setAttribute("aria-disabled",String(!e)),n.title=e?"":t,n.classList.toggle("opacity-40",!e),n.classList.toggle("cursor-not-allowed",!e)}function ae(e=!1){var b,f,y;if(document.querySelector("[data-rowmenu-pop]:not(.hidden)")||!e&&document.querySelector("#hostSelector[open]"))return;const t=$(),n=K(t),a=n.kind==="remote"?n.name:n.kind;i("hostSelectorSlot").innerHTML=wt(a);const r=o.data.instances;let s=0,c=0,u="";if(n.kind==="local")s=r.length,c=r.filter(x=>x.running).length,u=r.length?r.map(De).join(""):'<div class="px-3 py-3 text-[11px] text-neutral-400">No local instances yet</div>';else if(n.kind==="remote"){const x=o.remote[n.name],v=((b=x==null?void 0:x.instances)==null?void 0:b.rows)||[];s=v.length,c=v.filter(w=>w.running).length,u=Ne(n.name)}else{s=r.length,c=r.filter(x=>x.running).length,u=Ee("Local host")+(r.length?r.map(De).join(""):'<div class="px-3 py-2 text-[11px] text-neutral-400">No local instances</div>');for(const x of o.data.remotes){const v=((y=(f=o.remote[x.name])==null?void 0:f.instances)==null?void 0:y.rows)||[];s+=v.length,c+=v.filter(w=>w.running).length,u+=Ee(x.name+" · remote")+Ne(x.name)}}i("list").innerHTML=u,n.kind==="remote"&&!o.remote[n.name]?(i("runcount").textContent=o.remoteBusy[n.name]?"…":"—",i("footstat").textContent=o.remoteBusy[n.name]?"loading remote inventory":"remote inventory not loaded"):(i("runcount").textContent=c+"/"+s,i("footstat").textContent=s?c+" of "+s+" running":n.kind==="all"?"no known instances":"no instances reported"),en(n.kind==="local")}let He="";function tn(e){if(e.page==="instance"){const t=o.data.instances.find(n=>n.name===e.name)||null;return t?JSON.stringify(["instance",t.name,t.running,t.server,t.focus,t.project,!!t.pending,t.url,t.domain||"",o.busy[t.name]||"",o.data.plugins.length]):"instance:none:"+e.name}return e.page==="usage"?"usage:"+(o.usage?"loaded":"pending"):e.page==="local-host"?"local-host:"+JSON.stringify(o.data.instances):e.page==="remote"||e.page==="remote-instance"?e.page+":"+e.name+":"+(e.page==="remote-instance"?e.instance:"")+":"+!!o.remoteBusy[e.name]+":"+JSON.stringify(o.remote[e.name]||null):e.page==="home"?"home:"+o.sync.refreshing+":"+o.sync.lastCompleted+":"+o.sync.error+":"+JSON.stringify(o.data.remotes)+":"+JSON.stringify(o.remote):e.page}function nn(){const e=document.activeElement;return!!(e&&(e.tagName==="INPUT"||e.tagName==="SELECT"||e.tagName==="TEXTAREA")||!i("modal").classList.contains("hidden"))}function an(e){switch(e.page){case"create":return Qt();case"usage":return gt();case"local-host":return St();case"remote":return qe(e.name,o.remote[e.name]);case"remote-instance":return qe(e.name,o.remote[e.name],e.instance);case"instance":{const t=o.data.instances.find(n=>n.name===e.name)||null;return yt(t)}case"home":return Le();default:return Le()}}function P(e){const t=$(),n=tn(t);!e&&n===He||!e&&nn()||(He=n,i("detail").innerHTML=an(t))}function ge(){ae(!0),P(!0)}let X=null,he=!1;const se=new Map;function ve(e,t="fast"){const n=se.get(e);if(n&&t==="fast")return n;const r=(async()=>{n&&await n,o.remoteBusy[e]=!0,ae(!1),P(!1);try{o.remote[e]=await ot(e,t)}finally{delete o.remoteBusy[e],ae(!1)}})().finally(()=>{se.get(e)===r&&se.delete(e)});return se.set(e,r),r}async function sn(){if(!o.paused){o.sync.refreshing=!0,o.sync.error=null,P(!1);try{o.data=await rt();const e=$();(e.page==="remote"||e.page==="remote-instance")&&await ve(e.name),o.sync.lastCompleted=Date.now()}catch(e){o.sync.error=e instanceof Error?e.message:"Refresh failed"}finally{o.sync.refreshing=!1,ae(),P(!1)}}}function q(e=!1){return X?(e&&(he=!0),X):(X=sn().finally(()=>{X=null,he&&(he=!1,q())}),X)}async function Oe(){if(K().kind!=="local"){m("Agent usage is available only for the local host","err");return}_("/usage"),i("detail").innerHTML='<div class="px-6 py-12 text-center text-neutral-400 text-[13px]">Loading agent usage…</div>';try{o.usage=await $e()}catch(e){o.usage={available:!1}}P(!0)}function rn(){_("/")}function on(e){_(W(e))}function ln(e){_(F(e))}function ke(e){return K().kind==="local"?!0:(m(`${e} is available only for the local host`,"err"),!1)}async function dn(e,t=!1){try{await ve(e,t?"deep":"fast"),P(!0)}catch(n){m("remote inventory refresh failed","err")}}async function cn(){const e=o.data.remotes.filter(a=>a.control_ready).map(a=>a.name),n=(await Promise.allSettled(e.map(a=>ve(a)))).filter(a=>a.status==="rejected").length;n&&m(`${n} host ${n===1?"refresh":"refreshes"} failed`,"err"),P(!0)}function Ue(){V({title:"How AI agents work here",okText:"Got it",desc:`The sandbox gives Codex, Claude, and other connected agents a live WordPress to act in, so they can verify instead of guess: run WP-CLI, hit REST + the DB, open pages in a real browser, read/edit your plugin's code, and tail logs. Say "focus <plugin>" or "work on <plugin>" in chat — the agent picks the matching environment, symlinks the plugin in, loads its code + context, and can build, reproduce, and fix end-to-end. One MCP server (mcp__sandbox__*) serves every project — each tool takes the project directory and resolves the right environment from the registry. Open an environment on the left for its exact snippet. It's real WordPress — break it freely, snapshot or delete anytime.`})}const un={navigate:_,goHome:rn,selectInstance:on,selectRemote:ln,refreshRemote:dn,refreshHosts:cn,showUsage:Oe,showHelp:Ue,openTerminal:ne,submitCreate:Vt,doCreate:Me,doDelete:Dt,doFocus:qt,doServer:At,doSnapshot:Nt,doRestore:Ht,doSeed:Ot,doWp:Et,doInstall:Ut,plugFilter:()=>Wt(fe()),loadUsageThenRender:Jt,act:M,op:H,cselToggle:bt,cselPick:xt,cselFilter:Se,rowMenuToggle:Gt,rowMenuClose:Ae,consoleClose:pe,copyText:Ft};window.sb=un;function pn(){Pt({refresh:()=>q(!0),render:ge}),Lt(()=>q(!0)),jt(),Mt(),mt(),Kt(),ct(),i("newBtn").onclick=Me,i("startAll").onclick=()=>{ke("Start all")&&M("*","start-all")},i("stopAll").onclick=()=>{ke("Stop all")&&M("*","stop-all")},i("usageBtn").onclick=t=>{t.preventDefault(),t.stopPropagation(),Oe()},i("helpBtn").onclick=Ue,i("termBtn").onclick=()=>{if(!ke("Terminal"))return;const t=fe()||o.data.instances[0]&&o.data.instances[0].name;if(!t){m("create an instance first","err");return}_(W(t,!0)),ne(t)},dt(t=>{ge(),t.page==="create"&&Pe(),(t.page==="remote"||t.page==="remote-instance"||t.page==="home"||t.page==="local-host")&&q(),t.page==="instance"&&t.console?ne(t.name):pe()}),ge(),$().page==="create"&&Pe();const e=$();e.page==="instance"&&e.console&&ne(e.name),bn()}const We=3e4;let Fe=0;function bn(){const e=()=>{window.clearTimeout(Fe),Fe=window.setTimeout(async()=>{document.visibilityState==="visible"&&await q(),e()},We)};q().finally(e),document.addEventListener("visibilitychange",()=>{document.visibilityState==="visible"&&(!o.sync.lastCompleted||Date.now()-o.sync.lastCompleted>We)&&q()})}pn()})();
