import {
  app, BrowserWindow, dialog, ipcMain, Menu, session, shell,
  type IpcMainInvokeEvent, type OpenDialogOptions,
} from "electron";
import { join } from "node:path";
import { installAppProtocol, RECOVERY_URL, registerAppScheme } from "./app-protocol";
import { createAuthenticatedProxy, handshakeBackend, type AuthenticatedProxy } from "./backend-proxy";
import { desktopChannels } from "./contracts";
import { isRecoveryUrl, isSameDashboardOrigin, parseDashboardUrl, parseExternalUrl } from "./security";

const DEFAULT_DASHBOARD_URL = "http://127.0.0.1:8765";
let mainWindow: BrowserWindow | null = null;
let proxy: AuthenticatedProxy | null = null;
let dashboardOrigin: URL | null = null;
let connecting = false;

registerAppScheme();
const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) app.quit();

function configuredBackend(): URL {
  return parseDashboardUrl(process.env.SANDBOX_DESKTOP_DEV_URL || process.env.SANDBOX_DESKTOP_URL || DEFAULT_DASHBOARD_URL);
}

function configuredBackendOrigin(): string | null {
  try {
    return configuredBackend().origin;
  } catch {
    return null;
  }
}

function installSecurityBoundary(): void {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
}

function isMainFrame(event: IpcMainInvokeEvent): boolean {
  return mainWindow !== null && event.senderFrame !== null
    && event.sender === mainWindow.webContents && event.senderFrame === mainWindow.webContents.mainFrame;
}

function isDashboardFrame(event: IpcMainInvokeEvent): boolean {
  return isMainFrame(event) && event.senderFrame !== null && dashboardOrigin !== null
    && isSameDashboardOrigin(event.senderFrame.url, dashboardOrigin);
}

function registerIpc(): void {
  ipcMain.handle(desktopChannels.chooseProjectDirectory, async (event) => {
    if (!isDashboardFrame(event)) throw new Error("Unauthorized desktop IPC caller");
    const options: OpenDialogOptions = { title: "Choose a Sandbox project", properties: ["openDirectory", "createDirectory"] };
    const result = mainWindow ? await dialog.showOpenDialog(mainWindow, options) : await dialog.showOpenDialog(options);
    return result.canceled || result.filePaths.length !== 1 ? null : (result.filePaths[0] ?? null);
  });
  ipcMain.handle(desktopChannels.retryBackend, async (event) => {
    if (!isMainFrame(event) || !event.senderFrame || !isRecoveryUrl(event.senderFrame.url)) throw new Error("Unauthorized desktop IPC caller");
    await connectDashboard();
  });
}

async function openValidatedExternal(raw: string): Promise<void> {
  const external = parseExternalUrl(raw);
  if (external) await shell.openExternal(external.toString());
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    title: "Sandbox", width: 1280, height: 820, minWidth: 920, minHeight: 600,
    show: false, backgroundColor: "#09090b", titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false,
      sandbox: true, webSecurity: true, allowRunningInsecureContent: false, spellcheck: false,
    },
  });
  window.webContents.setWindowOpenHandler(({ url }) => { void openValidatedExternal(url); return { action: "deny" }; });
  window.webContents.on("will-navigate", (event, url) => {
    if (isRecoveryUrl(url) || (dashboardOrigin && isSameDashboardOrigin(url, dashboardOrigin))) return;
    event.preventDefault();
    void openValidatedExternal(url);
  });
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  window.on("closed", () => { if (mainWindow === window) mainWindow = null; });
  return window;
}

async function showRecovery(reason: string, backendOrigin = configuredBackendOrigin()): Promise<void> {
  dashboardOrigin = null;
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const recoveryUrl = new URL(RECOVERY_URL);
  recoveryUrl.searchParams.set("reason", reason.slice(0, 160));
  if (backendOrigin) recoveryUrl.searchParams.set("endpoint", backendOrigin);
  await mainWindow.loadURL(recoveryUrl.toString());
}

async function connectDashboard(): Promise<void> {
  if (connecting || !mainWindow) return;
  connecting = true;
  let target: URL | null = null;
  try {
    target = configuredBackend();
    await handshakeBackend(target);
    if (!proxy) proxy = await createAuthenticatedProxy(target);
    await session.defaultSession.cookies.set({
      url: proxy.origin.toString(), name: proxy.cookieName, value: proxy.cookieValue,
      httpOnly: true, secure: false, sameSite: "strict", path: "/",
    });
    dashboardOrigin = proxy.origin;
    await mainWindow.loadURL(proxy.origin.toString());
  } catch (error) {
    await showRecovery(error instanceof Error ? error.message : "Backend unavailable", target?.origin ?? configuredBackendOrigin());
  } finally {
    connecting = false;
  }
}

function installApplicationMenu(): void {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: app.name, submenu: [{ role: "about" }, { type: "separator" }, { role: "hide" }, { role: "hideOthers" }, { role: "unhide" }, { type: "separator" }, { role: "quit" }] },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ role: "reload" }, { role: "togglefullscreen" }] },
    { label: "Window", submenu: [{ role: "minimize" }, { role: "zoom" }, { role: "front" }] },
  ]));
}

if (singleInstance) {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  app.whenReady().then(async () => {
    installAppProtocol(app.getAppPath());
    installSecurityBoundary();
    registerIpc();
    installApplicationMenu();
    mainWindow = createWindow();
    await showRecovery("Connecting to the Sandbox backend…");
    await connectDashboard();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) { mainWindow = createWindow(); void connectDashboard(); }
    });
  }).catch((error: unknown) => { console.error("Failed to start Sandbox desktop", error); app.quit(); });
}

app.on("before-quit", () => { if (proxy) void proxy.close(); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
