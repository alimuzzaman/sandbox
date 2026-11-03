// Single-owner polling for dashboard jobs. Free of DOM and store access so it can be
// tested directly with `node --test`.

export interface PollSnapshot {
  offset?: number;
  done?: boolean;
}

export interface PollHandlers<T extends PollSnapshot> {
  onSnapshot(snapshot: T): void;
  onDone(snapshot: T): void;
  onLost(): void;
  onCancel(): void;
}

export interface PollHandle {
  cancel(): void;
}

export const MAX_CONSECUTIVE_FAILURES = 5;

export function startPoll<T extends PollSnapshot>(
  fetchSnapshot: (offset: number) => Promise<T>,
  intervalMs: number,
  handlers: PollHandlers<T>,
): PollHandle {
  let offset = 0;
  let failures = 0;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const lose = (): void => {
    stopped = true;
    handlers.onLost();
  };

  const tick = async (): Promise<void> => {
    timer = null;
    let snapshot: T;
    try {
      snapshot = await fetchSnapshot(offset);
      if (snapshot === null || typeof snapshot !== "object") throw new Error("invalid snapshot");
    } catch {
      if (stopped) return;
      failures += 1;
      if (failures >= MAX_CONSECUTIVE_FAILURES) lose();
      else timer = setTimeout(tick, intervalMs);
      return;
    }
    if (stopped) return;
    failures = 0;
    if (typeof snapshot.offset === "number") offset = snapshot.offset;
    try {
      handlers.onSnapshot(snapshot);
    } catch {
      lose();
      return;
    }
    if (snapshot.done) {
      stopped = true;
      handlers.onDone(snapshot);
      return;
    }
    timer = setTimeout(tick, intervalMs);
  };

  timer = setTimeout(tick, intervalMs);
  return {
    cancel(): void {
      if (stopped) return;
      stopped = true;
      if (timer !== null) clearTimeout(timer);
      handlers.onCancel();
    },
  };
}
