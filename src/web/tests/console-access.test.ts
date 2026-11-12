import { test } from "node:test";
import assert from "node:assert/strict";
import { setConsoleExpanded, setConsoleCovered } from "../src/ui/console-access.ts";

function panel(): HTMLElement {
  const classes = new Set(["w-0"]);
  return { inert: false, classList: {
    contains: (value: string) => classes.has(value),
    toggle: (value: string, force: boolean) => {
      if (force) classes.add(value); else classes.delete(value);
    },
  } } as unknown as HTMLElement;
}

test("initial navigation setup does not enable a collapsed console", () => {
  const value = panel();
  setConsoleExpanded(value, false);
  setConsoleCovered(value, false);
  assert.equal(value.inert, true);
});

test("menu dismissal preserves collapsed console restriction", () => {
  const value = panel();
  setConsoleCovered(value, true);
  setConsoleCovered(value, false);
  assert.equal(value.inert, true);
});

test("menu dismissal restores an already expanded console", () => {
  const value = panel();
  setConsoleExpanded(value, true);
  assert.equal(value.inert, false);
  setConsoleCovered(value, true);
  assert.equal(value.inert, true);
  setConsoleCovered(value, false);
  assert.equal(value.inert, false);
});

test("opening a console while covered cannot bypass menu restriction", () => {
  const value = panel();
  setConsoleCovered(value, true);
  setConsoleExpanded(value, true);
  assert.equal(value.inert, true);
  setConsoleCovered(value, false);
  assert.equal(value.inert, false);
});

test("closing a covered console remains restricted after menu dismissal", () => {
  const value = panel();
  setConsoleExpanded(value, true);
  setConsoleCovered(value, true);
  setConsoleExpanded(value, false);
  setConsoleCovered(value, false);
  assert.equal(value.inert, true);
  assert.equal(value.classList.contains("w-0"), true);
  assert.equal(value.classList.contains("w-[26rem]"), false);
});
