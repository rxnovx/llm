import type { NextConfig } from "next";
import { existsSync } from "node:fs";
import path from "node:path";

// Next.js loads frontend/.env.local first. Root .env only fills missing values;
// explicit process variables take priority over both files.
const sharedEnv = path.resolve(process.cwd(), "../.env");
if (existsSync(sharedEnv)) process.loadEnvFile(sharedEnv);

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "X-Frame-Options", value: "DENY" },
    ] }, { source: "/apps/gitlab/callback", headers: [
      { key: "Referrer-Policy", value: "no-referrer" }, { key: "Cache-Control", value: "no-store" },
    ] }];
  },
};
export default config;
