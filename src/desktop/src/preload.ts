import { contextBridge, ipcRenderer } from "electron";
import { desktopChannels, type SandboxDesktopApi } from "./contracts";

const api: SandboxDesktopApi = Object.freeze({
  platform: "darwin",
  chooseProjectDirectory: () => ipcRenderer.invoke(desktopChannels.chooseProjectDirectory) as Promise<string | null>,
  retryBackend: () => ipcRenderer.invoke(desktopChannels.retryBackend) as Promise<void>,
});

contextBridge.exposeInMainWorld("sandboxDesktop", api);
