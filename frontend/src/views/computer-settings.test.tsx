import { api } from "@/lib/api";
import type { ComputerStatus } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ComputerSettings } from "./computer-settings";

const ready: ComputerStatus = {
  docker_available: true,
  docker_problem: null,
  image_ready: true,
  image_update_available: false,
  building: false,
  build_log: [],
  docker_desktop_available: true,
  docker_action: null,
};
beforeEach(() => {
  useSettings.setState({
    connections: null,
    loadConnections: vi.fn().mockResolvedValue(undefined),
    error: null,
  });
  vi.spyOn(api, "computerStatus").mockResolvedValue(ready);
});
afterEach(() => vi.restoreAllMocks());
function show() {
  render(<ComputerSettings />);
}

it("offers a first-time build when no desktop image exists", async () => {
  vi.mocked(api.computerStatus).mockResolvedValue({
    ...ready,
    image_ready: false,
  });
  show();
  expect(await screen.findByRole("button", { name: "Build the desktop" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Update desktop" })).not.toBeInTheDocument();
});

it("starts a stopped engine, shows progress, and replaces the stale problem with readiness", async () => {
  vi.mocked(api.computerStatus).mockResolvedValue({
    ...ready,
    image_ready: false,
    docker_problem: "Docker engine is not running.",
  });
  let finish!: (status: ComputerStatus) => void;
  const recover = vi.spyOn(api, "manageDocker").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start Docker" }));
  expect(recover).toHaveBeenCalledExactlyOnceWith("start");
  expect(screen.getByRole("status")).toHaveTextContent("Starting Docker");
  expect(screen.getByRole("button", { name: "Restart Docker" })).toBeDisabled();
  // A stopped engine can't list images: the panel says so instead of "not built".
  expect(screen.getByText("Start Docker to check the desktop image.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Build the desktop" })).not.toBeInTheDocument();
  await act(async () => finish(ready));
  expect(screen.getByText("Docker is installed and running.")).toBeVisible();
  expect(screen.getByText(/The desktop image is built and ready/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Start Docker" })).not.toBeInTheDocument();
});

it("requires confirmation before restarting and lets the user cancel", async () => {
  const recover = vi.spyOn(api, "manageDocker").mockResolvedValue(ready);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Restart Docker" }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    "including agent desktops and containers used by other apps",
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(recover).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Restart Docker" }));
  fireEvent.click(
    within(screen.getByRole("alertdialog")).getByRole("button", {
      name: "Restart Docker",
    }),
  );
  await screen.findByText("Docker is installed and running.");
  expect(recover).toHaveBeenCalledExactlyOnceWith("restart");
});

it("shows recovery failures and allows another attempt", async () => {
  vi.mocked(api.computerStatus).mockResolvedValue({
    ...ready,
    docker_problem: "Engine stopped",
  });
  vi.spyOn(api, "manageDocker").mockRejectedValue(new Error("Docker Desktop did not respond"));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start Docker" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Docker Desktop did not respond");
  expect(screen.getByRole("button", { name: "Start Docker" })).toBeEnabled();
});

it("does not offer Docker Desktop commands for unsupported engines", async () => {
  vi.mocked(api.computerStatus).mockResolvedValue({
    ...ready,
    docker_desktop_available: false,
  });
  show();
  await screen.findByText("Docker is installed and running.");
  expect(screen.queryByRole("button", { name: "Restart Docker" })).not.toBeInTheDocument();
});
