import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";
import { UpdateManager, type CancellationTokenLike, type UpdateInfoLike, type UpdaterLike } from "./updateManager";

class FakeUpdater extends EventEmitter implements UpdaterLike {
  autoDownload = true;
  autoInstallOnAppQuit = true;
  allowPrerelease = true;
  fullChangelog = false;
  checkForUpdates = vi.fn(async () => undefined);
  downloadUpdate = vi.fn(async () => [] as string[]);
  quitAndInstall = vi.fn();

  available(info: Partial<UpdateInfoLike> = {}): void {
    this.emit("update-available", { version: "0.1.2", ...info });
  }
}

function setup(options: { enabled?: boolean; ignoredVersion?: string | null } = {}) {
  const updater = new FakeUpdater();
  let ignoredVersion = options.ignoredVersion || null;
  let cancelled = false;
  const token: CancellationTokenLike = { get cancelled() { return cancelled; }, cancel() { cancelled = true; } };
  const states: string[] = [];
  const manager = new UpdateManager({
    updater,
    currentVersion: "0.1.1",
    enabled: options.enabled ?? true,
    store: {
      readIgnoredVersion: () => ignoredVersion,
      writeIgnoredVersion: (version) => { ignoredVersion = version; }
    },
    createCancellationToken: () => token,
    onState: (state) => states.push(state.status)
  });
  return { updater, manager, token, states, ignored: () => ignoredVersion };
}

describe("UpdateManager", () => {
  it("disables network checks outside a packaged Windows build", async () => {
    const { updater, manager } = setup({ enabled: false });
    expect(manager.getState().status).toBe("disabled");
    await manager.check();
    expect(updater.checkForUpdates).not.toHaveBeenCalled();
  });

  it("reports an available release and configures manual downloads", () => {
    const { updater, manager } = setup();
    updater.available({ releaseName: "Release", releaseNotes: "Fixes" });
    expect(manager.getState()).toMatchObject({ status: "available", availableVersion: "0.1.2", releaseNotes: "Fixes" });
    expect(updater.autoDownload).toBe(false);
    expect(updater.autoInstallOnAppQuit).toBe(false);
    expect(updater.allowPrerelease).toBe(false);
  });

  it("persists ignored versions and can restore the update", () => {
    const { updater, manager, ignored } = setup();
    updater.available();
    manager.ignoreAvailableVersion();
    expect(manager.getState().status).toBe("ignored");
    expect(ignored()).toBe("0.1.2");
    manager.clearIgnoredVersion();
    expect(manager.getState().status).toBe("available");
    expect(ignored()).toBeNull();
  });

  it("tracks progress and cancels an in-flight download", async () => {
    const { updater, manager, token } = setup();
    updater.available();
    updater.downloadUpdate.mockImplementation(() => new Promise<string[]>((_resolve, reject) => {
      const timer = setInterval(() => {
        if (token.cancelled) { clearInterval(timer); reject(new Error("cancelled")); }
      }, 1);
    }));
    const download = manager.download();
    updater.emit("download-progress", { percent: 42, bytesPerSecond: 1024, transferred: 42, total: 100 });
    expect(manager.getState()).toMatchObject({ status: "downloading", progress: 42 });
    manager.cancelDownload();
    await download;
    expect(token.cancelled).toBe(true);
    expect(manager.getState().status).toBe("available");
  });

  it("only installs a completed download", () => {
    const { updater, manager } = setup();
    expect(() => manager.quitAndInstall()).toThrow("尚未下载完成");
    updater.emit("update-downloaded", { version: "0.1.2" });
    manager.quitAndInstall();
    expect(updater.quitAndInstall).toHaveBeenCalledWith(false, true);
  });
});
