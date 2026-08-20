import { describe, expect, it } from "vitest";
import { progressPercent, upsertNewest } from "./jobs";

describe("job view helpers", () => {
  it("clamps progress and handles unknown totals", () => {
    expect(progressPercent({ id: "a", progressCurrent: 8, progressTotal: 10 })).toBe(80);
    expect(progressPercent({ id: "a", progressCurrent: 20, progressTotal: 10 })).toBe(100);
    expect(progressPercent({ id: "a", progressCurrent: 1, progressTotal: 0 })).toBe(0);
  });

  it("moves updates to the front without duplicates", () => {
    expect(upsertNewest([{ id: "a", value: 1 }, { id: "b", value: 2 }], { id: "b", value: 3 }))
      .toEqual([{ id: "b", value: 3 }, { id: "a", value: 1 }]);
  });
});
