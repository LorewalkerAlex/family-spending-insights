import { spawn, spawnSync } from "node:child_process";
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import process from "node:process";
import { randomUUID } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_PATH = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(SCRIPT_PATH), "..");
const RUNTIME_ROOT = path.join(REPO_ROOT, ".runtime");
const STATE_PATH = path.join(RUNTIME_ROOT, "dev.json");
const LOG_ROOT = path.join(RUNTIME_ROOT, "logs");
const STATE_SCHEMA_VERSION = 2;
const ROLES = ["api", "web"];

export function parsePreferredPort(value, fallback) {
  if (value === undefined || value === null || String(value).trim() === "") {
    return fallback;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(`Invalid local development port: ${value}`);
  }
  return parsed;
}

export function runtimeUrls(ports) {
  return {
    api: `http://127.0.0.1:${ports.api}`,
    web: `http://127.0.0.1:${ports.web}/overview`,
  };
}

export function workerSignature(role, runtimeId) {
  if (!ROLES.includes(role)) {
    throw new Error(`Unknown runtime role: ${role}`);
  }
  return ["dev-runtime.mjs", "child", role, runtimeId];
}

export function isRuntimeState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (value.schema_version !== STATE_SCHEMA_VERSION) return false;
  if (typeof value.runtime_id !== "string" || !value.runtime_id) return false;
  if (!value.ports || typeof value.ports !== "object") return false;
  if (!value.urls || typeof value.urls !== "object") return false;
  if (!value.processes || typeof value.processes !== "object") return false;

  for (const key of ROLES) {
    const port = value.ports[key];
    if (!Number.isInteger(port) || port < 1 || port > 65535) return false;
  }

  for (const [role, processInfo] of Object.entries(value.processes)) {
    if (!ROLES.includes(role)) return false;
    if (!processInfo || typeof processInfo !== "object") return false;
    if (!Number.isInteger(processInfo.pid) || processInfo.pid <= 0) return false;
    if (processInfo.role !== role) return false;
  }

  return true;
}

function ensureRuntimeDirectories(runtimeId) {
  mkdirSync(RUNTIME_ROOT, { recursive: true });
  mkdirSync(LOG_ROOT, { recursive: true });
  if (runtimeId) {
    mkdirSync(path.join(LOG_ROOT, runtimeId), { recursive: true });
  }
}

function loadState() {
  if (!existsSync(STATE_PATH)) return null;

  try {
    const parsed = JSON.parse(readFileSync(STATE_PATH, "utf8"));
    if (!isRuntimeState(parsed)) {
      throw new Error("state contract mismatch");
    }
    return parsed;
  } catch (error) {
    throw new Error(`Cannot read managed dev runtime state: ${error.message}`);
  }
}

function writeState(state) {
  ensureRuntimeDirectories(state.runtime_id);
  const temporary = `${STATE_PATH}.tmp-${process.pid}`;
  writeFileSync(temporary, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  renameSync(temporary, STATE_PATH);
}

function removeState() {
  rmSync(STATE_PATH, { force: true });
}

function findFreePort(startPort, reserved = new Set()) {
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      if (port > 65535) {
        reject(new Error(`No free local port found from ${startPort}`));
        return;
      }
      if (reserved.has(port)) {
        tryPort(port + 1);
        return;
      }

      const server = net.createServer();
      server.unref();
      server.once("error", () => tryPort(port + 1));
      server.listen({ host: "127.0.0.1", port, exclusive: true }, () => {
        server.close(() => resolve(port));
      });
    };

    tryPort(startPort);
  });
}

function httpText(url, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      const chunks = [];
      response.setEncoding("utf8");
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        if ((response.statusCode ?? 500) >= 400) {
          reject(new Error(`${url} returned HTTP ${response.statusCode}`));
          return;
        }
        resolve(chunks.join(""));
      });
    });

    request.once("timeout", () => {
      request.destroy(new Error(`${url} timed out`));
    });
    request.once("error", reject);
  });
}

