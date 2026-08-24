import {
  app, BrowserWindow, dialog, ipcMain, Menu, session, shell,
  type IpcMainInvokeEvent, type OpenDialogOptions,
} from "electron";
import { join } from "node:path";
import { desktopChannels } from "./contracts";
import { isSameDashboardOrigin, parseDashboardUrl, parseExternalUrl } from "./security";

const DEFAULT_DASHBOARD_URL = "http://127.0.0.1:8765";
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
].join("; ");

let mainWindow: BrowserWindow | null = null;

function dashboardUrl(): URL {
  const configured = process.env.SANDBOX_DESKTOP_DEV_URL
    || process.env.SANDBOX_DESKTOP_URL
    || DEFAULT_DASHBOARD_URL;
  return parseDashboardUrl(configured);
}

function installSecurityHeaders(): void {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [CONTENT_SECURITY_POLICY],
      },
    });
  });
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
}

function isMainDashboardFrame(event: IpcMainInvokeEvent, target: URL): boolean {
  return mainWindow !== null
    && event.sender === mainWindow.webContents
    && event.senderFrame === mainWindow.webContents.mainFrame
    && isSameDashboardOrigin(event.senderFrame.url, target);
}

function registerIpc(target: URL): void {
  ipcMain.handle(desktopChannels.chooseProjectDirectory, async (event) => {
    if (!isMainDashboardFrame(event, target)) throw new Error("Unauthorized desktop IPC caller");
    const options: OpenDialogOptions = {
      title: "Choose a Sandbox project",
      properties: ["openDirectory", "createDirectory"],
    };
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options);
    if (result.canceled || result.filePaths.length !== 1) return null;
    return result.filePaths[0] ?? null;
  });
}

async function openValidatedExternal(raw: string): Promise<void> {
  const external = parseExternalUrl(raw);
  if (external) await shell.openExternal(external.toString());
}

function createWindow(target: URL): BrowserWindow {
  const window = new BrowserWindow({
    title: "Sandbox",
    width: 1280,
    height: 820,
    minWidth: 920,
    minHeight: 600,
    show: false,
    backgroundColor: "#0a0a0a",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
    },
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    void openValidatedExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (isSameDashboardOrigin(url, target)) return;
    event.preventDefault();
    void openValidatedExternal(url);
  });
  window.once("ready-to-show", () => window.show());
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });

  void window.loadURL(target.toString());
  return window;
}

function installApplicationMenu(): void {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ role: "reload" }, { role: "togglefullscreen" }] },
    { label: "Window", submenu: [{ role: "minimize" }, { role: "zoom" }, { role: "front" }] },
  ]));
}

app.whenReady().then(() => {
  const target = dashboardUrl();
  installSecurityHeaders();
  registerIpc(target);
  installApplicationMenu();
  mainWindow = createWindow(target);
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) mainWindow = createWindow(target);
  });
}).catch((error: unknown) => {
  console.error("Failed to start Sandbox desktop", error);
  app.quit();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
