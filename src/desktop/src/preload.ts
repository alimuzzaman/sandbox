import { contextBridge, ipcRenderer } from "electron";
import type { SandboxDesktopApi } from "./contracts";

// Sandboxed Electron preloads cannot require local CommonJS modules. Keep these
// runtime constants inline while checking them against the shared contract.
const desktopChannels = {
  chooseProjectDirectory: "sandbox:choose-project-directory",
  retryBackend: "sandbox:retry-backend",
} as const satisfies typeof import("./contracts").desktopChannels;

const api: SandboxDesktopApi = Object.freeze({
  platform: "darwin",
  chooseProjectDirectory: () => ipcRenderer.invoke(desktopChannels.chooseProjectDirectory) as Promise<string | null>,
  retryBackend: () => ipcRenderer.invoke(desktopChannels.retryBackend) as Promise<void>,
});

contextBridge.exposeInMainWorld("sandboxDesktop", api);
