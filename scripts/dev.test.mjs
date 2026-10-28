import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const root = path.resolve(import.meta.dirname, "..");
async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
function launch(ui, api) {
  const child = spawn(process.execPath, ["scripts/dev.mjs", "--fixture", "--port", String(ui), "--api-port", String(api)], { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  child.stdout.on("data", data => { output += data; });
  child.stderr.on("data", data => { output += data; });
  const done = new Promise(resolve => child.once("exit", code => resolve(code)));
  return { child, done, output: () => output, ui, api };
}
// Every request stops when the test does (`stop`), so a stalled one can't keep the sessions running
// past the test's timeout. Each poll also gives up after 2 s and tries again. A single request
// otherwise waits for its answer: the first message after startup waits for the provider SDK the
// server only starts importing once the workspace has loaded, which can take seconds on a busy machine.
async function until(operation, detail, stop) {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    stop.throwIfAborted();
    try { const value = await operation(AbortSignal.any([stop, AbortSignal.timeout(2000)])); if (value) return value; } catch { /* server is starting */ }
    await delay(100);
  }
  assert.fail(`Timed out: ${detail()}`);
}
async function json(port, route, options) {
  const response = await fetch(`http://127.0.0.1:${port}${route}`, options);
  assert.ok(response.ok, `${route}: ${response.status}`);
  return response.json();
}

test("two development sessions keep requests/data separate and clean up on stop; occupied ports fail safely", { timeout: 120000 }, async t => {
  const stop = t.signal;
  const ports = new Set();
  while (ports.size < 4) ports.add(await freePort());
  const [uiA, apiA, uiB, apiB] = [...ports];
  const sessions = [];
  try {
    const a = launch(uiA, apiA); sessions.push(a);
    const b = launch(uiB, apiB); sessions.push(b);
    await Promise.all(sessions.map(s => until(signal => json(s.ui, "/health", { signal }), s.output, stop)));
    const workspaceA = await json(uiA, "/api/coding/workspace", { signal: stop });
    const workspaceB = await json(uiB, "/api/coding/workspace", { signal: stop });
    assert.equal(workspaceA.threads.length, 2);
    assert.equal(workspaceB.threads.length, 2);
    assert.notEqual(workspaceA.agent_id, workspaceB.agent_id);
    assert.notDeepEqual(workspaceA.threads.map(t => t.id), workspaceB.threads.map(t => t.id));
    // The fixture opens past setup: a project, and a connected model its chat runs on.
    const connections = await json(uiA, "/api/connections", { signal: stop });
    assert.equal(workspaceA.projects.length, 2);
    assert.ok(connections.enabled_models.some(key => !key.startsWith("demo::")), JSON.stringify(connections.enabled_models));
    assert.equal(connections.model.provider, "openai-compatible");
    assert.equal(workspaceA.threads[0].model.model, "fixture-model");
    const run = await json(uiA, `/api/coding/threads/${workspaceA.threads[0].id}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: "Reply for fixture verification" }), signal: stop,
    });
    const completed = await until(async signal => {
      const current = await json(uiA, `/api/runs/${run.id}`, { signal });
      return ["completed", "failed", "cancelled"].includes(current.status) && current;
    }, a.output, stop);
    assert.equal(completed.status, "completed");
    assert.match(JSON.stringify(completed), /Synthetic reply/);
    assert.equal((await fetch(`http://127.0.0.1:${uiB}/api/runs/${run.id}`, { signal: stop })).status, 404);
    const collision = launch(uiA, apiB); sessions.push(collision);
    assert.equal(await collision.done, 1);
    assert.match(collision.output(), /occupied/);
    // A server on all interfaces holds the port too, though macOS would let 127.0.0.1 bind.
    const wildcard = net.createServer();
    await new Promise(resolve => wildcard.listen(0, "0.0.0.0", resolve));
    try {
      const shadowed = launch(await freePort(), wildcard.address().port); sessions.push(shadowed);
      assert.equal(await shadowed.done, 1);
      assert.match(shadowed.output(), /occupied/);
    } finally {
      await new Promise(resolve => wildcard.close(resolve));
    }
    assert.equal((await json(uiA, "/health", { signal: stop })).status, "healthy");
    b.child.kill("SIGHUP");
    assert.equal(await b.done, 0, b.output());
    assert.equal((await json(uiA, "/health", { signal: stop })).status, "healthy");
  } finally {
    for (const s of sessions) if (s.child.exitCode === null) s.child.kill("SIGTERM");
    await Promise.all(sessions.map(s => s.done));
    for (const s of sessions) {
      const directory = s.output().match(/Synthetic workspace: (.+)/)?.[1];
      if (directory) assert.equal(existsSync(directory), false, directory);
    }
    for (const port of ports) {
      const server = net.createServer();
      await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
      await new Promise(resolve => server.close(resolve));
    }
  }
});
