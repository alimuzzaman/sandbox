// Promise-based modal supporting text / select / checkbox / checklist / label
// fields. Resolves with a values object (or null on cancel).

import { $, esc } from "../dom";
import { csel, nextCselId, type CselOpt } from "./csel";
import type {
  ModalOpts, ModalResult, ModalField, ChecklistOption,
} from "../types";

let resolveFn: ((v: ModalResult) => void) | null = null;

function renderField(f: ModalField): string {
  if (f.type === "label") {
    return `<div class="text-[11px] font-medium uppercase tracking-wide
      text-neutral-400 pt-1.5">${esc(f.label || "")}</div>`;
  }
  if (f.type === "select") {
    const id = nextCselId();
    const options = (f.options || []) as string[];
    const optobjs: CselOpt[] = options.map((o) => ({ v: o, label: o }));
    const val = (f.value as string) || options[0] || "";
    // Hidden input carries the value for modalValues(); the csel pick callback
    // writes the chosen value into it.
    return `<input type="hidden" data-k="${f.key}" id="${id}_val" value="${esc(val)}">`
      + csel(id, optobjs, val, (v) => {
          (document.getElementById(`${id}_val`) as HTMLInputElement).value = v;
        }, false, true);
  }
  if (f.type === "checkbox") {
    return `<label class="flex items-center gap-2 text-[13px] text-neutral-700
      dark:text-neutral-300 cursor-pointer select-none">
      <input type="checkbox" data-k="${f.key}" data-type="checkbox"
        ${f.value ? "checked" : ""} class="accent-accent w-3.5 h-3.5">
      ${esc(f.label || f.key || "")}</label>`;
  }
  if (f.type === "checklist") {
    const opts = (f.options || []) as ChecklistOption[];
    const items = opts.map((o) => `
      <label class="flex items-start gap-2 px-2 py-1.5 rounded text-[13px] cursor-pointer
        hover:bg-neutral-100 dark:hover:bg-neutral-800">
        <input type="checkbox" data-checklist="${f.key}" value="${esc(o.value)}"
          class="accent-accent w-3.5 h-3.5 mt-0.5">
        <span class="flex-1 min-w-0">
          <span class="text-neutral-800 dark:text-neutral-200">${esc(o.label)}</span>
          ${o.desc ? `<span class="block text-[11.5px] text-neutral-400 truncate">${esc(o.desc)}</span>` : ""}
        </span></label>`).join("");
    return `<div data-checklist-group="${f.key}"
      class="flex flex-col gap-0.5 max-h-44 overflow-y-auto rounded border
      border-brdin dark:border-neutral-700 p-1">${items
      || `<div class="text-[12px] text-neutral-400 px-2 py-1">none available</div>`}</div>`;
  }
  const oninput = f.oninput ? ` oninput="${esc(f.oninput)}"` : "";
  return `<input data-k="${f.key}" data-field="${esc(f.key || "")}"${oninput}
    placeholder="${esc(f.placeholder || "")}" value="${esc((f.value as string) || "")}"
    class="w-full px-3 py-1.5 rounded border border-brdin dark:border-neutral-700 bg-app
    dark:bg-neutral-900 text-[13px] focus:border-accent outline-none">`;
}

export function modal(opts: ModalOpts = {}): Promise<ModalResult> {
  return new Promise((res) => {
    resolveFn = res;
    $("mTitle").textContent = opts.title || "";
    $("mDesc").textContent = opts.desc || "";
    $("mFields").innerHTML = (opts.fields || []).map(renderField).join("");
    const ok = $("mOk");
    ok.textContent = opts.okText || "Confirm";
    ok.className = "px-3 py-1.5 rounded text-[13px] text-white border " +
      (opts.danger ? "bg-red-600 border-red-600 hover:bg-red-700"
                   : "bg-accent border-accent hover:bg-blue-700");
    $("modal").classList.remove("hidden");
    setTimeout(() => {
      const f = $("mFields").querySelector("input,select") as HTMLElement | null;
      (f || ok).focus();
    }, 30);
  });
}

export function closeModal(val: ModalResult): void {
  $("modal").classList.add("hidden");
  if (resolveFn) { const r = resolveFn; resolveFn = null; r(val); }
}

export function modalValues(): ModalResult {
  const o: Record<string, string | boolean | string[]> = {};
  $("mFields").querySelectorAll<HTMLInputElement>("[data-k]").forEach((e) => {
    if (e.dataset.type === "checkbox") o[e.dataset.k!] = e.checked;
    else o[e.dataset.k!] = (e.value || "").trim();
  });
  $("mFields").querySelectorAll<HTMLElement>("[data-checklist-group]").forEach((g) => {
    const key = g.dataset.checklistGroup!;
    o[key] = [...g.querySelectorAll<HTMLInputElement>("input[type=checkbox]:checked")]
      .map((c) => c.value);
  });
  return o;
}

export function initModal(): void {
  ($("mCancel") as HTMLButtonElement).onclick = () => closeModal(null);
  ($("mOk") as HTMLButtonElement).onclick = () => closeModal(modalValues());
  $("modal").addEventListener("keydown", (e) => {
    if ((e as KeyboardEvent).key === "Enter") ($("mOk") as HTMLButtonElement).click();
    if ((e as KeyboardEvent).key === "Escape") closeModal(null);
  });
  $("modal").addEventListener("click", (e) => {
    if (e.target === $("modal")) closeModal(null);
  });
}
