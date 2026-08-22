import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

describe("gallery view modes", () => {
  it("keeps both overview grid and single-image review available", () => {
    const source = readFileSync(fileURLToPath(new URL("./Gallery.tsx", import.meta.url)), "utf8");

    expect(source).toContain('className="gallery-view-switch segmented"');
    expect(source).toContain('className="gallery-scroll"');
    expect(source).toContain('"review-stage"');
    expect(source).toContain('window.matchMedia("(max-width: 1250px)")');
  });
});
