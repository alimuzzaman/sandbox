// Rows for a stack's declared facts (template `stack.facts`). No imports and
// no DOM, so `node --test` covers it; pages/stack.ts escapes and renders.

export interface StackFactLike {
  label: string;
  kind: "url" | "text";
  value: string;
  page_path: string | null;
}

export interface FactRow {
  label: string;
  text: string;
  // An https address to open, or null for text facts and anything else.
  href: string | null;
  // The panel page of the stack that serves this address, when one does.
  stackPath: string | null;
}

export function factRows(facts: StackFactLike[] | undefined): FactRow[] {
  return (facts || []).map(fact => ({
    label: fact.label,
    text: fact.value,
    href: fact.kind === "url" && /^https:\/\/[^\s"<>]+$/.test(fact.value) ? fact.value : null,
    stackPath: fact.kind === "url" && typeof fact.page_path === "string" && fact.page_path.startsWith("/hosting/")
      ? fact.page_path : null,
  }));
}
