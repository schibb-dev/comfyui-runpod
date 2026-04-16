#!/usr/bin/env node
/**
 * Cross-platform Experiments UI dev launcher (Windows / Linux / macOS).
 *
 * Usage:
 *   node scripts/experiments-ui-dev.mjs vite [--tailscale] [--no-open] [--port N] [--ensure-container]
 *   node scripts/experiments-ui-dev.mjs all [--tailscale] [--no-open] [--port N]
 *   node scripts/experiments-ui-dev.mjs api
 *       → Python API only (same as the backend half of `all`). Restart this process after editing
 *       scripts/experiments_ui_server.py without restarting Vite.
 *   node scripts/experiments-ui-dev.mjs api-watch
 *       → Same as `api`, but uses `npx nodemon` to restart when experiments_ui_server.py changes
 *       (Node ecosystem; first run may download nodemon). Pair with `npm run ui:dev:vite` in another terminal.
 *   node scripts/experiments-ui-dev.mjs start
 *       → Recommended host dev: **api-watch + Vite** in one process (same as `npm run ui:dev:start`).
 *
 * Env: EXPERIMENTS_UI_PROXY_TARGET (default http://127.0.0.1:8791 for vite mode)
 *      EXPERIMENTS_UI_API_HOST / EXPERIMENTS_UI_API_PORT (optional; default 127.0.0.1:8791 for `api` mode)
 */

import { spawn, spawnSync, execFileSync } from "child_process";
import fs from "fs";
import path from "path";
import process from "process";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const webDir = path.join(repoRoot, "workspace", "experiments_ui", "web");
const apiScript = path.join(repoRoot, "scripts", "experiments_ui_server.py");

const isWin = process.platform === "win32";

function die(msg) {
  console.error(msg);
  process.exit(1);
}

function resolvePython() {
  const bins = isWin ? ["python", "py"] : ["python3", "python"];
  for (const bin of bins) {
    try {
      const args = bin === "py" ? ["-3", "--version"] : ["--version"];
      execFileSync(bin, args, { stdio: "ignore" });
      return bin === "py" ? { cmd: "py", argsPrefix: ["-3"] } : { cmd: bin, argsPrefix: [] };
    } catch {
      /* try next */
    }
  }
  die("Need python3/python on PATH (or py -3 on Windows).");
}

function parseArgs(argv) {
  const mode = argv[2];
  if (mode !== "vite" && mode !== "all" && mode !== "api" && mode !== "api-watch" && mode !== "start") {
    die(
      "Usage: node scripts/experiments-ui-dev.mjs <vite|all|api|api-watch|start> [--tailscale] [--no-open] [--port N] [--ensure-container]",
    );
  }
  let tailscale = false;
  let noOpen = false;
  let port = 5178;
  let ensureContainer = false;
  for (let i = 3; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--tailscale") tailscale = true;
    else if (a === "--no-open") noOpen = true;
    else if (a === "--ensure-container") ensureContainer = true;
    else if (a.startsWith("--port=")) port = Number.parseInt(a.slice("--port=".length), 10) || port;
    else if (a === "--port") {
      port = Number.parseInt(argv[++i] || "", 10) || port;
    } else die(`Unknown arg: ${a}`);
  }
  return { mode, tailscale, noOpen, port, ensureContainer };
}

function tailscaleIpv4() {
  try {
    const out = execFileSync("tailscale", ["ip", "-4"], { encoding: "utf8" });
    const line = out.split(/\r?\n/).find((l) => l.trim());
    return line ? line.trim() : "";
  } catch {
    return "";
  }
}

function openBrowser(url) {
  try {
    if (isWin) {
      spawn("cmd.exe", ["/c", "start", "", url], {
        detached: true,
        stdio: "ignore",
      }).unref();
    } else if (process.platform === "darwin") {
      spawn("open", [url], { detached: true, stdio: "ignore" }).unref();
    } else {
      spawn("xdg-open", [url], { detached: true, stdio: "ignore" }).unref();
    }
  } catch {
    /* ignore */
  }
}

