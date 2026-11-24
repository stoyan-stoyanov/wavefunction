/**
 * What differs between the computers the app runs on: macOS and Linux.
 *
 * Interface code asks here and never reads the user agent itself (scripts/check-platform.mjs). So
 * a feature only the macOS app has is one entry in `has`, which Linux support then changes, and a
 * word that differs is one entry in `words`. The browser development build counts as the computer
 * its browser runs on.
 */
const desktop = () => "__TAURI_INTERNALS__" in window;
const linux = () => navigator.userAgent.includes("Linux");

/** The Linux package this app runs from, which Tauri's bundler writes into each one: "appimage",
 * "deb", or null for one built and run from source. */
let linuxPackage: string | null = null;

/** Reads the Linux package once, before the first render. macOS never asks. */
export async function readLinuxPackage() {
  if (!desktop() || !linux()) return;
  const { getBundleType } = await import("@tauri-apps/api/app");
  linuxPackage = await getBundleType().catch(() => null);
}

/** What this build of the app has. */
export const has = {
  /** A see-through window whose page draws the title bar, under the traffic lights. Linux keeps
   * the system's own title bar and paints the window like the browser. */
  windowChrome: () => desktop() && !linux(),
  /** Installing newer releases from Settings › About. On Linux only the AppImage replaces
   * itself; the .deb updates through the package manager and a source build by building again. */
  updates: () => desktop() && (!linux() || linuxPackage === "appimage"),
  /** Installed from the release's .deb, so updated by installing the newer one. */
  debPackage: () => desktop() && linux() && linuxPackage === "deb",
  /** The system's dictation typing into the composer. */
  dictation: () => desktop() && !linux(),
};

/** Words that name this computer and its parts. */
export const words = {
  thisComputer: () => (linux() ? "this computer" : "this Mac"),
  yourComputer: () => (linux() ? "your computer" : "your Mac"),
  fileManager: () => (linux() ? "File manager" : "Finder"),
  system: () => (linux() ? "Linux" : "macOS"),
  /** The key that shortcuts hold, written before the other key: ⌘0 or Ctrl+0. */
  shortcutKey: () => (linux() ? "Ctrl+" : "⌘"),
};
