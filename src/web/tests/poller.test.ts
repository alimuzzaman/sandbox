import { test } from "node:test";
import assert from "node:assert/strict";
import { jobFlags, startPoll, MAX_CONSECUTIVE_FAILURES } from "../src/ui/poller.ts";

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
    },
  };
}

test("only one request is in flight", async () => {
  let calls = 0;
  let def = deferred<{ offset?: number; chunk?: string; done?: boolean }>();
  const rec = recorder();
  startPoll(
    async () => {
      calls += 1;
      return def.promise;
    },
    INTERVAL,
    rec.handlers,
  );
  while (calls === 0) await settle(1);
  assert.equal(calls, 1);
  const nextDef = deferred<{ offset?: number; chunk?: string; done?: boolean }>();
  def.resolve({ offset: 1, chunk: "a" });
  def = nextDef;
  while (calls === 1) await settle(1);
  assert.equal(calls, 2);
  def.resolve({ done: true });
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
  startPoll(
    async () => {
      calls += 1;
      return { done: true, chunk: "end" };
    },
    INTERVAL,
    rec.handlers,
  );
  while (calls === 0) await settle(1);
  assert.equal(calls, 1);
  assert.deepEqual(rec.events, ["snapshot:end", "done"]);
  const callsAfterDone = calls;
  await settle();
  assert.equal(calls, callsAfterDone);
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

test("lost is reported once even with a late success impossible", async () => {
  let calls = 0;
  const rec = recorder();
  startPoll(
    async () => {
      calls += 1;
      throw new Error("always reject");
    },
    INTERVAL,
    rec.handlers,
  );
  await settle(60);
  assert.deepEqual(rec.events, ["lost"]);
  assert.equal(calls, MAX_CONSECUTIVE_FAILURES);
  const callsAtLost = calls;
  await settle(20);
  assert.equal(calls, callsAtLost);
  assert.deepEqual(rec.events, ["lost"]);
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
    },
  );
  while (calls === 0) await settle(1);
  assert.equal(calls, 1);
  assert.deepEqual(events, ["snapshot", "lost"]);
  await settle();
  assert.equal(calls, 1);
});

test("jobFlags pauses until the last job ends", () => {
  const store = { paused: false, busy: {} };
  const flags = jobFlags(store);
  const first = flags.begin(null);
  assert.equal(store.paused, true);
  const second = flags.begin(null);
  assert.equal(store.paused, true);
  first();
  assert.equal(store.paused, true);
  second();
  assert.equal(store.paused, false);
});

test("jobFlags clears busy only for the newest job on an instance", () => {
  const store = { paused: false, busy: {} as Record<string, unknown> };
  const flags = jobFlags(store);
  store.busy.a = "start";
  const first = flags.begin("a");
  store.busy.a = "restart";
  const second = flags.begin("a");
  first();
  assert.equal(store.busy.a, "restart");
  second();
  assert.equal("a" in store.busy, false);
});

test("jobFlags keeps other instances", () => {
  const store = { paused: false, busy: {} as Record<string, unknown> };
  const flags = jobFlags(store);
  store.busy.a = "work_a";
  store.busy.b = "work_b";
  const relA = flags.begin("a");
  const relB = flags.begin("b");
  relA();
  assert.equal("a" in store.busy, false);
  assert.equal(store.busy.b, "work_b");
  relB();
  assert.equal("b" in store.busy, false);
});

test("jobFlags release is idempotent", () => {
  const store = { paused: false, busy: {} as Record<string, unknown> };
  const flags = jobFlags(store);
  const first = flags.begin(null);
  const second = flags.begin(null);
  first();
  first();
  assert.equal(store.paused, true);
  second();
  assert.equal(store.paused, false);
});
