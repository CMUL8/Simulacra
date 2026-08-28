import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./node_modules/.cache/playwright-results",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 15_000,
  use: {
    baseURL: "http://app.localhost:4173",
    headless: true,
  },
});
