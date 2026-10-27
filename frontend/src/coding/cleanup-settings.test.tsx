import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { codingApi, type WorktreeCleanupSettings as CleanupSettings } from "./api";
import { WorktreeCleanupSettings } from "./cleanup-settings";

it("loads, validates and persists cleanup retention or Never, preserving edits after a failed save", async () => {
  const load = vi
    .spyOn(codingApi, "worktreeCleanup")
    .mockRejectedValueOnce(new Error("Settings unavailable"))
    .mockResolvedValue({ retention_days: 14 });
  const save = vi.spyOn(codingApi, "saveWorktreeCleanup").mockRejectedValueOnce(new Error("Could not save"));
  let view = render(<WorktreeCleanupSettings />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading cleanup settings");
  expect(await screen.findByRole("alert")).toHaveTextContent("Settings unavailable");
  expect(screen.queryByRole("spinbutton")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  const days = await screen.findByRole("spinbutton", { name: "Days of inactivity" });
  const submit = screen.getByRole("button", { name: "Save cleanup settings" });
  expect(days).toHaveValue(14);
  expect(submit).toBeDisabled();
  for (const value of ["0", "3651", "1.5", ""]) {
    fireEvent.change(days, { target: { value } });
    expect(days).toHaveAttribute("aria-invalid", "true");
    expect(submit).toBeDisabled();
  }
  expect(save).not.toHaveBeenCalled();
  fireEvent.change(days, { target: { value: "30" } });
  fireEvent.click(submit);
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not save");
  expect(days).toHaveValue(30);
  expect(submit).toBeEnabled();

  let finish!: (settings: CleanupSettings) => void;
  save.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.click(submit);
  expect(days).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Delete unused worktrees" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  await act(async () => finish({ retention_days: 30 }));
  expect(screen.getByRole("status")).toHaveTextContent("Cleanup settings saved");
  expect(save).toHaveBeenLastCalledWith({ retention_days: 30 });
  expect(submit).toBeDisabled();
  expect(screen.queryByRole("alert")).toBeNull();

  view.unmount();
  load.mockResolvedValue({ retention_days: 30 });
  view = render(<WorktreeCleanupSettings />);
  expect(await screen.findByRole("spinbutton", { name: "Days of inactivity" })).toHaveValue(30);
  fireEvent.change(screen.getByRole("combobox", { name: "Delete unused worktrees" }), {
    target: { value: "never" },
  });
  expect(screen.queryByRole("spinbutton")).toBeNull();
  save.mockResolvedValue({ retention_days: null });
  fireEvent.click(screen.getByRole("button", { name: "Save cleanup settings" }));
  await waitFor(() => expect(save).toHaveBeenLastCalledWith({ retention_days: null }));
  await screen.findByText("Cleanup settings saved");

  view.unmount();
  load.mockResolvedValue({ retention_days: null });
  view = render(<WorktreeCleanupSettings />);
  expect(await screen.findByRole("combobox", { name: "Delete unused worktrees" })).toHaveValue("never");
  expect(screen.queryByRole("spinbutton")).toBeNull();
  expect(screen.getByRole("button", { name: "Save cleanup settings" })).toBeDisabled();
  view.unmount();
  vi.restoreAllMocks();
});
