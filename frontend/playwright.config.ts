import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests", workers: 1, fullyParallel: false,
  timeout: 60_000, expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.ACCOUNTS_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure", screenshot: "only-on-failure",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"], defaultBrowserType: "chromium" } },
  ],
});
