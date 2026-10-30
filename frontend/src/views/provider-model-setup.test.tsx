import type { Connections, OpenRouterModel } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { makeModel } from "@/test/fixtures";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelsSettings } from "./models-settings";

const catalogueModel = (id: string, name: string, release_date: string | null): OpenRouterModel => ({
  id,
  name,
  description: "",
  context_length: 1_000_000,
  input_modalities: ["text"],
  output_modalities: ["text"],
  supported_parameters: ["tools"],
  pricing: {},
  reasoning_efforts: null,
  release_date,
});
const models = [
  catalogueModel("vendor/older", "Older", null),
  catalogueModel("vendor/newer", "Newer", "2026-10-26"),
];
const connections: Connections = {
  model: makeModel(),
  enabled_models: ["demo::demo-reliable", "ollama::agent:8b"],
  secrets: {
    openai_api_key: false,
    openrouter_api_key: true,
    vercel_api_key: false,
    anthropic_api_key: false,
    gemini_api_key: false,
    tavily_api_key: false,
  },
  codex: { connected: false, account_id: "", expires_at: 0, email: "", signing_in: false, error: "" },
  codex_models: [],
  coding_models: [],
  ollama: null,
  require_zdr: false,
};

describe("Adding a provider's models in Settings", () => {
  const saveEnabledModels = vi.fn().mockResolvedValue(undefined);
  const setDefaultModel = vi.fn().mockResolvedValue(undefined);
  beforeEach(() => {
    saveEnabledModels.mockClear();
    setDefaultModel.mockClear();
    useSettings.setState({
      connections,
      openRouterModels: models,
      loadOpenRouterModels: vi.fn().mockResolvedValue(undefined),
      modelOptionsLoading: false,
      saveEnabledModels,
      setDefaultModel,
      modelPreferencesSaving: false,
    });
  });
  it("lists every model with a checkbox and makes the first one picked the default", async () => {
    render(
      <ModelsSettings
        connections={connections}
        models={models}
        loading={false}
        savingKey={false}
        apiKey=""
        onApiKeyChange={() => undefined}
        onConnect={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Set up OpenRouter" }));
    expect(screen.getByRole("checkbox", { name: "Enable Older" })).toHaveTextContent("1.0M context");
    expect(screen.getByRole("checkbox", { name: "Enable Newer" })).toHaveTextContent(/Released .*2026/);
    const save = screen.getByRole("button", { name: "Save models" });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable Newer" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable Older" }));
    fireEvent.click(save);
    await waitFor(() =>
      expect(setDefaultModel).toHaveBeenCalledWith(
        expect.objectContaining({ provider: "openrouter", model: "vendor/newer" }),
      ),
    );
    expect(saveEnabledModels).toHaveBeenCalledWith([
      "ollama::agent:8b",
      "openrouter::vendor/newer",
      "openrouter::vendor/older",
    ]);
  });
});
