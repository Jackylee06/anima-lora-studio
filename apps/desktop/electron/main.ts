import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, net, protocol, safeStorage, Tray } from "electron";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import type { WorkerEvent } from "@anima/contracts" with { "resolution-mode": "import" };
import { WorkerBridge } from "./workerBridge";

protocol.registerSchemesAsPrivileged([
  { scheme: "anima-file", privileges: { secure: true, standard: true, supportFetchAPI: true, stream: true } }
]);

const bridge = new WorkerBridge();
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let isQuitting = false;
let hasActiveJobs = false;
const activeJobIds = new Set<string>();
const allowedRoots = new Set<string>();

function normalizeRoot(value: string): string {
  return path.resolve(value).toLocaleLowerCase();
}

function allowProjectPaths(result: unknown): void {
  if (!result || typeof result !== "object") return;
  const candidate = result as Record<string, unknown>;
  for (const key of ["workspacePath", "sourceRoot"]) {
    if (typeof candidate[key] === "string") allowedRoots.add(normalizeRoot(candidate[key]));
  }
}

function isAllowedFile(filePath: string): boolean {
  const resolved = normalizeRoot(filePath);
  return [...allowedRoots].some((root) => resolved === root || resolved.startsWith(`${root}${path.sep}`));
}

function createTray(): void {
  if (tray) return;
  const pixel = nativeImage.createFromDataURL(
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAKElEQVR42mNgGAWjYBSMglEwCkbB////MzD8Z2Bg+M8ABYwMDAwMAAAfCQMe4rX5OAAAAABJRU5ErkJggg=="
  );
  tray = new Tray(pixel);
  tray.setToolTip("Anima LoRA Studio");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "显示 Anima LoRA Studio", click: () => mainWindow?.show() },
    { type: "separator" },
    { label: "退出", click: () => { isQuitting = true; app.quit(); } }
  ]));
  tray.on("double-click", () => mainWindow?.show());
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 1120,
    minHeight: 720,
    backgroundColor: "#0c0d11",
    show: false,
    title: "Anima LoRA Studio",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("close", (event) => {
    if (!isQuitting && hasActiveJobs) {
      event.preventDefault();
      mainWindow?.hide();
      createTray();
    }
  });
  mainWindow.on("closed", () => { mainWindow = null; });
  const devServer = process.env.VITE_DEV_SERVER_URL;
  if (devServer) void mainWindow.loadURL(devServer);
  else void mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
}

function secretFile(): string {
  return path.join(app.getPath("userData"), "secrets.json");
}

function readSecrets(): Record<string, string> {
  try { return JSON.parse(fs.readFileSync(secretFile(), "utf8")) as Record<string, string>; }
  catch { return {}; }
}

function writeSecrets(values: Record<string, string>): void {
  fs.mkdirSync(path.dirname(secretFile()), { recursive: true });
  fs.writeFileSync(secretFile(), JSON.stringify(values), { encoding: "utf8", mode: 0o600 });
}

app.on("before-quit", () => { isQuitting = true; });
app.on("will-quit", (event) => {
  if (!isQuitting) return;
  event.preventDefault();
  void bridge.stop().finally(() => app.exit(0));
});

app.whenReady().then(async () => {
  protocol.handle("anima-file", (request) => {
    try {
      const encoded = new URL(request.url).pathname.replace(/^\//, "");
      const filePath = Buffer.from(encoded, "base64url").toString("utf8");
      if (!isAllowedFile(filePath)) return new Response("Forbidden", { status: 403 });
      return net.fetch(pathToFileURL(filePath).toString());
    } catch {
      return new Response("Bad request", { status: 400 });
    }
  });

  ipcMain.handle("worker:rpc", async (_event, method: string, params: unknown) => {
    const result = await bridge.request(method, params);
    if (method === "project.create" || method === "project.open" || method === "project.current") {
      allowProjectPaths(result);
    }
    return result;
  });
  ipcMain.handle("dialog:directory", async (_event, title?: string) => {
    const result = await dialog.showOpenDialog({ title: title || "选择文件夹", properties: ["openDirectory", "createDirectory"] });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  ipcMain.handle("dialog:file", async (_event, filters?: Electron.FileFilter[]) => {
    const result = await dialog.showOpenDialog({ title: "选择文件", properties: ["openFile"], filters });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  ipcMain.handle("secret:get", (_event, key: string) => {
    const encoded = readSecrets()[key];
    if (!encoded || !safeStorage.isEncryptionAvailable()) return null;
    try { return safeStorage.decryptString(Buffer.from(encoded, "base64")); } catch { return null; }
  });
  ipcMain.handle("secret:set", (_event, key: string, value: string) => {
    if (!safeStorage.isEncryptionAvailable()) throw new Error("Windows 安全存储当前不可用");
    const secrets = readSecrets();
    secrets[key] = safeStorage.encryptString(value).toString("base64");
    writeSecrets(secrets);
  });
  ipcMain.handle("secret:delete", (_event, key: string) => {
    const secrets = readSecrets();
    delete secrets[key];
    writeSecrets(secrets);
  });

  bridge.on("event", (payload: WorkerEvent) => {
    if (payload.event === "job.updated" && payload.data && typeof payload.data === "object") {
      const job = payload.data as Record<string, unknown>;
      const jobId = String(job.id || "");
      const active = ["queued", "running", "pause_requested"].includes(String(job.state));
      if (jobId && active) activeJobIds.add(jobId);
      else if (jobId) activeJobIds.delete(jobId);
      hasActiveJobs = activeJobIds.size > 0;
    }
    mainWindow?.webContents.send("worker:event", payload);
  });
  await bridge.start();
  createWindow();
});

app.on("activate", () => {
  if (!mainWindow) createWindow();
  else mainWindow.show();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin" && !hasActiveJobs) app.quit();
});
