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
 *
 * Tailscale / LAN (iPhone): run scripts/dev_experiments_ui.ps1 -Tailscale and set EXPERIMENTS_UI_HMR_HOST
 * so the HMR WebSocket uses your tailnet IP (see that script).
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // Prefer process.env so dev launchers (PowerShell, experiments-ui-dev.mjs) override stale .env values.
  const apiTarget =
    (process.env.EXPERIMENTS_UI_PROXY_TARGET || env.EXPERIMENTS_UI_PROXY_TARGET || "").trim() ||
    "http://127.0.0.1:8791";
  /** When the dev server is opened via Tailscale/LAN IP, HMR must use that host (not localhost). */
  const hmrPublicHost = (env.EXPERIMENTS_UI_HMR_HOST || "").trim();
  const devPort =
    Number.parseInt((env.EXPERIMENTS_UI_DEV_PORT || "").trim(), 10) || 5178;

  return {
    plugins: [react()],
    server: {
      port: devPort,
      strictPort: true,
      proxy: {
        "/api": apiTarget,
        "/files": apiTarget,
      },
      ...(hmrPublicHost
        ? {
            hmr: {
              host: hmrPublicHost,
              port: devPort,
              clientPort: devPort,
              protocol: "ws",
            },
          }
        : {}),
    },
    build: {
      // Build to workspace/experiments_ui/dist (served by experiments_ui_server.py)
      outDir: "../dist",
      emptyOutDir: true,
    },
  };
});
