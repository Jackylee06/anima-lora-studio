/// <reference types="vite/client" />

import type { AppUpdateState, WorkerEvent } from "@anima/contracts";

declare global {
  interface Window {
    anima: {
      rpc<T = unknown>(method: string, params?: unknown): Promise<T>;
      onWorkerEvent(callback: (event: WorkerEvent) => void): () => void;
      chooseDirectory(title?: string): Promise<string | null>;
      chooseFile(filters?: Array<{ name: string; extensions: string[] }>): Promise<string | null>;
      fileUrl(path: string): string;
      getSecret(key: string): Promise<string | null>;
      setSecret(key: string, value: string): Promise<void>;
      deleteSecret(key: string): Promise<void>;
      getUpdateState(): Promise<AppUpdateState>;
      checkForUpdates(): Promise<AppUpdateState>;
      downloadUpdate(): Promise<AppUpdateState>;
      cancelUpdateDownload(): Promise<AppUpdateState>;
      ignoreUpdate(): Promise<AppUpdateState>;
      clearIgnoredUpdate(): Promise<AppUpdateState>;
      installUpdate(): Promise<void>;
      onUpdateState(callback: (state: AppUpdateState) => void): () => void;
      platform: NodeJS.Platform;
    };
  }
}

export {};
