import { useCallback, useEffect, useSyncExternalStore } from "react";

import { publishTrafficLightCenter } from "@/lib/titlebar";

// The ladder desktop browsers use, trimmed to what stays legible in a window
// with a 1080px minimum width.
const zoomLevels = [0.75, 0.85, 1, 1.15, 1.3, 1.5, 1.75, 2] as const;

export const defaultZoom = 1.15;

const storageKey = "wavefunction.zoom";

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function nearestZoomIndex(zoom: number): number {
  let best = 0;
  for (let index = 1; index < zoomLevels.length; index += 1) {
    if (Math.abs(zoomLevels[index] - zoom) < Math.abs(zoomLevels[best] - zoom)) {
      best = index;
    }
  }
  return best;
}

function readStoredZoom(): number {
  try {
    const stored = Number(window.localStorage?.getItem(storageKey));
    if (!Number.isFinite(stored) || stored <= 0) return defaultZoom;
    return zoomLevels[nearestZoomIndex(stored)];
  } catch {
    return defaultZoom;
  }
}

function applyZoom(zoom: number): void {
  document.documentElement.style.setProperty("--app-zoom", String(zoom));
  // Align the native buttons with the toolbar's centre after each zoom change.
  void publishTrafficLightCenter(zoom);

  if (isTauri()) {
    // Real webview zoom, so viewport units and device pixel ratio stay correct.
    void import("@tauri-apps/api/webview")
      .then(async ({ getCurrentWebview }) => {
        await getCurrentWebview().setZoom(zoom);
        document.documentElement.style.zoom = "";
        document.documentElement.style.setProperty("--css-zoom", "1");
      })
      .catch(() => {
        document.documentElement.style.zoom = String(zoom);
        document.documentElement.style.setProperty("--css-zoom", String(zoom));
      });
    return;
  }
  document.documentElement.style.zoom = zoom === 1 ? "" : String(zoom);
  document.documentElement.style.setProperty("--css-zoom", String(zoom));
}

// Zoom is a window-level setting, so it lives outside React. Both the shortcut
// handler and the Settings stepper have to read and write the same value.
let currentZoom = defaultZoom;
let hydrated = false;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): number {
  if (!hydrated) {
    currentZoom = readStoredZoom();
    hydrated = true;
  }
  return currentZoom;
}

export function setZoom(zoom: number): void {
  const next = zoomLevels[nearestZoomIndex(zoom)];
  if (next === getSnapshot()) return;
  currentZoom = next;
  applyZoom(next);
  try {
    window.localStorage?.setItem(storageKey, String(next));
  } catch {
    // A restricted web preview can omit localStorage; the desktop app always provides it.
  }
  for (const listener of listeners) listener();
}

export function zoomBy(steps: number): void {
  const index = nearestZoomIndex(getSnapshot());
  setZoom(zoomLevels[Math.min(zoomLevels.length - 1, Math.max(0, index + steps))]);
}

/**
 * Window zoom with the shortcuts every desktop app uses: Cmd/Ctrl with plus,
 * minus and zero. Call `bindShortcuts` from exactly one place.
 */
export function useZoom({ bindShortcuts = false }: { bindShortcuts?: boolean } = {}) {
  const zoom = useSyncExternalStore(subscribe, getSnapshot, () => defaultZoom);

  useEffect(() => {
    if (!bindShortcuts) return;
    applyZoom(getSnapshot());
  }, [bindShortcuts]);

  useEffect(() => {
    if (!bindShortcuts) return;

    function handleKeyDown(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      // Match on `code` as well so the shortcuts survive non-US layouts, where
      // the glyph on the key does not match `event.key`.
      if (event.code === "Equal" || event.key === "+" || event.key === "=") {
        event.preventDefault();
        zoomBy(1);
      } else if (event.code === "Minus" || event.key === "-") {
        event.preventDefault();
        zoomBy(-1);
      } else if (event.code === "Digit0" || event.key === "0") {
        event.preventDefault();
        setZoom(defaultZoom);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [bindShortcuts]);

  return {
    zoom,
    zoomIn: useCallback(() => zoomBy(1), []),
    zoomOut: useCallback(() => zoomBy(-1), []),
    resetZoom: useCallback(() => setZoom(defaultZoom), []),
    canZoomIn: zoom < zoomLevels[zoomLevels.length - 1],
    canZoomOut: zoom > zoomLevels[0],
    canResetZoom: zoom !== defaultZoom,
  };
}
