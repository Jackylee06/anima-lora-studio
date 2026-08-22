import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

describe("job notification layout", () => {
  it("reserves a stable dismiss column before and after a job completes", () => {
    const styles = readFileSync(fileURLToPath(new URL("./styles.css", import.meta.url)), "utf8");
    const toastRule = styles.match(/\.job-toast\s*\{([^}]*)\}/)?.[1] || "";

    expect(toastRule).toContain("grid-template-columns: 30px minmax(0, 1fr) 20px");
    expect(styles).toContain(".job-toast > div:nth-child(2) { min-width: 0; }");
    expect(styles).toContain(".job-toast .mini-progress { width: 100%; }");
  });
});
