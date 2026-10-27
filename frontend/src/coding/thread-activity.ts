import { useEffect, useState } from "react";
import type { CodingSnapshot } from "./api";
import { useCoding } from "./store";

const storageKey = "wavefunction.read-runs";
type ReadRuns = Record<string, string | null>;
function savedReads(): ReadRuns {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "{}");
    if (value && typeof value === "object" && !Array.isArray(value))
      return Object.fromEntries(
        Object.entries(value).filter(([, run]) => run === null || typeof run === "string"),
      );
  } catch {
    /* Keep tracking in memory when storage is unavailable. */
  }
  return {};
}

export function useThreadActivity(workspace: CodingSnapshot | null, viewing: string | null) {
  const [reads, setReads] = useState(savedReads);
  const [focused, setFocused] = useState(() => document.hasFocus() && document.visibilityState === "visible");
  useEffect(() => {
    const updateFocus = () => setFocused(document.hasFocus() && document.visibilityState === "visible");
    const refresh = () => {
      if (document.visibilityState === "visible") {
        const state = useCoding.getState();
        // Startup owns the first snapshot and chat selection; polling must not supersede it.
        if (state.loading || !state.workspace) return;
        void (state.selected ? state.sync(state.selected) : state.refresh()).catch(() => undefined);
      }
    };
    const onFocus = () => {
      updateFocus();
      refresh();
    };
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", updateFocus);
    document.addEventListener("visibilitychange", onFocus);
    // Only the window in front polls; focusing one refreshes it at once.
    const timer = window.setInterval(() => {
      if (document.hasFocus()) refresh();
    }, 5000);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", updateFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, []);
  if (workspace) {
    const next = { ...reads };
    let changed = false;
    for (const thread of workspace.threads) {
      const key = `${workspace.agent_id}:${thread.id}`;
      const finished = thread.last_finished_run_id ?? null;
      // Establish a baseline on first use; acknowledge only the focused chat.
      if (!(key in reads) || (focused && viewing === thread.id)) {
        if (!(key in reads) || reads[key] !== finished) {
          next[key] = finished;
          changed = true;
        }
      }
    }
    if (changed) setReads(next);
  }
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(reads));
    } catch {
      /* Session still works. */
    }
  }, [reads]);
  return new Set(
    (workspace?.threads ?? [])
      .filter((thread) => {
        const key = `${workspace!.agent_id}:${thread.id}`;
        return thread.last_finished_run_id && key in reads && reads[key] !== thread.last_finished_run_id;
      })
      .map((thread) => thread.id),
  );
}
