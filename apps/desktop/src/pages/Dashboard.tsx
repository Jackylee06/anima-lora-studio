import type { AssetPage, Project } from "@anima/contracts";
import { useEffect, useState } from "react";
import { ArrowRight, CheckCircle2, Database, Images, ScanSearch, Tags } from "lucide-react";
import { Button, Panel } from "../components/ui";
import type { Page } from "../components/Sidebar";
import { errorMessage, rpc } from "../lib/api";

export function Dashboard({ project, onPage, refreshToken }: { project: Project; onPage: (page: Page) => void; refreshToken: number }) {
  const [summary, setSummary] = useState<AssetPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function refresh() {
    setSummary(await rpc<AssetPage>("assets.query", { limit: 1 }));
  }
  useEffect(() => { void refresh(); }, [project.id, refreshToken]);

  async function scan() {
    setBusy(true); setMessage(null);
    try {
      const job = await rpc<{ id: string }>("scan.start");
      setMessage(`扫描任务已加入队列：${job.id.slice(-8)}`);
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); }
  }

  const total = summary?.total || 0;
  return <div className="page-content">
    <header className="page-heading"><div><p className="eyebrow">WORKSPACE</p><h1>{project.name}</h1><p>原始目录保持只读。筛选、caption 与训练实验都记录在独立项目中。</p></div><Button onClick={scan} busy={busy}><ScanSearch size={17} />扫描源目录</Button></header>
    {message && <div className="info-box">{message}</div>}
    <div className="stat-grid">
      <div className="stat-card"><Images /><span>已索引图片</span><strong>{total}</strong></div>
      <div className="stat-card accent"><CheckCircle2 /><span>已保留</span><strong>{summary?.counts.kept || 0}</strong></div>
      <div className="stat-card"><Tags /><span>待审核</span><strong>{summary?.counts.pending || 0}</strong></div>
      <div className="stat-card"><Database /><span>已排除</span><strong>{summary?.counts.rejected || 0}</strong></div>
    </div>
    <div className="dashboard-grid">
      <Panel title="工作流">
        <div className="workflow-list">
          {[
            ["01", "导入与筛选", "扫描 Pixiv 目录，处理损坏、低清和近重复图片。", "gallery"],
            ["02", "Caption 管线", "运行 WD14、JoyCaption 与基于 Anima 规则的 LLM refine。", "captions"],
            ["03", "导出与训练", "冻结数据快照，生成 sd-scripts 配置并管理训练。", "training"],
          ].map(([index, title, detail, page]) => <button key={index} className="workflow-item" onClick={() => onPage(page as Page)}><span>{index}</span><div><strong>{title}</strong><p>{detail}</p></div><ArrowRight size={17} /></button>)}
        </div>
      </Panel>
      <Panel title="项目约束">
        <dl className="detail-list"><div><dt>LoRA 类型</dt><dd>{project.profile}</dd></div><div><dt>Trigger</dt><dd>{project.profile === "style" ? "@" : ""}{project.trigger}</dd></div><div><dt>源目录</dt><dd title={project.sourceRoot}>{project.sourceRoot}</dd></div><div><dt>路径模板</dt><dd title={project.pathTemplate}>{project.pathTemplate}</dd></div><div><dt>规则包</dt><dd>Anima Base v1.0</dd></div></dl>
      </Panel>
    </div>
  </div>;
}
