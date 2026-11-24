import { expect, it, vi } from "vitest";
import { has } from "./platform";
import { installLinuxKeys } from "./linux-keys";

const window_ = vi.hoisted(() => ({
  close: vi.fn().mockResolvedValue(undefined),
  isFullscreen: vi.fn().mockResolvedValue(false),
  setFullscreen: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => window_ }));

it("quits through the window's close and toggles full screen on Linux, before the page sees the keys", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  // jsdom says "linux" in lower case, which no real Linux webview does.
  expect(has.systemMenu()).toBe(true);
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15",
  );
  expect(has.systemMenu()).toBe(false);
  installLinuxKeys();
  const page = vi.fn();
  document.body.addEventListener("keydown", page);
  const press = (init: KeyboardEventInit) => {
    const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
    document.body.dispatchEvent(event);
    return event.defaultPrevented;
  };

  expect(press({ key: "q", ctrlKey: true })).toBe(true);
  expect(window_.close).toHaveBeenCalledOnce();
  expect(press({ key: "F11" })).toBe(true);
  await vi.waitFor(() => expect(window_.setFullscreen).toHaveBeenCalledExactlyOnceWith(true));
  expect(page).not.toHaveBeenCalled();
  // Held keys and other shortcuts pass by.
  expect(press({ key: "q", ctrlKey: true, repeat: true })).toBe(true);
  expect(press({ key: "Q", ctrlKey: true, shiftKey: true })).toBe(false);
  expect(press({ key: "q" })).toBe(false);
  expect(window_.close).toHaveBeenCalledOnce();
  expect(page).toHaveBeenCalledTimes(2);
});
