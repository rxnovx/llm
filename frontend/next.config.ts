import type { NextConfig } from "next";
import { existsSync } from "node:fs";
import path from "node:path";

// Share .env with python main.py. Explicit process variables take priority.
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
    ] }];
  },
};
export default config;
