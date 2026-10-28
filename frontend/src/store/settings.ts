import { api, openExternal, type SecretKey } from "@/lib/api";
import { keyProviderIds, keyProviders, type KeyProviderId } from "@/lib/models";
import type {
  Connections,
  Connector,
  ConnectorProbe,
  GrantState,
  ModelSpec,
  OpenRouterModel,
} from "@/lib/types";
import { create } from "zustand";

interface SettingsState {
  action: string | null;
  error: string | null;
  connections: Connections | null;
  openRouterModels: OpenRouterModel[];
  /** Each API-key provider's catalogue, empty until read or when its key is missing. */
  keyModels: Record<KeyProviderId, OpenRouterModel[]>;
  modelOptionsLoading: boolean;
  modelPreferencesSaving: boolean;
  clearError: () => void;
  connectors: Connector[];
  loadConnectors: () => Promise<void>;
  addConnector: (server: Record<string, unknown>) => Promise<void>;
  updateConnector: (name: string, server: Record<string, unknown>) => Promise<void>;
  probeConnector: (url: string) => Promise<ConnectorProbe>;
  setConnectorSecret: (name: string, value: string) => Promise<void>;
  clearConnectorSecret: (name: string) => Promise<void>;
  refreshConnector: (name: string) => Promise<void>;
  removeConnector: (name: string) => Promise<void>;
  signInConnector: (name: string) => Promise<void>;
  signOutConnector: (name: string) => Promise<void>;
  grants: GrantState | null;
  loadGrants: () => Promise<void>;
  addGrant: (path: string, writable: boolean) => Promise<void>;
  loadConnections: () => Promise<void>;
  loadOpenRouterModels: () => Promise<void>;
  loadKeyModels: (provider: KeyProviderId) => Promise<void>;
  saveProviderKey: (provider: KeyProviderId, apiKey: string) => Promise<void>;
  /** Forget a saved key. Its provider's models stay enabled for when a key is back. */
  forgetKey: (key: SecretKey) => Promise<void>;
  startCodexLogin: () => Promise<void>;
  cancelCodexLogin: () => Promise<void>;
  signOutCodex: () => Promise<void>;
  saveOpenRouterKey: (apiKey: string) => Promise<void>;
  saveEnabledModels: (models: string[]) => Promise<void>;
  setDefaultModel: (model: ModelSpec) => Promise<void>;
  saveSearchKey: (apiKey: string) => Promise<void>;
  setZeroDataRetention: (enabled: boolean) => Promise<void>;
}

type Setter = (patch: Partial<SettingsState> | ((state: SettingsState) => Partial<SettingsState>)) => void;

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

// The newest read of the connections wins. Reading them takes a moment (the ChatGPT model list
// comes with them), so one started before a change could otherwise land after the read that
// follows the change and put back what it replaced: a provider shown connected after removal.
// A saved change counts as newer than any read already under way.
let connectionsRead = 0;
// A model choice saved after a read started is newer than the models that read found; the rest
// of it, such as a key saved just before, still lands.
let modelsSaved = 0;
function keepSavedModels(read: Connections, current: Connections | null, since: number): Connections {
  return since === modelsSaved || !current
    ? read
    : { ...read, enabled_models: current.enabled_models, model: current.model };
}

