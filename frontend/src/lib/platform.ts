/**
 * What differs between the computers the app runs on: macOS, and Linux built from source.
 *
 * Interface code asks here and never reads the user agent itself (scripts/check-platform.mjs). So
 * a feature only the macOS app has is one entry in `has`, which Linux support then changes, and a
 * word that differs is one entry in `words`. The browser development build counts as the computer
 * its browser runs on.
 */
const desktop = () => "__TAURI_INTERNALS__" in window;
const linux = () => navigator.userAgent.includes("Linux");

/** What this build of the app has. */
export const has = {
  /** A see-through window whose page draws the title bar, under the traffic lights. Linux keeps
   * the system's own title bar and paints the window like the browser. */
  windowChrome: () => desktop() && !linux(),
  /** Installing newer releases from Settings › About. Linux builds come from source. */
  updates: () => desktop() && !linux(),
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