/** @returns {number} exit code */
function runVite(opts) {
  const { tailscale, noOpen, port, backend, ensureContainer } = opts;

  if (ensureContainer) {
    console.log("Ensuring container is up (docker compose up -d)...");
    spawnSync("docker", ["compose", "up", "-d"], { stdio: "inherit", cwd: repoRoot, shell: isWin });
  }

  if (!fs.existsSync(webDir)) die(`Missing web dir: ${webDir}`);
  const npmCmd = isWin ? "npm.cmd" : "npm";
  try {
    execFileSync(npmCmd, ["--version"], { stdio: "ignore", shell: isWin });
  } catch {
    die("npm not found on PATH. Install Node.js.");
  }
  if (!fs.existsSync(path.join(webDir, "node_modules"))) {
    console.log("Installing dependencies (npm install)...");
    const inst = spawnSync(npmCmd, ["install"], {
      cwd: webDir,
      stdio: "inherit",
      shell: isWin,
    });
    if (inst.status !== 0) die("npm install failed.");
  }

  const env = { ...process.env };
  env.EXPERIMENTS_UI_PROXY_TARGET = backend;
  env.EXPERIMENTS_UI_DEV_PORT = String(port);

  let viteHost = "127.0.0.1";
  let openUrl = `http://127.0.0.1:${port}`;

  if (tailscale) {
    viteHost = "0.0.0.0";
    let tsIp = tailscaleIpv4();
    if (!tsIp && process.env.EXPERIMENTS_UI_HMR_HOST) {
      tsIp = process.env.EXPERIMENTS_UI_HMR_HOST.trim();
    }
    if (tsIp) {
      env.EXPERIMENTS_UI_HMR_HOST = tsIp;
      console.log(`EXPERIMENTS_UI_HMR_HOST=${tsIp} (HMR for remote devices)`);
      openUrl = `http://${tsIp}:${port}`;
    } else {
      delete env.EXPERIMENTS_UI_HMR_HOST;
      console.warn(
        "Could not detect Tailscale IPv4. Set EXPERIMENTS_UI_HMR_HOST=100.x.y.z for HMR on iPhone.",
      );
      console.warn(`On iPhone: open http://<tailscale-ip>:${port}`);
    }
    console.log(`Tailscale mode: Vite on 0.0.0.0:${port}`);
    if (tsIp) console.log(`On iPhone (Tailscale on): ${openUrl}`);
  } else {
    delete env.EXPERIMENTS_UI_HMR_HOST;
    console.log(`Vite dev server: http://127.0.0.1:${port}`);
  }

  console.log(`Proxy /api and /files → ${backend}`);

  if (!noOpen) openBrowser(openUrl);

  const result = spawnSync(npmCmd, ["run", "dev", "--", "--host", viteHost], {
    cwd: webDir,
    stdio: "inherit",
    env,
    shell: isWin,
  });
  return result.status === null ? 1 : result.status;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function runStart(opts) {
  if (!fs.existsSync(apiScript)) die(`Missing API script: ${apiScript}`);
  resolvePython();

  console.log(
    "[ui:dev:start] Vite + watched Python API on :8791 (nodemon restarts experiments_ui_server.py on save; Ctrl+C stops both)",
  );
  const api = spawnNodemonExperimentsApi("[ui:dev:start]");

  const stopApi = () => {
    if (api && !api.killed && api.exitCode === null) {
      console.log(`[ui:dev:start] stopping backend watcher (pid=${api.pid})`);
      try {
        api.kill(isWin ? undefined : "SIGTERM");
      } catch {
        /* ignore */
      }
    }
  };

  const onSig = () => {
    stopApi();
    process.exit(130);
  };
  process.on("SIGINT", onSig);
  process.on("SIGTERM", () => {
    stopApi();
    process.exit(143);
  });

  await sleep(1000);
  if (api.exitCode !== null) {
    die(`Backend watcher exited early with code ${api.exitCode}`);
  }

  try {
    const code = runVite({
      ...opts,
      backend: "http://127.0.0.1:8791",
      ensureContainer: false,
    });
    stopApi();
    process.exit(code);
  } catch (e) {
    stopApi();
    throw e;
  }
}

async function runAll(opts) {
  if (!fs.existsSync(apiScript)) die(`Missing API script: ${apiScript}`);

  const py = resolvePython();
  const pyArgs = [...py.argsPrefix, apiScript, "--host", "127.0.0.1", "--port", "8791"];

  console.log("[ui:dev:all] starting backend API at http://127.0.0.1:8791");
  const api = spawn(py.cmd, pyArgs, {
    cwd: repoRoot,
    stdio: "inherit",
    shell: isWin,
  });

  const stopApi = () => {
    if (api && !api.killed && api.exitCode === null) {
      console.log(`[ui:dev:all] stopping backend API (pid=${api.pid})`);
      try {
        api.kill(isWin ? undefined : "SIGTERM");
      } catch {
        /* ignore */
      }
    }
  };

  const onSig = () => {
    stopApi();
    process.exit(130);
  };
  process.on("SIGINT", onSig);
  process.on("SIGTERM", () => {
    stopApi();
    process.exit(143);
  });

  await sleep(1000);
  if (api.exitCode !== null) {
    die(`Backend API exited early with code ${api.exitCode}`);
  }

  try {
    const code = runVite({
      ...opts,
      backend: "http://127.0.0.1:8791",
      ensureContainer: false,
    });
    stopApi();
    process.exit(code);
  } catch (e) {
    stopApi();
    throw e;
  }
}

function runApiOnly() {
  if (!fs.existsSync(apiScript)) die(`Missing API script: ${apiScript}`);
  const py = resolvePython();
  const { host, portApi } = resolveApiListenOpts();
  const pyArgs = [...py.argsPrefix, apiScript, "--host", host, "--port", String(portApi)];
  console.log(`[ui:dev:api] http://${host}:${portApi}  (${py.cmd} ${pyArgs.join(" ")})`);
  const api = spawn(py.cmd, pyArgs, {
    cwd: repoRoot,
    stdio: "inherit",
    shell: isWin,
  });
  api.on("exit", (code) => process.exit(code === null ? 1 : code));
  const onSig = () => {
    try {
      api.kill(isWin ? undefined : "SIGTERM");
    } catch {
      /* ignore */
    }
    process.exit(130);
  };
  process.on("SIGINT", onSig);
  process.on("SIGTERM", onSig);
}

function resolveApiListenOpts() {
  const host = (process.env.EXPERIMENTS_UI_API_HOST || "127.0.0.1").trim() || "127.0.0.1";
  const portApi = Number.parseInt(process.env.EXPERIMENTS_UI_API_PORT || "8791", 10) || 8791;
  return { host, portApi };
}

function buildPythonExecForNodemon(py, host, portApi) {
  const relScript = path.join("scripts", "experiments_ui_server.py");
  if (py.cmd === "py") {
    return `py -3 ${relScript} --host ${host} --port ${portApi}`;
  }
  return `${py.cmd} ${relScript} --host ${host} --port ${portApi}`;
}

/** @param {string} logPrefix e.g. "[ui:dev:api:watch]" */
function spawnNodemonExperimentsApi(logPrefix) {
  if (!fs.existsSync(apiScript)) die(`Missing API script: ${apiScript}`);
  const py = resolvePython();
  const { host, portApi } = resolveApiListenOpts();
  const relScript = path.join("scripts", "experiments_ui_server.py");
  const execStr = buildPythonExecForNodemon(py, host, portApi);

  const args = [
    "--yes",
    "nodemon",
    "--quiet",
    "--watch",
    relScript,
    "--ext",
    "py",
    "--exec",
    execStr,
  ];
  console.log(
    `${logPrefix} http://${host}:${portApi} — restart on ${relScript} changes (npx nodemon; first run may fetch nodemon)`,
  );
  console.log(`${logPrefix} exec: ${execStr}`);

  return spawn("npx", args, {
    cwd: repoRoot,
    stdio: "inherit",
    shell: isWin,
  });
}

function runApiWatch() {
  const proc = spawnNodemonExperimentsApi("[ui:dev:api:watch]");
  proc.on("exit", (code) => process.exit(code === null ? 1 : code));
  const onSig = () => {
    try {
      proc.kill(isWin ? undefined : "SIGTERM");
    } catch {
      /* ignore */
    }
    process.exit(130);
  };
  process.on("SIGINT", onSig);
  process.on("SIGTERM", onSig);
}

const opts = parseArgs(process.argv);
if (opts.mode === "vite") {
  const backend = process.env.EXPERIMENTS_UI_PROXY_TARGET?.trim() || "http://127.0.0.1:8791";
  process.exit(runVite({ ...opts, backend }));
} else if (opts.mode === "api") {
  runApiOnly();
} else if (opts.mode === "api-watch") {
  runApiWatch();
} else if (opts.mode === "start") {
  runStart(opts).catch((e) => {
    console.error(e);
    process.exit(1);
  });
} else {
  runAll(opts).catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
