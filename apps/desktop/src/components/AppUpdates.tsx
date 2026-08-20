import type { AppUpdateState } from "@anima/contracts";
import { Download, RefreshCw, RotateCcw, ShieldCheck, X, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Panel } from "./ui";

const statusLabel: Record<AppUpdateState["status"], string> = {
  disabled: "开发模式",
  idle: "尚未检查",
  checking: "正在检查",
  available: "发现新版本",
  ignored: "已忽略",
  downloading: "正在下载",
  downloaded: "可以安装",
  "not-available": "已是最新版",
  error: "更新失败"
};

function formatBytes(value?: number): string {
  if (!value || value < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
  return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function AppUpdates() {
  const [state, setState] = useState<AppUpdateState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void window.anima.getUpdateState().then(setState).catch((error) => setMessage(String(error)));
    return window.anima.onUpdateState(setState);
  }, []);

  async function run(name: string, action: () => Promise<AppUpdateState | void>) {
    setBusy(name); setMessage(null);
    try {
      const next = await action();
      if (next) setState(next);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  }

  const status = state?.status || "idle";
  const tone = status === "downloaded" || status === "not-available"
    ? "good"
    : status === "error" ? "bad" : status === "available" || status === "downloading" ? "accent" : "neutral";
  const canRetryDownload = status === "error" && Boolean(state?.availableVersion);

  return <Panel
    title="应用更新"
    className="update-panel"
    action={<div className="update-badges"><Badge tone="neutral">v{state?.currentVersion || "…"}</Badge><Badge tone={tone}>{statusLabel[status]}</Badge></div>}
  >
    <div className="update-card">
      <div className="update-icon"><ShieldCheck /></div>
      <div className="update-copy">
        <strong>{state?.availableVersion ? `Anima LoRA Studio v${state.availableVersion}` : "GitHub Releases 稳定通道"}</strong>
        <p>{status === "disabled"
          ? "开发环境不会连接更新源；安装版将在启动后静默检查。"
          : status === "available" ? "新版本已找到。确认后再下载，不会中断当前任务。"
          : status === "ignored" ? "此版本已忽略；后续更高版本仍会正常提示。"
          : status === "downloading" ? `${formatBytes(state?.transferred)} / ${formatBytes(state?.total)} · ${formatBytes(state?.bytesPerSecond)}/s`
          : status === "downloaded" ? "安装包校验完成。重启应用即可安装更新。"
          : status === "not-available" ? "当前安装版本与稳定通道一致。"
          : status === "checking" ? "正在检查 GitHub Releases…"
          : status === "error" ? "更新操作失败，可重试且不会影响当前版本。"
          : "启动后会自动检查，也可以立即手动检查。"}</p>
        {state?.checkedAt && <small>上次检查：{new Date(state.checkedAt).toLocaleString()}</small>}
      </div>
      <div className="update-actions">
        {!["available", "ignored", "downloading", "downloaded", "disabled"].includes(status) && <Button className="button-secondary" busy={busy === "check" || status === "checking"} onClick={() => void run("check", window.anima.checkForUpdates)}><RefreshCw size={15} />检查更新</Button>}
        {(status === "available" || canRetryDownload) && <Button busy={busy === "download"} onClick={() => void run("download", window.anima.downloadUpdate)}><Download size={15} />{canRetryDownload ? "重试下载" : "下载更新"}</Button>}
        {status === "available" && <Button className="button-secondary" onClick={() => void run("ignore", window.anima.ignoreUpdate)}>忽略此版本</Button>}
        {status === "ignored" && <Button className="button-secondary" onClick={() => void run("restore", window.anima.clearIgnoredUpdate)}><RotateCcw size={15} />恢复提示</Button>}
        {status === "downloading" && <Button className="button-secondary" busy={busy === "cancel"} onClick={() => void run("cancel", window.anima.cancelUpdateDownload)}><X size={15} />取消下载</Button>}
        {status === "downloaded" && <Button busy={busy === "install"} onClick={() => void run("install", window.anima.installUpdate)}><Zap size={15} />重启并安装</Button>}
      </div>
    </div>
    {status === "downloading" && <div className="update-progress"><span style={{ width: `${state?.progress || 0}%` }} /></div>}
    {(state?.error || message) && <div className="error-box update-error">{message || state?.error}</div>}
    {state?.releaseNotes && <details className="update-notes"><summary>{state.releaseName || "查看更新说明"}</summary><pre>{state.releaseNotes}</pre></details>}
  </Panel>;
}
