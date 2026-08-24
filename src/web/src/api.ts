// Typed fetch wrappers for the Python /api endpoints.

import type { AppData, ActionResult, JobSnapshot, Usage, RemoteInventory } from "./types";

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path);
  const body = await r.json() as T & { error?: string };
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

export const fetchData = () => getJSON<AppData>("/api/instances");
export const fetchUsage = () => getJSON<Usage>("/api/usage");
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
