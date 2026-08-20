import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import path from "node:path";
import readline from "node:readline";
import { app } from "electron";
import type { RpcFailure, RpcRequest, RpcResponse, WorkerEvent } from "@anima/contracts" with { "resolution-mode": "import" };

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

export class WorkerBridge extends EventEmitter {
  private process: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, PendingRequest>();
  private sequence = 0;
  private shuttingDown = false;

  async start(): Promise<void> {
    if (this.process && !this.process.killed) return;
    const { command, args, cwd } = this.resolveWorkerCommand();
    this.process = spawn(command, args, {
      cwd,
      windowsHide: true,
      env: { ...process.env, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
      stdio: ["pipe", "pipe", "pipe"]
    });
    const child = this.process;
    readline.createInterface({ input: child.stdout }).on("line", (line) => this.handleLine(line));
    readline.createInterface({ input: child.stderr }).on("line", (line) => {
      this.emit("event", { v: 1, event: "worker.log", data: { level: "stderr", message: line } });
    });
    child.on("exit", (code, signal) => {
      this.process = null;
      const reason = `Worker 已退出（code=${String(code)}, signal=${String(signal)}）`;
      for (const request of this.pending.values()) {
        clearTimeout(request.timer);
        request.reject(new Error(reason));
      }
      this.pending.clear();
      if (!this.shuttingDown) {
        this.emit("event", { v: 1, event: "worker.crashed", data: { code, signal } });
      }
    });
    await this.request("system.ping", {}, 20_000);
  }

  async request<T>(method: string, params: unknown = {}, timeoutMs = 120_000): Promise<T> {
    if (!this.process || this.process.killed) await this.start();
    const id = `${Date.now().toString(36)}-${++this.sequence}`;
    const payload: RpcRequest = { v: 1, id, method, params };
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Worker 请求超时：${method}`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timer
      });
      this.process?.stdin.write(`${JSON.stringify(payload)}\n`, "utf8");
    });
  }

  async stop(): Promise<void> {
    this.shuttingDown = true;
    if (!this.process) return;
    try {
      await this.request("system.shutdown", {}, 5_000);
    } catch {
      this.process.kill();
    }
  }

  private handleLine(line: string): void {
    let message: RpcResponse | WorkerEvent;
    try {
      message = JSON.parse(line) as RpcResponse | WorkerEvent;
    } catch {
      this.emit("event", { v: 1, event: "worker.log", data: { level: "stdout", message: line } });
      return;
    }
    if ("event" in message) {
      this.emit("event", message);
      return;
    }
    const request = this.pending.get(message.id);
    if (!request) return;
    clearTimeout(request.timer);
    this.pending.delete(message.id);
    if (message.ok) {
      request.resolve(message.result);
    } else {
      const failure = message as RpcFailure;
      request.reject(new Error(`${failure.error.code}: ${failure.error.message}`));
    }
  }

  private resolveWorkerCommand(): { command: string; args: string[]; cwd: string } {
    if (app.isPackaged) {
      const executable = path.join(process.resourcesPath, "worker", "anima-worker.exe");
      return { command: executable, args: [], cwd: path.dirname(executable) };
    }
    const workerRoot = path.resolve(__dirname, "../../../services/worker");
    const python = process.env.ANIMA_WORKER_PYTHON || "python";
    return { command: python, args: [path.join(workerRoot, "main.py")], cwd: workerRoot };
  }
}
