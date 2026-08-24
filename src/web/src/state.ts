// App store: the polled data, transient busy flags, and cached usage. The
// "current view" is NOT stored here — it derives from the URL (see router.ts).

import type { AppData, Usage, RemoteInventory } from "./types";

export const store: {
  data: AppData;
  busy: Record<string, string>;   // instance name -> in-flight action
  usage: Usage | null;
  remote: Record<string, RemoteInventory>;
  paused: boolean;                // pause the refresh tick while a job streams
} = {
  data: {
    instances: [], plugins: [], seeds: [],
    servers: ["apache", "nginx", "litespeed"], domains_ready: false, remotes: [],
  },
  busy: {},
  usage: null,
  remote: {},
  paused: false,
};
