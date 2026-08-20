import { useVirtualizer } from "@tanstack/react-virtual";
import type { Asset, AssetPage, ReviewState } from "@anima/contracts";
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, ImageOff, RotateCcw, Search, X } from "lucide-react";
import clsx from "clsx";
import { Badge, Button, Empty } from "../components/ui";
import { errorMessage, rpc } from "../lib/api";

const COLUMNS = 4;

export function Gallery() {
  const [data, setData] = useState<AssetPage>({ items: [], total: 0, counts: { pending: 0, kept: 0, rejected: 0 } });
  const [reviewState, setReviewState] = useState<ReviewState | "all">("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function load() {
    setBusy(true); setError(null);
    try {
      setData(await rpc<AssetPage>("assets.query", { reviewState, search, limit: 500, sort: "score_desc" }));
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  useEffect(() => { const timer = setTimeout(() => void load(), 150); return () => clearTimeout(timer); }, [reviewState, search]);

  const rows = useMemo(() => Array.from({ length: Math.ceil(data.items.length / COLUMNS) }, (_, index) => data.items.slice(index * COLUMNS, index * COLUMNS + COLUMNS)), [data.items]);
  const virtualizer = useVirtualizer({ count: rows.length, getScrollElement: () => scrollRef.current, estimateSize: () => 332, overscan: 2 });

  async function setState(state: ReviewState, ids = [...selected]) {
    if (!ids.length) return;
    await rpc("assets.setReview", { assetIds: ids, reviewState: state });
    setSelected(new Set());
    await load();
  }

  function toggle(asset: Asset) {
    setSelected((current) => { const next = new Set(current); next.has(asset.id) ? next.delete(asset.id) : next.add(asset.id); return next; });
  }

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement)?.matches("input,textarea,select")) return;
      if (event.key.toLowerCase() === "k") void setState("kept");
      if (event.key.toLowerCase() === "x") void setState("rejected");
      if (event.key.toLowerCase() === "u") void setState("pending");
    };
    window.addEventListener("keydown", handler); return () => window.removeEventListener("keydown", handler);
  }, [selected]);

  return <div className="page-content gallery-page">
    <header className="page-heading compact"><div><p className="eyebrow">CURATION</p><h1>图片筛选</h1><p>自动指标只负责排序；最终保留与排除始终由你确认。</p></div><div className="shortcut-hint"><kbd>K</kbd>保留 <kbd>X</kbd>排除 <kbd>U</kbd>待定</div></header>
    <div className="toolbar">
      <div className="segmented">
        {(["all", "pending", "kept", "rejected"] as const).map((state) => <button key={state} className={clsx(reviewState === state && "active")} onClick={() => setReviewState(state)}>{state === "all" ? "全部" : state === "pending" ? "待定" : state === "kept" ? "保留" : "排除"}<span>{state === "all" ? data.total : data.counts[state]}</span></button>)}
      </div>
      <div className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索路径、作者、标题或标签" /></div>
      <Button className="button-secondary" onClick={() => setSelected(new Set(data.items.map((item) => item.id)))}>全选当前</Button>
    </div>
    {selected.size > 0 && <div className="bulk-bar"><strong>已选择 {selected.size} 张</strong><Button onClick={() => void setState("kept")}><Check size={15} />保留</Button><Button className="button-danger" onClick={() => void setState("rejected")}><X size={15} />排除</Button><Button className="button-secondary" onClick={() => void setState("pending")}><RotateCcw size={15} />待定</Button></div>}
    {error && <div className="error-box">{error}</div>}
    {!busy && data.items.length === 0 ? <Empty title="还没有可显示的图片" detail="回到工作台扫描 Pixiv 源目录，或调整当前筛选条件。" /> :
      <div ref={scrollRef} className="gallery-scroll"><div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((virtualRow) => <div key={virtualRow.key} className="gallery-row" style={{ transform: `translateY(${virtualRow.start}px)` }}>
          {rows[virtualRow.index]?.map((asset) => <AssetCard key={asset.id} asset={asset} selected={selected.has(asset.id)} onToggle={() => toggle(asset)} onState={(state) => void setState(state, [asset.id])} />)}
        </div>)}
      </div></div>}
  </div>;
}

function AssetCard({ asset, selected, onToggle, onState }: { asset: Asset; selected: boolean; onToggle: () => void; onState: (state: ReviewState) => void }) {
  const metadata = asset.metadata;
  return <article className={clsx("asset-card", selected && "selected", asset.reviewState)}>
    <button className="asset-image" onClick={onToggle}>
      {asset.thumbnailPath ? <img src={window.anima.fileUrl(asset.thumbnailPath)} alt={asset.relativePath} loading="lazy" /> : <ImageOff size={30} />}
      <span className={clsx("select-dot", selected && "checked")}>{selected && <Check size={13} />}</span>
      {asset.metrics.duplicateGroup && <Badge tone="warn"><Copy size={12} />近重复</Badge>}
      {asset.metrics.semanticGroup && <Badge tone="accent">语义相似</Badge>}
      {!asset.eligible && <span className="excluded-overlay">{asset.exclusionReason}</span>}
    </button>
    <div className="asset-body"><strong title={asset.relativePath}>{String(metadata.title || asset.relativePath.split("/").pop())}</strong><p>{String(metadata.user || "未知作者")} · {String(metadata.age || "未标年龄")}</p><div className="metric-row"><span>{asset.metrics.width || "?"}×{asset.metrics.height || "?"}</span><span>清晰 {asset.metrics.blurScore?.toFixed(1) || "-"}</span><span>评分 {asset.metrics.technicalScore?.toFixed(0) || "-"}</span></div></div>
    <div className="asset-actions"><button title="保留" className={clsx(asset.reviewState === "kept" && "active good")} onClick={() => onState("kept")}><Check size={15} /></button><button title="待定" className={clsx(asset.reviewState === "pending" && "active")} onClick={() => onState("pending")}><RotateCcw size={14} /></button><button title="排除" className={clsx(asset.reviewState === "rejected" && "active bad")} onClick={() => onState("rejected")}><X size={15} /></button></div>
  </article>;
}
