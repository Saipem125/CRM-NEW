import { defineConfig } from "@playwright/test";

// Uses the installed Edge/Chrome channel so no browser download is needed offline.
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  retries: 0,
  use: { baseURL: process.env.WFO_UI ?? "http://localhost:5173", channel: process.env.PW_CHANNEL ?? "msedge", viewport: { width: 1280, height: 800 }, headless: true },
  reporter: [["list"]],
});
