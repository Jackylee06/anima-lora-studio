import path from "node:path";

const SOURCE_EXTENSIONS = new Set([
  ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".jxl",
  ".gif", ".apng", ".zip", ".txt", ".epub",
]);

export type SourceWatchListener = (filename: string | null, eventType?: "rename" | "change") => void;

export interface SourceWatchHandle {
  close(): void;
}

interface SourceChangeWatcherOptions {
  watch(root: string, listener: SourceWatchListener): SourceWatchHandle;
  scan(): Promise<unknown>;
  isBusy(): boolean;
  debounceMs?: number;
  onError?(error: unknown): void;
}

function isRelevantSourceChange(filename: string | null, eventType?: "rename" | "change"): boolean {
  if (!filename) return true;
  const extension = path.extname(filename).toLowerCase();
  if (eventType === "change" && extension === "") return false;
  return extension === "" || SOURCE_EXTENSIONS.has(extension);
}

export class SourceChangeWatcher {
  private readonly options: SourceChangeWatcherOptions;
  private readonly debounceMs: number;
  private handle: SourceWatchHandle | null = null;
  private root: string | null = null;
  private timer: NodeJS.Timeout | null = null;
  private pending = false;
  private running = false;

  constructor(options: SourceChangeWatcherOptions) {
    this.options = options;
    this.debounceMs = options.debounceMs ?? 2_000;
  }

  configure(root: string | null, scanNow = false): void {
    if (root === this.root && this.handle) {
      if (scanNow) this.requestScan();
      return;
    }
    this.closeHandle();
    this.root = root;
    if (!root) return;
    try {
      this.handle = this.options.watch(root, (filename, eventType) => {
        if (isRelevantSourceChange(filename, eventType)) this.requestScan();
      });
    } catch (error) {
      this.options.onError?.(error);
    }
    if (scanNow) this.requestScan();
  }

  activeStateChanged(): void {
    if (this.pending && !this.options.isBusy() && !this.running) this.schedule();
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.pending = false;
    this.closeHandle();
    this.root = null;
  }

  private requestScan(): void {
    this.pending = true;
    this.schedule();
  }

  private schedule(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.debounceMs);
    this.timer.unref?.();
  }

  private async flush(): Promise<void> {
    if (!this.pending || this.running || this.options.isBusy()) return;
    this.pending = false;
    this.running = true;
    try {
      await this.options.scan();
    } catch (error) {
      this.options.onError?.(error);
    } finally {
      this.running = false;
      if (this.pending) this.schedule();
    }
  }

  private closeHandle(): void {
    this.handle?.close();
    this.handle = null;
  }
}
