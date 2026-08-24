export const desktopChannels = {
  chooseProjectDirectory: "sandbox:choose-project-directory",
  retryBackend: "sandbox:retry-backend",
} as const;

export interface SandboxDesktopApi {
  readonly platform: "darwin";
  chooseProjectDirectory(): Promise<string | null>;
  retryBackend(): Promise<void>;
}
