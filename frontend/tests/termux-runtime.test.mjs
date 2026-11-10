import assert from "node:assert/strict";
import { test } from "node:test";
import vm from "node:vm";
import { createRequire } from "node:module";
import { prepareNextWasm } from "../scripts/prepare-next-wasm.mjs";

const require = createRequire(import.meta.url);

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
