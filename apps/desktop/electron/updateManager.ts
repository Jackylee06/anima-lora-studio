import type { AppUpdateState } from "@anima/contracts" with { "resolution-mode": "import" };

export interface UpdateInfoLike {
  version: string;
  releaseName?: string | null;
  releaseNotes?: string | Array<{ version: string; note: string | null }> | null;
  releaseDate?: string;
}

export interface ProgressInfoLike {
  percent: number;
  bytesPerSecond: number;
  transferred: number;
  total: number;
}

export interface CancellationTokenLike {
  readonly cancelled?: boolean;
  cancel(): void;
}

export interface UpdaterLike {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  allowPrerelease: boolean;
  fullChangelog: boolean;
  on(event: "checking-for-update", listener: () => void): unknown;
  on(event: "update-available" | "update-not-available" | "update-downloaded" | "update-cancelled", listener: (info: UpdateInfoLike) => void): unknown;
  on(event: "download-progress", listener: (progress: ProgressInfoLike) => void): unknown;
  on(event: "error", listener: (error: Error) => void): unknown;
  checkForUpdates(): Promise<unknown>;
  downloadUpdate(token?: CancellationTokenLike): Promise<string[]>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

export interface UpdatePreferenceStore {
  readIgnoredVersion(): string | null;
  writeIgnoredVersion(version: string | null): void;
}

interface UpdateManagerOptions {
  updater: UpdaterLike;
  currentVersion: string;
  enabled: boolean;
  store: UpdatePreferenceStore;
  createCancellationToken: () => CancellationTokenLike;
  onState: (state: AppUpdateState) => void;
}

function releaseNotesText(notes: UpdateInfoLike["releaseNotes"]): string | undefined {
  if (typeof notes === "string") return notes.trim() || undefined;
  if (!Array.isArray(notes)) return undefined;
  const value = notes
    .map((entry) => [entry.version, entry.note].filter(Boolean).join("\n"))
    .filter(Boolean)
    .join("\n\n");
  return value || undefined;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class UpdateManager {
  private state: AppUpdateState;
  private readonly options: UpdateManagerOptions;
  private downloadToken: CancellationTokenLike | null = null;
  private autoCheckTimer: NodeJS.Timeout | null = null;

  constructor(options: UpdateManagerOptions) {
    this.options = options;
    const ignoredVersion = options.store.readIgnoredVersion() || undefined;
    this.state = {
      currentVersion: options.currentVersion,
      status: options.enabled ? "idle" : "disabled",
      ignoredVersion,
      error: options.enabled ? undefined : "仅 Windows 安装版支持应用更新"
    };

    options.updater.autoDownload = false;
    options.updater.autoInstallOnAppQuit = false;
    options.updater.allowPrerelease = false;
    options.updater.fullChangelog = true;
    this.bindEvents();
  }

  private bindEvents(): void {
    const updater = this.options.updater;
    updater.on("checking-for-update", () => this.patch({ status: "checking", error: undefined }));
    updater.on("update-available", (info) => {
      const update = {
        availableVersion: info.version,
        releaseName: info.releaseName || undefined,
        releaseNotes: releaseNotesText(info.releaseNotes),
        releaseDate: info.releaseDate,
        checkedAt: new Date().toISOString(),
        progress: undefined,
        error: undefined
      };
      this.patch({
        ...update,
        status: this.state.ignoredVersion === info.version ? "ignored" : "available"
      });
    });
    updater.on("update-not-available", () => this.patch({
      status: "not-available",
      availableVersion: undefined,
      releaseName: undefined,
      releaseNotes: undefined,
      releaseDate: undefined,
      checkedAt: new Date().toISOString(),
      progress: undefined,
      error: undefined
    }));
    updater.on("download-progress", (progress) => this.patch({
      status: "downloading",
      progress: Math.max(0, Math.min(100, progress.percent)),
      bytesPerSecond: progress.bytesPerSecond,
      transferred: progress.transferred,
      total: progress.total,
      error: undefined
    }));
    updater.on("update-downloaded", (info) => {
      this.downloadToken = null;
      this.patch({ status: "downloaded", availableVersion: info.version, progress: 100, error: undefined });
    });
    updater.on("update-cancelled", () => {
      this.downloadToken = null;
      this.patch({ status: "available", progress: undefined, error: undefined });
    });
    updater.on("error", (error) => {
      if (this.downloadToken?.cancelled) return;
      this.patch({ status: "error", error: errorText(error), progress: undefined });
    });
  }

  private patch(next: Partial<AppUpdateState>): AppUpdateState {
    this.state = { ...this.state, ...next };
    this.options.onState(this.getState());
    return this.getState();
  }

  getState(): AppUpdateState {
    return { ...this.state };
  }

  startAutoCheck(delayMs = 12_000): void {
    if (!this.options.enabled || this.autoCheckTimer) return;
    this.autoCheckTimer = setTimeout(() => {
      this.autoCheckTimer = null;
      void this.check();
    }, delayMs);
    this.autoCheckTimer.unref?.();
  }

  dispose(): void {
    if (this.autoCheckTimer) clearTimeout(this.autoCheckTimer);
    this.autoCheckTimer = null;
    this.downloadToken?.cancel();
    this.downloadToken = null;
  }

  async check(): Promise<AppUpdateState> {
    if (!this.options.enabled) return this.getState();
    if (["checking", "downloading"].includes(this.state.status)) return this.getState();
    this.patch({ status: "checking", error: undefined, progress: undefined });
    try {
      await this.options.updater.checkForUpdates();
      if (this.state.status === "checking") {
        this.patch({ status: "not-available", checkedAt: new Date().toISOString() });
      }
    } catch (error) {
      this.patch({ status: "error", error: errorText(error), progress: undefined });
    }
    return this.getState();
  }

  async download(): Promise<AppUpdateState> {
    if (!this.options.enabled || !this.state.availableVersion) return this.getState();
    if (this.state.status === "downloading") return this.getState();
    const token = this.options.createCancellationToken();
    this.downloadToken = token;
    this.patch({ status: "downloading", progress: 0, error: undefined });
    try {
      await this.options.updater.downloadUpdate(token);
    } catch (error) {
      if (token.cancelled) {
        this.patch({ status: "available", progress: undefined, error: undefined });
      } else {
        this.patch({ status: "error", error: errorText(error), progress: undefined });
      }
    } finally {
      if (this.downloadToken === token) this.downloadToken = null;
    }
    return this.getState();
  }

  cancelDownload(): AppUpdateState {
    this.downloadToken?.cancel();
    this.downloadToken = null;
    return this.patch({ status: "available", progress: undefined, error: undefined });
  }

  ignoreAvailableVersion(): AppUpdateState {
    if (!this.state.availableVersion) return this.getState();
    this.options.store.writeIgnoredVersion(this.state.availableVersion);
    return this.patch({ status: "ignored", ignoredVersion: this.state.availableVersion });
  }

  clearIgnoredVersion(): AppUpdateState {
    this.options.store.writeIgnoredVersion(null);
    return this.patch({
      ignoredVersion: undefined,
      status: this.state.status === "ignored" ? "available" : this.state.status
    });
  }

  quitAndInstall(): void {
    if (this.state.status !== "downloaded") throw new Error("更新尚未下载完成");
    this.options.updater.quitAndInstall(false, true);
  }
}
