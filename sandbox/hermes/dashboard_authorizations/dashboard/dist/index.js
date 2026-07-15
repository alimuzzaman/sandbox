(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Input, Label } = SDK.components;
  const { useEffect, useState } = SDK.hooks;
  const base = "/api/plugins/sandbox-authorizations";
  function request(path, options) { return SDK.fetchJSON(base + path, options); }
  function Page() {
    const [data, setData] = useState({requests: [], status: {}});
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [form, setForm] = useState({job_name: "", scope: "", replay_origin: "", rationale: "", expires_in_minutes: "60"});
    const refresh = () => request("/requests").then(setData).catch(e => setError(e.message || "Authorization API unavailable"));
    useEffect(() => { refresh(); }, []);
    const sync = () => { setBusy(true); request("/sync", {method: "POST"}).then(refresh).catch(e => setError(e.message)).finally(() => setBusy(false)); };
    const approve = id => { if (!window.confirm("Approve this exact scoped request?")) return; setBusy(true); request("/requests/" + id + "/approve", {method: "POST", body: JSON.stringify({confirm: true})}).then(refresh).catch(e => setError(e.message)).finally(() => setBusy(false)); };
    const create = event => { event.preventDefault(); setBusy(true); request("/requests", {method: "POST", body: JSON.stringify({...form, expires_in_minutes: Number(form.expires_in_minutes)})}).then(refresh).catch(e => setError(e.message)).finally(() => setBusy(false)); };
    const field = (key, label, placeholder) => React.createElement("div", {className: "grid gap-1"}, React.createElement(Label, {htmlFor: key}, label), React.createElement(Input, {id: key, value: form[key], placeholder, onChange: e => setForm({...form, [key]: e.target.value})}));
    const rows = (data.requests || []).map(item => React.createElement("div", {key: item.id, className: "border border-border p-3 flex flex-col gap-2"},
      React.createElement("div", {className: "flex items-center justify-between gap-2"}, React.createElement("code", null, item.job_name), React.createElement(Badge, {variant: "outline"}, item.status)),
      item.blocker && React.createElement("p", {className: "text-sm text-muted-foreground"}, item.blocker),
      item.scope && React.createElement("p", {className: "text-xs"}, item.scope + " · " + item.replay_origin),
      item.status === "pending" && React.createElement(Button, {disabled: busy, onClick: () => approve(item.id)}, "Approve")));
    return React.createElement("div", {className: "flex flex-col gap-6"},
      React.createElement(Card, null, React.createElement(CardHeader, null, React.createElement(CardTitle, null, "Sandbox Authorizations")),
        React.createElement(CardContent, {className: "flex flex-col gap-3"},
          React.createElement("p", {className: "text-sm text-muted-foreground"}, "Review bounded Hermes work before approving it. Approval is audited and applies to one cataloged cron job."),
          React.createElement("div", {className: "flex gap-2"}, React.createElement(Button, {disabled: busy, onClick: sync}, busy ? "Working…" : "Sync review-required output"), React.createElement(Button, {variant: "outline", onClick: refresh}, "Refresh")),
          error && React.createElement("p", {className: "text-sm text-red-500"}, error))),
      React.createElement(Card, null, React.createElement(CardHeader, null, React.createElement(CardTitle, null, "Request authorization")), React.createElement(CardContent, null,
        React.createElement("form", {className: "grid gap-3", onSubmit: create}, field("job_name", "Catalog job", "lenzora-todo-task"), field("scope", "Scope", "preview-overlay"), field("replay_origin", "HTTPS replay origin", "https://lenzora.dev"), field("rationale", "Rationale", "Dev-only bounded test"), field("expires_in_minutes", "Expiry minutes", "60"), React.createElement(Button, {disabled: busy, type: "submit"}, "Create request"))),
      React.createElement(Card, null, React.createElement(CardHeader, null, React.createElement(CardTitle, null, "Requests")), React.createElement(CardContent, {className: "grid gap-3"}, rows.length ? rows : React.createElement("p", {className: "text-sm text-muted-foreground"}, "No authorization requests."))));
  }
  window.__HERMES_PLUGINS__.register("sandbox-authorizations", Page);
})();
