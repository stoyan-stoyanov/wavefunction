import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { defaultDesign, forcedAppearance, restoreDesign, useDesign } from "@/lib/design";
import { request } from "@/lib/transport";
import { GeneralSettings } from "@/views/general-settings";

vi.mock("@/lib/transport", () => ({ request: vi.fn() }));

const themes = {
  folder: "/data/themes",
  themes: [
    {
      id: "mine",
      name: "Mine",
      description: "My own look.",
      appearances: ["light", "dark"],
      window_controls: "native",
      error: null,
    },
    {
      id: "broken",
      name: "Broken",
      description: "",
      appearances: ["light", "dark"],
      window_controls: "native",
      error: 'theme.json needs a "name".',
    },
  ],
};

function serve(listed: unknown = themes) {
  vi.mocked(request).mockImplementation(async (path: string) => {
    if (path === "/api/themes") return listed;
    if (path === "/api/themes/mine/stylesheet") return { css: ":root { --background: #123456; }" };
    throw new Error(`unexpected request ${path}`);
  });
}

/** The app's own wiring: a design that supports one appearance forces it. */
function Settings() {
  const forcedTheme = useDesign(forcedAppearance);
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} forcedTheme={forcedTheme}>
      <GeneralSettings />
    </ThemeProvider>
  );
}

function choose(name: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Design" }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name }));
}

const stylesheet = () => document.getElementById("wavefunction-design");

beforeEach(() => {
  localStorage.clear();
  useDesign.setState({ active: defaultDesign, userDesigns: null, folder: null, error: null });
});

afterEach(() => {
  vi.mocked(request).mockReset();
  stylesheet()?.remove();
  delete document.documentElement.dataset.design;
  document.documentElement.classList.remove("light", "dark");
});

it("switches designs from Settings in place, forcing the appearance of a light-only one", async () => {
  serve();
  render(<Settings />);
  const heading = screen.getByRole("heading", { name: "General" });
  await screen.findByText(/Broken: theme\.json needs a "name"\./);
  expect(screen.getByText("/data/themes")).toBeInTheDocument();
  const theme = screen.getByRole("radiogroup", { name: "Theme" });
  await waitFor(() => expect(document.documentElement).toHaveClass("dark"));

  choose("Windows 7");
  await waitFor(() => expect(document.documentElement.dataset.design).toBe("windows-7"));
  expect(stylesheet()?.textContent).toContain("--w7-glass");
  expect(localStorage.getItem("wavefunction.design")).toBe("windows-7");
  await waitFor(() => expect(document.documentElement).toHaveClass("light"));
  expect(within(theme).getByRole("radio", { name: "Light" })).toHaveAttribute("aria-checked", "true");
  expect(within(theme).getByRole("radio", { name: "Dark" })).toBeDisabled();
  expect(screen.getByText("Windows 7 has a light look only.")).toBeInTheDocument();

  fireEvent.keyDown(screen.getByRole("combobox", { name: "Design" }), { key: "ArrowDown" });
  expect(screen.getByRole("option", { name: "Broken" })).toHaveAttribute("aria-disabled", "true");
  fireEvent.click(screen.getByRole("option", { name: "Mine" }));
  await waitFor(() => expect(document.documentElement.dataset.design).toBe("user:mine"));
  expect(stylesheet()?.textContent).toBe(":root { --background: #123456; }");
  expect(request).toHaveBeenCalledWith("/api/themes/mine/stylesheet");
  await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
  expect(within(theme).getByRole("radio", { name: "Dark" })).toBeEnabled();

  choose("Default");
  await waitFor(() => expect(document.documentElement.dataset.design).toBeUndefined());
  expect(stylesheet()).toBeNull();
  expect(localStorage.getItem("wavefunction.design")).toBeNull();
  // Nothing remounted: the page the person was on is the same page.
  expect(screen.getByRole("heading", { name: "General" })).toBe(heading);
});

it("restores the saved design at startup and falls back to the default when it has gone", async () => {
  localStorage.setItem("wavefunction.design", "windows-7");
  await restoreDesign();
  // A bundled design is in place by the time the app first renders.
  expect(document.documentElement.dataset.design).toBe("windows-7");
  expect(stylesheet()?.textContent).toContain("--w7-glass");

  serve({ folder: "/data/themes", themes: [] });
  localStorage.setItem("wavefunction.design", "user:gone");
  await restoreDesign();
  await waitFor(() => expect(localStorage.getItem("wavefunction.design")).toBeNull());
  expect(document.documentElement.dataset.design).toBeUndefined();
  expect(stylesheet()).toBeNull();
  expect(useDesign.getState().error).toBe("That theme is no longer in the themes folder.");
});
