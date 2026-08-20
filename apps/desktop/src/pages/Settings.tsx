import type { ModelEntry, Project, WorkerEvent } from "@anima/contracts";
import { useEffect, useState } from "react";
import { CheckCircle2, Cpu, Download, FolderOpen, HardDrive, RefreshCw, Shield, Wrench } from "lucide-react";
import { Badge, Button, Field, Panel } from "../components/ui";
import { AppUpdates } from "../components/AppUpdates";
import { errorMessage, onWorkerEvent, rpc } from "../lib/api";

interface TorchRuntime { version: string; cudaRuntime: string; cudaAvailable: boolean; bf16Supported: boolean; device: string; validated: boolean }
interface Diagnostics { nvidia: Array<{ name: string; memoryTotalMiB: number; memoryFreeMiB: number; driver: string }> | null; runtime: { environments: Record<string, { ready: boolean; python: string; torch?: TorchRuntime; reason?: string }>; engine: { ready: boolean; root: string; commit: string } | null } }

export function SettingsPage({ project, onProject }: { project: Project; onProject: (project: Project) => void }) {
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [template, setTemplate] = useState(project.pathTemplate);
  const [baseProfile, setBaseProfile] = useState(String(project.settings.baseProfile || "character"));
  const [captionMode, setCaptionMode] = useState(String(project.settings.captionMode || (project.profile === "style" ? "hybrid" : "tags")));
  const [customInstructions, setCustomInstructions] = useState(String(project.settings.customInstructions || ""));
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    const [modelData, diagnosticData] = await Promise.all([rpc<ModelEntry[]>("models.list"), rpc<Diagnostics>("system.diagnostics")]);
    setModels(modelData); setDiagnostics(diagnosticData);
  }
  useEffect(() => {
    void load();
    return onWorkerEvent((event: WorkerEvent) => {
      if (event.event === "job.updated" && (event.data as { kind?: string; state?: string }).kind === "model_download" && ["succeeded", "failed"].includes(String((event.data as { state?: string }).state))) void load();
    });
  }, []);

  async function register(model: ModelEntry) {
    const directoryKinds = ["wd14", "joycaption", "trainer"];
    const localPath = directoryKinds.includes(model.kind)
      ? await window.anima.chooseDirectory(`注册 ${model.name}`)
      : await window.anima.chooseFile([{ name: model.name, extensions: model.kind === "vae" ? ["safetensors", "pth"] : ["safetensors", "onnx", "csv"] }]);
    if (!localPath) return;
    setBusy(model.id); setMessage(null);
    try { await rpc("models.register", { ...model, localPath, verifyHash: true }); await load(); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }

  async function download(model: ModelEntry) {
    const metadata = model.metadata as { files?: string[]; file?: string };
    const filenames = metadata.files || (metadata.file ? [metadata.file] : []);
    if (!filenames.length) { setMessage("该模型由 Hugging Face/Transformers 在首次推理时自动缓存，请使用本地注册指定已有缓存目录。"); return; }
    setBusy(`download-${model.id}`); setMessage(null);
    try { await rpc("models.download", { modelId: model.id, filenames }); setMessage(`${model.name} 下载任务已进入可断点续传队列。`); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }

  async function saveProject() {
    setBusy("project"); setMessage(null);
    try { onProject(await rpc<Project>("project.update", { pathTemplate: template, settings: { baseProfile, captionMode, customInstructions } })); setMessage("项目设置已保存。"); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }

  async function copyProfile(source: "character" | "style") {
    setBusy("profile"); setMessage(null);
    const mode = source === "style" ? "hybrid" : "tags";
    try {
      const updated = await rpc<Project>("project.update", { profile: "custom", settings: { baseProfile: source, captionMode: mode, customInstructions } });
      setBaseProfile(source); setCaptionMode(mode); onProject(updated); setMessage(`已复制${source === "style" ? "画风" : "角色"} profile，可继续自定义规则。`);
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }

  async function bootstrap(name: "caption" | "trainer") {
    setBusy(name); setMessage(null);
    try { await rpc("runtime.bootstrap", { name }); setMessage(`${name} 环境安装任务已进入队列。`); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }

  return <div className="page-content">
    <header className="page-heading compact"><div><p className="eyebrow">SYSTEM</p><h1>模型与设置</h1><p>模型文件可复用现有 ComfyUI 目录；大文件不会复制进项目。</p></div><Button className="button-secondary" onClick={() => void load()}><RefreshCw size={15} />刷新状态</Button></header>
    {message && <div className="info-box">{message}</div>}
    <AppUpdates />
    <div className="settings-grid">
      <Panel title="硬件诊断">
        {diagnostics?.nvidia?.length ? diagnostics.nvidia.map((gpu) => <div className="hardware-card" key={gpu.name}><div className="hardware-icon"><Cpu /></div><div><strong>{gpu.name}</strong><p>{gpu.memoryTotalMiB} MiB VRAM · 当前空闲 {gpu.memoryFreeMiB} MiB</p><small>Driver {gpu.driver}</small></div><Badge tone={gpu.memoryTotalMiB >= 15000 ? "good" : "warn"}>{gpu.memoryTotalMiB >= 15000 ? "适合 NF4 + LoRA" : "建议低显存预设"}</Badge></div>) : <p className="muted">未检测到 NVIDIA GPU 或 nvidia-smi。</p>}
      </Panel>
      <Panel title="隔离运行环境">
        <div className="runtime-list">{(["caption", "trainer"] as const).map((name) => { const status = diagnostics?.runtime.environments[name]; return <div key={name}><div><Wrench size={17} /><span><strong>{name === "caption" ? "Caption 环境" : "训练环境"}</strong><small>{status?.ready ? `${status.python} · Torch ${status.torch?.version} · CUDA ${status.torch?.cudaRuntime}` : status?.reason || "未安装"}</small></span></div><Badge tone={status?.ready ? "good" : "warn"}>{status?.ready ? "CUDA 已验证" : "未就绪"}</Badge><Button className="button-secondary" busy={busy === name} onClick={() => void bootstrap(name)}>{status?.ready ? "校验并更新" : "安装"}</Button></div>; })}</div>
        {diagnostics?.runtime.engine && <code className="path-code" title={diagnostics.runtime.engine.commit}>固定 sd-scripts：{diagnostics.runtime.engine.root}</code>}
      </Panel>
    </div>
    <Panel title="模型注册表">
      <div className="model-table">{models.map((model) => <div className="model-row" key={model.id}><div className="model-kind"><HardDrive size={18} /></div><div className="model-info"><strong>{model.name}</strong><span>{model.kind} · {model.source}</span><small title={model.localPath || ""}>{model.localPath || "尚未注册本地路径"}</small></div><Badge tone={model.status === "ready" ? "good" : model.status === "invalid" ? "bad" : "neutral"}>{model.status}</Badge><Button className="button-secondary" busy={busy === `download-${model.id}`} onClick={() => void download(model)}><Download size={15} />下载</Button><Button className="button-secondary" busy={busy === model.id} onClick={() => void register(model)}><FolderOpen size={15} />注册本地文件</Button></div>)}</div>
    </Panel>
    <Panel title="Pixiv 路径解析">
      <div className="form-grid"><Field label="路径模板" hint="修改后重新扫描；旧 caption 与审核状态按绝对路径保留"><input value={template} onChange={(event) => setTemplate(event.target.value)} /></Field></div>
      <div className="form-actions"><Button busy={busy === "project"} onClick={() => void saveProject()}>保存并验证模板</Button></div>
    </Panel>
    <Panel title="可复制的自定义 LoRA Profile" action={<Badge tone={project.profile === "custom" ? "accent" : "neutral"}>{project.profile}</Badge>}>
      <div className="form-grid two"><Field label="基础规则"><select value={baseProfile} onChange={(event) => setBaseProfile(event.target.value)}><option value="character">角色规则</option><option value="style">画风规则</option></select></Field><Field label="Caption 模式"><select value={captionMode} onChange={(event) => setCaptionMode(event.target.value)}><option value="tags">Tag-only</option><option value="hybrid">Tags + 自然语言</option></select></Field></div>
      <Field label="额外 Refine 规则" hint="只作为项目规则发送给 LLM；不会发送原图"><textarea value={customInstructions} onChange={(event) => setCustomInstructions(event.target.value)} placeholder="例如：保留武器型号；忽略固定发色。" /></Field>
      <div className="form-actions"><Button className="button-secondary" busy={busy === "profile"} onClick={() => void copyProfile("character")}>复制角色 Profile</Button><Button className="button-secondary" busy={busy === "profile"} onClick={() => void copyProfile("style")}>复制画风 Profile</Button><Button busy={busy === "project"} onClick={() => void saveProject()}>保存 Profile</Button></div>
    </Panel>
    <Panel title="安全与隐私">
      <div className="security-list"><div><Shield /><span><strong>原始目录只读</strong><small>扫描器只打开源文件读取；缩略图、数据库和导出写入项目目录。</small></span></div><div><CheckCircle2 /><span><strong>云端不接收原图</strong><small>LLM refine 仅发送 WD14 标签、JoyCaption 文本和规则；API Key 使用 Windows 安全存储。</small></span></div><div><Download /><span><strong>下载可校验</strong><small>本地模型注册时计算 SHA-256；Hugging Face 下载支持 .part 断点续传。</small></span></div></div>
    </Panel>
  </div>;
}
