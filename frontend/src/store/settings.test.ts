import { api } from "@/lib/api";
import type { Connections } from "@/lib/types";
import { makeModel } from "@/test/fixtures";
import { afterEach, expect, it, vi } from "vitest";
import { useSettings } from "./settings";

const connections = (
  enabled: string[],
  model = makeModel({ provider: "codex", model: "gpt-a" }),
): Connections => ({
  model,
  enabled_models: enabled,
  secrets: {
    openai_api_key: false,
    openrouter_api_key: true,
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

afterEach(() => {
  vi.restoreAllMocks();
});

// Opening Settings or a model picker reads the connections; with ChatGPT signed in that read
// waits on chatgpt.com, so it can answer after a change the user made meanwhile.
function slowRead() {
  let finish!: (value: Connections) => void;
  vi.spyOn(api, "getConnections").mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const read = useSettings.getState().loadConnections();
  return async (stale: Connections) => {
    finish(stale);
    await read;
  };
}

it("keeps an enabled model and a chosen default when an earlier connections read lands after them", async () => {
  useSettings.setState({
    connections: connections(["codex::gpt-a"]),
    action: null,
    modelPreferencesSaving: false,
  });
  const put = vi.spyOn(api, "updateEnabledModels").mockImplementation(async (models) => ({
    models,
    model: makeModel({ provider: "codex", model: "gpt-a" }),
  }));

  let land = slowRead();
  await useSettings.getState().saveEnabledModels(["codex::gpt-a", "openrouter::x-ai/grok"]);
  await land(connections(["codex::gpt-a"]));
  // The next toggle is built from the list shown, so a stale list would save without Grok.
  const shown = useSettings.getState().connections!.enabled_models;
  await useSettings.getState().saveEnabledModels([...shown, "openrouter::openai/gpt-x"]);
  expect(put).toHaveBeenLastCalledWith(["codex::gpt-a", "openrouter::x-ai/grok", "openrouter::openai/gpt-x"]);

  const chosen = makeModel({ provider: "openrouter", model: "x-ai/grok" });
  vi.spyOn(api, "updateDefaultModel").mockResolvedValue({ model: chosen });
  land = slowRead();
  await useSettings.getState().setDefaultModel(chosen);
  await land(connections(["codex::gpt-a"]));
  expect(useSettings.getState().connections?.model).toEqual(chosen);
});
