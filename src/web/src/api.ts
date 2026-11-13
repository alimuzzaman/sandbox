// Typed fetch wrappers for the Python /api endpoints.

import type {
  AppData, ActionResult, JobSnapshot, Usage, RemoteInventory, RemoteSummary, HostingView,
  ShareCreated, ShareList, ShareRevoked, ShareRefused, SharePartial,
} from "./types";

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path);
  const body = await r.json() as T & { error?: string };
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

export const fetchData = () => getJSON<AppData>("/api/instances");
export const fetchRemotes = () =>
  getJSON<{ remotes: RemoteSummary[] }>("/api/remotes");
export const fetchUsage = () => getJSON<Usage>("/api/usage");
export const fetchHosting = () => getJSON<HostingView>("/api/hosting");
export const fetchRemote = (name: string, mode: "fast" | "deep" = "fast") =>
  getJSON<RemoteInventory>(`/api/remote/${encodeURIComponent(name)}${mode === "deep" ? "?deep=1" : ""}`);
export const fetchJob = (id: string, offset: number) =>
  getJSON<JobSnapshot>(`/api/job/${id}?offset=${offset}`);
export const fetchSnapshots = (name: string) =>
  getJSON<{ snapshots: string[] }>(`/api/snapshots/${name}`);

export async function postAction(
  payload: Record<string, unknown>,
): Promise<ActionResult> {
  const r = await fetch("/api/action", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await r.json() as ActionResult;
  if (!r.ok && !body.output) throw new Error(`Request failed (${r.status})`);
  return body;
}

// "Share this stack" (spec 081 follow-up). The server refuses a share request
// without this exact JSON body and panel header, or with an Origin other than
// its own (contracts/panel-http.md). The result is never cached or kept.
type ShareTarget = { remote: string; stack: string };
export type SignInResult = { ok: true; url: string; expires_at: number; one_time: true; user: string } | ShareRefused;

function signInUnavailable(): ShareRefused {
  return { ok: false, code: "sign_in_unavailable", message: "WordPress sign-in could not be confirmed. Check its status before requesting another link." };
}

// Only the immediate caller receives this URL. Malformed responses must never
// reach navigation, the polling store, logs, or a reflected error message.
function signInResponse(value: unknown): SignInResult {
  if (typeof value !== "object" || value === null || !("ok" in value) || value.ok !== true
      || !("url" in value) || typeof value.url !== "string" || value.url.length > 4096
      || !("expires_at" in value) || typeof value.expires_at !== "number"
      || !Number.isSafeInteger(value.expires_at) || value.expires_at <= 0
      || !("one_time" in value) || value.one_time !== true
      || !("user" in value) || typeof value.user !== "string"
      || !/^[\x21-\x7e]{1,60}$/.test(value.user)) return signInUnavailable();
  try {
    const url = new URL(value.url);
    if (url.protocol !== "https:" || url.username || url.password
        || url.search !== "?sandbox_autologin=1" || !/^#[A-Za-z0-9_-]{43}$/.test(url.hash))
      return signInUnavailable();
    return { ok: true, url: url.href, expires_at: value.expires_at, one_time: true, user: value.user };
  } catch {
    return signInUnavailable();
  }
}

export async function postSignIn(body: ShareTarget): Promise<SignInResult> {
  try {
    const response = await fetch('/api/hosting/sign-in', {
      method: 'POST', cache: 'no-store', credentials: 'same-origin', redirect: 'error',
      headers: { 'content-type': 'application/json', 'x-sandbox-panel': 'share' },
      body: JSON.stringify(body),
    });
    if (!response.ok) return signInUnavailable();
    const value: unknown = await response.json();
    return signInResponse(value);
  } catch {
    return signInUnavailable();
  }
}
export function postShare(body: ShareTarget & { action: "create" }): Promise<ShareCreated | ShareRefused | SharePartial>;
export function postShare(body: ShareTarget & { action: "list" }): Promise<ShareList | ShareRefused>;
export function postShare(body: ShareTarget & { action: "revoke"; id: string }): Promise<ShareRevoked | ShareRefused | SharePartial>;
export async function postShare(body: ShareTarget & { action: string; id?: string }): Promise<unknown> {
  let r: Response;
  try {
    r = await fetch("/api/hosting/share", {
      method: "POST", cache: "no-store", credentials: "same-origin", redirect: "error",
      headers: { "content-type": "application/json", "x-sandbox-panel": "share" },
      body: JSON.stringify(body),
    });
  } catch {
    return { ok: false, code: "panel_unreachable", message: "The panel could not be reached." };
  }
  try {
    return await r.json();
  } catch {
    return { ok: false, code: "share_failed", message: `Request failed (${r.status})` };
  }
}
