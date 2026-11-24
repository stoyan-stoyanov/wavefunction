import { getCurrentWindow } from "@tauri-apps/api/window";
import { windowAction } from "@/lib/titlebar";

/**
 * Ctrl+Q and F11 for a window without a system menu (`has.systemMenu`), which is where macOS has ⌘Q
 * and full screen. Quitting closes the window, as its close button does, so the shell still warns
 * about running work first.
 *
 * The listener runs before any element's, so these keys quit or go full screen from the terminal and
 * editors too, and never reach them.
 */
export function installLinuxKeys(): void {
  window.addEventListener(
    "keydown",
    (event) => {
      const modifiers = event.altKey || event.metaKey || event.shiftKey;
      const quit = event.ctrlKey && !modifiers && event.key.toLowerCase() === "q";
      const fullscreen = !event.ctrlKey && !modifiers && event.key === "F11";
      if (!quit && !fullscreen) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return;
      if (quit) windowAction("close");
      else void toggleFullscreen();
    },
    true,
  );
}

async function toggleFullscreen() {
  const current = getCurrentWindow();
  try {
    await current.setFullscreen(!(await current.isFullscreen()));
  } catch {
    // The window stays as it is.
  }
}
