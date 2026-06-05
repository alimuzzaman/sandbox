import { $ } from "../dom";

type ToastType = "ok" | "err" | "info";

const COLORS: Record<ToastType, string> = {
  ok: "bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:border-emerald-800",
  err: "bg-red-50 text-red-800 border-red-200 dark:bg-red-950/70 dark:text-red-200 dark:border-red-900",
  info: "bg-app text-neutral-700 border-brd dark:bg-card-dark dark:text-neutral-200 dark:border-brd-dark",
};

export function toast(msg: string, type: ToastType = "info"): void {
  const el = document.createElement("div");
  el.className =
    "pointer-events-auto text-[13px] px-3.5 py-2 rounded-lg border shadow-md max-w-xs " +
    (COLORS[type] || COLORS.info);
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 220); }, 2600);
}
