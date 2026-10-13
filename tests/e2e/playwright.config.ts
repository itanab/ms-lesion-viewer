import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(__dirname, "../..");
const start = path.join(root, "scripts/start.sh");
const base = { MSLV_FRONTEND_DIST: path.join(root, "frontend/dist"), MSLV_ENABLE_MOCK: "1" };

// Two servers: one with the mock adapter (job flow), one with no adapter (viewer must stay fully usable).
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  globalSetup: "./global-setup.ts",
  use: { headless: true, viewport: { width: 1440, height: 900 }, trace: "retain-on-failure" },
  webServer: [
    {
      command: `sh -c 'rm -rf .data-mock && PORT=8100 MSLV_DATA_ROOT=$PWD/.data-mock MSLV_ADAPTER=mock MSLV_ADAPTER_CONFIG="{\\"mode\\":\\"ok\\",\\"delay_s\\":1.0}" ${start}'`,
      url: "http://127.0.0.1:8100/api/v1/health", env: base, reuseExistingServer: false, timeout: 30_000,
    },
    {
      command: `sh -c 'rm -rf .data-none && PORT=8101 MSLV_DATA_ROOT=$PWD/.data-none MSLV_ADAPTER=none ${start}'`,
      url: "http://127.0.0.1:8101/api/v1/health", env: base, reuseExistingServer: false, timeout: 30_000,
    },
  ],
});
