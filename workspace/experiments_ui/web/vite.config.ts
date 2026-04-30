import http from "node:http";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

/** Avoid queueing concurrent /api calls behind one long request (default agent limits). */
const proxyAgent = new http.Agent({ keepAlive: true, maxSockets: 100, maxFreeSockets: 20 });

/**
 * Dev proxy target for the Experiments UI API (/api, /files).
 *
 * Default **8790** matches Docker Compose: host :8790 → container Experiments UI (see docker-compose.yml).
 * Use **8791** (set EXPERIMENTS_UI_PROXY_TARGET) when running a host-only `experiments_ui_server.py` alongside Vite
 * (`npm run ui:dev:start` sets the proxy to 8791 via the launcher).
 *
 * Inside the ComfyUI container, set EXPERIMENTS_UI_PROXY_TARGET=http://127.0.0.1:8790 (see dev_experiments_ui_container.ps1).
 * Docker publishes container :5178 on host :51780 by default (`EXPERIMENTS_UI_VITE_HOST_PORT`) so host Vite can use :5178.
 *
 * Tailscale / LAN (iPhone): dev launchers bind 0.0.0.0 by default; set EXPERIMENTS_UI_HMR_HOST to this PC's
 * tailnet IP (or use `tailscale ip -4`) so the HMR WebSocket matches the URL you open on the phone.
 * Opt out of remote bind: EXPERIMENTS_UI_DEV_LOCALONLY=1 (see scripts/experiments-ui-dev.mjs).
 *
 * Vite 5.4+ rejects unknown Host headers (incl. WebSocket upgrades). Tailscale MagicDNS (*.ts.net) and
 * mDNS (*.local) are not in the implicit allowlist — without this, remote browsers often show only
 * "connection unexpectedly dropped". Optional: EXPERIMENTS_UI_EXTRA_ALLOWED_HOSTS=host1,host2
 * EXPERIMENTS_UI_ALLOW_ANY_DEV_HOST=1 → server.allowedHosts true (only for trusted dev networks).
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // Prefer process.env so dev launchers (PowerShell, experiments-ui-dev.mjs) override stale .env values.
  const apiTarget =
    (process.env.EXPERIMENTS_UI_PROXY_TARGET || env.EXPERIMENTS_UI_PROXY_TARGET || "").trim() ||
    "http://127.0.0.1:8790";
  /** When the dev server is opened via Tailscale/LAN IP, HMR must use that host (not localhost). */
  const hmrPublicHost = (process.env.EXPERIMENTS_UI_HMR_HOST || env.EXPERIMENTS_UI_HMR_HOST || "").trim();
  const devPort =
    Number.parseInt((env.EXPERIMENTS_UI_DEV_PORT || process.env.EXPERIMENTS_UI_DEV_PORT || "").trim(), 10) || 5178;
  const localOnlyRaw = (process.env.EXPERIMENTS_UI_DEV_LOCALONLY || "").trim().toLowerCase();
  const devListenHost =
    localOnlyRaw === "1" || localOnlyRaw === "true" || localOnlyRaw === "yes" ? "127.0.0.1" : true;

  const allowAnyDevHost = ["1", "true", "yes"].includes(
    (process.env.EXPERIMENTS_UI_ALLOW_ANY_DEV_HOST || "").trim().toLowerCase(),
  );
  const extraAllowedHosts = (process.env.EXPERIMENTS_UI_EXTRA_ALLOWED_HOSTS || env.EXPERIMENTS_UI_EXTRA_ALLOWED_HOSTS || "")
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);

  const proxyCommon = { target: apiTarget, changeOrigin: true, secure: false } as const;

  return {
    plugins: [react()],
    server: {
      host: devListenHost,
      port: devPort,
      strictPort: true,
      /** Remote dev: Vite's default CORS only trusts localhost origins; allow any origin on LAN/Tailscale. */
      ...(devListenHost === "127.0.0.1" ? {} : { cors: true as const }),
      allowedHosts: allowAnyDevHost ? true : [".ts.net", ".local", ...extraAllowedHosts],
      proxy: {
        "/api": { ...proxyCommon, agent: proxyAgent },
        "/files": { ...proxyCommon, agent: proxyAgent },
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
