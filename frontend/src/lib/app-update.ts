import { useSyncExternalStore } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Update } from "@tauri-apps/plugin-updater";

/** Where the installed app stands against the newest published release. */
export type UpdateState =
  | { status: "idle" | "checking" | "current"; version?: string }
  | { status: "available" | "installing" | "ready"; version?: string; next: string; notes?: string }
  | { status: "error"; version?: string; error: string };

const CHECK_EVERY_MS = 6 * 60 * 60 * 1000;
let state: UpdateState = { status: "idle" };
let pending: Update | null = null;
const listeners = new Set<() => void>();

function set(next: UpdateState) {
  state = { ...next, version: next.version ?? state.version };
  listeners.forEach((listener) => listener());
}

/** Only the packaged desktop app updates itself; a development build has nothing to replace. */
const updatable = () => "__TAURI_INTERNALS__" in window && !import.meta.env.DEV;

export async function checkForUpdate() {
  if (!updatable() || state.status === "installing" || state.status === "ready") return;
  const found = state.status === "available" ? state : null;
  set({ status: "checking" });
  try {
    const [{ check }, { getVersion }] = await Promise.all([
      import("@tauri-apps/plugin-updater"),
      import("@tauri-apps/api/app"),
    ]);
    const version = await getVersion();
    pending = await check();
    set(
      pending
        ? { status: "available", version, next: pending.version, notes: pending.body }
        : { status: "current", version },
    );
  } catch (error) {
    // A re-check that fails, offline say, leaves the update it already found installable.
    set(
      found && pending
        ? found
        : { status: "error", error: error instanceof Error ? error.message : String(error) },
    );
  }
}

/**
 * Download and install the update, then restart. The restart goes through the app's quit check,
 * which asks first when tasks are running; declining leaves the update to apply on next launch.
 */
export async function installUpdate() {
  if (!pending || state.status !== "available") return;
  const next = state.next;
  set({ status: "installing", next });
  try {
    await pending.downloadAndInstall();
    set({ status: "ready", next });
    await restartToUpdate();
  } catch (error) {
    set({ status: "error", error: error instanceof Error ? error.message : String(error) });
  }
}

export const restartToUpdate = () => invoke("restart_app");

let started = false;
/** Check shortly after launch and then a few times a day. */
export function startUpdateChecks() {
  if (started || !updatable()) return;
  started = true;
  setTimeout(() => void checkForUpdate(), 10_000);
  setInterval(() => void checkForUpdate(), CHECK_EVERY_MS);
}

export function useAppUpdate() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
  );
}
