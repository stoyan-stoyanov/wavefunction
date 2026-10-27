import { api } from "@/lib/api";
import type { Connections, OllamaConnection } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { makeModel } from "@/test/fixtures";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OllamaSettings } from "./models-settings";

const model = makeModel();
const initial: Connections = {
  model,
  enabled_models: ["demo::demo-reliable"],
  secrets: {
    openai_api_key: false,
    openrouter_api_key: false,
    vercel_api_key: false,
    anthropic_api_key: false,
    gemini_api_key: false,
    tavily_api_key: false,
  },
  codex: {
    connected: false,
    account_id: "",
    expires_at: 0,
    email: "",
    signing_in: false,
    error: "",
  },
  codex_models: [],
  coding_models: [],
  ollama: null,
  require_zdr: false,
};
const ollama: OllamaConnection = {
  base_url: "http://127.0.0.1:11434",
  num_ctx: 32768,
  connected: true,
  models: [
    {
      id: "agent:8b",
      name: "agent:8b",
      capabilities: ["tools"],
      context_length: 32768,
      size: 100,
      description: "Tool calling supported",
    },
    {
      id: "embed:latest",
      name: "embed:latest",
      capabilities: ["embedding"],
      context_length: 8192,
      size: 100,
      description: "Tool calling unavailable",
    },
  ],
};

describe("Ollama settings", () => {
  const saveEnabledModels = vi.fn().mockResolvedValue(undefined);
  beforeEach(() => {
    vi.restoreAllMocks();
    saveEnabledModels.mockClear();
    useSettings.setState({
      connections: initial,
      saveEnabledModels,
      modelPreferencesSaving: false,
      loadConnections: vi.fn().mockResolvedValue(undefined),
    });
  });
  it("connects without a key and enables a usable first model instead of the demo", async () => {
    const connect = vi.spyOn(api, "connectOllama").mockResolvedValue(ollama);
    render(<OllamaSettings />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await screen.findByRole("button", { name: "Refresh models" });
    expect(connect).toHaveBeenCalledWith(ollama.base_url, 32768);
    expect(useSettings.getState().connections?.ollama?.connected).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Choose enabled Ollama models" }));
    expect(screen.getByRole("option", { name: "Enable embed:latest" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    fireEvent.click(screen.getByRole("option", { name: "Enable agent:8b" }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(saveEnabledModels).toHaveBeenCalledWith(["ollama::agent:8b"]));
  });
  it("keeps saved configuration on failure and explains how to retry", async () => {
    useSettings.setState({ connections: { ...initial, ollama } });
    vi.spyOn(api, "connectOllama").mockRejectedValue(new Error("Start Ollama and try again."));
    render(<OllamaSettings />);
    fireEvent.change(screen.getByLabelText("Server address"), {
      target: { value: "http://other:11434" },
    });
    expect(screen.getByRole("button", { name: "Choose enabled Ollama models" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save and connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Start Ollama");
    expect(useSettings.getState().connections?.ollama?.base_url).toBe(ollama.base_url);
  });
});
