import { contextBridge, ipcRenderer } from "electron";
import type { WorkerEvent } from "@anima/contracts" with { "resolution-mode": "import" };

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
  platform: process.platform
});
