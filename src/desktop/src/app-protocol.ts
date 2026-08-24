import { protocol } from "electron";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const CONTENT_TYPES: Readonly<Record<string, string>> = Object.freeze({
  "/recovery.html": "text/html; charset=utf-8",
  "/recovery.css": "text/css; charset=utf-8",
  "/recovery.js": "text/javascript; charset=utf-8",
  "/icon.svg": "image/svg+xml",
});

export const RECOVERY_URL = "sandbox-app://app/recovery.html";

export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([{
    scheme: "sandbox-app",
    privileges: { standard: true, secure: true, supportFetchAPI: false, corsEnabled: false },
  }]);
}

export function installAppProtocol(appRoot: string): void {
  protocol.handle("sandbox-app", async (request) => {
    const url = new URL(request.url);
    const contentType = url.hostname === "app" ? CONTENT_TYPES[url.pathname] : undefined;
    if (!contentType || request.method !== "GET") return new Response("Not found", { status: 404 });
    const asset = await readFile(join(appRoot, "assets", url.pathname.slice(1)));
    return new Response(asset, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  });
}
