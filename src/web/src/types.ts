// Shapes returned by the Python /api endpoints + the client app store.

export interface Instance {
  name: string;
  running: boolean;
  pending?: boolean;
  server: string;
  url: string;
  admin_url?: string;
  login_url?: string;
  mcp_server: string;
  project: string;
  focus: string;
  domain?: string;
  wordpress_port: number | string;
  mailpit_port: number | string;
}

export interface AppData {
  instances: Instance[];
  plugins: string[];
  seeds: string[];
  servers: string[];
  domains_ready?: boolean;
  remotes: RemoteSummary[];
}

export interface RemoteSummary { name: string; provisioned: boolean; control_ready: boolean }
export interface ResourceRow { name?: string; pid?: number; cpu_percent?: number; rss_bytes?: number; memory_used_bytes?: number; memory_percent?: number; pids?: number; process_count?: number; container_count?: number; attribution_status?: string }
export interface RemoteInventory {
  ok: boolean; name?: string; inventory_schema: number; transport: "control";
  evidence_status: "complete" | "partial" | "unavailable";
  scan_mode?: "fast" | "deep";
  partial_reasons: string[];
  instances?: { total: number; running: number; stopped: number; rows: Array<{ name: string; running: boolean; server: string; project: string; label: string }> };
  host?: { memory_total_mb?: number | null; memory_used_mb?: number | null; memory_used_percent?: number | null; load_1m?: number | null; disk_total_bytes?: number | null; disk_used_bytes?: number | null; disk_free_bytes?: number | null };
  jobs?: { total?: number | null; active?: number | null; queued?: number | null; by_lifecycle?: Record<string, number> };
  process_view?: { status: string; processes?: ResourceRow[]; apps?: ResourceRow[]; limitations?: string[] };
  containers?: { status: string; rows?: ResourceRow[] };
  per_instance_usage?: Array<{ name: string; attribution_status: string; container_count: number; memory_used_bytes: number; cpu_percent: number }>;
  unattributed_containers?: ResourceRow[];
  storage?: { status: string; attribution_status: string; capacity?: { total_bytes?: number; used_bytes?: number; available_bytes?: number } | null; category_outcomes?: Array<Record<string, unknown>> };
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
  selectRemote(name: string): void;
  refreshRemote(name: string, deep?: boolean): void;
  refreshHosts(): void;
  submitCreate(): void;
  showUsage(): void;
  showHelp(): void;
  openTerminal(name: string): void;
  doCreate(): void;
  doDelete(name: string): void;
  doFocus(name: string, slug: string): void;
  doServer(name: string, server: string): void;
  doSnapshot(name: string): void;
  doRestore(name: string): void;
  doSeed(name: string): void;
  doWp(name: string): void;
  doInstall(name: string): void;
  plugFilter(): void;
  loadUsageThenRender(): void;
  act(instance: string, action: string, extra?: Record<string, unknown>): void;
  op(name: string, action: string, extra?: Record<string, unknown>): void;
  cselToggle(id: string): void;
  cselPick(id: string, v: string): void;
  cselFilter(id: string): void;
  rowMenuToggle(id: string): void;
  rowMenuClose(): void;
  consoleClose(): void;
  copyText(t: string, btn: HTMLElement): void;
}

export interface SandboxDesktopApi {
  readonly platform: "darwin";
  chooseProjectDirectory(): Promise<string | null>;
}

declare global {
  interface Window {
    sb: SbApi;
    sandboxDesktop?: SandboxDesktopApi;
  }
}
