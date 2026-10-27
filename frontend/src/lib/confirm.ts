import { ask } from "@tauri-apps/plugin-dialog";

/**
 * Ask before a destructive action and wait for the answer.
 *
 * In the desktop app the dialog plugin replaces `window.confirm` with an asynchronous function,
 * so its result is a Promise that is always truthy. The native question is awaited there instead;
 * an ordinary browser keeps its own confirmation.
 */
export async function confirmAction(message: string): Promise<boolean> {
  if ("__TAURI_INTERNALS__" in window) return ask(message, { kind: "warning" });
  return window.confirm(message);
}
