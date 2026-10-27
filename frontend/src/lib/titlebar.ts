import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { MouseEvent } from "react";

export function beginWindowDrag(event: MouseEvent<HTMLElement>) {
  if (event.button !== 0 || event.detail > 2 || !document.documentElement.classList.contains("tauri")) return;
  const target = event.target as HTMLElement;
  if (
    target.closest(
      "button, a, input, select, textarea, [contenteditable=true], [role=button], [role=separator]",
    )
  )
    return;
  if (event.detail === 2) {
    // A double-click zooms the window, as a title bar does. Tauri does it itself only where the
    // clicked element is marked as a drag region; doing it again there would undo it.
    const region = target.getAttribute("data-tauri-drag-region");
    if (region === null || region === "false")
      void getCurrentWindow()
        .toggleMaximize()
        .catch(() => undefined);
    return;
  }
  holdDuringDrag(event.currentTarget);
  void getCurrentWindow()
    .startDragging()
    .catch(() => {
      // The data-tauri-drag-region attribute remains the native fallback.
    });
}

/** How long the window must be still before pointer events count again after a drag. */
const WINDOW_REST_MS = 300;

/**
 * Marks what a window drag started from as held (data-held) until the window has come to rest and
 * the pointer then moves, button up, or presses outside it. While the window moves, the page's idea
 * of where the pointer is cannot be trusted, so a toolbar shown on hover would blink.
 */
function holdDuringDrag(element: HTMLElement) {
  if ("held" in element.dataset) return;
  element.dataset.held = "";
  let moved = performance.now();
  const unlisten = Promise.resolve()
    .then(() => getCurrentWindow().onMoved(() => (moved = performance.now())))
    .catch(() => undefined);
  const release = (event: globalThis.MouseEvent) => {
    if (performance.now() - moved < WINDOW_REST_MS) return;
    if (event.type === "mousemove" && event.buttons !== 0) return;
    if (event.target instanceof Node && element.contains(event.target)) return;
    delete element.dataset.held;
    document.removeEventListener("mousemove", release);
    document.removeEventListener("mousedown", release);
    void unlisten.then((stop) => stop?.());
  };
  document.addEventListener("mousemove", release);
  document.addEventListener("mousedown", release);
}

/**
 * Align the native window buttons with the toolbar, including saved webview zoom.
 * AppKit's button frames use window points, while the toolbar uses zoomed CSS pixels.
 * Call it when the zoom changes; the native side keeps the buttons there through resizes.
 */
export async function publishTrafficLightCenter(zoom: number): Promise<void> {
  if (!("__TAURI_INTERNALS__" in window)) return;
  if (!Number.isFinite(zoom) || zoom <= 0) return;

  const toolbar = document.querySelector<HTMLElement>(".shell-titlebar");
  const style = toolbar ? getComputedStyle(toolbar) : null;
  const center =
    toolbar && style
      ? (parseFloat(style.height) + parseFloat(style.borderTopWidth) - parseFloat(style.borderBottomWidth)) /
        2
      : 23;
  try {
    const measured = await invoke<number | null>("align_window_controls", {
      centerY: center * zoom,
    });
    if (typeof measured !== "number" || !Number.isFinite(measured)) return;
    const root = document.documentElement.style;
    const value = `${measured / zoom}px`;
    // A write to the root style restyles the whole page even when nothing changed.
    if (root.getPropertyValue("--traffic-light-center") !== value)
      root.setProperty("--traffic-light-center", value);
  } catch {
    // The normal native titlebar remains usable if AppKit cannot expose its button frames.
  }
}

/**
 * Tell the native window which theme the app is showing.
 *
 * macOS decides the vibrancy layer's colour from the window's appearance, and that follows the
 * system setting unless we say otherwise. Without this the sidebar's blur is dark while the app
 * is light, or the other way round.
 *
 * The window also paints the page's background colour behind the page, so the area a resize
 * uncovers is solid before the page has drawn it. Its blur covers only the sidebar unless
 * `glassEverywhere`, for a screen whose see-through part is wider.
 */
export async function syncWindowTheme(dark: boolean, glassEverywhere = false): Promise<void> {
  if (!("__TAURI_INTERNALS__" in window)) return;
  // next-themes switches the root class in its own effect, which React runs after the caller's.
  await Promise.resolve();
  try {
    await invoke("set_window_theme", { dark, background: pageBackground(), glassEverywhere });
  } catch {
    // Not fatal: the window keeps the system appearance.
  }
}

/** The theme's background colour as sRGB bytes, which is what the native window takes. */
function pageBackground(): [number, number, number] | null {
  const color = getComputedStyle(document.documentElement).getPropertyValue("--background").trim();
  // The theme defines its colours in oklch; a canvas converts any CSS colour to sRGB.
  const context = color ? document.createElement("canvas").getContext("2d") : null;
  if (!context) return null;
  // An invalid colour leaves the fill transparent, which is rejected below.
  context.fillStyle = "transparent";
  context.fillStyle = color;
  context.fillRect(0, 0, 1, 1);
  const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
  return alpha === 255 ? [red, green, blue] : null;
}
