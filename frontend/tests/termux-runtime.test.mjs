import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";
import { createRequire } from "node:module";
import { prepareNextWasm } from "../scripts/prepare-next-wasm.mjs";

const require = createRequire(import.meta.url);
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("the pinned WASM compiler handles TypeScript through Next's fallback loader", async () => {
  await prepareNextWasm({ force: true });
  const bindings = await require("next/dist/build/swc").loadBindings(true);
  assert.equal(bindings.isWasm, true);
  const result = await bindings.transform("const value: number = 6 * 7; globalThis.answer = value;", {
    filename: "portable.ts", jsc: { parser: { syntax: "typescript" }, target: "es2020" },
    module: { type: "commonjs" },
  });
  const context = vm.createContext({});
  vm.runInContext(result.code, context);
  assert.equal(context.answer, 42);
});

function checkFixture(source) {
  const fixture = mkdtempSync(path.join(tmpdir(), "llm-termux-types-"));
  try {
    symlinkSync(path.join(frontend, "node_modules"), path.join(fixture, "node_modules"),
      process.platform === "win32" ? "junction" : "dir");
    writeFileSync(path.join(fixture, "fixture.ts"), source);
    writeFileSync(path.join(fixture, "tsconfig.json"), JSON.stringify({
      compilerOptions: {
        target: "ES2020", lib: ["es2020"], module: "esnext", moduleResolution: "bundler",
        strict: true, skipLibCheck: true, noEmit: true, incremental: false, types: [],
        esModuleInterop: true, resolveJsonModule: true, isolatedModules: true, jsx: "react-jsx",
      },
      include: ["fixture.ts"], exclude: ["node_modules"],
    }));
    const program = `
      import assert from "node:assert/strict";
      import { createRequire } from "node:module";
      import { prepareNextWasm } from ${JSON.stringify(pathToFileURL(path.join(frontend, "scripts/prepare-next-wasm.mjs")).href)};
      const require = createRequire(${JSON.stringify(path.join(frontend, "package.json"))});
      await prepareNextWasm({ force: true });
      assert.equal((await require("next/dist/build/swc").loadBindings(true)).isWasm, true);
      const { PHASE_PRODUCTION_BUILD } = require("next/constants");
      const config = await require("next/dist/server/config").default(
        PHASE_PRODUCTION_BUILD, ${JSON.stringify(frontend)});
      const { CompileError } = require("next/dist/lib/compile-error");
      assert.equal(config.experimental.useTypeScriptCli, true,
        "The production WASM configuration must select Next's TypeScript CLI checker");
      assert.notEqual(config.typescript.ignoreBuildErrors, true,
        "The production configuration must not skip TypeScript errors");
      try {
        const result = await require("next/dist/lib/verify-typescript-setup").verifyAndRunTypeScript({
          dir: ${JSON.stringify(fixture)}, distDir: ".next", tsconfigPath: "tsconfig.json",
          shouldRunTypeCheck: !config.typescript.ignoreBuildErrors,
          useTypeScriptCli: config.experimental.useTypeScriptCli,
          hasAppDir: false, hasPagesDir: false, disableStaticImages: true,
          strictRouteTypes: false, typedRoutes: false,
        });
        assert.equal(result.typeCheckMode, "typescript-cli");
        assert.ok(result.result, "The checker must run rather than only configure TypeScript");
        console.log("TYPECHECK_PASSED:typescript-cli");
      } catch (error) {
        // Next has already printed the original compiler diagnostics.
        // Configuration errors above remain uncaught and retain their full stack.
        if (!(error instanceof CompileError)) throw error;
        process.exitCode = 1;
      }
    `;
    const env = { ...process.env, NEXT_TEST_WASM: "1", NEXT_TELEMETRY_DISABLED: "1",
      NODE_ENV: "production", CI: "1", FORCE_COLOR: "0" };
    delete env.__NEXT_PRIVATE_STANDALONE_CONFIG;
    delete env.__NEXT_PROCESSED_ENV;
    return spawnSync(process.execPath, ["--input-type=module", "-e", program], {
      cwd: frontend, env, encoding: "utf8", timeout: 45_000, maxBuffer: 1024 * 1024,
    });
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
}

test("the production WASM type checker reports a TypeScript error with its file location and fails", () => {
  const result = checkFixture('export const count: number = "wrong";\n');
  const output = result.stdout + result.stderr;
  assert.ifError(result.error);
  assert.equal(result.signal, null, output);
  assert.equal(result.status, 1, output);
  assert.match(output, /fixture\.ts\(1,14\): error TS2322:/);
  assert.match(output, /Type 'string' is not assignable to type 'number'/);
  assert.doesNotMatch(output, /invalid type: unit value|expected usize/);
  assert.doesNotMatch(output, /TYPECHECK_PASSED/);
});

test("the production WASM type checker accepts a valid TypeScript project", () => {
  const result = checkFixture("export const count: number = 42;\n");
  const output = result.stdout + result.stderr;
  assert.ifError(result.error);
  assert.equal(result.signal, null, output);
  assert.equal(result.status, 0, output);
  assert.match(output, /TYPECHECK_PASSED:typescript-cli/);
  assert.doesNotMatch(output, /error TS\d+:/);
});
