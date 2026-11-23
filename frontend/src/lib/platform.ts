/**
 * The desktop app built for Linux. Its window has the system's own title bar and no see-through
 * sidebar, and it is built from source, so it has no updater and no macOS notifications.
 */
export const linuxDesktop = () => "__TAURI_INTERNALS__" in window && navigator.userAgent.includes("Linux");
