import type { WorkerEvent } from "@anima/contracts";

export async function rpc<T>(method: string, params: unknown = {}): Promise<T> {
  return window.anima.rpc<T>(method, params);
}

export function onWorkerEvent(callback: (event: WorkerEvent) => void): () => void {
  return window.anima.onWorkerEvent(callback);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

