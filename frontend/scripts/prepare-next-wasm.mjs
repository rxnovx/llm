import { access, lstat, mkdir, readFile, symlink } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);

export async function prepareNextWasm({ force = false } = {}) {
  if (process.platform !== "android" && !force) return;
  const nextDirectory = path.dirname(require.resolve("next/package.json"));
  const wasmDirectory = path.dirname(require.resolve("@next/swc-wasm-nodejs/package.json"));
  const readPackage = async (directory) => JSON.parse(await readFile(path.join(directory, "package.json"), "utf8"));
  const next = await readPackage(nextDirectory);
  const wasm = await readPackage(wasmDirectory);
  if (next.version !== wasm.version) throw new Error("next와 @next/swc-wasm-nodejs 버전을 맞춘 뒤 npm ci로 다시 설치해주세요.");
  await access(path.join(wasmDirectory, "wasm.js"));
  await access(path.join(wasmDirectory, "wasm_bg.wasm"));

  // Next 16's fallback loader reads this cache before downloading SWC again.
  // Link the pinned npm dependency into that cache without patching Next code.
  const target = path.join(nextDirectory, "wasm", "@next", "swc-wasm-nodejs");
  const exists = await lstat(target).then(() => true, (error) => {
    if (error.code === "ENOENT") return false;
    throw error;
  });
  const validateCache = async () => {
    const cached = await readPackage(target);
    if (cached.version !== next.version) throw new Error("Next.js WASM 캐시 버전이 다릅니다. npm ci로 다시 설치해주세요.");
    await access(path.join(target, "wasm.js"));
    await access(path.join(target, "wasm_bg.wasm"));
  };
  if (exists) {
    await validateCache();
    return;
  }
  await mkdir(path.dirname(target), { recursive: true });
  try { await symlink(path.relative(path.dirname(target), wasmDirectory), target, "dir"); }
  catch (error) { if (error.code !== "EEXIST") throw error; }
  await validateCache();
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  await prepareNextWasm({ force: process.argv.includes("--force") });
}
