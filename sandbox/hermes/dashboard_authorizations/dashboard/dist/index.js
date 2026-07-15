(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__ || typeof window.__HERMES_PLUGINS__.register !== "function") return;
  const { React } = SDK;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button } = SDK.components;
  const { useEffect, useState } = SDK.hooks;
  const base = "/api/plugins/sandbox-authorizations";
  function request(path, options) { return SDK.fetchJSON(base + path, options); }
  function errorMessage(error) {
    const raw = String(error && error.message || error || "Authorization API unavailable");
    const body = raw.replace(/^\d{3}:\s*/, "");
    try {
      const parsed = JSON.parse(body);
      return typeof parsed.detail === "string" ? parsed.detail : raw;
    } catch (_) {
      return body;
    }
  }
  function Page() {
    const [data, setData] = useState({requests: [], status: {}});
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const refresh = () => request("/requests").then(setData).catch(e => setError(errorMessage(e)));
    useEffect(() => { refresh(); }, []);
    const approve = item => { const message = "Approve this pending authorization?\n\nCron: " + item.job_name + "\nScope: " + item.scope + "\nOrigin: " + item.replay_origin + "\nExpires: " + item.expires_at; if (!window.confirm(message)) return; setBusy(true); request("/requests/" + item.id + "/approve", {method: "POST", body: JSON.stringify({confirm: true})}).then(refresh).catch(e => setError(errorMessage(e))).finally(() => setBusy(false)); };
    const rows = (data.requests || []).map(item => React.createElement("div", {key: item.id, className: "border border-border p-3 flex flex-col gap-2"},
      React.createElement("div", {className: "flex items-center justify-between gap-2"}, React.createElement("code", null, item.job_name), React.createElement(Badge, {variant: "outline"}, item.status)),
      item.blocker && React.createElement("p", {className: "text-sm text-muted-foreground"}, item.blocker),
      item.scope && React.createElement("p", {className: "text-xs"}, item.scope + " · " + item.replay_origin),
      item.rationale && React.createElement("p", {className: "text-sm text-muted-foreground"}, item.rationale),
      React.createElement("p", {className: "text-xs text-muted-foreground"}, "Expires: " + item.expires_at),
      item.status === "pending" && React.createElement(Button, {disabled: busy, onClick: () => approve(item)}, "Review and approve")));
    return React.createElement("div", {className: "flex flex-col gap-6"},
      React.createElement(Card, null, React.createElement(CardHeader, null, React.createElement(CardTitle, null, "Sandbox Authorizations")),
        React.createElement(CardContent, {className: "flex flex-col gap-3"},
          React.createElement("p", {className: "text-sm text-muted-foreground"}, "Eligible cron jobs create bounded pending requests automatically. Review the exact job, scope, origin, and expiry before approving."),
          React.createElement("div", {className: "flex gap-2"}, React.createElement(Button, {variant: "outline", onClick: refresh}, "Refresh")),
          error && React.createElement("p", {className: "text-sm text-red-500"}, error))),
      React.createElement(Card, null, React.createElement(CardHeader, null, React.createElement(CardTitle, null, "Requests")), React.createElement(CardContent, {className: "grid gap-3"}, rows.length ? rows : React.createElement("p", {className: "text-sm text-muted-foreground"}, "No authorization requests."))));
  }
  window.__HERMES_PLUGINS__.register("sandbox-authorizations", Page);
})();
