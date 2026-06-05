// Tiny DOM helpers shared across modules.

export const $ = (id: string): HTMLElement => document.getElementById(id)!;

const ESC: Record<string, string> = {
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
};
export const esc = (s: unknown): string =>
  String(s).replace(/[&<>"]/g, (c) => ESC[c]);

export const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
