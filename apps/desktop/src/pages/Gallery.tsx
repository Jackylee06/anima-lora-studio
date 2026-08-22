import type { Asset, AssetPage, AssetQuery, ReviewState } from "@anima/contracts";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Copy, ImageOff, LoaderCircle, RotateCcw, Search, X } from "lucide-react";
import clsx from "clsx";
import { Badge, Button, Empty } from "../components/ui";
import { cursorWindow, reviewCursorAfterStateChange, shouldIgnoreGalleryShortcut, type ReviewFilter } from "../lib/galleryNavigation";
import { errorMessage, rpc } from "../lib/api";

type Sort = NonNullable<AssetQuery["sort"]>;

const EMPTY_PAGE: AssetPage = { items: [], total: 0, counts: { pending: 0, kept: 0, rejected: 0 } };

export function Gallery({ refreshToken }: { refreshToken: number }) {
  const [data, setData] = useState<AssetPage>(EMPTY_PAGE);
  const [windowOffset, setWindowOffset] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [reviewState, setReviewState] = useState<ReviewFilter>("pending");
  const [sort, setSort] = useState<Sort>("path");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  async function load(targetCursor = cursor, totalHint = data.total) {
    const requestId = ++requestSequence.current;
    setLoading(true);
    setError(null);
    try {
      let reviewWindow = cursorWindow(targetCursor, totalHint);
      let page = await rpc<AssetPage>("assets.query", {
        kind: "image", reviewState, search, sort, offset: reviewWindow.offset, limit: reviewWindow.limit,
      });
      const normalizedCursor = page.total ? Math.min(Math.max(0, targetCursor), page.total - 1) : 0;
      const normalizedWindow = cursorWindow(normalizedCursor, page.total);
      if (page.total && normalizedWindow.offset !== reviewWindow.offset) {
        reviewWindow = normalizedWindow;
        page = await rpc<AssetPage>("assets.query", {
          kind: "image", reviewState, search, sort, offset: reviewWindow.offset, limit: reviewWindow.limit,
        });
      } else {
        reviewWindow = normalizedWindow;
      }
      if (requestId !== requestSequence.current) return;
      setData(page);
      setCursor(normalizedCursor);
      setWindowOffset(reviewWindow.offset);
    } catch (caught) {
      if (requestId === requestSequence.current) setError(errorMessage(caught));
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }

  useEffect(() => {
    const timer = setTimeout(() => void load(0, 0), 180);
    return () => clearTimeout(timer);
  }, [reviewState, search, sort, refreshToken]);

  const currentIndex = cursor - windowOffset;
  const previous = currentIndex > 0 ? data.items[currentIndex - 1] : undefined;
  const current = data.items[currentIndex];
  const next = currentIndex >= 0 ? data.items[currentIndex + 1] : undefined;

  async function move(delta: number) {
    if (loading || reviewing || !data.total) return;
    const target = Math.max(0, Math.min(cursor + delta, data.total - 1));
    if (target !== cursor) await load(target, data.total);
  }

  async function setState(state: ReviewState) {
    if (!current || reviewing) return;
    setReviewing(true);
    setError(null);
    try {
      await rpc("assets.setReview", { assetIds: [current.id], reviewState: state });
      const target = reviewCursorAfterStateChange(cursor, data.total, reviewState, state);
      await load(target.cursor, target.total);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setReviewing(false);
    }
  }

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (shouldIgnoreGalleryShortcut(event.target)) return;
      const key = event.key.toLowerCase();
      if (["arrowleft", "arrowright", "a", "d", "k", "x", "u"].includes(key)) event.preventDefault();
      if (key === "arrowleft" || key === "a") void move(-1);
      if (key === "arrowright" || key === "d") void move(1);
      if (key === "k") void setState("kept");
      if (key === "x") void setState("rejected");
      if (key === "u") void setState("pending");
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [current?.id, cursor, data.total, reviewState, loading, reviewing]);

  const position = data.total ? `${cursor + 1} / ${data.total}` : "0 / 0";
  const metadata = current?.metadata || {};
  const title = current ? String(metadata.title || current.relativePath.split("/").pop()) : "";
  const detailItems = useMemo(() => current ? [
    String(metadata.user || "未知作者"),
    String(metadata.age || "未标年龄"),
    current.metrics.width && current.metrics.height ? `${current.metrics.width}×${current.metrics.height}` : "尺寸分析中",
    current.metrics.technicalScore == null ? "评分分析中" : `技术评分 ${current.metrics.technicalScore.toFixed(0)}`,
  ] : [], [current]);

  return <div className="page-content gallery-page review-page">
    <header className="page-heading compact review-heading">
      <div><p className="eyebrow">CURATION</p><h1>图片筛选</h1><p>逐张审核；保留、排除或待定后自动进入下一张。</p></div>
      <div className="shortcut-hint"><kbd>A</kbd><kbd>D</kbd>切换 <kbd>K</kbd>保留 <kbd>X</kbd>排除 <kbd>U</kbd>待定</div>
    </header>

    <div className="toolbar review-toolbar">
      <div className="segmented">
        {(["all", "pending", "kept", "rejected"] as const).map((state) => <button key={state} className={clsx(reviewState === state && "active")} onClick={() => setReviewState(state)}>
          {state === "all" ? "全部" : state === "pending" ? "待定" : state === "kept" ? "保留" : "排除"}
          <span>{state === "all" ? Object.values(data.counts).reduce((sum, value) => sum + value, 0) : data.counts[state]}</span>
        </button>)}
      </div>
      <div className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索路径、作者或标题" /></div>
      <select className="review-sort" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
        <option value="path">按路径顺序</option>
        <option value="score_desc">按技术评分</option>
        <option value="updated_desc">按更新时间</option>
      </select>
    </div>

    {error && <div className="error-box">{error}</div>}
    {!loading && !current ? <Empty title="当前筛选没有图片" detail="切换顶部状态、清除搜索，或回到工作台扫描源目录。" /> : <>
      <div className="review-progress"><span>{position}</span><div><i style={{ width: `${data.total ? (cursor + 1) / data.total * 100 : 0}%` }} /></div></div>
      <div className={clsx("review-stage", (loading || reviewing) && "busy")}>
        <SidePreview asset={previous} direction="previous" onClick={() => void move(-1)} />
        <figure className={clsx("review-current", current?.reviewState)}>
          {current?.kind === "image" ? <img key={current.id} src={window.anima.fileUrl(current.sourcePath)} alt={current.relativePath} /> : <div className="review-image-missing"><ImageOff /><span>{current?.exclusionReason || "无法显示图片"}</span></div>}
          {current && !current.eligible && <span className="review-exclusion">{current.exclusionReason}</span>}
          {(loading || reviewing) && <div className="review-loading"><LoaderCircle className="spin" /></div>}
        </figure>
        <SidePreview asset={next} direction="next" onClick={() => void move(1)} />
      </div>

      {current && <div className="review-info">
        <div className="review-title"><strong title={current.relativePath}>{title}</strong><p title={current.relativePath}>{current.relativePath}</p></div>
        <div className="review-meta">{detailItems.map((item) => <span key={item}>{item}</span>)}</div>
        <div className="review-badges">
          <Badge tone={current.reviewState === "kept" ? "good" : current.reviewState === "rejected" ? "warn" : "accent"}>{current.reviewState === "kept" ? "已保留" : current.reviewState === "rejected" ? "已排除" : "待定"}</Badge>
          {current.metrics.duplicateGroup && <Badge tone="warn"><Copy size={12} />近重复</Badge>}
          {current.metrics.semanticGroup && <Badge tone="accent">语义相似</Badge>}
        </div>
      </div>}

      <div className="review-actions">
        <Button className="button-danger" disabled={!current || reviewing} onClick={() => void setState("rejected")}><X size={18} />排除 <kbd>X</kbd></Button>
        <Button className="button-secondary" disabled={!current || reviewing} onClick={() => void setState("pending")}><RotateCcw size={17} />待定 <kbd>U</kbd></Button>
        <Button disabled={!current || reviewing} onClick={() => void setState("kept")}><Check size={18} />保留 <kbd>K</kbd></Button>
      </div>
    </>}
  </div>;
}

function SidePreview({ asset, direction, onClick }: { asset?: Asset; direction: "previous" | "next"; onClick: () => void }) {
  const label = direction === "previous" ? "上一张" : "下一张";
  const Icon = direction === "previous" ? ArrowLeft : ArrowRight;
  return <button className={clsx("review-side", direction)} disabled={!asset} onClick={onClick} aria-label={label}>
    {asset ? <>
      <div>{asset.thumbnailPath ? <img src={window.anima.fileUrl(asset.thumbnailPath)} alt={asset.relativePath} /> : asset.kind === "image" ? <img src={window.anima.fileUrl(asset.sourcePath)} alt={asset.relativePath} /> : <ImageOff />}</div>
      <span><Icon size={15} />{label}</span>
    </> : <><div className="review-side-empty"><ImageOff /></div><span><Icon size={15} />{label}</span></>}
  </button>;
}
