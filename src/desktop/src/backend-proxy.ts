import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { createServer, request as httpRequest, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { type AddressInfo } from "node:net";
import { MAX_BACKEND_REQUEST_BYTES, MAX_BACKEND_RESPONSE_BYTES } from "./security";

const HOP_HEADERS = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "cookie", "set-cookie"]);
const PROXY_COOKIE = "sandbox_desktop";

export interface BackendHandshake {
  readonly protocol: 1;
  readonly instances: number;
  readonly remotes: number;
}

export interface AuthenticatedProxy {
  readonly origin: URL;
  readonly cookieName: string;
  readonly cookieValue: string;
  close(): Promise<void>;
}

function boundedBody(request: IncomingMessage, maximum: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    request.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > maximum) {
        request.destroy();
        reject(new Error("body_too_large"));
      } else chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks)));
    request.on("error", reject);
  });
}

function requestBackend(target: URL, path: string, method: string, headers: Record<string, string>, body: Buffer, timeoutMs: number): Promise<{ status: number; headers: IncomingMessage["headers"]; body: Buffer }> {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ protocol: "http:", hostname: target.hostname, port: target.port, path, method, headers, timeout: timeoutMs }, (response) => {
      boundedBody(response, MAX_BACKEND_RESPONSE_BYTES).then((responseBody) => {
        resolve({ status: response.statusCode ?? 502, headers: response.headers, body: responseBody });
      }, reject);
    });
    req.on("timeout", () => req.destroy(new Error("backend_timeout")));
    req.on("error", reject);
    req.end(body);
  });
}

export async function handshakeBackend(target: URL, timeoutMs = 5_000): Promise<BackendHandshake> {
  const response = await requestBackend(target, "/api/instances", "GET", { Accept: "application/json", "User-Agent": "SandboxDesktop/1" }, Buffer.alloc(0), timeoutMs);
  if (response.status !== 200) throw new Error(`backend_status_${response.status}`);
  if (!String(response.headers["content-type"] ?? "").startsWith("application/json")) throw new Error("backend_content_type");
  let payload: unknown;
  try { payload = JSON.parse(response.body.toString("utf8")); } catch { throw new Error("backend_invalid_json"); }
  if (!payload || typeof payload !== "object") throw new Error("backend_protocol_mismatch");
  const record = payload as Record<string, unknown>;
  if (!Array.isArray(record.instances) || !Array.isArray(record.remotes)) throw new Error("backend_protocol_mismatch");
  return { protocol: 1, instances: record.instances.length, remotes: record.remotes.length };
}

function cspHash(value: string): string {
  return `'sha256-${createHash("sha256").update(value).digest("base64")}'`;
}

function dashboardCsp(body: Buffer): string {
  const html = body.toString("utf8");
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => cspHash(match[1] ?? ""));
  const styles = [...html.matchAll(/<style(?:\s[^>]*)?>([\s\S]*?)<\/style>/gi)].map((match) => cspHash(match[1] ?? ""));
  const handlers = [...html.matchAll(/\son[a-z]+\s*=\s*(?:"([^"]*)"|'([^']*)')/gi)].map((match) => cspHash(match[1] ?? match[2] ?? ""));
  return [
    "default-src 'none'", "script-src 'self'", `script-src-elem 'self' ${scripts.join(" ")}`,
    `script-src-attr 'unsafe-hashes' ${handlers.join(" ")}`, "style-src 'self'",
    `style-src-elem 'self' ${styles.join(" ")}`, "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:", "font-src 'self' data:", "connect-src 'self'", "object-src 'none'",
    "base-uri 'none'", "frame-ancestors 'none'", "form-action 'self'",
  ].join("; ");
}

function authorized(request: IncomingMessage, secret: Buffer): boolean {
  const cookie = String(request.headers.cookie ?? "").split(";").map((item) => item.trim()).find((item) => item.startsWith(`${PROXY_COOKIE}=`));
  if (!cookie) return false;
  const candidate = Buffer.from(cookie.slice(PROXY_COOKIE.length + 1), "base64url");
  return candidate.length === secret.length && timingSafeEqual(candidate, secret);
}

function sendError(response: ServerResponse, status: number, message: string): void {
  const body = Buffer.from(JSON.stringify({ error: message }));
  response.writeHead(status, { "Content-Type": "application/json", "Content-Length": body.length, "Cache-Control": "no-store" });
  response.end(body);
}

export async function createAuthenticatedProxy(target: URL): Promise<AuthenticatedProxy> {
  const secret = randomBytes(32);
  const server: Server = createServer(async (request, response) => {
    if (!authorized(request, secret)) return sendError(response, 401, "unauthorized");
    if (!request.url?.startsWith("/")) return sendError(response, 400, "invalid_request");
    try {
      const body = await boundedBody(request, MAX_BACKEND_REQUEST_BYTES);
      const headers: Record<string, string> = {};
      for (const [name, value] of Object.entries(request.headers)) {
        if (!HOP_HEADERS.has(name) && value !== undefined) headers[name] = Array.isArray(value) ? value.join(", ") : value;
      }
      headers.host = target.host;
      const upstream = await requestBackend(target, request.url, request.method ?? "GET", headers, body, 30_000);
      const outbound: Record<string, string | string[]> = {};
      for (const [name, value] of Object.entries(upstream.headers)) {
        if (!HOP_HEADERS.has(name) && value !== undefined && name !== "content-length" && name !== "content-security-policy") outbound[name] = value;
      }
      outbound["content-length"] = String(upstream.body.length);
      outbound["x-content-type-options"] = "nosniff";
      outbound["referrer-policy"] = "no-referrer";
      if (String(upstream.headers["content-type"] ?? "").startsWith("text/html")) outbound["content-security-policy"] = dashboardCsp(upstream.body);
      response.writeHead(upstream.status, outbound);
      response.end(upstream.body);
    } catch (error) {
      sendError(response, 502, error instanceof Error ? error.message : "backend_unavailable");
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address() as AddressInfo;
  return {
    origin: new URL(`http://127.0.0.1:${address.port}/`), cookieName: PROXY_COOKIE,
    cookieValue: secret.toString("base64url"),
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}
