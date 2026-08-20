import type { Project } from "@anima/contracts";
import { Captions, Database, Images, LayoutDashboard, Settings2, SlidersHorizontal, Sparkles } from "lucide-react";
import clsx from "clsx";

export type Page = "dashboard" | "gallery" | "captions" | "training" | "settings";

const items: Array<{ id: Page; label: string; icon: typeof Images }> = [
  { id: "dashboard", label: "工作台", icon: LayoutDashboard },
  { id: "gallery", label: "图片筛选", icon: Images },
  { id: "captions", label: "Caption", icon: Captions },
  { id: "training", label: "训练与评估", icon: SlidersHorizontal },
  { id: "settings", label: "模型与设置", icon: Settings2 },
];

export function Sidebar({ project, page, onPage }: { project: Project; page: Page; onPage: (page: Page) => void }) {
  return <aside className="sidebar">
    <div className="sidebar-brand"><div className="mini-mark"><Sparkles size={18} /></div><div><strong>Anima</strong><span>LoRA Studio</span></div></div>
    <nav>{items.map((item) => <button key={item.id} className={clsx("nav-item", page === item.id && "active")} onClick={() => onPage(item.id)}><item.icon size={18} /><span>{item.label}</span></button>)}</nav>
    <div className="sidebar-project"><Database size={16} /><div><span>当前项目</span><strong title={project.name}>{project.name}</strong><small>{project.profile === "character" ? "角色" : project.profile === "style" ? "画风" : "自定义"} · {project.trigger}</small></div></div>
  </aside>;
}

