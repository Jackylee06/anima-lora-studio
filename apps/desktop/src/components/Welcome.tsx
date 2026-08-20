import { useState } from "react";
import type { Project, ProjectProfile } from "@anima/contracts";
import { FolderOpen, Plus, Sparkles } from "lucide-react";
import { Button, Field } from "./ui";
import { errorMessage, rpc } from "../lib/api";

const DEFAULT_TEMPLATE = "pixiv/{AI}/{age}/{user}-{user_id}/{id}-{title}";

export function Welcome({ onOpen }: { onOpen: (project: Project, created: boolean) => void }) {
  const [mode, setMode] = useState<"welcome" | "create">("welcome");
  const [name, setName] = useState("我的 Anima LoRA");
  const [sourceRoot, setSourceRoot] = useState("");
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [profile, setProfile] = useState<ProjectProfile>("character");
  const [trigger, setTrigger] = useState("charx001");
  const [pathTemplate, setPathTemplate] = useState(DEFAULT_TEMPLATE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function choose(setter: (value: string) => void, title: string) {
    const value = await window.anima.chooseDirectory(title);
    if (value) setter(value);
  }

  async function create() {
    setBusy(true); setError(null);
    try {
      const project = await rpc<Project>("project.create", { name, sourceRoot, workspaceRoot, profile, trigger, pathTemplate });
      onOpen(project, true);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }

  async function openExisting() {
    const workspacePath = await window.anima.chooseDirectory("选择 .alora 项目目录");
    if (!workspacePath) return;
    setBusy(true); setError(null);
    try { onOpen(await rpc<Project>("project.open", { workspacePath }), false); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }

  if (mode === "welcome") {
    return <main className="welcome-shell">
      <div className="welcome-card">
        <div className="brand-mark"><Sparkles size={28} /></div>
        <p className="eyebrow">LOCAL · PRIVATE · REPRODUCIBLE</p>
        <h1>Anima LoRA Studio</h1>
        <p className="welcome-copy">从 Pixiv 原图到可复现的 Anima LoRA，用一个只读、可审核的本地工作流完成。</p>
        <div className="welcome-actions">
          <Button onClick={() => setMode("create")}><Plus size={17} />新建项目</Button>
          <Button className="button-secondary" onClick={openExisting} busy={busy}><FolderOpen size={17} />打开项目</Button>
        </div>
        {error && <div className="error-box">{error}</div>}
      </div>
    </main>;
  }

  return <main className="welcome-shell">
    <div className="setup-card">
      <div className="setup-heading"><button className="text-button" onClick={() => setMode("welcome")}>← 返回</button><div><p className="eyebrow">NEW PROJECT</p><h2>创建只读数据集项目</h2></div></div>
      <div className="form-grid two">
        <Field label="项目名称"><input value={name} onChange={(event) => setName(event.target.value)} /></Field>
        <Field label="LoRA 类型"><select value={profile} onChange={(event) => setProfile(event.target.value as ProjectProfile)}><option value="character">角色 LoRA</option><option value="style">画风 LoRA</option><option value="custom">自定义</option></select></Field>
        <Field label="唯一 Trigger" hint="3–32 位字母/数字，不使用空格或下划线"><input value={trigger} onChange={(event) => setTrigger(event.target.value)} /></Field>
        <Field label="Pixiv 源目录（只读）"><div className="path-input"><input value={sourceRoot} readOnly placeholder="选择 pixiv 文件夹" /><Button className="button-icon" onClick={() => choose(setSourceRoot, "选择 Pixiv 源目录")}><FolderOpen size={16} /></Button></div></Field>
        <Field label="项目保存位置"><div className="path-input"><input value={workspaceRoot} readOnly placeholder="选择项目父目录" /><Button className="button-icon" onClick={() => choose(setWorkspaceRoot, "选择项目保存位置")}><FolderOpen size={16} /></Button></div></Field>
        <Field label="Pixiv 路径模板" hint="支持完整 Pixiv 下载器命名标记"><input value={pathTemplate} onChange={(event) => setPathTemplate(event.target.value)} /></Field>
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="form-actions"><Button className="button-secondary" onClick={() => setMode("welcome")}>取消</Button><Button busy={busy} disabled={!sourceRoot || !workspaceRoot || !trigger} onClick={create}>创建项目</Button></div>
    </div>
  </main>;
}
