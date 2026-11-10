import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const template = readFileSync(path.join(frontend, ".env.example"), "utf8");
const keys = template.split("\n").filter((line) => line && !line.startsWith("#") && line.includes("="))
  .map((line) => line.split("=", 1)[0]);

for (const localFile of [null, ".env", ".env.local"]) {
  test(`Next.js environment precedence with ${localFile ?? "only the shared root file"}`, () => {
    const root = mkdtempSync(path.join(tmpdir(), "llm-env-"));
    try {
      const web = path.join(root, "frontend");
      mkdirSync(web);
      writeFileSync(path.join(root, ".env"), "APP_URL=http://localhost:8080\nAUTH_SERVER_URL=http://api:8000\n");
      copyFileSync(path.join(frontend, "next.config.ts"), path.join(web, "next.config.ts"));
      if (localFile) writeFileSync(path.join(web, localFile), template);
      for (const exported of [false, true]) {
        const environment = { ...process.env, NODE_ENV: "development" };
        for (const key of [...keys, "__NEXT_PROCESSED_ENV"]) delete environment[key];
        if (exported) environment.APP_URL = "http://localhost:3200";
        const program = `
          import { createRequire } from "node:module";
          const { loadEnvConfig } = createRequire(${JSON.stringify(import.meta.url)})("@next/env");
          loadEnvConfig(process.cwd(), true, { info() {}, error(message) { throw new Error(message); } });
          await import(${JSON.stringify(pathToFileURL(path.join(web, "next.config.ts")).href)});
          console.log(JSON.stringify({ app: process.env.APP_URL, api: process.env.AUTH_SERVER_URL,
            origins: process.env.APP_ALLOWED_ORIGINS }));
        `;
        const result = spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", program],
          { cwd: web, env: environment, encoding: "utf8" });
        assert.equal(result.status, 0, result.stderr);
        const values = JSON.parse(result.stdout);
        assert.equal(values.app, exported ? "http://localhost:3200" : `http://localhost:${localFile ? 3000 : 8080}`);
        assert.equal(values.api, localFile ? "http://127.0.0.1:8000" : "http://api:8000");
        assert.equal(values.origins, localFile ? "http://127.0.0.1:3000" : undefined);
      }
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
}
