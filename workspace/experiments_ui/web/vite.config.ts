import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Dev proxy target for experiments_ui_server.py (/api, /files).
 *
 * Default 8791 avoids clashing with Docker Compose, which maps host :8790 → the *container's*
 * Experiments UI (see docker-compose.yml). If you proxy to 8790 while Docker holds that port,
 * traffic hits the container — not a host-started Python server — and routes can 404 or add load.
 *
 * Inside the ComfyUI container, set EXPERIMENTS_UI_PROXY_TARGET=http://127.0.0.1:8790 (see dev_experiments_ui_container.ps1).
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget =
    (env.EXPERIMENTS_UI_PROXY_TARGET || "").trim() || "http://127.0.0.1:8791";

  return {
    plugins: [react()],
    server: {
      port: 5178,
      proxy: {
        "/api": apiTarget,
        "/files": apiTarget,
      },
    },
    build: {
      // Build to workspace/experiments_ui/dist (served by experiments_ui_server.py)
      outDir: "../dist",
      emptyOutDir: true,
    },
  };
});

