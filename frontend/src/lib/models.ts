import type { Connections, ModelSpec, OpenRouterModel } from "@/lib/types";

export const builtInModel = {
  key: "demo::demo-reliable",
  provider: "demo" as const,
  model: "demo-reliable",
  name: "Built-in demo",
};

export function modelKey(model: Pick<ModelSpec, "provider" | "model">): string {
  return `${model.provider}::${model.model}`;
}

export function codexModelKey(modelId: string): string {
  return `codex::${modelId}`;
}

export function parseModelKey(value: string): Pick<ModelSpec, "provider" | "model"> | null {
  const separator = value.indexOf("::");
  if (separator < 1) return null;
  const provider = value.slice(0, separator);
  const model = value.slice(separator + 2);
  if (
    !model ||
    ![
      "demo",
      "openai",
      "openrouter",
      "vercel",
      "anthropic",
      "gemini",
      "openai-compatible",
      "codex",
      "ollama",
    ].includes(provider)
  ) {
    return null;
  }
  return { provider: provider as ModelSpec["provider"], model };
}

export function modelDisplayName(model: ModelSpec, openRouterModels: OpenRouterModel[]): string {
  if (model.provider === "demo") return builtInModel.name;
  if (model.provider === "openrouter" || model.provider === "vercel") {
    return openRouterModels.find((item) => item.id === model.model)?.name ?? model.model;
  }
  return model.model;
}

export function providerDisplayName(provider: ModelSpec["provider"]): string {
  if (provider === "demo") return "On-device";
  if (provider === "openrouter") return "OpenRouter";
  if (provider === "openai-compatible") return "Compatible endpoint";
  // Named for what the user did, not for the endpoint behind it: they signed in to ChatGPT.
  if (provider === "codex") return "ChatGPT";
  if (provider === "ollama") return "Ollama";
  if (isKeyProvider(provider)) return keyProviders[provider].name;
  return "OpenAI";
}

/** A model the app can actually run on is enabled, as opposed to the built-in demo. */
export function realModelEnabled(connections: Connections | null): boolean {
  return Boolean(connections?.enabled_models.some((key) => !key.startsWith("demo::")));
}

/** A model's provider can run it: signed in, its key saved, or its server connected. A compatible
 * server's address comes with each model, so those count as connected. */
export function providerConnected(connections: Connections, provider: ModelSpec["provider"]): boolean {
  if (provider === "demo" || provider === "openai-compatible") return true;
  if (provider === "openai") return connections.secrets.openai_api_key;
  return isProviderConnected(connections, provider);
}

/** The default model needs its own connection, even when another provider is available. */
export function defaultModelConnected(connections: Connections | null): boolean {
  if (!connections) return false;
  const { provider, base_url } = connections.model;
  if (provider === "openai-compatible") return Boolean(base_url);
  return provider !== "demo" && providerConnected(connections, provider);
}

export interface ModelChoice {
  id: string;
  name: string;
  description?: string;
  context_length?: number;
  unavailable?: string;
}
/** Providers connected with an API key alone, whose catalogue the backend reads with it. */
export const keyProviders = {
  vercel: {
    name: "Vercel AI Gateway",
    secret: "vercel_api_key",
    variable: "AI_GATEWAY_API_KEY",
    placeholder: "vck_…",
    label: "gateway",
    // Listed without a key, so someone can see a model is there before connecting.
    publicCatalogue: true,
  },
  anthropic: {
    name: "Anthropic",
    secret: "anthropic_api_key",
    variable: "ANTHROPIC_API_KEY",
    placeholder: "sk-ant-…",
    label: "Anthropic",
    publicCatalogue: false,
  },
  gemini: {
    name: "Google Gemini",
    secret: "gemini_api_key",
    variable: "GEMINI_API_KEY",
    placeholder: "AIza…",
    label: "Gemini",
    publicCatalogue: false,
  },
} as const;
export type KeyProviderId = keyof typeof keyProviders;
export const keyProviderIds = Object.keys(keyProviders) as KeyProviderId[];
export function isKeyProvider(provider: string): provider is KeyProviderId {
  return provider in keyProviders;
}
export type ProviderId = "codex" | "openrouter" | KeyProviderId | "ollama";
export const modelProviders: Array<{
  id: ProviderId;
  name: string;
  description: string;
}> = [
  {
    id: "codex",
    name: "ChatGPT",
    description: "Sign in with your ChatGPT Plus or Pro account.",
  },
  {
    id: "anthropic",
    name: "Anthropic",
    description: "Use Claude models with an Anthropic API key.",
  },
  {
    id: "gemini",
    name: "Google Gemini",
    description: "Use Gemini models with a Google AI Studio API key.",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    description: "Use an API key to access models from many providers.",
  },
  {
    id: "vercel",
    name: "Vercel AI Gateway",
    description: "Connect models with a Vercel AI Gateway API key.",
  },
  {
    id: "ollama",
    name: "Ollama",
    description: "Connect models running on your computer or your own server.",
  },
];
export function isProviderConnected(connections: Connections, provider: ProviderId): boolean {
  if (provider === "codex") return connections.codex.connected;
  if (provider === "ollama") return Boolean(connections.ollama?.connected);
  if (provider === "openrouter") return connections.secrets.openrouter_api_key;
  return connections.secrets[keyProviders[provider].secret];
}
/** Any way to reach a model is set up: a provider above, an OpenAI key or a compatible server. */
export function hasModelProvider(connections: Connections): boolean {
  return (
    modelProviders.some(({ id }) => isProviderConnected(connections, id)) ||
    connections.secrets.openai_api_key ||
    Boolean(connections.model.provider === "openai-compatible" && connections.model.base_url)
  );
}
/** The models a connected provider offers; Ollama's only those that can call tools. */
export function providerCatalogue(
  provider: ProviderId,
  connections: Connections,
  openRouterModels: OpenRouterModel[],
  keyModels: Record<KeyProviderId, OpenRouterModel[]>,
): ModelChoice[] {
  if (provider === "codex") return connections.codex_models;
  if (provider === "openrouter") return openRouterModels;
  if (isKeyProvider(provider)) return keyModels[provider];
  return (connections.ollama?.models ?? []).filter((model) => model.capabilities.includes("tools"));
}

/** A provider is connected and one of its models enabled: chats can run, so setup is done. */
export function modelSetupDone(connections: Connections): boolean {
  return hasModelProvider(connections) && realModelEnabled(connections);
}
