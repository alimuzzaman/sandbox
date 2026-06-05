// Shapes returned by the Python /api endpoints + the client app store.

export interface Instance {
  name: string;
  running: boolean;
  pending?: boolean;
  server: string;
  url: string;
  mcp_server: string;
  project: string;
  focus: string;
  domain?: string;
  wordpress_port: number | string;
  mailpit_port: number | string;
}

export interface Project {
  name: string;
  description: string;
  plugins: string[];
}

export interface AppData {
  instances: Instance[];
  plugins: string[];
  projects: Project[];
  seeds: string[];
  servers: string[];
  domains_ready?: boolean;
}

export interface ActionResult {
  ok?: boolean;
  output?: string;
  job_id?: string;
}

export interface JobSnapshot {
  status?: string;
  chunk?: string;
  offset?: number;
  done?: boolean;
  ok?: boolean | null;
}

// Claude usage payload.
export interface TokenBucket { in?: number; out?: number; cw?: number; cr?: number; cost?: number }
export interface UsageSession { id: string; model: string; instances?: string[]; tokens: number; cost: number }
export interface Usage {
  available: boolean;
  tokens?: number;
  cost?: number;
  total?: TokenBucket;
  by_model?: Record<string, TokenBucket>;
  per_instance?: Record<string, TokenBucket>;
  sessions?: UsageSession[];
}

// Modal field descriptors.
export type ModalFieldType = "text" | "select" | "checkbox" | "checklist" | "label";
export interface ChecklistOption { value: string; label: string; desc?: string }
export interface ModalField {
  key?: string;
  type?: ModalFieldType;
  label?: string;
  placeholder?: string;
  value?: string | boolean;
  options?: string[] | ChecklistOption[];
  oninput?: string;
}
export interface ModalOpts {
  title?: string;
  desc?: string;
  okText?: string;
  danger?: boolean;
  fields?: ModalField[];
}
export type ModalResult = Record<string, string | boolean | string[]> | null;

// The global handler surface exposed on window.sb for inline on* attributes.
export interface SbApi {
  navigate(path: string): void;
  goHome(): void;
  selectInstance(name: string): void;
  showUsage(): void;
  showHelp(): void;
  openTerminal(name: string): void;
  doCreate(): void;
  doDelete(name: string): void;
  doFocus(name: string, slug: string): void;
  doSnapshot(name: string): void;
  doRestore(name: string): void;
  doSeed(name: string): void;
  doWp(name: string): void;
  doInstall(name: string): void;
  plugFilter(): void;
  loadUsageThenRender(): void;
  act(instance: string, action: string, extra?: Record<string, unknown>): void;
  op(name: string, action: string, extra?: Record<string, unknown>): void;
  syncDomainFromName(el: HTMLInputElement): void;
  domainEdited(): void;
  cselToggle(id: string): void;
  cselPick(id: string, v: string): void;
  cselFilter(id: string): void;
  consoleClose(): void;
  copyText(t: string, btn: HTMLElement): void;
}

declare global {
  interface Window { sb: SbApi }
}
