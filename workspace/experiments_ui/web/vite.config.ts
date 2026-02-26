import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    proxy: {
      "/api": "http://127.0.0.1:8790",
      "/files": "http://127.0.0.1:8790",
    },
  },
  build: {
    // Build to workspace/experiments_ui/dist (served by experiments_ui_server.py)
    outDir: "../dist",
    emptyOutDir: true,
  },
});

