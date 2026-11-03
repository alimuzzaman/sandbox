import { test } from "node:test";
import assert from "node:assert/strict";
import { startPoll, MAX_CONSECUTIVE_FAILURES } from "../src/ui/poller.ts";

const INTERVAL = 1;
const settle = (ms = 25) => new Promise((resolve) => setTimeout(resolve, ms));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function recorder() {
  const events: string[] = [];
  return {
    events,
    handlers: {
      onSnapshot: (s: { chunk?: string }) => { events.push("snapshot:" + (s.chunk ?? "")); },
      onDone: () => { events.push("done"); },
      onLost: () => { events.push("lost"); },
      onCancel: () => { events.push("cancel"); },
    },
  };
}

test("only one request is in flight", async () => {
  let calls = 0;
  let def = deferred<{ offset?: number; chunk?: string }>();
  const rec = recorder();
  const handle = startPoll(
    async () => {
      calls += 1;
      return def.promise;
    },
    INTERVAL,
    rec.handlers,
  );
  await settle();
  assert.equal(calls, 1);
  const nextDef = deferred<{ offset?: number; chunk?: string }>();
  def.resolve({ offset: 1, chunk: "a" });
  def = nextDef;
  await settle();
  assert.equal(calls, 2);
  handle.cancel();
});

test("slow response is not written twice and the offset advances", async () => {
  const offsets: number[] = [];
  const rec = recorder();
  let callCount = 0;
  startPoll(
    async (offset) => {
      offsets.push(offset);
      callCount += 1;
      if (callCount === 1) {
        await settle(10);
        return { offset: 5, chunk: "a" };
      }
      return { offset: 9, chunk: "b", done: true };
    },
    INTERVAL,
    rec.handlers,
  );
  await settle(40);
  assert.deepEqual(offsets, [0, 5]);
  assert.deepEqual(rec.events, ["snapshot:a", "snapshot:b", "done"]);
});

test("done stops polling", async () => {
  let calls = 0;
  const rec = recorder();
  const handle = startPoll(
    async () => {
      calls += 1;
      return { done: true, chunk: "end" };
    },
    INTERVAL,
    rec.handlers,
  );
  await settle();
  assert.equal(calls, 1);
  assert.deepEqual(rec.events, ["snapshot:end", "done"]);
  const callsAfterDone = calls;
  await settle();
  assert.equal(calls, callsAfterDone);
  handle.cancel();
  assert.deepEqual(rec.events, ["snapshot:end", "done"]);
});

test("one failure then success keeps polling and resets the count", async () => {
  let step = 0;
  const rec = recorder();
  startPoll(
    async () => {
      step += 1;
      if (step <= MAX_CONSECUTIVE_FAILURES - 1) {
        throw new Error("fail 1");
      }
      if (step === MAX_CONSECUTIVE_FAILURES) {
        return { chunk: "ok" };
      }
      if (step < MAX_CONSECUTIVE_FAILURES + MAX_CONSECUTIVE_FAILURES) {
        throw new Error("fail 2");
      }
      return { chunk: "finish", done: true };
    },
    INTERVAL,
    rec.handlers,
  );
  await settle(50);
  assert.ok(!rec.events.includes("lost"));
  assert.equal(rec.events[rec.events.length - 1], "done");
  assert.deepEqual(rec.events, ["snapshot:ok", "snapshot:finish", "done"]);
});

test("consecutive failures report lost once and stop", async () => {
  let calls = 0;
  const rec = recorder();
  startPoll(
    async () => {
      calls += 1;
      throw new Error("network error");
    },
    INTERVAL,
    rec.handlers,
  );
  await settle(60);
  assert.deepEqual(rec.events, ["lost"]);
  assert.equal(calls, MAX_CONSECUTIVE_FAILURES);
});

test("invalid snapshot counts as a failure", async () => {
  const rec = recorder();
  let calls = 0;
  startPoll(
    async () => {
      calls += 1;
      return null as any;
    },
    INTERVAL,
    rec.handlers,
  );
  await settle(60);
  assert.deepEqual(rec.events, ["lost"]);
  assert.equal(calls, MAX_CONSECUTIVE_FAILURES);
});

test("cancel during a request ignores its response", async () => {
  const def = deferred<{ chunk?: string; done?: boolean }>();
  const rec = recorder();
  const handle = startPoll(
    async () => def.promise,
    INTERVAL,
    rec.handlers,
  );
  await settle();
  handle.cancel();
  def.resolve({ chunk: "late", done: true });
  await settle();
  assert.deepEqual(rec.events, ["cancel"]);
});

test("cancel during a failing request ignores the failure", async () => {
  const def = deferred<{ chunk?: string }>();
  const rec = recorder();
  const handle = startPoll(
    async () => def.promise,
    INTERVAL,
    rec.handlers,
  );
  await settle();
  handle.cancel();
  def.reject(new Error("late failure"));
  await settle();
  assert.deepEqual(rec.events, ["cancel"]);
});

test("cancel between requests stops the timer", async () => {
  let calls = 0;
  let def = deferred<{ chunk?: string }>();
  const rec = recorder();
  const handle = startPoll(
    async () => {
      calls += 1;
      return def.promise;
    },
    20,
    rec.handlers,
  );
  await settle(5);
  def.resolve({ chunk: "one" });
  await settle(5);
  handle.cancel();
  const callsAtCancel = calls;
  await settle(30);
  assert.equal(calls, callsAtCancel);
  assert.deepEqual(rec.events, ["snapshot:one", "cancel"]);
});

test("a second cancel is a no-op", async () => {
  const rec = recorder();
  const handle = startPoll(
    async () => ({ chunk: "a" }),
    INTERVAL,
    rec.handlers,
  );
  handle.cancel();
  handle.cancel();
  assert.deepEqual(rec.events, ["cancel"]);
});

test("a throwing onSnapshot reports lost and stops", async () => {
  let calls = 0;
  const events: string[] = [];
  startPoll(
    async () => {
      calls += 1;
      return { chunk: "boom" };
    },
    INTERVAL,
    {
      onSnapshot: () => {
        events.push("snapshot");
        throw new Error("handler explosion");
      },
      onDone: () => { events.push("done"); },
      onLost: () => { events.push("lost"); },
      onCancel: () => { events.push("cancel"); },
    },
  );
  await settle();
  assert.equal(calls, 1);
  assert.deepEqual(events, ["snapshot", "lost"]);
  await settle();
  assert.equal(calls, 1);
});
