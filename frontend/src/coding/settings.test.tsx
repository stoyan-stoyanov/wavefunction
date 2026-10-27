import { useSettings } from "@/store/settings";
import { applySavedSidebarTransparency } from "@/lib/sidebar-transparency";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { codingApi, type AnalyticsStats, type CodingAnalytics } from "./api";
import CodingSettings from "./settings";
import { useCoding } from "./store";

vi.mock("@/views/models-settings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/views/models-settings")>()),
  ModelsSettings: () => <h2>Models</h2>,
}));

vi.mock("@/views/computer-settings", () => ({ ComputerSettings: () => <h2>Computer use</h2> }));

beforeEach(() => {
  localStorage.clear();
  useSettings.setState({
    connections: null,
    openRouterModels: [],
    modelOptionsLoading: false,
    grants: null,
    action: null,
    error: null,
  });
  useCoding.setState({ selected: "thread-a", workspace: null });
  vi.spyOn(useSettings.getState(), "loadConnections").mockResolvedValue(undefined);
  vi.spyOn(useSettings.getState(), "loadGrants").mockResolvedValue(undefined);
  vi.spyOn(useCoding.getState(), "refresh").mockResolvedValue(undefined);
  vi.spyOn(codingApi, "skillCatalog").mockResolvedValue({ skills: [], warnings: [] });
  vi.spyOn(codingApi, "access").mockResolvedValue({ access: "network" });
  vi.spyOn(codingApi, "defaultMode").mockResolvedValue({ mode: "full" });
  vi.spyOn(codingApi, "worktreeCleanup").mockResolvedValue({ retention_days: 14 });
});

afterEach(() => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove("light", "dark");
  document.documentElement.style.removeProperty("--sidebar-light-opacity");
  document.documentElement.style.removeProperty("--sidebar-dark-opacity");
});

function open(props: Partial<Parameters<typeof CodingSettings>[0]> = {}) {
  const onBack = vi.fn();
  const view = render(
    <ThemeProvider defaultTheme="light" enableSystem={false} attribute="class">
      <div className="coding-shell" data-testid="shell">
        <CodingSettings onBack={onBack} {...props} />
      </div>
    </ThemeProvider>,
  );
  return { ...view, onBack };
}

