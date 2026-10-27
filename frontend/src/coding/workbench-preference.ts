import { useSyncExternalStore } from "react";

const storageKey = "harness.workbench.enabled";
const listeners = new Set<() => void>();
let unsavedEnabled: boolean | undefined;

function getSnapshot(): boolean {
  if (unsavedEnabled !== undefined) return unsavedEnabled;
  try {
    return window.localStorage.getItem(storageKey) !== "false";
  } catch {
    return true;
  }
}

function notify() {
  for (const listener of listeners) listener();
}

function storageChanged(event: StorageEvent) {
  if (event.key !== storageKey && event.key !== null) return;
  if (event.storageArea !== null) {
    try {
      if (event.storageArea !== window.localStorage) return;
    } catch {
      return;
    }
  }
  unsavedEnabled = undefined;
  notify();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) window.addEventListener("storage", storageChanged);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) window.removeEventListener("storage", storageChanged);
  };
}

function setEnabled(value: boolean) {
  unsavedEnabled = value;
  try {
    window.localStorage.setItem(storageKey, String(value));
    unsavedEnabled = undefined;
  } catch {
    // Keep the preference shared for this window when storage is unavailable.
  }
  notify();
}

export function useWorkbenchPreference(): {
  enabled: boolean;
  setEnabled: (value: boolean) => void;
} {
  const enabled = useSyncExternalStore(subscribe, getSnapshot, () => true);
  return { enabled, setEnabled };
}
