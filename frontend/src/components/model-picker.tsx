import { useEffect, useMemo } from "react";
import {
  builtInModel,
  keyProviderIds,
  keyProviders,
  modelDisplayName,
  modelKey,
  parseModelKey,
  providerConnected,
  providerDisplayName,
  type KeyProviderId,
} from "@/lib/models";
import type { ModelSpec } from "@/lib/types";
import { useSettings } from "@/store/settings";

export interface ModelOption {
  key: string;
  provider: ModelSpec["provider"];
  model: string;
  name: string;
  detail: string;
}

/**
 * The enabled models as rows, with `current` always among them.
 *
 * Shared by conversation model pickers and the default model in Settings. `current` is included even when it is not enabled, or the list would
 * show nothing selected and switching away would be a one-way door.
 */
export function useModelOptions(current: ModelSpec) {
  const connections = useSettings((state) => state.connections);
  const openRouterModels = useSettings((state) => state.openRouterModels);
  const keyModels = useSettings((state) => state.keyModels);
  const loadKeyModels = useSettings((state) => state.loadKeyModels);
  const loading = useSettings((state) => state.modelOptionsLoading);
  const loadConnections = useSettings((state) => state.loadConnections);
  const loadOpenRouterModels = useSettings((state) => state.loadOpenRouterModels);
  const selectedKey = modelKey(current);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

  useEffect(() => {
    if (connections?.secrets.openrouter_api_key) {
      void loadOpenRouterModels().catch(() => undefined);
    }
  }, [connections?.secrets.openrouter_api_key, loadOpenRouterModels]);

  // These catalogues are otherwise only fetched by Settings, so without this an enabled model
  // shows here under its raw id until someone happens to open that page. Joined into a string
  // so a reload of the connections, which happens on every mount, does not fetch them again.
  const keyed = keyProviderIds
    .filter((provider) => connections?.secrets[keyProviders[provider].secret])
    .join();
  useEffect(() => {
    for (const provider of keyed ? keyed.split(",") : []) void loadKeyModels(provider as KeyProviderId);
  }, [keyed, loadKeyModels]);

  const options = useMemo(() => {
    const enabled = new Set(connections?.enabled_models ?? [modelKey(current)]);
    const options: ModelOption[] = [];
    const seen = new Set<string>();
    const add = (option: ModelOption) => {
      if (!enabled.has(option.key) || seen.has(option.key)) return;
      // A disconnected provider's models stay enabled, for when it is connected again, but are
      // not offered: a chat on one could not run. The chat's own model is still shown below.
      if (connections && !providerConnected(connections, option.provider)) return;
      if (
        option.provider === "ollama" &&
        !connections?.ollama?.models.some(
          (model) => model.id === option.model && model.capabilities.includes("tools"),
        )
      )
        return;
      seen.add(option.key);
      options.push(option);
    };

    add({
      key: builtInModel.key,
      provider: builtInModel.provider,
      model: builtInModel.model,
      name: builtInModel.name,
      detail: "On-device · no API key",
    });
    // Every enabled model, whoever serves it. One loop rather than one per provider: a
    // provider whose loop nobody wrote is a provider you can enable in Settings and then never
    // find here, which is what happened to the gateway and to ChatGPT.
    const catalogues: Record<string, Array<{ id: string; name: string }>> = {
      openrouter: openRouterModels,
      ...keyModels,
      codex: connections?.codex_models ?? [],
      ollama: connections?.ollama?.models ?? [],
    };
    for (const key of connections?.enabled_models ?? []) {
      const parsed = parseModelKey(key);
      if (!parsed || parsed.provider === "demo") continue;
      const catalogue = catalogues[parsed.provider];
      // Named by its own id when no catalogue knows it: a ChatGPT model someone typed by hand,
      // or a gateway list that has not loaded yet. Better a row under its id than no row.
      const known = catalogue?.find((item) => item.id === parsed.model);
      add({
        key,
        provider: parsed.provider,
        model: parsed.model,
        name: known?.name ?? parsed.model,
        detail: `${providerDisplayName(parsed.provider)} · ${parsed.model}`,
      });
    }
    for (const configured of [connections?.model, ...(connections?.coding_models ?? []), current]) {
      if (!configured || configured.provider === "demo" || configured.provider === "openrouter") {
        continue;
      }
      add({
        key: modelKey(configured),
        provider: configured.provider,
        model: configured.model,
        name: configured.model,
        detail: providerDisplayName(configured.provider),
      });
    }
    // The chat's own model always belongs in the list, even when it is not in the enabled set:
    // otherwise the picker shows nothing selected and switching away is a one-way door.
    if (
      !seen.has(selectedKey) &&
      (current.provider !== "ollama" ||
        connections?.ollama?.models.some(
          (model) => model.id === current.model && model.capabilities.includes("tools"),
        ))
    ) {
      seen.add(selectedKey);
      options.push({
        key: selectedKey,
        provider: current.provider,
        model: current.model,
        name: modelDisplayName(current, openRouterModels),
        detail:
          connections && !providerConnected(connections, current.provider)
            ? `${providerDisplayName(current.provider)} · not connected`
            : `${providerDisplayName(current.provider)} · ${current.model}`,
      });
    }
    return options;
  }, [
    connections?.codex_models,
    connections?.ollama?.models,
    connections?.enabled_models,
    connections?.model,
    connections?.coding_models,
    current,
    openRouterModels,
    selectedKey,
    keyModels,
  ]);

  // Named from the list it is in, so the button says "GPT-5.6-Sol" where the row does.
  // `modelDisplayName` only knows the OpenRouter catalogue, so on its own it left every
  // ChatGPT model showing its raw id.
  const selectedName =
    options.find((option) => option.key === selectedKey)?.name ?? modelDisplayName(current, openRouterModels);

  /** The spec for a chosen row, carrying over what a key alone does not say. */
  function specFor(value: string): ModelSpec | null {
    const selected = parseModelKey(value);
    if (!selected) return null;
    const configured = [...(connections?.coding_models ?? []), connections?.model, current].find(
      (item) => item?.provider === selected.provider && item.model === selected.model,
    );
    const baseUrl =
      selected.provider === "openai-compatible" || selected.provider === "ollama"
        ? (configured?.base_url ?? null)
        : null;
    return {
      provider: selected.provider,
      model: selected.model,
      base_url: baseUrl,
      temperature: current.temperature,
      max_tokens: current.max_tokens,
      // Different models may accept different levels. Keep a level only when this is the
      // same model and endpoint, otherwise start with the provider's default.
      reasoning_effort:
        selectedKey === value && (current.base_url ?? null) === baseUrl
          ? (current.reasoning_effort ?? null)
          : null,
    };
  }

  return { options, selectedKey, selectedName, loading, specFor };
}
