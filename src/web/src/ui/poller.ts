// Polling for dashboard jobs. Free of DOM access so it can be tested directly with
// `node --test`. A poll always ends, by completion or lost contact, so it can always report.

export interface PollSnapshot {
  offset?: number;
  done?: boolean;
}

export interface PollHandlers<T extends PollSnapshot> {
  onSnapshot(snapshot: T): void;
  onDone(snapshot: T): void;
  onLost(): void;
}

export const MAX_CONSECUTIVE_FAILURES = 5;
export const REQUEST_TIMEOUT_MS = 30_000;

// A request that never settles counts as a failure; a late answer is ignored.
function withTimeout<T>(request: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const expired = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error("request timed out")), timeoutMs);
  });
  return Promise.race([request, expired]).finally(() => clearTimeout(timer));
}

export function startPoll<T extends PollSnapshot>(
  fetchSnapshot: (offset: number) => Promise<T>,
  intervalMs: number,
  handlers: PollHandlers<T>,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): void {
  let offset = 0;
  let failures = 0;

  const tick = async (): Promise<void> => {
    let snapshot: T;
    try {
      snapshot = await withTimeout(fetchSnapshot(offset), timeoutMs);
      if (snapshot === null || typeof snapshot !== "object") throw new Error("invalid snapshot");
    } catch {
      failures += 1;
      if (failures >= MAX_CONSECUTIVE_FAILURES) handlers.onLost();
      else setTimeout(tick, intervalMs);
      return;
    }
    failures = 0;
    if (typeof snapshot.offset === "number") offset = snapshot.offset;
    try {
      handlers.onSnapshot(snapshot);
    } catch {
      handlers.onLost();
      return;
    }
    if (snapshot.done) {
      handlers.onDone(snapshot);
      return;
    }
    setTimeout(tick, intervalMs);
  };

  setTimeout(tick, intervalMs);
}

export interface JobFlagStore {
  paused: boolean;
  busy: Record<string, unknown>;
}

// Tracks the flags job polls hold. begin() returns the release for that one job.
export function jobFlags(store: JobFlagStore): { begin(instance: string | null): () => void } {
  let running = 0;
  const owners = new Map<string, object>();
  return {
    begin(instance) {
      const owner = {};
      let released = false;
      running += 1;
      store.paused = true;
      if (instance) owners.set(instance, owner);
      return () => {
        if (released) return;
        released = true;
        running -= 1;
        store.paused = running > 0;
        if (instance && owners.get(instance) === owner) {
          owners.delete(instance);
          delete store.busy[instance];
        }
      };
    },
  };
}

export function busyHolder(busy: Record<string, unknown>, instance: string): string | null {
  const v = busy[instance] ?? busy["*"];
  if (v != null) return String(v);
  if (instance === "*") {
    for (const k of Object.keys(busy)) {
      if (busy[k] != null) return String(busy[k]);
    }
  }
  return null;
}
