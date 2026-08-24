export const desktopChannels = {
  chooseProjectDirectory: "sandbox:choose-project-directory",
} as const;

export interface SandboxDesktopApi {
  readonly platform: "darwin";
  chooseProjectDirectory(): Promise<string | null>;
}