async function waitForUrl(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;

  while (Date.now() < deadline) {
    try {
      await httpText(url, 2500);
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
  }

  throw new Error(`Timed out waiting for ${url}: ${lastError?.message ?? "not ready"}`);
}

function getProcessCommandLine(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return "";

  if (process.platform === "win32") {
    const command = [
      "$p = Get-CimInstance Win32_Process -Filter 'ProcessId = " + pid + "' -ErrorAction SilentlyContinue;",
      "if ($null -ne $p) { [Console]::Write($p.CommandLine) }",
    ].join(" ");
    const result = spawnSync("powershell.exe", ["-NoProfile", "-Command", command], {
      encoding: "utf8",
      windowsHide: true,
    });
    return result.status === 0 ? result.stdout.trim() : "";
  }

  const result = spawnSync("ps", ["-p", String(pid), "-o", "command="], {
    encoding: "utf8",
  });
  return result.status === 0 ? result.stdout.trim() : "";
}

function processMatches(processInfo, runtimeId) {
  const commandLine = getProcessCommandLine(processInfo.pid);
  if (!commandLine) return false;

  return workerSignature(processInfo.role, runtimeId).every((token) => commandLine.includes(token));
}

function killProcessTree(processInfo, runtimeId) {
  if (!processMatches(processInfo, runtimeId)) {
    return { stopped: false, reason: "ownership mismatch or process already exited" };
  }

  if (process.platform === "win32") {
    const result = spawnSync("taskkill", ["/PID", String(processInfo.pid), "/T", "/F"], {
      encoding: "utf8",
      windowsHide: true,
    });
    if (result.status !== 0 && processMatches(processInfo, runtimeId)) {
      throw new Error(`Unable to stop managed ${processInfo.role} process ${processInfo.pid}`);
    }
    return { stopped: true };
  }

  try {
    process.kill(-processInfo.pid, "SIGTERM");
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
  return { stopped: true };
}

function tailLog(filePath, maxLines = 80) {
  if (!filePath || !existsSync(filePath)) return "";
  const lines = readFileSync(filePath, "utf8").split(/\r?\n/);
  return lines.slice(-maxLines).join("\n").trim();
}

function printRuntime(state, prefix = "RUNNING") {
  console.log(`${prefix}: managed Family Spending development runtime`);
  console.log(`Desktop : ${state.urls.web}`);
  console.log(`API     : ${state.urls.api}`);
  console.log("Mini    : import frontend/apps/mini in WeChat Developer Tools");
  console.log(`State   : ${path.relative(REPO_ROOT, STATE_PATH).replaceAll(path.sep, "/")}`);
  console.log(`PIDs    : ${ROLES.map((role) => state.processes[role]?.pid ?? "-").join(", ")}`);
}

async function inspectState(state) {
  const processChecks = Object.fromEntries(
    ROLES.map((role) => [role, Boolean(state.processes[role] && processMatches(state.processes[role], state.runtime_id))]),
  );

  const endpointChecks = { api: false, web: false };
  if (processChecks.api) {
    endpointChecks.api = await httpText(`${state.urls.api}/api/health`).then(() => true, () => false);
  }
  if (processChecks.web) {
    endpointChecks.web = await httpText(state.urls.web).then(() => true, () => false);
  }

  return {
    healthy: ROLES.every((role) => processChecks[role] && endpointChecks[role]),
    processChecks,
    endpointChecks,
  };
}

async function stopState(state, { quiet = false } = {}) {
  for (const role of [...ROLES].reverse()) {
    const processInfo = state.processes[role];
    if (!processInfo) continue;
    const result = killProcessTree(processInfo, state.runtime_id);
    if (!quiet) {
      if (result.stopped) {
        console.log(`STOPPED: ${role} PID ${processInfo.pid}`);
      } else {
        console.log(`SKIPPED: ${role} PID ${processInfo.pid} (${result.reason})`);
      }
    }
  }
}

function spawnWorker(role, runtimeId, ports) {
  const runtimeLogDir = path.join(LOG_ROOT, runtimeId);
  ensureRuntimeDirectories(runtimeId);
  const stdoutPath = path.join(runtimeLogDir, `${role}.out.log`);
  const stderrPath = path.join(runtimeLogDir, `${role}.err.log`);
  const stdoutFd = openSync(stdoutPath, "a");
  const stderrFd = openSync(stderrPath, "a");
  const env = {
    ...process.env,
    FAMILY_SPENDING_API_PORT: String(ports.api),
    FAMILY_SPENDING_WEB_PORT: String(ports.web),
  };

  let child;
  try {
    child = spawn(process.execPath, [SCRIPT_PATH, "child", role, runtimeId], {
      cwd: REPO_ROOT,
      env,
      detached: true,
      stdio: ["ignore", stdoutFd, stderrFd],
      windowsHide: true,
    });
  } finally {
    closeSync(stdoutFd);
    closeSync(stderrFd);
  }

  if (!Number.isInteger(child.pid) || child.pid <= 0) {
    throw new Error(`Unable to start managed ${role} wrapper process`);
  }
  child.unref();
  return {
    role,
    pid: child.pid,
    stdout: path.relative(REPO_ROOT, stdoutPath).replaceAll(path.sep, "/"),
    stderr: path.relative(REPO_ROOT, stderrPath).replaceAll(path.sep, "/"),
  };
}

export function workerCommand(role, env, platform = process.platform) {
  if (!ROLES.includes(role)) {
    throw new Error(`Unknown runtime role: ${role}`);
  }

  if (role === "api") {
    const pythonPath = path.join(REPO_ROOT, "src");
    return {
      command: "uv",
      args: [
        "run",
        "--frozen",
        "python",
        "-m",
        "family_spending",
        "serve",
        "--port",
        env.FAMILY_SPENDING_API_PORT,
      ],
      env: {
        ...env,
        PYTHONPATH: env.PYTHONPATH ? `${pythonPath}${path.delimiter}${env.PYTHONPATH}` : pythonPath,
      },
    };
  }

  if (platform === "win32") {
    return {
      command: env.ComSpec || env.COMSPEC || "cmd.exe",
      args: ["/d", "/s", "/c", "npm run dev:web"],
      env,
    };
  }

  return {
    command: "npm",
    args: ["run", "dev:web"],
    env,
  };
}

async function runWorker(role, runtimeId) {
  if (!ROLES.includes(role) || !runtimeId) {
    throw new Error("Invalid managed runtime child invocation");
  }

  const spec = workerCommand(role, process.env);
  const child = spawn(spec.command, spec.args, {
    cwd: REPO_ROOT,
    env: spec.env,
    stdio: "inherit",
    windowsHide: true,
  });

  const forward = (signal) => {
    try {
      child.kill(signal);
    } catch {
      // The child may already have exited while the wrapper is shutting down.
    }
  };
  process.once("SIGINT", () => forward("SIGINT"));
  process.once("SIGTERM", () => forward("SIGTERM"));

  await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (signal) {
        reject(new Error(`${role} child exited from signal ${signal}`));
        return;
      }
      if ((code ?? 0) !== 0) {
        reject(new Error(`${role} child exited with code ${code}`));
        return;
      }
      resolve();
    });
  });
}