it("fully hides and reopens its navigation while preserving the selected page", async () => {
  open({ initialPage: "models" });
  const sidebar = screen.getByRole("complementary", {
    name: "Settings sidebar",
  });
  expect(sidebar).toContainElement(screen.getByRole("button", { name: "Hide sidebar" }));
  fireEvent.click(screen.getByRole("button", { name: "Hide sidebar" }));
  expect(screen.queryByRole("navigation", { name: "Settings pages" })).toBeNull();
  expect(screen.queryByRole("complementary", { name: "Settings sidebar" })).toBeNull();
  expect(screen.queryByRole("complementary", { name: "Collapsed sidebar" })).toBeNull();
  expect(screen.queryByRole("separator", { name: "Resize sidebar" })).toBeNull();
  expect(screen.getByRole("banner", { name: "Settings toolbar" })).toContainElement(
    screen.getByRole("button", { name: "Show sidebar" }),
  );
  expect(screen.getByRole("heading", { name: "Models" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
  expect(screen.getByRole("button", { name: "Models" })).toHaveAttribute("aria-current", "page");
});

it("opens the skill catalog from settings and returns to the selected chat", async () => {
  useSettings.setState({ error: "Previous provider error" });
  const { onBack } = open();
  const navigation = screen.getByRole("navigation", { name: "Settings pages" });
  expect(screen.getByRole("alert")).toHaveTextContent("Previous provider error");
  fireEvent.click(within(navigation).getByRole("button", { name: "Skills" }));
  expect(await screen.findByRole("heading", { name: "Skills" })).toBeVisible();
  await waitFor(() => expect(codingApi.skillCatalog).toHaveBeenCalledOnce());
  expect(screen.getByRole("complementary", { name: "Settings sidebar" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(onBack).toHaveBeenCalledOnce();
  expect(useCoding.getState().selected).toBe("thread-a");
});

it("keeps the existing theme control in General and persists appearance changes", async () => {
  open();
  const theme = screen.getByRole("radiogroup", { name: "Theme" });
  expect(screen.getAllByRole("radiogroup", { name: "Theme" })).toHaveLength(1);
  expect(screen.getByRole("main")).toContainElement(theme);
  expect(screen.getByRole("complementary", { name: "Settings sidebar" })).not.toContainElement(theme);
  fireEvent.click(within(theme).getByRole("radio", { name: "Dark" }));
  await waitFor(() => expect(localStorage.getItem("theme")).toBe("dark"));
  expect(within(theme).getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(within(theme).getByRole("radio", { name: "Light" }));
  await waitFor(() => expect(localStorage.getItem("theme")).toBe("light"));
});

it("previews and restores independent sidebar transparency for each theme", async () => {
  const first = open();
  const slider = () => screen.getByRole("slider", { name: "Sidebar transparency" });
  const theme = () => screen.getByRole("radiogroup", { name: "Theme" });
  expect(slider()).toHaveValue("85");
  fireEvent.change(slider(), { target: { value: "100" } });
  expect(slider()).toHaveAttribute("aria-valuetext", "100% transparent");
  expect(document.documentElement.style.getPropertyValue("--sidebar-light-opacity")).toBe("0%");

  fireEvent.click(within(theme()).getByRole("radio", { name: "Dark" }));
  await waitFor(() => expect(slider()).toHaveValue("75"));
  fireEvent.change(slider(), { target: { value: "0" } });
  expect(document.documentElement.style.getPropertyValue("--sidebar-dark-opacity")).toBe("100%");
  fireEvent.click(within(theme()).getByRole("radio", { name: "Light" }));
  await waitFor(() => expect(slider()).toHaveValue("100"));
  first.unmount();

  // A fresh document must apply saved values before Settings is ever opened.
  document.documentElement.style.removeProperty("--sidebar-light-opacity");
  document.documentElement.style.removeProperty("--sidebar-dark-opacity");
  applySavedSidebarTransparency();
  expect(document.documentElement.style.getPropertyValue("--sidebar-light-opacity")).toBe("0%");
  expect(document.documentElement.style.getPropertyValue("--sidebar-dark-opacity")).toBe("100%");
  open();
  expect(slider()).toHaveValue("100");
  fireEvent.click(within(theme()).getByRole("radio", { name: "Dark" }));
  await waitFor(() => expect(slider()).toHaveValue("0"));
});

it("persists Show workbench from General across settings pages and reopen", async () => {
  const first = open();
  const workbench = screen.getByRole("switch", { name: "Show workbench" });
  expect(workbench).toBeChecked();
  expect(workbench).toHaveAccessibleDescription(
    "Show Files, Terminal, and Changes in the repository sidebar.",
  );
  expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeVisible();
  fireEvent.click(workbench);
  expect(workbench).not.toBeChecked();
  expect(localStorage.getItem("harness.workbench.enabled")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Models" }));
  fireEvent.click(screen.getByRole("button", { name: "General" }));
  expect(screen.getByRole("switch", { name: "Show workbench" })).not.toBeChecked();
  first.unmount();
  open();
  const restored = screen.getByRole("switch", { name: "Show workbench" });
  expect(restored).not.toBeChecked();
  fireEvent.click(restored);
  expect(localStorage.getItem("harness.workbench.enabled")).toBe("true");
});

it("preserves Models routing and access controls", async () => {
  open({ initialPage: "models" });
  expect(screen.getByRole("heading", { name: "Models" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Models" })).toHaveAttribute("aria-current", "page");
  fireEvent.click(screen.getByRole("button", { name: "Access" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Command access" }), {
    target: { value: "network" },
  });
  await waitFor(() => expect(codingApi.access).toHaveBeenCalledWith("network"));
  expect(useCoding.getState().refresh).toHaveBeenCalled();
  fireEvent.change(screen.getByRole("combobox", { name: "Mode for new chats" }), {
    target: { value: "full" },
  });
  await waitFor(() => expect(codingApi.defaultMode).toHaveBeenCalledWith("full"));
});

it("loads analytics on demand, retries failures and preserves partial and unknown usage", async () => {
  const unknown = { value: null, known_runs: 0 };
  const recorded = (value: number) => ({ value, known_runs: 2 });
  const empty: AnalyticsStats = {
    runs: 0,
    completed_runs: 0,
    failed_runs: 0,
    cancelled_runs: 0,
    active_runs: 0,
    user_requests: 0,
    last_active_at: null,
    input_tokens: { value: 0, known_runs: 0 },
    output_tokens: { value: 0, known_runs: 0 },
    total_tokens: { value: 0, known_runs: 0 },
    cached_input_tokens: { value: 0, known_runs: 0 },
    uncached_input_tokens: { value: 0, known_runs: 0 },
    cost_usd: { value: 0, known_runs: 0 },
    duration_ms: { value: 0, known_runs: 0 },
    average_duration_ms: { value: 0, known_runs: 0 },
  };
  const known: AnalyticsStats = {
    ...empty,
    runs: 2,
    completed_runs: 2,
    user_requests: 1,
    last_active_at: "2026-10-07T12:00:00Z",
    input_tokens: recorded(10_000),
    output_tokens: recorded(1000),
    total_tokens: recorded(11_000),
    cached_input_tokens: recorded(8000),
    uncached_input_tokens: recorded(2000),
    cost_usd: recorded(0),
    duration_ms: recorded(360_000),
    average_duration_ms: recorded(180_000),
  };
  const missing: AnalyticsStats = {
    ...empty,
    runs: 1,
    failed_runs: 1,
    user_requests: 1,
    input_tokens: unknown,
    output_tokens: unknown,
    total_tokens: unknown,
    cached_input_tokens: unknown,
    uncached_input_tokens: unknown,
    cost_usd: unknown,
    duration_ms: unknown,
    average_duration_ms: unknown,
  };
  const totals = { ...known, runs: 3, failed_runs: 1, user_requests: 2 };
  const response: CodingAnalytics = {
    generated_at: "2026-10-07T12:00:00Z",
    timezone: "UTC",
    totals,
    models: [
      { ...known, provider: "openrouter", model: "deepseek/deepseek-v4-pro-0813" },
      { ...missing, provider: null, model: null },
    ],
    daily: Array.from({ length: 14 }, (_, index) => ({
      ...(index === 13 ? totals : empty),
      date: new Date(Date.UTC(2026, 8, 24 + index)).toISOString().slice(0, 10),
    })),
  };
  let resolveFirst!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    resolveFirst = resolve;
  });
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockReturnValueOnce(pending)
    .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ ...response, totals: empty, models: [] }), { status: 200 }),
    );
  open();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Analytics" }));
  expect(screen.getByRole("status")).toHaveTextContent("Loading analytics…");
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/coding/analytics",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );
  await act(async () => {
    resolveFirst(
      new Response(JSON.stringify({ detail: "Analytics temporarily unavailable" }), { status: 503 }),
    );
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("Analytics temporarily unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  const models = await screen.findByRole("table", { name: "All-time recorded usage by model" });
  const knownRow = within(models).getByRole("row", { name: /deepseek\/deepseek-v4-pro-0813/ });
  expect(within(knownRow).getByRole("rowheader")).toHaveTextContent(/^deepseek\/deepseek-v4-pro-0813$/);
  expect(
    within(knownRow)
      .getAllByRole("cell")
      .map((cell) => cell.textContent),
  ).toEqual(["2", "10,000", "8,000", "2,000", "1,000", "6m 0s", "$0.00"]);
  const unknownRow = within(models).getByRole("row", { name: /Unknown model/ });
  expect(within(unknownRow).getByRole("rowheader")).toHaveTextContent(/^Unknown model$/);
  expect(
    within(unknownRow)
      .getAllByRole("cell")
      .map((cell) => cell.textContent),
  ).toEqual(["1", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"]);
  const summary = screen.getByLabelText("All-time usage summary");
  // Summary totals shorten to K/M/B/T and keep the exact count in the tooltip.
  expect(within(summary).getByText("10K")).toHaveAttribute("title", "10,000");
  expect(within(summary).getAllByText("Reported for 2 of 3 runs").length).toBeGreaterThan(0);
  expect(within(summary).getByText("6m 0s")).toBeVisible();
  const chart = screen.getByRole("list", { name: "Daily token usage" });
  expect(within(chart).getAllByRole("img")).toHaveLength(14);
  expect(within(chart).getByRole("img", { name: /11,000 recorded tokens, 3 runs/ })).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh analytics" }));
  expect(await screen.findByRole("heading", { name: "No recorded activity yet" })).toBeVisible();
  expect(screen.queryByRole("table", { name: "All-time recorded usage by model" })).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(3);
});
