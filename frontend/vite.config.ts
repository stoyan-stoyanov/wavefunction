import path from "node:path";
import { readdirSync, readFileSync } from "node:fs";
import { configDefaults, defineConfig } from "vitest/config";
import type { Reporter } from "vitest/reporters";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Time budget (docs/TESTING.md): Vitest's 5 s timeout limits one test; this limits all of them
// together. A subset can only exceed it if the whole suite would. A shared CI runner's times vary
// by a third between runs of the same code, so CI reports the total and a local run fails on it.
const CASES = 230;
const SUITE_SECONDS = 90;
// The case count is shown for a run of the whole suite, not of chosen files.
const testFiles = readdirSync(path.resolve(import.meta.dirname, "src"), {
  recursive: true,
  encoding: "utf8",
}).filter((file) => /\.test\.tsx?$/.test(file) && !file.startsWith(".verification")).length;

function testBudgetReporter(): Reporter {
  const counts = new Map<string, number>();
  return {
    onTestRunStart() {
      counts.clear();
    },
    onTestRunEnd(testModules) {
      const seconds = testModules.reduce((sum, module) => sum + module.diagnostic().duration, 0) / 1000;
      const cases = [...counts.values()].reduce((sum, count) => sum + count, 0);
      const whole = testModules.length === testFiles ? `Cases: ${cases} of ${CASES}. ` : "";
      console.log(`${whole}Test time: ${Math.round(seconds)} s of the ${SUITE_SECONDS} s budget.`);
      if (seconds > SUITE_SECONDS) {
        console.error(
          `The tests took ${Math.round(seconds)} s together, over the ${SUITE_SECONDS} s budget. Speed up the slowest files before adding more.${process.env.CI ? " CI reports this; a local run fails on it." : ""}`,
        );
        if (!process.env.CI) process.exitCode = 1;
      }
    },
    onTestModuleCollected(module) {
      counts.set(module.moduleId, [...module.children.allTests()].length);
      const total = [...counts.values()].reduce((sum, count) => sum + count, 0);
      if (total > CASES) {
        throw new Error(
          `Frontend test budget exceeded: ${total}/${CASES} cases. Replace redundant coverage before adding cases.`,
        );
      }
    },
  };
}

// The released version, from the desktop app's own configuration.
const appVersion = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, "../src-tauri/tauri.conf.json"), "utf8"),
).version;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(appVersion) },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      // ws: the desktop stream when taking control of computer use.
      "/api": { target: `http://127.0.0.1:${process.env.WAVEFUNCTION_API_PORT || "8765"}`, ws: true },
      "/health": `http://127.0.0.1:${process.env.WAVEFUNCTION_API_PORT || "8765"}`,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    // Local checks write throwaway tests under src/.verification (gitignored); they are not the suite.
    exclude: [...configDefaults.exclude, "src/.verification/**"],
    reporters: ["default", testBudgetReporter()],
    maxWorkers: 2,
  },
});
