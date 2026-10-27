import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import process from "node:process";

const root = resolve(import.meta.dirname, "..");
const backend = join(root, "backend");

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: {
      ...process.env,
      // uv's cache is safe to share between builds. PyInstaller's is cleared on every build (--clean),
      // so each checkout keeps its own and parallel builds in other worktrees don't collide.
      UV_CACHE_DIR: process.env.UV_CACHE_DIR || "/private/tmp/harness-uv-cache",
      PYINSTALLER_CONFIG_DIR: process.env.PYINSTALLER_CONFIG_DIR || join(backend, "build", "pyinstaller-config"),
    },
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

run(
  "uv",
  [
    "run",
    "--no-sync",
    "pyinstaller",
    "--clean",
    "--noconfirm",
    "--distpath",
    "dist",
    "--workpath",
    "build",
    "wavefunction-backend.spec",
  ],
  backend,
);

const distribution = join(backend, "dist", "wavefunction-backend");
const executable = join(distribution, `wavefunction-backend${process.platform === "win32" ? ".exe" : ""}`);
if (!existsSync(executable) || !existsSync(join(distribution, "_internal")))
  throw new Error(`PyInstaller did not create the backend distribution in ${distribution}`);
// Tauri includes this whole directory as a resource in development and release.
process.stdout.write(`Prepared desktop backend: ${distribution}\n`);
