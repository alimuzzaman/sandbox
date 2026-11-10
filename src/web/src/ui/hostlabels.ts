// Plain-language labels for hosted targets and their spec 077 lifetimes
// (spec 076 follow-up). No imports and no DOM, so `node --test` covers it.
// Every label here is read-only text: nothing in the panel acts on a hosted target.

export type Tone = "good" | "warn" | "bad" | "quiet";

export interface LifetimeLike {
  classification: "recorded" | "unclassified" | "unknown";
  reason: string | null;
  lifetime: "temporary" | "hosted" | "permanent" | null;
  owner: string | null;
  expires_at: number | null;
  canonical: boolean;
  reap_eligible: boolean;
  reap_reason: "expired" | "merged" | null;
  state: "active" | "reaping" | "reaped" | null;
  extended_count: number;
}

export const CPU_LABEL = "CPU (sample)";
export const CPU_TITLE = "A point-in-time sample from one docker stats read; a short spike can look like steady load.";
export const UNCLASSIFIED = "unclassified, protected";
export const NEAR_EXPIRY_SECONDS = 24 * 3600;

/** `required_evidence_missing` -> `required evidence missing`; anything odd -> "". */
export function humanReason(reason: string | null | undefined): string {
  return reason && /^[a-z][a-z0-9_]{0,63}$/.test(reason) ? reason.replace(/_/g, " ") : "";
}

/** The last apply as words. A target without a summary was applied before tracking. */
export function applyLabel(status: string | null | undefined, reason: string | null | undefined,
  recorded: boolean): { text: string; tone: Tone } {
  const why = humanReason(reason);
  if (!status) return recorded ? { text: "unknown", tone: "quiet" } : { text: "applied before tracking", tone: "quiet" };
  if (status === "unverified")
    return { text: `applied · delivery unverified${why ? ` (${why})` : ""}`, tone: "warn" };
  if (status === "failed") return { text: `failed${why ? ` (${why})` : ""}`, tone: "bad" };
  if (status === "succeeded") return { text: "succeeded", tone: "good" };
  if (status === "applying") return { text: "applying", tone: "warn" };
  return { text: status, tone: "quiet" };
}

function span(seconds: number): string {
  const days = Math.floor(seconds / 86400), hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return hours ? `${days} d ${hours} h` : `${days} d`;
  if (hours) return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
  return `${minutes} min`;
}

/** "2 h 5 min left", "expired 1 h ago"; "" when there is no expiry. */
export function timeLeft(expiresAt: number | null | undefined, now: number): string {
  if (expiresAt == null) return "";
  const left = Math.round(expiresAt - now);
  if (left < 0) return `expired ${span(-left)} ago`;
  if (left < 60) return "under a minute left";
  return `${span(left)} left`;
}

/** "bad" for reapable or expired copies, "warn" within a day of expiry. */
export function expiryTone(view: LifetimeLike | undefined, now: number): Tone {
  if (!view || view.classification !== "recorded" || view.lifetime !== "temporary") return "quiet";
  if (view.reap_eligible || (view.expires_at != null && view.expires_at <= now)) return "bad";
  if (view.expires_at != null && view.expires_at - now < NEAR_EXPIRY_SECONDS) return "warn";
  return "quiet";
}

export function lifetimeLabel(view: LifetimeLike | undefined): string {
  if (!view || (view.classification === "unknown" && view.reason !== "loading")) return "lifetime unknown";
  if (view.classification === "unknown") return "lifetime loading…";
  if (view.classification !== "recorded" || !view.lifetime) return UNCLASSIFIED;
  return view.canonical ? `${view.lifetime} · canonical` : view.lifetime;
}

/** Only a valid record can ever read as reapable. */
export function reapLabel(view: LifetimeLike | undefined): string {
  if (!view || view.classification === "unknown") return "unknown";
  if (view.classification !== "recorded") return "protected";
  return view.reap_eligible ? `reapable${view.reap_reason ? ` · ${view.reap_reason}` : ""}` : "not reapable";
}

/** "Start all" / "Stop all" act on Sandbox dev instances only; hide them when there are none. */
export function showBulkActions(devInstances: number): boolean {
  return devInstances > 0;
}

export function hostedCountText(total: number | null | undefined, running: number | null | undefined): string {
  if (!total) return "";
  return `${total} hosted app${total === 1 ? "" : "s"} · ${running ?? 0} running`;
}

// The same order the server uses for /hosting: temporary copies first,
// reapable ones before the rest, soonest expiry first; unclassified last.
export function lifetimeRank(view: LifetimeLike | undefined): number {
  if (!view || view.classification === "unknown") return 4;
  if (view.classification !== "recorded") return 5;
  if (view.lifetime === "temporary") return view.reap_eligible ? 0 : 1;
  return view.lifetime === "hosted" ? 2 : view.lifetime === "permanent" ? 3 : 4;
}

export function compareLifetimes(a: LifetimeLike | undefined, b: LifetimeLike | undefined): number {
  const rank = lifetimeRank(a) - lifetimeRank(b);
  if (rank) return rank;
  const ea = a?.expires_at ?? Number.POSITIVE_INFINITY, eb = b?.expires_at ?? Number.POSITIVE_INFINITY;
  if (ea !== eb) return ea < eb ? -1 : 1;
  return (a?.owner || "").localeCompare(b?.owner || "");
}
