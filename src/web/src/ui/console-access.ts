// Visibility and temporary navigation coverage independently restrict focus.
const covered = new WeakMap<HTMLElement, boolean>();

function sync(panel: HTMLElement): void {
  panel.inert = panel.classList.contains("w-0") || covered.get(panel) === true;
}

export function setConsoleExpanded(panel: HTMLElement, expanded: boolean): void {
  panel.classList.toggle("w-0", !expanded);
  panel.classList.toggle("w-[26rem]", expanded);
  sync(panel);
}

export function setConsoleCovered(panel: HTMLElement, value: boolean): void {
  covered.set(panel, value);
  sync(panel);
}
