import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
const startDragging = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ startDragging }),
}));

afterEach(() => {
  document.documentElement.classList.remove("tauri");
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});

describe("beginWindowDrag", () => {
  beforeEach(() => {
    startDragging.mockClear();
    document.documentElement.classList.add("tauri");
  });

  it("moves the native window from blank sidebar space", async () => {
    const { beginWindowDrag } = await import("./titlebar");
    const aside = document.createElement("aside");
    beginWindowDrag({
      button: 0,
      detail: 1,
      target: aside,
      currentTarget: aside,
    } as unknown as Parameters<typeof beginWindowDrag>[0]);
    expect(startDragging).toHaveBeenCalledOnce();
  });

  it("keeps sidebar links, forms, buttons and resizing interactive", async () => {
    const { beginWindowDrag } = await import("./titlebar");
    for (const markup of [
      "<button><span>New conversation</span></button>",
      "<a href='/'>Link</a>",
      "<input>",
      "<textarea></textarea>",
      "<select></select>",
      "<div role='separator'></div>",
    ]) {
      const parent = document.createElement("div");
      parent.innerHTML = markup;
      const target = parent.querySelector("span") ?? parent.firstElementChild;
      beginWindowDrag({ button: 0, detail: 1, target } as unknown as Parameters<typeof beginWindowDrag>[0]);
    }
    expect(startDragging).not.toHaveBeenCalled();
  });
});

describe("publishTrafficLightCenter", () => {
  beforeEach(() => {
    vi.resetModules();
    invoke.mockReset();
    document.documentElement.style.removeProperty("--traffic-light-center");
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  });

  async function load() {
    return (await import("./titlebar")).publishTrafficLightCenter;
  }

  it("moves native buttons to the toolbar centre at each webview zoom", async () => {
    invoke.mockResolvedValueOnce(23).mockResolvedValueOnce(26.45);
    const publish = await load();

    await publish(1);
    expect(document.documentElement.style.getPropertyValue("--traffic-light-center")).toBe("23px");
    expect(invoke).toHaveBeenNthCalledWith(1, "align_window_controls", {
      centerY: 23,
    });

    await publish(1.15);
    expect(document.documentElement.style.getPropertyValue("--traffic-light-center")).toBe("23px");
    expect(invoke).toHaveBeenNthCalledWith(2, "align_window_controls", {
      centerY: 26.45,
    });
  });
});

describe("syncWindowTheme", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.style.removeProperty("--background");
  });

  it("paints the native window with the theme the page switches to, glass included", async () => {
    vi.resetModules();
    invoke.mockReset().mockResolvedValue(undefined);
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    // A canvas stand-in that knows the two theme colours, as WebKit's would.
    const pixels: Record<string, number[]> = {
      "oklch(0.963 0.004 286)": [244, 244, 246, 255],
      "oklch(0.201 0.004 286)": [24, 24, 27, 255],
    };
    let fill = "";
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      set fillStyle(value: string) {
        fill = value;
      },
      fillRect: () => undefined,
      getImageData: () => ({ data: pixels[fill] ?? [0, 0, 0, 0] }),
    } as unknown as CanvasRenderingContext2D);
    const { syncWindowTheme } = await import("./titlebar");
    const root = document.documentElement.style;
    root.setProperty("--background", "oklch(0.963 0.004 286)");

    // next-themes swaps the root theme only after the caller's effect has run.
    const synced = syncWindowTheme(true, true);
    root.setProperty("--background", "oklch(0.201 0.004 286)");
    await synced;

    expect(invoke).toHaveBeenCalledExactlyOnceWith("set_window_theme", {
      dark: true,
      background: [24, 24, 27],
      glassEverywhere: true,
    });
  });
});
