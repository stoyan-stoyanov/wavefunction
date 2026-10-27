import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "@/lib/transport";
import { NotificationSettings } from "./notification-settings";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@/lib/transport", () => ({ request: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

let permission: string;
let saved: Record<string, boolean>;
beforeEach(() => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  permission = "not_requested";
  saved = { desktop: false, input: true, sound: true, previews: true };
  vi.mocked(invoke).mockImplementation(async (command, args) => {
    if (command === "notification_permission") {
      // macOS asks once; the answer is whatever the person chose.
      return (args as { request: boolean }).request && permission === "not_requested" ? "denied" : permission;
    }
    return undefined;
  });
  vi.mocked(request).mockImplementation(async (_path, init) => {
    if (init?.method === "PUT") saved = JSON.parse(String(init.body));
    return { ...saved };
  });
});
afterEach(() => vi.unstubAllGlobals());

it("turns notifications on only once macOS allows them, then saves each choice and sends a test", async () => {
  render(<NotificationSettings />);
  const finishes = await screen.findByRole("switch", { name: "When a chat finishes" });
  expect(screen.getByRole("switch", { name: "When a chat needs you" })).toBeDisabled();
  expect(screen.getByText(/macOS asks for permission when the first notification is due/)).toBeVisible();

  // Declined in the macOS prompt: nothing is saved, and the page says where to allow it.
  fireEvent.click(finishes);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Allow them in System Settings › Notifications.",
  );
  expect(request).not.toHaveBeenCalledWith(
    "/api/notifications/settings",
    expect.objectContaining({ method: "PUT" }),
  );
  expect(screen.getByRole("button", { name: "Send test notification" })).toBeDisabled();

  // Allowed there later: turning it on saves.
  permission = "granted";
  fireEvent.click(finishes);
  await waitFor(() => expect(saved.desktop).toBe(true));
  expect(screen.queryByRole("alert")).toBeNull();
  const previews = screen.getByRole("switch", { name: "Show previews" });
  await waitFor(() => expect(previews).toBeEnabled());
  fireEvent.click(previews);
  await waitFor(() => expect(saved).toEqual({ desktop: true, input: true, sound: true, previews: false }));

  fireEvent.click(screen.getByRole("button", { name: "Send test notification" }));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("notification_test", { sound: true }));
});
