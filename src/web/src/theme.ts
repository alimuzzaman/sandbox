// Semantic Tailwind recipes shared by the host/resource views. Keeping these
// decisions in one place prevents each card from inventing its own contrast,
// spacing, and focus treatment while retaining the vendored Tailwind build.
export const theme = {
  page: "min-h-full bg-neutral-50 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-50",
  shell: "max-w-7xl mx-auto px-4 py-5 sm:px-6 sm:py-7 lg:px-8",
  panel: "rounded-xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-sm",
  inset: "rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950",
  muted: "text-neutral-600 dark:text-neutral-300",
  quiet: "text-neutral-500 dark:text-neutral-400",
  label: "text-[11px] font-semibold uppercase tracking-[0.12em] text-neutral-500 dark:text-neutral-400",
  button: "inline-flex min-h-9 items-center justify-center rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 px-3 py-2 text-[12px] font-medium text-neutral-800 dark:text-neutral-100 hover:bg-neutral-50 dark:hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950",
  primary: "inline-flex min-h-9 items-center justify-center rounded-lg bg-blue-700 px-3 py-2 text-[12px] font-semibold text-white hover:bg-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-neutral-950",
};
