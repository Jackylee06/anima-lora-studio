import { rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDirectory, "../apps/desktop");

for (const relativePath of ["dist", "dist-electron"]) {
  const target = path.resolve(desktopRoot, relativePath);
  if (path.dirname(target) !== desktopRoot) throw new Error(`拒绝清理意外路径：${target}`);
  rmSync(target, { recursive: true, force: true });
}
