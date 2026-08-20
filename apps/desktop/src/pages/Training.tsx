import { defaultTrainingConfig, type AssetPage, type ModelEntry, type Project, type TrainingRun, type WorkerEvent } from "@anima/contracts";
import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, FileJson, FolderOpen, Pause, Play, ShieldCheck, Square, TestTube2 } from "lucide-react";
import { Badge, Button, Field, Panel } from "../components/ui";
import { errorMessage, onWorkerEvent, rpc } from "../lib/api";
import { progressPercent, upsertNewest } from "../lib/jobs";

interface Job { id: string; kind: string; state: string; progressCurrent: number; progressTotal: number; message: string; result?: Record<string, unknown>; error?: string }
interface TrainingSample { runId: string; path: string; name: string; relativePath: string; modifiedAt: number }

export function TrainingPage({ project }: { project: Project }) {
  const [kept, setKept] = useState(0);
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [samples, setSamples] = useState<TrainingSample[]>([]);
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [exportPath, setExportPath] = useState("");
  const [engineRoot, setEngineRoot] = useState("");
  const [trainerPython, setTrainerPython] = useState("");
  const [animaBasePath, setAnimaBasePath] = useState("");
  const [qwen3Path, setQwen3Path] = useState("");
  const [vaePath, setVaePath] = useState("");
  const [plan, setPlan] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const effectiveProfile = project.profile === "custom" ? String(project.settings.baseProfile || "character") as "character" | "style" : project.profile;
  const config = useMemo(() => defaultTrainingConfig(effectiveProfile, kept), [effectiveProfile, kept]);

  async function load() {
    const [assetData, modelData, runData, jobData, license, runtime, sampleData] = await Promise.all([
      rpc<AssetPage>("assets.query", { reviewState: "kept", eligible: true, limit: 1 }),
      rpc<ModelEntry[]>("models.list"), rpc<TrainingRun[]>("training.list"), rpc<Job[]>("jobs.list", { limit: 50 }),
      rpc<{ accepted: boolean }>("license.status"), rpc<{ environments: Record<string, { ready: boolean; python: string }>; engine: { ready: boolean; root: string } | null }>("runtime.status"),
      rpc<TrainingSample[]>("training.samples"),
    ]);
    setKept(assetData.total); setModels(modelData); setRuns(runData); setJobs(jobData); setLicenseAccepted(license.accepted); setSamples(sampleData);
    if (runtime.environments.trainer?.ready) setTrainerPython(runtime.environments.trainer.python);
    if (runtime.engine?.ready) setEngineRoot((value) => value || runtime.engine!.root);
    const ready = (kind: ModelEntry["kind"]) => modelData.find((model) => model.kind === kind && model.status === "ready")?.localPath || "";
    setAnimaBasePath((value) => value || ready("anima_base")); setQwen3Path((value) => value || ready("qwen3")); setVaePath((value) => value || ready("vae"));
  }
  useEffect(() => {
    void load();
    return onWorkerEvent((event: WorkerEvent) => {
      if (event.event !== "job.updated") return;
      const job = event.data as Job;
      setJobs((current) => upsertNewest(current, job));
      if (job.kind === "export" && job.state === "succeeded" && job.result?.path) setExportPath(String(job.result.path));
      if (job.kind === "training" && ["succeeded", "failed", "paused", "cancelled"].includes(job.state)) void load();
    });
  }, []);

  async function acceptLicense() {
    const result = await rpc<{ accepted: boolean }>("license.accept", { accepted: true }); setLicenseAccepted(result.accepted);
  }
  async function exportData() {
    setBusy("export"); setMessage(null);
    try { await rpc("export.start", { requireApproved: true, trainingConfig: config }); setMessage("导出任务已进入队列。完成后路径会自动填入。"); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }
  function trainingParams() { return { exportPath, engineRoot, trainerPython, animaBasePath, qwen3Path, vaePath, trainingConfig: config, outputName: project.name }; }
  async function preview() {
    setBusy("plan"); setMessage(null);
    try { setPlan(await rpc<Record<string, unknown>>("training.plan", trainingParams())); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }
  async function start() {
    setBusy("start"); setMessage(null);
    try { await rpc("training.start", trainingParams()); setMessage("训练任务已进入 GPU 单队列。"); }
    catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }
  async function resume(run: TrainingRun) {
    if (!run.latestCheckpoint) { setMessage("该暂停记录没有可恢复的 checkpoint。"); return; }
    setBusy(`resume-${run.id}`); setMessage(null);
    try {
      await rpc("training.start", {
        ...trainingParams(), exportPath: run.exportPath, trainingConfig: run.config, resumePath: run.latestCheckpoint,
      });
      setMessage("已使用原数据快照、原配置和 checkpoint 创建新的恢复 run。");
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(null); }
  }
  async function chooseDirectory(setter: (value: string) => void, title: string) { const value = await window.anima.chooseDirectory(title); if (value) setter(value); }
  async function chooseFile(setter: (value: string) => void, extensions: string[]) { const value = await window.anima.chooseFile([{ name: "模型/程序", extensions }]); if (value) setter(value); }

  return <div className="page-content">
    <header className="page-heading compact"><div><p className="eyebrow">TRAIN & EVALUATE</p><h1>训练与固定种子评估</h1><p>训练基于冻结的导出快照；安全预设只训练 DiT，并冻结 Qwen3 与 LLM adapter。</p></div><Badge tone={licenseAccepted ? "good" : "warn"}>{licenseAccepted ? "许可已确认" : "需要确认许可"}</Badge></header>
    {message && <div className="info-box">{message}</div>}
    {!licenseAccepted && <Panel className="license-panel"><div className="license-content"><ShieldCheck size={32} /><div><h3>CircleStone Labs 模型许可</h3><p>Anima 模型及衍生模型受官方非商业许可约束。继续前请阅读 Hugging Face 模型卡中的完整条款。</p><a href="https://huggingface.co/circlestone-labs/Anima" target="_blank" rel="noreferrer">打开官方许可说明 ↗</a></div><Button onClick={() => void acceptLicense()}>我已阅读并接受</Button></div></Panel>}
    <div className="training-grid">
      <Panel title="数据快照" action={<Badge tone={kept ? "good" : "warn"}>{kept} 张已保留</Badge>}>
        <p className="panel-copy">仅导出已保留且 caption 状态为 approved 的图片。硬链接失败时自动回退到复制。</p>
        <Button busy={busy === "export"} disabled={!kept} onClick={() => void exportData()}><FileJson size={16} />冻结并导出训练集</Button>
        {exportPath && <code className="path-code">{exportPath}</code>}
      </Panel>
      <Panel title="安全预设" action={<Badge tone="accent">Anima Base v1.0</Badge>}>
        <dl className="preset-list"><div><dt>Rank / Alpha</dt><dd>{config.networkDim} / {config.networkAlpha}</dd></div><div><dt>学习率</dt><dd>{config.learningRate}</dd></div><div><dt>有效 Batch</dt><dd>{config.batchSize * config.gradientAccumulationSteps}</dd></div><div><dt>总步数</dt><dd>{config.maxTrainSteps}</dd></div><div><dt>分辨率桶</dt><dd>{config.minBucketResolution}–{config.maxBucketResolution}</dd></div><div><dt>保存间隔</dt><dd>{config.saveEverySteps}</dd></div></dl>
      </Panel>
    </div>
    <Panel title="训练后端与模型路径">
      <div className="form-grid two training-paths">
        <Field label="sd-scripts 目录"><PathPicker value={engineRoot} onPick={() => chooseDirectory(setEngineRoot, "选择 kohya-ss/sd-scripts 目录")} /></Field>
        <Field label="Trainer Python"><PathPicker value={trainerPython} onPick={() => chooseFile(setTrainerPython, ["exe"])} /></Field>
        <Field label="Anima Base v1.0"><PathPicker value={animaBasePath} onPick={() => chooseFile(setAnimaBasePath, ["safetensors"])} /></Field>
        <Field label="Qwen3 0.6B"><PathPicker value={qwen3Path} onPick={() => chooseFile(setQwen3Path, ["safetensors"])} /></Field>
        <Field label="Qwen Image VAE"><PathPicker value={vaePath} onPick={() => chooseFile(setVaePath, ["safetensors", "pth"])} /></Field>
        <Field label="导出快照"><PathPicker value={exportPath} onPick={() => chooseDirectory(setExportPath, "选择训练集导出目录")} /></Field>
      </div>
      <div className="form-actions"><Button className="button-secondary" busy={busy === "plan"} disabled={!licenseAccepted || !exportPath} onClick={() => void preview()}><TestTube2 size={16} />前检与命令预览</Button><Button busy={busy === "start"} disabled={!licenseAccepted || !exportPath || !engineRoot || !trainerPython || !animaBasePath || !qwen3Path || !vaePath} onClick={() => void start()}><Play size={16} />开始训练</Button></div>
      {plan && <details className="command-preview" open><summary>训练命令与配置</summary><pre>{JSON.stringify(plan, null, 2)}</pre></details>}
    </Panel>
    <div className="section-heading"><div><h2>任务与训练记录</h2><p>暂停会在下一个保存点结束进程；恢复使用相同快照与配置。</p></div></div>
    <div className="run-list">
      {jobs.filter((job) => ["training", "export"].includes(job.kind)).map((job) => <article key={job.id} className="run-row"><div className="run-icon">{job.state === "succeeded" ? <CheckCircle2 /> : <Play />}</div><div className="run-main"><div><strong>{job.kind === "training" ? "Anima LoRA 训练" : "数据集导出"}</strong><Badge tone={job.state === "succeeded" ? "good" : job.state === "failed" ? "bad" : job.state === "paused" ? "warn" : "accent"}>{job.state}</Badge></div><p>{job.error || job.message || job.id}</p><div className="progress-track"><span style={{ width: `${progressPercent(job)}%` }} /></div></div>{["running", "pause_requested"].includes(job.state) && <div className="run-actions"><Button className="button-secondary" onClick={() => void rpc("jobs.pause", { jobId: job.id })}><Pause size={14} /></Button><Button className="button-danger" onClick={() => void rpc("jobs.cancel", { jobId: job.id })}><Square size={13} /></Button></div>}</article>)}
      {runs.map((run) => <article key={run.id} className="run-row historical"><div className="run-icon"><CheckCircle2 /></div><div className="run-main"><div><strong>{run.id.slice(-12)}</strong><Badge tone={run.state === "succeeded" ? "good" : run.state === "failed" ? "bad" : "warn"}>{run.state}</Badge></div><p>{run.latestCheckpoint || run.outputPath}</p><small>{run.currentStep}/{run.totalSteps} steps{run.latestLoss != null ? ` · loss ${run.latestLoss}` : ""}</small></div>{run.state === "paused" && <Button busy={busy === `resume-${run.id}`} className="button-secondary" onClick={() => void resume(run)}><Play size={14} />恢复为新 run</Button>}</article>)}
    </div>
    {samples.length > 0 && <><div className="section-heading"><div><h2>固定种子样图矩阵</h2><p>最新训练 run 的角色/画风泛化、无 trigger 对照与不同保存步样图。</p></div><Badge tone="accent">{samples.length} 张</Badge></div><div className="sample-grid">{samples.map((sample) => <figure key={sample.path}><img src={window.anima.fileUrl(sample.path)} alt={sample.name} loading="lazy" /><figcaption title={sample.relativePath}>{sample.relativePath}</figcaption></figure>)}</div></>}
  </div>;
}

function PathPicker({ value, onPick }: { value: string; onPick: () => void }) {
  return <div className="path-input"><input value={value} readOnly placeholder="尚未选择" /><Button className="button-icon" onClick={onPick}><FolderOpen size={16} /></Button></div>;
}
