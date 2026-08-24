const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const EXTERNAL_PROTOCOLS = new Set(["https:", "http:"]);
export const MAX_BACKEND_RESPONSE_BYTES = 32 * 1024 * 1024;
export const MAX_BACKEND_REQUEST_BYTES = 4 * 1024 * 1024;

export function parseDashboardUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("Sandbox dashboard URL must be a valid absolute URL");
  }
  if (url.protocol !== "http:" || !LOOPBACK_HOSTS.has(url.hostname)) {
    throw new Error("Sandbox desktop only connects to a loopback HTTP dashboard");
  }
  if (url.username || url.password) {
    throw new Error("Sandbox dashboard URL must not contain credentials");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Sandbox dashboard URL must contain only an origin");
  }
  return url;
}

export function parseExternalUrl(raw: string): URL | null {
  if (raw.length > 2048) return null;
  try {
    const url = new URL(raw);
    if (!EXTERNAL_PROTOCOLS.has(url.protocol) || url.username || url.password) return null;
    return url;
  } catch {
    return null;
  }
}

export function isSameDashboardOrigin(candidate: string, dashboard: URL): boolean {
  try {
    return new URL(candidate).origin === dashboard.origin;
  } catch {
    return false;
  }
}

export function isRecoveryUrl(candidate: string): boolean {
  try {
    const url = new URL(candidate);
    return url.protocol === "sandbox-app:" && url.hostname === "app" && url.pathname === "/recovery.html";
  } catch {
    return false;
  }
}
