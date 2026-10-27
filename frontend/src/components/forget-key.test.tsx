import { useModelOptions } from "@/components/model-picker";
import { api } from "@/lib/api";
import type { Connections, ModelSpec } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { makeModel } from "@/test/fixtures";
import { ModelRoles } from "@/views/models-settings";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { KeyStatus } from "./forget-key";

const connections = (openrouter: boolean): Connections => ({
  model: makeModel({ provider: "codex", model: "gpt-a" }),
  enabled_models: ["openrouter::x-ai/grok", "codex::gpt-a"],
  secrets: {
    openai_api_key: false,
    openrouter_api_key: openrouter,
    vercel_api_key: false,
    anthropic_api_key: false,
    gemini_api_key: false,
    tavily_api_key: false,
  },
  codex: { connected: true, account_id: "", email: "", expires_at: 0, signing_in: false, error: "" },
  codex_models: [{ id: "gpt-a", name: "GPT A", description: "" }],
  coding_models: [],
  ollama: null,
  require_zdr: false,
});

function ModelMenu({ current }: { current: ModelSpec }) {
  const { options } = useModelOptions(current);
  return (
    <ul>
      {options.map((option) => (
        <li key={option.key}>{`${option.name}: ${option.detail}`}</li>
      ))}
    </ul>
  );
}

function OpenRouterStatus() {
  const saved = useSettings((state) => state.connections?.secrets.openrouter_api_key);
  return <KeyStatus secret="openrouter_api_key" connected={Boolean(saved)} />;
}

it("stays disconnected through a read that lands late, and takes the provider's models out of the menus, the default included", async () => {
  useSettings.setState({ connections: connections(true), action: null });
  let finishEarlier!: (value: Connections) => void;
  const read = vi
    .spyOn(api, "getConnections")
    .mockReturnValueOnce(
      new Promise((resolve) => {
        finishEarlier = resolve;
      }),
    )
    .mockResolvedValueOnce(connections(false));
  const remove = vi.spyOn(api, "deleteSecret").mockResolvedValue(undefined);
  // Opening Settings reads the connections; with the ChatGPT model list that takes a moment.
  const earlier = useSettings.getState().loadConnections();
  render(<OpenRouterStatus />);
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
  await screen.findByText("Not connected");
  expect(remove).toHaveBeenCalledWith("openrouter_api_key");
  // The earlier read still says connected: it must not put the provider back.
  finishEarlier(connections(true));
  await earlier;
  expect(screen.getByText("Not connected")).toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(2);

  // Its models leave the model menus, though still enabled for when a key is back. A chat already
  // on one keeps it, marked as not connected, so switching away is not a one-way door.
  cleanup();
  read.mockResolvedValue(connections(false));
  render(<ModelMenu current={makeModel({ provider: "codex", model: "gpt-a" })} />);
  expect(await screen.findByText("GPT A: ChatGPT · gpt-a")).toBeInTheDocument();
  expect(screen.queryByText(/x-ai\/grok/)).not.toBeInTheDocument();
  expect(useSettings.getState().connections?.enabled_models).toContain("openrouter::x-ai/grok");
  cleanup();
  render(<ModelMenu current={makeModel({ provider: "openrouter", model: "x-ai/grok" })} />);
  expect(await screen.findByText("x-ai/grok: OpenRouter · not connected")).toBeInTheDocument();

  // The default model is not a chat's: one whose provider is gone is not named as the default,
  // so Settings asks for another. A connected default is named as before.
  cleanup();
  const removedDefault = {
    ...connections(false),
    model: makeModel({ provider: "openrouter", model: "x-ai/grok" }),
  };
  read.mockResolvedValue(removedDefault);
  render(<ModelRoles connections={removedDefault} />);
  expect(await screen.findByRole("button", { name: "Default model: Choose a model" })).toBeInTheDocument();
  cleanup();
  render(<ModelRoles connections={connections(false)} />);
  expect(await screen.findByRole("button", { name: "Default model: GPT A" })).toBeInTheDocument();
});
