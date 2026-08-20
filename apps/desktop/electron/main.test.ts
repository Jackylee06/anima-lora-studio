import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

describe("Electron main process build", () => {
  it("uses the CommonJS named export shape exposed by electron-updater", () => {
    const source = readFileSync(fileURLToPath(new URL("./main.ts", import.meta.url)), "utf8");
    const output = ts.transpileModule(source, {
      fileName: "main.ts",
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.Node16,
        esModuleInterop: true,
        allowSyntheticDefaultImports: true
      }
    }).outputText;

    expect(output).not.toContain("electron_updater_1.default");
    expect(output).toContain("electron_updater_1.autoUpdater");
  });
});
