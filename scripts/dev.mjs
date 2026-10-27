import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import net from "node:net";
import { parseArgs } from "node:util";

const root = path.resolve(import.meta.dirname, "..");
const { values } = parseArgs({ options: {
  port: { type: "string" },
  "api-port": { type: "string" },
  fixture: { type: "boolean", default: false },
} });
// The fixture takes free ports unless told otherwise, so several can run at once.
async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const { port: free } = server.address();
  await new Promise(resolve => server.close(resolve));
  return String(free);
}
values.port ??= values.fixture ? await freePort() : "5173";
values["api-port"] ??= values.fixture ? await freePort() : "8765";
function port(value) {
  const number = Number(value);
  if (!/^\d+$/.test(value) || number < 1024 || number > 65535) throw new Error("Ports must be integers from 1024 to 65535.");
  return number;
}
const uiPort = port(values.port);
const apiPort = port(values["api-port"]);
if (uiPort === apiPort) throw new Error("UI and API ports must differ.");
// Check both before creating data or launching either process. Never reuse another checkout's API.
for (const selected of [uiPort, apiPort]) {
  const occupied = new Error(`Port ${selected} is occupied. Choose another --port / --api-port pair.`);
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", () => reject(occupied));
    server.listen(selected, "127.0.0.1", () => server.close(resolve));
  });
  // macOS lets this bind beside a server listening on 0.0.0.0, :: or ::1, which would then
  // answer this launcher's requests: anything that accepts a connection holds the port.
  for (const host of ["127.0.0.1", "::1"]) {
    const answered = await new Promise(resolve => {
      const socket = net.connect({ port: selected, host });
      socket.once("connect", () => { socket.destroy(); resolve(true); });
      socket.once("error", () => resolve(false));
    });
    if (answered) throw occupied;
  }
}
const data = values.fixture ? mkdtempSync(path.join(tmpdir(), "wavefunction-fixture-")) : path.join(root, ".wavefunction-data");
mkdirSync(data, { recursive: true });
const env = { ...process.env, WAVEFUNCTION_DATA_DIR: data, WAVEFUNCTION_PORT: String(apiPort),
  WAVEFUNCTION_DEVELOPMENT_AUTH_BYPASS: "true", WAVEFUNCTION_API_PORT: String(apiPort),
  WAVEFUNCTION_CORS_ORIGINS: JSON.stringify([`http://127.0.0.1:${uiPort}`, `http://localhost:${uiPort}`]),
};
const children = [];
let stopping = false;
async function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  const signal = (child, sig) => {
    if (!child.pid) return;
    try { process.kill(-child.pid, sig); } catch (error) { if (error.code !== "ESRCH") throw error; }
  };
  for (const child of children) signal(child, "SIGTERM");
  const timeout = setTimeout(() => { for (const child of children) signal(child, "SIGKILL"); }, 5000);
  await Promise.all(children.map(child => child.done));
  clearTimeout(timeout);
  if (values.fixture) rmSync(data, { recursive: true, force: true });
  console.log("Development processes stopped." + (values.fixture ? " Synthetic workspace removed." : " Workspace retained."));
  process.exitCode = code;
}
function start(command, args) {
  // macOS/Linux process groups include uv/npm descendants, so stopping this launcher stops its own trees.
  const child = spawn(command, args, { cwd: root, env, stdio: "inherit", detached: true });
  child.done = new Promise(resolve => {
    child.once("error", error => { console.error(error.message); resolve(); void shutdown(1); });
    child.once("exit", (code) => { resolve(); if (!stopping) void shutdown(code || 1); });
  });
  children.push(child);
}
process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());
process.on("SIGHUP", () => void shutdown());
console.log(`${values.fixture ? "Synthetic" : "Development"} workspace: ${data}`);
console.log(`Launcher PID: ${process.pid}. Stop it with Ctrl+C or kill -INT ${process.pid}; it stops everything it started.`);
start("uv", ["run", "--no-sync", "--project", "backend", ...(values.fixture
  ? ["python", "scripts/coding-fixture.py", "--port", String(apiPort)]
  // Reload for backend source only: the checkout's .wavefunction-data holds task scratch, where a
  // command writing a .py file would otherwise restart the backend mid-task.
  : ["uvicorn", "wavefunction.main:app", "--host", "127.0.0.1", "--port", String(apiPort), "--reload", "--reload-dir", "backend/src"])]);
// Do not serve a UI whose first workspace request races the Python startup.
let ready = false;
const deadline = Date.now() + 45000;
while (!stopping && Date.now() < deadline) {
  try {
    const health = await fetch(`http://127.0.0.1:${apiPort}/health`, {signal: AbortSignal.timeout(1000)});
    // Our own backend: one that keeps its data in this launcher's folder.
    const served = health.ok ? (await health.json()).data_dir : null;
    if (served && realpathSync(served).startsWith(realpathSync(data))) { ready = true; break; }
  } catch { /* The backend is still starting. */ }
  await new Promise(resolve => setTimeout(resolve, 100));
}
if (!stopping) {
  if (!ready) {
    console.error("The backend did not become ready. Check its startup error above.");
    await shutdown(1);
  } else {
    console.log(`UI: http://127.0.0.1:${uiPort} · API: http://127.0.0.1:${apiPort}`);
    start(process.execPath, ["frontend/node_modules/vite/bin/vite.js", "--config", "frontend/vite.config.ts", "--host", "127.0.0.1", "--port", String(uiPort), "--strictPort", "frontend"]);
  }
}
