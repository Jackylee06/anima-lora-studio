import { describe, expect, it } from "vitest";
import { cursorWindow, reviewCursorAfterStateChange, shouldIgnoreGalleryShortcut } from "./galleryNavigation";

describe("single-image review navigation", () => {
  it("keeps the cursor on the shifted next item when the reviewed item leaves the filter", () => {
    expect(reviewCursorAfterStateChange(4, 10, "pending", "kept")).toEqual({ cursor: 4, total: 9 });
    expect(reviewCursorAfterStateChange(9, 10, "pending", "rejected")).toEqual({ cursor: 8, total: 9 });
  });

  it("advances when the reviewed item stays in the active result set", () => {
    expect(reviewCursorAfterStateChange(4, 10, "all", "kept")).toEqual({ cursor: 5, total: 10 });
    expect(reviewCursorAfterStateChange(4, 10, "kept", "kept")).toEqual({ cursor: 5, total: 10 });
    expect(reviewCursorAfterStateChange(9, 10, "all", "rejected")).toEqual({ cursor: 9, total: 10 });
  });

  it("loads a three-image window around the cursor including boundaries", () => {
    expect(cursorWindow(0, 10)).toEqual({ offset: 0, current: 0, limit: 3 });
    expect(cursorWindow(5, 10)).toEqual({ offset: 4, current: 1, limit: 3 });
    expect(cursorWindow(9, 10)).toEqual({ offset: 7, current: 2, limit: 3 });
  });

  it("keeps shortcuts active after entering from a focused navigation button", () => {
    const target = (tag: string, contentEditable = false) => ({
      isContentEditable: contentEditable,
      matches: (selector: string) => selector.split(",").includes(tag),
    }) as unknown as EventTarget;

    expect(shouldIgnoreGalleryShortcut(target("button"))).toBe(false);
    expect(shouldIgnoreGalleryShortcut(target("input"))).toBe(true);
    expect(shouldIgnoreGalleryShortcut(target("div", true))).toBe(true);
  });
});
