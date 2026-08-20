import type { CaptionRevision, ModelEntry, Project } from "@anima/contracts";
import { useEffect, useState } from "react";
import { Bot, CheckCheck, Play, RefreshCw, Save, Tags, WandSparkles } from "lucide-react";
import { Badge, Button, Empty, Field, Panel } from "../components/ui";
import { errorMessage, rpc } from "../lib/api";

interface CaptionItem extends CaptionRevision { relativePath: string; thumbnailPath: string | null }
interface Frequency { tag: string; count: number; ratio: number; identityCandidate: boolean }

export function CaptionsPage({ project }: { project: Project }) {
  const [captions, setCaptions] = useState<CaptionItem[]>([]);
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [frequencies, setFrequencies] = useState<Frequency[]>([]);
  const [identityOmit, setIdentityOmit] = useState<Set<string>>(new Set());
  const [mock, setMock] = useState(true);
  const [generalThreshold, setGeneralThreshold] = useState(0.35);
  const [characterThreshold, setCharacterThreshold] = useState(0.85);
  const [providerKind, setProviderKind] = useState<"ollama" | "openai">("ollama");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:11434");
  const [llmModel, setLlmModel] = useState("");
  const [busyStage, setBusyStage] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState<Record<string, string>>({});

  async function load() {
    const [captionData, modelData, frequencyData] = await Promise.all([
      rpc<{ items: CaptionItem[] }>("captions.list", { limit: 500 }),
      rpc<ModelEntry[]>("models.list"),
      rpc<{ items: Frequency[] }>("assets.tagFrequency"),
    ]);
    setCaptions(captionData.items); setModels(modelData); setFrequencies(frequencyData.items);
    setIdentityOmit((current) => current.size ? current : new Set(frequencyData.items.filter((item) => item.identityCandidate).map((item) => item.tag)));
  }
  useEffect(() => { void load(); }, []);

  async function runStage(stage: "wd14" | "joycaption" | "refine") {
    setBusyStage(stage); setMessage(null);
    try {
      if (stage === "wd14") {
        const wd = models.find((model) => model.kind === "wd14" && model.status === "ready");
        const wdRoot = wd?.localPath?.toLowerCase().endsWith(".onnx") ? wd.localPath.replace(/[\\/][^\\/]+$/, "") : wd?.localPath;
        await rpc("pipeline.wd14", mock ? { mock: true, modelId: "mock", generalThreshold, characterThreshold } : {
          modelId: wd?.id, modelPath: wd?.localPath?.toLowerCase().endsWith(".onnx") ? wd.localPath : wdRoot ? `${wdRoot}\\model.onnx` : "", tagsPath: wdRoot ? `${wdRoot}\\selected_tags.csv` : "",
          modelFingerprint: wd?.sha256 || JSON.stringify(wd?.metadata.fileHashes || {}), generalThreshold, characterThreshold,
        });
      } else if (stage === "joycaption") {
        const joy = models.find((model) => model.kind === "joycaption");
        await rpc("pipeline.joycaption", mock ? { mock: true, modelId: "mock" } : {
          modelId: "fancyfeast/llama-joycaption-beta-one-hf-llava", precision: "nf4",
          revision: joy?.metadata.revision,
        });
      } else {
        const apiKey = await window.anima.getSecret("llm-api-key");
        await rpc("pipeline.refine", mock ? { mock: true, provider: { kind: "mock" }, identityOmit: [...identityOmit], mode: project.settings.captionMode } : {
          provider: { kind: providerKind, baseUrl, model: llmModel, apiKey }, identityOmit: [...identityOmit], mode: project.settings.captionMode,
        });
      }
      setMessage(`${stage} 任务已进入队列；完成后刷新本页查看结果。`);
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusyStage(null); }
  }

  async function saveApiKey(value: string) { await window.anima.setSecret("llm-api-key", value); setMessage("API Key 已使用 Windows 安全存储保存。"); }
  async function approveAll() { await rpc("captions.setStatus", { assetIds: captions.map((item) => item.assetId), status: "approved" }); await load(); }
  async function save(item: CaptionItem) {
    await rpc("captions.edit", { assetId: item.assetId, finalText: editing[item.assetId] ?? item.finalText, status: "approved" });
    setEditing((current) => { const next = { ...current }; delete next[item.assetId]; return next; });
    await load();
  }

  return <div className="page-content">
    <header className="page-heading compact"><div><p className="eyebrow">CAPTION PIPELINE</p><h1>Caption 生成与审核</h1><p>先生成可追溯的中间结果，再按 Anima Base v1.0 规则确定性组装。</p></div><label className="switch"><input type="checkbox" checked={mock} onChange={(event) => setMock(event.target.checked)} /><span />测试后端</label></header>
    {message && <div className="info-box">{message}</div>}
    <div className="pipeline-grid">
      <Panel title="1 · WD14 Tagging" action={<Badge tone="accent">EVA02 v3</Badge>}>
        <p className="panel-copy">提取 rating、character 和 general 标签；原始置信度完整保留。</p>
        <div className="inline-fields"><Field label="General 阈值"><input type="number" step="0.05" min="0" max="1" value={generalThreshold} onChange={(event) => setGeneralThreshold(Number(event.target.value))} /></Field><Field label="Character 阈值"><input type="number" step="0.05" min="0" max="1" value={characterThreshold} onChange={(event) => setCharacterThreshold(Number(event.target.value))} /></Field></div>
        <Button busy={busyStage === "wd14"} onClick={() => void runStage("wd14")}><Tags size={16} />运行 WD14</Button>
      </Panel>
      <Panel title="2 · JoyCaption" action={<Badge tone="accent">Beta One · NF4</Badge>}>
        <p className="panel-copy">生成客观英文视觉描述；不猜测角色、画师、作品或质量等级。</p>
        <div className="stage-spacer" />
        <Button busy={busyStage === "joycaption"} onClick={() => void runStage("joycaption")}><Bot size={16} />运行 JoyCaption</Button>
      </Panel>
      <Panel title="3 · LLM Refine" action={<Badge tone="accent">Anima rules</Badge>}>
        <p className="panel-copy">LLM 只接收文本中间结果，输出受 JSON Schema 约束的 caption 分区。</p>
        {!mock && <div className="compact-form"><select value={providerKind} onChange={(event) => setProviderKind(event.target.value as "ollama" | "openai")}><option value="ollama">Ollama</option><option value="openai">OpenAI-compatible</option></select><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="Base URL" /><input value={llmModel} onChange={(event) => setLlmModel(event.target.value)} placeholder="模型名称" /><input type="password" placeholder="API Key（失焦保存）" onBlur={(event) => event.target.value && void saveApiKey(event.target.value)} /></div>}
        <Button busy={busyStage === "refine"} disabled={!mock && !llmModel} onClick={() => void runStage("refine")}><WandSparkles size={16} />运行 Refine</Button>
      </Panel>
    </div>
    {frequencies.some((item) => item.identityCandidate) && <Panel title="高频身份特征候选" action={<span className="muted">角色 LoRA 可从 caption 中省略，让 trigger 学习这些恒定特征</span>}>
      <div className="tag-cloud">{frequencies.filter((item) => item.identityCandidate).slice(0, 30).map((item) => <label key={item.tag} className={identityOmit.has(item.tag) ? "tag-chip selected" : "tag-chip"}><input type="checkbox" checked={identityOmit.has(item.tag)} onChange={() => setIdentityOmit((current) => { const next = new Set(current); next.has(item.tag) ? next.delete(item.tag) : next.add(item.tag); return next; })} />{item.tag.replaceAll("_", " ")} <small>{Math.round(item.ratio * 100)}%</small></label>)}</div>
    </Panel>}
    <div className="section-heading"><div><h2>最新 Caption</h2><p>{captions.length} 条；编辑会创建新 revision，不覆盖历史。</p></div><div><Button className="button-secondary" onClick={() => void load()}><RefreshCw size={15} />刷新</Button> <Button disabled={!captions.length} onClick={() => void approveAll()}><CheckCheck size={15} />全部审核通过</Button></div></div>
    {!captions.length ? <Empty title="还没有 Caption" detail="保留图片后依次运行 WD14、JoyCaption 和 LLM Refine。测试后端可在不下载模型时验证流程。" /> : <div className="caption-list">
      {captions.map((item) => <article className="caption-row" key={item.id}>
        <div className="caption-thumb">{item.thumbnailPath && <img src={window.anima.fileUrl(item.thumbnailPath)} alt="" />}</div>
        <div className="caption-editor"><div><strong>{item.relativePath}</strong><Badge tone={item.status === "approved" ? "good" : item.status === "needs_review" ? "warn" : "neutral"}>{item.status}</Badge><span>rev {item.revision}</span></div><textarea value={editing[item.assetId] ?? item.finalText} onChange={(event) => setEditing((current) => ({ ...current, [item.assetId]: event.target.value }))} /></div>
        <Button className="button-secondary" onClick={() => void save(item)}><Save size={15} />保存并通过</Button>
      </article>)}
    </div>}
  </div>;
}
