import { afterEach, expect, it, vi } from "vitest";

const getBundleType = vi.hoisted(() => vi.fn<() => Promise<string | null>>());
vi.mock("@tauri-apps/api/app", () => ({ getBundleType }));

afterEach(() => {
  vi.restoreAllMocks();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});

it("offers updates on macOS and, on Linux, only in the AppImage", async () => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  const cases = [
    { system: "Macintosh; Intel Mac OS X 10_15_7", bundle: "app", updates: true, deb: false },
    { system: "X11; Linux x86_64", bundle: "appimage", updates: true, deb: false },
    { system: "X11; Linux x86_64", bundle: "deb", updates: false, deb: true },
    { system: "X11; Linux x86_64", bundle: null, updates: false, deb: false },
  ];
  for (const { system, bundle, updates, deb } of cases) {
    vi.resetModules();
    getBundleType.mockReset().mockResolvedValue(bundle);
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(`Mozilla/5.0 (${system})`);
    const { has, readLinuxPackage } = await import("./platform");
    await readLinuxPackage();
    expect([has.updates(), has.debPackage()], `${system} ${bundle}`).toEqual([updates, deb]);
    // The Mac app never asks, so nothing about its updates depends on the answer.
    expect(getBundleType).toHaveBeenCalledTimes(bundle === "app" ? 0 : 1);
  }
});
