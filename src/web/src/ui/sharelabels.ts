// Words for the panel's "Share this stack" button (spec 081 follow-up). No imports
// and no DOM, so `node --test` covers it. Nothing here ever sees a link.

/** What to change for the refusals an owner can fix themselves; null when the message says enough. */
export function refusalHelp(code: string, message: string, remote: string, slug: string): string | null {
  const cli = `sb host share create --remote ${remote} --stack ${slug} --json`;
  if (code === "agent_mint_not_enabled")
    return "This stack's target does not allow agent-made links. Set `share.agent_mint: true` in the " +
      "`share:` block of its manifest and apply it again, or run `sb host share create` in a terminal.";
  if (code === "stack_share_not_declared")
    return "This stack's manifest has no `share:` block. Add one (hostname, agent_mint and the gate " +
      "fields) to the stack template, then run `sb host stack up` again.";
  if (code === "stack_unknown" || (code === "share_failed" && /^(no remote named|remote .* is not provisioned)/.test(message)))
    return `This panel's machine has no controller record for this stack. Make the link on the controller: ${cli}`;
  if (code === "panel_request_refused")
    return "The request did not come from this panel's own page. Reload the panel and try again.";
  return null;
}

/** "expires in 6d 23h (date)" plus "no use limit" / "N uses". */
export function shareTerms(expiresAt: number, uses: number | null, clamped: boolean, now: number): string {
  const left = Math.max(0, expiresAt - now);
  const days = Math.floor(left / 86400), hours = Math.floor((left % 86400) / 3600), minutes = Math.floor((left % 3600) / 60);
  const span = days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`;
  const limit = uses == null ? "no use limit" : `${uses} use${uses === 1 ? "" : "s"}`;
  return `expires in ${span}${clamped ? " (shortened to the gate's end date)" : ""} · ${limit}`;
}

/** The share buttons need a remote name; a server's own record is named "this-server". */
export function canShare(remote: string): boolean {
  return remote !== "this-server" && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(remote);
}

/**
 * A revoke target the panel may send: one link or session, never `all`. A link id is
 * `l-` and 12 hex; an old-form one (32 hex) lives until it expires (LEGACY(081 short links)).
 */
export function revocableId(id: string): boolean {
  return /^(?:l-[0-9a-f]{12}|[ls]-[0-9a-f]{32})$/.test(id);
}
