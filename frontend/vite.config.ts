import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev: the browser talks to Vite, which proxies /api to the FastAPI backend (same-origin for the page).
export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
