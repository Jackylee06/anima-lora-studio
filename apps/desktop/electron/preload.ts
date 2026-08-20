import { contextBridge, ipcRenderer } from "electron";
import type { AppUpdateState, WorkerEvent } from "@anima/contracts" with { "resolution-mode": "import" };

contextBridge.exposeInMainWorld("anima", {
  rpc: <T>(method: string, params: unknown = {}): Promise<T> => ipcRenderer.invoke("worker:rpc", method, params),
  onWorkerEvent: (callback: (event: WorkerEvent) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: WorkerEvent) => callback(payload);
    ipcRenderer.on("worker:event", listener);
    return () => ipcRenderer.removeListener("worker:event", listener);
  },
  chooseDirectory: (title?: string): Promise<string | null> => ipcRenderer.invoke("dialog:directory", title),
  chooseFile: (filters?: Array<{ name: string; extensions: string[] }>): Promise<string | null> =>
    ipcRenderer.invoke("dialog:file", filters),
  fileUrl: (filePath: string): string => {
    const encoded = Buffer.from(filePath, "utf8").toString("base64url");
    return `anima-file://local/${encoded}`;
  },
  getSecret: (key: string): Promise<string | null> => ipcRenderer.invoke("secret:get", key),
  setSecret: (key: string, value: string): Promise<void> => ipcRenderer.invoke("secret:set", key, value),
  deleteSecret: (key: string): Promise<void> => ipcRenderer.invoke("secret:delete", key),
  getUpdateState: (): Promise<AppUpdateState> => ipcRenderer.invoke("update:status"),
  checkForUpdates: (): Promise<AppUpdateState> => ipcRenderer.invoke("update:check"),
  downloadUpdate: (): Promise<AppUpdateState> => ipcRenderer.invoke("update:download"),
  cancelUpdateDownload: (): Promise<AppUpdateState> => ipcRenderer.invoke("update:cancel"),
  ignoreUpdate: (): Promise<AppUpdateState> => ipcRenderer.invoke("update:ignore"),
  clearIgnoredUpdate: (): Promise<AppUpdateState> => ipcRenderer.invoke("update:clear-ignore"),
  installUpdate: (): Promise<void> => ipcRenderer.invoke("update:install"),
  onUpdateState: (callback: (state: AppUpdateState) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, state: AppUpdateState) => callback(state);
    ipcRenderer.on("update:state", listener);
    return () => ipcRenderer.removeListener("update:state", listener);
  },
  platform: process.platform
});
