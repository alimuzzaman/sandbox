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

export interface RemoteSummary { name: string; provisioned: boolean; control_ready: boolean; local?: boolean }
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
  hosted_targets?: HostedTargets;
  storage?: { status: string; attribution_status: string; capacity?: { total_bytes?: number; used_bytes?: number; available_bytes?: number } | null; category_outcomes?: Array<Record<string, unknown>> };
}

// `sb host apply` targets on a host, with their containers (spec 076).
export interface HostedTargetUsage {
  key: string; project: string; environment: string; compose_project: string;
  stack_slug: string | null; recorded: boolean; last_apply_status: string | null;
  // Follow-up 2026-11-10; absent from hosts whose runtime predates it.
  last_apply_reason?: string | null; lifetime?: LifetimeView;
  attribution_status: string; container_count: number; running: boolean;
  memory_used_bytes: number; cpu_percent: number;
}
export interface HostedTargets { total: number; running: number; rows: HostedTargetUsage[] }

// GET /api/hosting: stacks and hosted sites (spec 076). Read-only.
export type HealthStatus = "checking" | "up" | "up_login" | "redirect" | "client_error" |
  "server_error" | "unreachable" | "not_checked";
export interface HostingHealth { status: HealthStatus; http_status: number | null; checked_at: number | null }
export interface HostingServer {
  hostname: string;
  url: string | null;
  origin: "route" | "linked" | "matched_by_name";
  mode: "serve" | "redirect";
  target: string | null;
  primary: boolean;
  state: "serving" | "redirecting" | "applying" | "failed" | "instance_not_found" | "unknown";
  ref: { branch: string | null; commit: string | null };
  instance: string | null;
  health: HostingHealth;
}
export type ApplyStatus = "applying" | "succeeded" | "unverified" | "failed";
export interface HostingApply { status: ApplyStatus | string; started_at: number | null; finished_at: number | null; request_id: string | null; reason?: string | null }

// One spec 077 lifetime record as the panel shows it. Anything that is not a
// valid record is "unclassified" (protected) or "unknown"; neither is reapable.
export interface LifetimeView {
  classification: "recorded" | "unclassified" | "unknown";
  reason: "no_record" | "invalid" | "unavailable" | "loading" | null;
  lifetime: "temporary" | "hosted" | "permanent" | null;
  owner: string | null;
  expires_at: number | null;
  seconds_left: number | null;
  canonical: boolean;
  reap_eligible: boolean;
  reap_reason: "expired" | "merged" | null;
  state: "active" | "reaping" | "reaped" | null;
  extended_count: number;
  kind: "stack" | "target" | null;
  slug: string | null; copy_id: string | null; ref_branch: string | null; commit: string | null;
}
export interface LifetimeRow extends LifetimeView {
  remote: string; source: "this-server" | "controller";
  project: string; environment: string; compose_project: string;
  name: string; card: boolean;
}
export type MailAccess =
  | { status: "ready"; url: string; upstream: string }
  | { status: "unavailable"; upstream: string | null; reason:
      "no_port" | "not_same_server" | "inventory_unavailable" | "inventory_partial" |
      "not_found" | "not_ready" | "ambiguous" };

export type RevisionEvidence = "verified" | "unverified" | "not_delivered";
export interface StackPageAccess {
  uses: string[];
  inbox: { url: string; hostname: string } | null;
  inbox_reason: string | null;
  revision_status: RevisionEvidence;
  sign_in: { available: boolean; code: string; message: string };
}
export interface HostingStack {
  key: string; remote: string; source: "this-server" | "controller";
  slug: string; project: string; environment: string;
  ref: string; ref_branch: string; commit: string; template: string;
  ports: Record<string, number>; mailpit_port: number | null; loopback_port: number | null;
  mail_access: MailAccess;
  created_at: number | null; ttl_expires_at: number | null;
  last_apply: HostingApply;
  compose_project: string; lifetime: LifetimeView;
  page_access?: StackPageAccess;
  servers: HostingServer[];
}
export interface HostingTarget {
  key: string; remote: string; source: "this-server" | "controller";
  project: string; environment: string; commit: string | null; loopback_port: number | null;
  last_apply: HostingApply | null; recorded: boolean;
  compose_project: string; lifetime: LifetimeView; stack_slug: string | null;
  servers: HostingServer[];
}
export interface HostingView {
  ok: boolean; generated_at: number;
  sources: Array<"this-server" | "controller">;
  stacks: HostingStack[]; targets: HostingTarget[];
  lifetimes: LifetimeRow[];
  partial_reasons: string[];
}

// POST /api/hosting/share (spec 081 follow-up). A `ShareCreated.link` is a
// login-bypass secret: it lives only in the dialog that shows it, never in `store`.
export interface ShareCreated {
  ok: true; link: string; link_id: string; target: string; entry: string; kind: string;
  scope: string; expires_at: number; expires_clamped: boolean; uses: number | null;
  hostname: string; notes?: string[]; attempt_id?: string; phase?: SharePhase;
}
export interface ShareLinkRow { id: string; kind: string; scope: string; issued_at: number; expires_at: number; uses: number | null; used: number }
export interface ShareSessionRow { id: string; link: string; created_at: number; expires_at: number }
export type SharePhase = "prepared" | "minting" | "active" | "revoking" | "cleanup_pending" | "revoked" | "expired";
export interface ShareGap { stack: string; target: string; hostname?: string; piece: string; code: string; fix: string }
export interface ShareGrant { target: string; entry: string; id: string; expires_at: number; status: "live" | "unknown"; reason_code: string }
export interface ShareAttempt { attempt_id: string; phase: SharePhase; link_id: string; expires_at: number; still_live?: ShareGrant[] }
export interface SharePartial { ok: false; attempt_id?: string; phase: "cleanup_pending"; still_live: ShareGrant[]; code: string; message: string }
export interface ShareList { ok: boolean; target?: string; entry?: string; links: ShareLinkRow[]; sessions: ShareSessionRow[]; attempts: ShareAttempt[]; partial_reasons?: string[] }
export interface ShareRevoked { ok: true; target: string; entry: string; revoked_links: number; revoked_sessions: number; revoked_keys: number }
export interface ShareRefused { ok: false; code: string; message: string; gaps?: ShareGap[] }

export interface ActionResult {
  ok?: boolean;
  output?: string;
  job_id?: string;
}

export interface JobSnapshot {
  job_id?: string;
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
  refreshHosting(): void;
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
  shareStack(button: HTMLElement): void;
  shareLinks(button: HTMLElement): void;
  signInStack(button: HTMLButtonElement): void;
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
