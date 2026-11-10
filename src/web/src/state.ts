// App store: the polled data, transient busy flags, and cached usage. The
// "current view" is NOT stored here — it derives from the URL (see router.ts).

import type { AppData, Usage, RemoteInventory, HostingView } from "./types";

export const store: {
  data: AppData;
  busy: Record<string, string>;   // instance name -> in-flight action
  usage: Usage | null;
  remote: Record<string, RemoteInventory>;
  remoteBusy: Record<string, boolean>;
  hosting: HostingView | null;
  hostingError: string | null;
  sync: { refreshing: boolean; lastCompleted: number | null; error: string | null };
  paused: boolean;                // pause the refresh tick while a job streams
} = {
  data: {
    instances: [], plugins: [], seeds: [],
    servers: ["apache", "nginx", "litespeed"], domains_ready: false, remotes: [],
  },
  busy: {},
  usage: null,
  remote: {},
  remoteBusy: {},
  hosting: null,
  hostingError: null,
  sync: { refreshing: false, lastCompleted: null, error: null },
  paused: false,
};

// On a Sandbox remote, `sb web` also lists its own machine as a remote marked
// `local`, for the memory, disk and container view. It is the same machine as
// the local host, so the two are named and counted as one server.
export function thisServer() { return store.data.remotes.find(remote => remote.local); }
export function localHostName(): string { return thisServer() ? "This server" : "Local host"; }
export function localHostStatus(): string { return thisServer() ? "Instances and actions" : "This machine"; }
export function remoteLabel(name: string): string {
  return store.data.remotes.find(remote => remote.name === name)?.local ? "This server · resources" : name;
}
export function otherRemotes() { return store.data.remotes.filter(remote => !remote.local); }
