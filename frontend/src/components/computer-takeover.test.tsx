import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ComputerTakeover } from "./computer-takeover";
import { computerControl } from "@/lib/computer-control";
const onResume = vi.fn<() => Promise<void>>();

vi.mock("@/lib/computer-control", () => ({ computerControl: vi.fn() }));
const user = { available: true, state: "user" as const, generation: 2, waiting: true };
let channel: ReturnType<typeof computerControl>;
beforeEach(() => {
  onResume.mockReset().mockResolvedValue();
  channel = {
    status: vi.fn().mockResolvedValue(user),
    take: vi.fn().mockResolvedValue(user),
    screen: vi.fn().mockResolvedValue("blob:private"),
    action: vi.fn().mockResolvedValue({ ok: true }),
    resume: vi.fn().mockResolvedValue({ ...user, state: "agent", waiting: false }),
  };
  vi.mocked(computerControl).mockReturnValue(channel);
  vi.stubGlobal("URL", { ...URL, revokeObjectURL: vi.fn() });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("sends private input through the control channel and closing leaves the agent paused", async () => {
  render(<ComputerTakeover onResume={onResume} id="run-1" requested />);
  fireEvent.click(await screen.findByRole("button", { name: "Sign in privately" }));
  await screen.findByRole("img", { name: "Private browser screen" });
  // Something editable takes the keys: the Mac's web view pastes into nothing else, so a
  // password could not be pasted into the desktop.
  const keyboard = screen.getByLabelText("Keyboard for the private desktop");
  expect(keyboard.tagName).toBe("TEXTAREA");
  fireEvent.paste(keyboard, { clipboardData: { getData: () => "synthetic-private-password" } });
  await waitFor(() =>
    expect(channel.action).toHaveBeenCalledWith(2, "type", { text: "synthetic-private-password" }),
  );
  expect(screen.queryByText("synthetic-private-password")).not.toBeInTheDocument();
  expect(keyboard).toHaveValue("");
  // Closing while in control says the agent waits until it is resumed.
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(await screen.findByText("It waits until you press Resume agent.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Close anyway" }));
  await waitFor(() =>
    expect(screen.queryByRole("img", { name: "Private browser screen" })).not.toBeInTheDocument(),
  );
  expect(channel.resume).not.toHaveBeenCalled();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:private");
});

it("resumes the agent from the warning shown when the window is closed", async () => {
  render(<ComputerTakeover onResume={onResume} id="run-1" requested />);
  fireEvent.click(await screen.findByRole("button", { name: "Sign in privately" }));
  await screen.findByRole("img", { name: "Private browser screen" });
  expect(screen.queryByText(/Check the website address|Closing this window/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  const warning = await screen.findByRole("alertdialog");
  fireEvent.click(within(warning).getByRole("button", { name: "Resume agent" }));
  await waitFor(() => expect(channel.resume).toHaveBeenCalledWith(2));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
});

it("drains input before resuming and stops displaying private frames", async () => {
  let finish!: () => void;
  vi.mocked(channel.action).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = () => resolve({ ok: true });
      }),
  );
  render(<ComputerTakeover onResume={onResume} id="run-1" requested />);
  fireEvent.click(await screen.findByRole("button", { name: "Sign in privately" }));
  await screen.findByRole("img", { name: "Private browser screen" });
  fireEvent.keyDown(screen.getByLabelText("Keyboard for the private desktop"), { key: "a" });
  await waitFor(() => expect(channel.action).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "Resume agent" }));
  expect(channel.resume).not.toHaveBeenCalled();
  await act(async () => finish());
  await waitFor(() => expect(channel.resume).toHaveBeenCalledWith(2));
  expect(onResume).toHaveBeenCalledOnce();
  expect(screen.queryByRole("img", { name: "Private browser screen" })).not.toBeInTheDocument();
});

it("does not permit input or resume before the backend acknowledges the pause", async () => {
  vi.mocked(channel.take).mockResolvedValue({ ...user, state: "pausing" });
  vi.mocked(channel.status).mockResolvedValue({ ...user, state: "agent", waiting: false });
  render(<ComputerTakeover onResume={onResume} id="run-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Take control" }));
  await screen.findByRole("heading", { name: "Pausing the agent…" });
  expect(screen.getByRole("button", { name: "Resume agent" })).toBeDisabled();
  fireEvent.keyDown(screen.getByLabelText("Keyboard for the private desktop"), { key: "p" });
  expect(channel.action).not.toHaveBeenCalled();
  expect(channel.screen).not.toHaveBeenCalled();
  // A refused takeover says so and can be closed, instead of waiting for control forever.
  cleanup();
  vi.mocked(channel.take).mockRejectedValue(
    new Error("The desktop is no longer running. Ask the agent to open it again."),
  );
  render(<ComputerTakeover onResume={onResume} id="run-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Take control" }));
  await screen.findByRole("heading", { name: "Couldn’t take control" });
  expect(screen.getByRole("alert")).toHaveTextContent("no longer running");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("heading", { name: "Couldn’t take control" })).toBeNull();
});

it("keeps one input request out and folds what arrives meanwhile into the next", async () => {
  // A busy page can make each request take a second. Sent one per key and wheel tick, typing a
  // login and one trackpad flick queued tens of seconds of input that kept replaying.
  const replies: Array<() => void> = [];
  vi.mocked(channel.action).mockImplementation(
    () =>
      new Promise((resolve) => {
        replies.push(() => resolve({ ok: true }));
      }),
  );
  render(<ComputerTakeover onResume={onResume} id="run-1" requested />);
  fireEvent.click(await screen.findByRole("button", { name: "Sign in privately" }));
  await screen.findByRole("img", { name: "Private browser screen" });
  const desktop = screen.getByRole("application", { name: "Private desktop" });
  const keyboard = screen.getByLabelText("Keyboard for the private desktop");
  for (const key of "user@example.test") fireEvent.keyDown(keyboard, { key });
  for (let tick = 0; tick < 60; tick++) fireEvent.wheel(desktop, { deltaY: 4 });
  fireEvent.keyDown(keyboard, { key: "Tab" });
  fireEvent.keyDown(keyboard, { key: "Tab" });
  expect(channel.action).toHaveBeenCalledTimes(1);
  expect(channel.action).toHaveBeenLastCalledWith(2, "type", { text: "u" });
  for (const expected of [
    ["type", { text: "ser@example.test" }],
    ["scroll", expect.objectContaining({ direction: "down", amount: 20 })],
    ["key", { keys: "Tab Tab" }],
  ]) {
    await act(async () => replies.shift()!());
    await waitFor(() => expect(channel.action).toHaveBeenLastCalledWith(2, ...expected));
  }
  await act(async () => replies.shift()!());
  expect(channel.action).toHaveBeenCalledTimes(4);
});
