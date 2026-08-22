import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SourceChangeWatcher, type SourceWatchListener } from "./sourceChangeWatcher";

describe("source directory change watcher", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function harness(initialBusy = false) {
    let listener: SourceWatchListener | undefined;
    let busy = initialBusy;
    const close = vi.fn();
    const scan = vi.fn(async () => undefined);
    const watch = vi.fn((_root: string, next: SourceWatchListener) => {
      listener = next;
      return { close };
    });
    const controller = new SourceChangeWatcher({ watch, scan, isBusy: () => busy, debounceMs: 100 });
    return { controller, scan, watch, close, listener: () => listener, setBusy: (value: boolean) => { busy = value; } };
  }

  it("debounces supported image changes into one incremental scan", async () => {
    const test = harness();
    test.controller.configure("C:\\pixiv", false);
    test.listener()?.("new-1.jpg");
    test.listener()?.("new-2.png");
    test.listener()?.("downloader.tmp");

    await vi.advanceTimersByTimeAsync(100);

    expect(test.scan).toHaveBeenCalledTimes(1);
    expect(test.watch).toHaveBeenCalledWith("C:\\pixiv", expect.any(Function));
  });

  it("ignores directory metadata changes caused by scanning but keeps directory renames", async () => {
    const test = harness();
    test.controller.configure("C:\\pixiv", false);
    test.listener()?.("artist-123", "change");
    await vi.advanceTimersByTimeAsync(100);
    expect(test.scan).not.toHaveBeenCalled();

    test.listener()?.("new-artist", "rename");
    await vi.advanceTimersByTimeAsync(100);
    expect(test.scan).toHaveBeenCalledTimes(1);
  });

  it("waits for GPU or training work and resumes when tasks become idle", async () => {
    const test = harness(true);
    test.controller.configure("C:\\pixiv", false);
    test.listener()?.("new.jpg");
    await vi.advanceTimersByTimeAsync(100);
    expect(test.scan).not.toHaveBeenCalled();

    test.setBusy(false);
    test.controller.activeStateChanged();
    await vi.advanceTimersByTimeAsync(100);

    expect(test.scan).toHaveBeenCalledTimes(1);
  });

  it("scans once when a project is opened and closes the old directory watcher", async () => {
    const test = harness();
    test.controller.configure("C:\\pixiv-a", true);
    await vi.advanceTimersByTimeAsync(100);
    expect(test.scan).toHaveBeenCalledTimes(1);

    test.controller.configure("C:\\pixiv-b", false);
    expect(test.close).toHaveBeenCalledTimes(1);
    test.controller.dispose();
    expect(test.close).toHaveBeenCalledTimes(2);
  });
});