async function verifyProxyConvergence(state) {
  const direct = await httpText(`${state.urls.api}/api/financial-summary`, 5000);
  const web = await httpText(`http://127.0.0.1:${state.ports.web}/api/financial-summary`, 5000);
  if (direct !== web) {
    throw new Error("Financial Summary differs across API and Desktop proxy");
  }
}

async function startRuntime() {
  ensureRuntimeDirectories();

  const existing = loadState();
  if (existing) {
    const inspection = await inspectState(existing);
    if (inspection.healthy) {
      printRuntime(existing, "REUSE");
      return;
    }

    console.log("STALE: cleaning only processes owned by the recorded Family Spending runtime");
    await stopState(existing, { quiet: true });
    removeState();
  }

  const reserved = new Set();
  const api = await findFreePort(parsePreferredPort(process.env.FAMILY_SPENDING_API_PORT, 18765), reserved);
  reserved.add(api);
  const web = await findFreePort(parsePreferredPort(process.env.FAMILY_SPENDING_WEB_PORT, 15173), reserved);
  const ports = { api, web };
  const urls = runtimeUrls(ports);
  const runtimeId = randomUUID();
  const state = {
    schema_version: STATE_SCHEMA_VERSION,
    runtime_id: runtimeId,
    status: "starting",
    started_at: new Date().toISOString(),
    ports,
    urls,
    processes: {},
  };

  try {
    state.processes.api = spawnWorker("api", runtimeId, ports);
    writeState(state);
    await waitForUrl(`${urls.api}/api/health`, 120_000);

    state.processes.web = spawnWorker("web", runtimeId, ports);
    writeState(state);
    await waitForUrl(urls.web, 120_000);
    await verifyProxyConvergence(state);

    state.status = "ready";
    state.ready_at = new Date().toISOString();
    writeState(state);
    printRuntime(state, "STARTED");
  } catch (error) {
    console.error(`FAILED: ${error.message}`);
    for (const role of ROLES) {
      const processInfo = state.processes[role];
      if (!processInfo) continue;
      const logPath = path.join(REPO_ROOT, processInfo.stderr);
      const tail = tailLog(logPath);
      if (tail) {
        console.error(`--- ${role} stderr ---\n${tail}`);
      }
    }
    await stopState(state, { quiet: true });
    removeState();
    throw error;
  }
}

async function stopRuntime() {
  const state = loadState();
  if (!state) {
    console.log("STOPPED: no managed Family Spending development runtime is recorded");
    return;
  }

  await stopState(state);
  removeState();
  console.log("STOPPED: managed Family Spending development runtime state removed");
}

async function statusRuntime() {
  const state = loadState();
  if (!state) {
    console.log("STOPPED: no managed Family Spending development runtime is recorded");
    return;
  }

  const inspection = await inspectState(state);
  printRuntime(state, inspection.healthy ? "RUNNING" : "STALE");
  if (!inspection.healthy) {
    console.log(`Process ownership: ${JSON.stringify(inspection.processChecks)}`);
    console.log(`Endpoints        : ${JSON.stringify(inspection.endpointChecks)}`);
  }
}

async function main() {
  const action = process.argv[2] ?? "start";
  if (action === "child") {
    await runWorker(process.argv[3], process.argv[4]);
    return;
  }

  process.chdir(REPO_ROOT);
  if (action === "start") {
    await startRuntime();
    return;
  }
  if (action === "stop") {
    await stopRuntime();
    return;
  }
  if (action === "status") {
    await statusRuntime();
    return;
  }
  if (action === "restart") {
    await stopRuntime();
    await startRuntime();
    return;
  }

  throw new Error(`Unknown dev runtime action: ${action}`);
}

const invokedDirectly = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (invokedDirectly) {
  main().catch((error) => {
    console.error(error.stack ?? error.message);
    process.exitCode = 1;
  });
}
