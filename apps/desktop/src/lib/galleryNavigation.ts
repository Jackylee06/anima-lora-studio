import type { ReviewState } from "@anima/contracts";

export type ReviewFilter = ReviewState | "all";

export function shouldIgnoreGalleryShortcut(target: EventTarget | null) {
  if (!target || typeof (target as HTMLElement).matches !== "function") return false;
  const element = target as HTMLElement;
  return element.isContentEditable || element.matches("input,textarea,select");
}

export function cursorWindow(cursor: number, total: number, limit = 3) {
  const safeTotal = Math.max(0, total);
  const safeLimit = Math.max(1, Math.min(limit, safeTotal || limit));
  const safeCursor = safeTotal ? Math.max(0, Math.min(cursor, safeTotal - 1)) : 0;
  const offset = Math.max(0, Math.min(safeCursor - 1, Math.max(0, safeTotal - safeLimit)));
  return { offset, current: safeCursor - offset, limit: safeLimit };
}

export function reviewCursorAfterStateChange(
  cursor: number,
  total: number,
  activeFilter: ReviewFilter,
  nextState: ReviewState,
) {
  const disappears = activeFilter !== "all" && activeFilter !== nextState;
  const nextTotal = Math.max(0, total - (disappears ? 1 : 0));
  if (nextTotal === 0) return { cursor: 0, total: 0 };
  const nextCursor = disappears ? cursor : cursor + 1;
  return { cursor: Math.max(0, Math.min(nextCursor, nextTotal - 1)), total: nextTotal };
}