export const useSettings = create<SettingsState>((set, get) => {
  let actionSequence = 0;
  async function readConnections(): Promise<Connections> {
    const [read, since] = [++connectionsRead, modelsSaved];
    const connections = await api.getConnections();
    if (read === connectionsRead)
      set({ connections: keepSavedModels(connections, get().connections, since) });
    return get().connections ?? connections;
  }

  async function withAction<T>(
    set: Setter,
    label: string | null,
    run: () => Promise<T>,
    options: { rethrow: true },
  ): Promise<T>;
  async function withAction<T>(
    set: Setter,
    label: string | null,
    run: () => Promise<T>,
    options?: { rethrow?: false },
  ): Promise<T | undefined>;
  async function withAction<T>(
    set: Setter,
    label: string | null,
    run: () => Promise<T>,
    { rethrow = false }: { rethrow?: boolean } = {},
  ): Promise<T | undefined> {
    const operation = label ? ++actionSequence : null;
    set(label ? { action: label, error: null } : { error: null });
    try {
      return await run();
    } catch (error) {
      set({ error: message(error) });
      if (rethrow) throw error;
      return undefined;
    } finally {
      if (label && operation === actionSequence) set({ action: null });
    }
  }

  return {
    action: null,
    error: null,
    connectors: [],
    connections: null,
    openRouterModels: [],
    keyModels: { vercel: [], anthropic: [], gemini: [] },
    modelOptionsLoading: false,
    modelPreferencesSaving: false,
    clearError: () => set({ error: null }),
    loadConnectors: async () => {
      try {
        set({ connectors: await api.listConnectors() });
      } catch (error) {
        set({ error: message(error) });
      }
    },
    addConnector: async (server) => {
      await withAction(
        set,
        "connecting",
        async () => {
          await api.addConnector(server);
          set({ connectors: await api.listConnectors() });
        },
        { rethrow: true },
      );
    },
    updateConnector: async (name, server) => {
      await withAction(
        set,
        "connecting",
        async () => {
          await api.updateConnector(name, server);
          set({ connectors: await api.listConnectors() });
        },
        { rethrow: true },
      );
    },
    probeConnector: async (url) => {
      let found: ConnectorProbe | undefined;
      await withAction(
        set,
        "connecting",
        async () => {
          found = await api.probeConnector(url);
        },
        { rethrow: true },
      );
      return found!;
    },
    setConnectorSecret: async (name, value) => {
      await withAction(
        set,
        "connecting",
        async () => {
          await api.setConnectorSecret(name, value);
          set({ connectors: await api.listConnectors() });
        },
        { rethrow: true },
      );
    },
    clearConnectorSecret: async (name) => {
      await withAction(set, "connecting", async () => {
        await api.clearConnectorSecret(name);
        set({ connectors: await api.listConnectors() });
      });
    },
    refreshConnector: async (name) => {
      await withAction(set, "connecting", async () => {
        await api.refreshConnector(name);
        set({ connectors: await api.listConnectors() });
      });
    },
    signInConnector: async (name) => {
      await withAction(set, "signing-in", async () => {
        await api.signInConnector(name);
        set({ connectors: await api.listConnectors() });
      });
    },
    signOutConnector: async (name) => {
      await withAction(set, "connecting", async () => {
        await api.signOutConnector(name);
        set({ connectors: await api.listConnectors() });
      });
    },
    removeConnector: async (name) => {
      await withAction(
        set,
        "connecting",
        async () => {
          await api.removeConnector(name);
          set({ connectors: await api.listConnectors() });
        },
        { rethrow: true },
      );
    },
    grants: null,
    loadGrants: async () => {
      try {
        set({ grants: await api.listGrants() });
      } catch (error) {
        set({ error: message(error) });
      }
    },
    addGrant: async (path, writable) => {
      await withAction(
        set,
        null,
        async () => {
          const { grants } = await api.addGrant(path, writable);
          set((state) => ({
            grants: state.grants ? { ...state.grants, grants } : state.grants,
          }));
        },
        { rethrow: true },
      );
    },
    loadConnections: async () => {
      await withAction(set, null, async () => {
        await readConnections();
      });
    },
    loadKeyModels: async (provider) => {
      const { secret, publicCatalogue } = keyProviders[provider];
      let models: OpenRouterModel[] = [];
      try {
        if (publicCatalogue || get().connections?.secrets[secret])
          models = await api.listKeyProviderModels(provider);
      } catch {
        // A provider being unreachable is not worth a banner: the picker simply has no rows.
      }
      set((state) => ({ keyModels: { ...state.keyModels, [provider]: models } }));
    },
    loadOpenRouterModels: async () => {
      if (get().modelOptionsLoading) return;
      let connections = get().connections;
      try {
        if (!connections) {
          connections = await readConnections();
        }
        if (!connections.secrets.openrouter_api_key) {
          set({ openRouterModels: [] });
          return;
        }
        set({ modelOptionsLoading: true });
        set({ openRouterModels: await api.listOpenRouterModels() });
      } catch (error) {
        set({ error: message(error), openRouterModels: [] });
        throw error;
      } finally {
        set({ modelOptionsLoading: false });
      }
    },
    saveOpenRouterKey: async (apiKey) => {
      await withAction(
        set,
        "saving-openrouter",
        async () => {
          await api.saveSecret("openrouter_api_key", apiKey);
          await readConnections();
          await get().loadOpenRouterModels();
        },
        { rethrow: true },
      );
    },
    saveProviderKey: async (provider, apiKey) => {
      await withAction(
        set,
        `saving-${provider}`,
        async () => {
          await api.saveSecret(keyProviders[provider].secret, apiKey);
          await readConnections();
          await get().loadKeyModels(provider);
        },
        { rethrow: true },
      );
    },
    forgetKey: async (key) => {
      await withAction(
        set,
        `forgetting-${key}`,
        async () => {
          await api.deleteSecret(key);
          // Gone as soon as the server says so, and no read already under way can bring it back.
          connectionsRead++;
          set((state) =>
            state.connections
              ? {
                  connections: {
                    ...state.connections,
                    secrets: { ...state.connections.secrets, [key]: false },
                  },
                }
              : {},
          );
          await readConnections();
          if (key === "openrouter_api_key") set({ openRouterModels: [] });
          const provider = keyProviderIds.find((id) => keyProviders[id].secret === key);
          if (provider) await get().loadKeyModels(provider);
        },
        { rethrow: true },
      );
    },
    startCodexLogin: async () => {
      await withAction(
        set,
        "signing-in-codex",
        async () => {
          const { url } = await api.codexLogin();
          // Opened through the shell so it lands in the user's real browser, where they are
          // already signed in to ChatGPT. The webview would ask them to sign in again.
          await openExternal(url);
          await readConnections();
        },
        { rethrow: true },
      );
    },
    cancelCodexLogin: async () => {
      await api.codexCancelLogin().catch(() => undefined);
      await readConnections();
    },
    signOutCodex: async () => {
      await withAction(
        set,
        null,
        async () => {
          await api.codexLogout();
          await readConnections();
        },
        { rethrow: true },
      );
    },
    saveEnabledModels: async (models) => {
      const connections = get().connections;
      if (!connections || get().modelPreferencesSaving) return;
      const previous = connections.enabled_models;
      const next = Array.from(new Set(models));
      modelsSaved++;
      set({
        modelPreferencesSaving: true,
        error: null,
        connections: { ...connections, enabled_models: next },
      });
      try {
        const { models: enabledModels, model } = await api.updateEnabledModels(next);
        modelsSaved++;
        const current = get().connections;
        // The default follows the enabled list returned by the server.
        if (current) {
          set({
            connections: { ...current, enabled_models: enabledModels, model },
          });
        }
      } catch (error) {
        const current = get().connections;
        set({
          error: message(error),
          connections: current ? { ...current, enabled_models: previous } : current,
        });
        throw error;
      } finally {
        set({ modelPreferencesSaving: false });
      }
    },
    setDefaultModel: async (model) => {
      const connections = get().connections;
      if (!connections) return;
      await withAction(
        set,
        "selecting-model",
        async () => {
          const { model: saved } = await api.updateDefaultModel(model);
          modelsSaved++;
          const current = get().connections;
          // Choosing enables: the server adds the model to the list it is picked from.
          const key = `${saved.provider}::${saved.model}`;
          if (current) {
            set({
              connections: {
                ...current,
                model: saved,
                enabled_models: current.enabled_models.includes(key)
                  ? current.enabled_models
                  : [...current.enabled_models, key],
              },
            });
          }
        },
        { rethrow: true },
      );
    },
    saveSearchKey: async (apiKey) => {
      await withAction(
        set,
        "saving-search",
        async () => {
          await api.saveSecret("tavily_api_key", apiKey);
          await readConnections();
        },
        { rethrow: true },
      );
    },
    setZeroDataRetention: async (enabled) => {
      await withAction(set, "saving-zdr", async () => {
        const { require_zdr } = await api.setZeroDataRetention(enabled);
        // A read started before the change must not put the old value back.
        connectionsRead++;
        set((state) => (state.connections ? { connections: { ...state.connections, require_zdr } } : {}));
      });
    },
  };
});
