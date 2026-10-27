/** ChatGPT sign-in, account state, and enabled model selection. */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CodexStatus, Connections } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { makeModel } from "@/test/fixtures";
import CodingSettings from "@/coding/settings";

const model = makeModel();

const signedOut: CodexStatus = {
  account_id: "",
  expires_at: 0,
  connected: false,
  email: "",
  signing_in: false,
  error: "",
};

function connections(codex: Partial<CodexStatus>, enabled: string[] = []): Connections {
  return {
    model: { ...model, provider: "codex", model: "gpt-5.6-sol" },
    enabled_models: codex.connected && !enabled.length ? ["codex::gpt-5.6-sol"] : enabled,
    secrets: {
      openai_api_key: false,
      openrouter_api_key: false,
      vercel_api_key: false,
      anthropic_api_key: false,
      gemini_api_key: false,
      tavily_api_key: false,
    },
    codex: { ...signedOut, ...codex },
    codex_models: [
      {
        id: "gpt-5.6-sol",
        name: "GPT-5.6-Sol",
        description: "Frontier agentic coding model",
      },
      { id: "gpt-5.5", name: "GPT-5.5", description: "Frontier model" },
    ],
    coding_models: [],
    ollama: null,
    require_zdr: false,
  };
}

function renderSettings() {
  render(<CodingSettings onBack={vi.fn()} initialPage="models" />);
  const connected = useSettings.getState().connections!.codex.connected;
  if (connected) {
    fireEvent.click(screen.getByRole("button", { name: "Manage ChatGPT" }));
  } else {
    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));
    fireEvent.click(screen.getByRole("button", { name: "ChatGPT" }));
  }
}

describe("Sign in with ChatGPT", () => {
  const startCodexLogin = vi.fn().mockResolvedValue(undefined);
  const signOutCodex = vi.fn().mockResolvedValue(undefined);
  const saveEnabledModels = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    startCodexLogin.mockClear();
    signOutCodex.mockClear();
    saveEnabledModels.mockClear();
    useSettings.setState({
      connections: connections({}),
      openRouterModels: [],
      modelOptionsLoading: false,
      modelPreferencesSaving: false,
      loadConnections: vi.fn().mockResolvedValue(undefined),
      loadOpenRouterModels: vi.fn().mockResolvedValue(undefined),
      loadKeyModels: vi.fn().mockResolvedValue(undefined),
      startCodexLogin,
      signOutCodex,
      saveEnabledModels,
      action: null,
      error: null,
    });
  });

  it("starts the sign-in when the button is pressed", async () => {
    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));

    await waitFor(() => expect(startCodexLogin).toHaveBeenCalled());
  });

  it("shows the account and a way out once signed in", () => {
    useSettings.setState({
      connections: connections({
        connected: true,
        email: "someone@example.com",
      }),
    });

    renderSettings();

    expect(screen.getByText("Signed in")).toBeVisible();
    expect(screen.getByText("someone@example.com")).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  it("waits on the browser while a sign-in is in flight, and offers to give up", () => {
    useSettings.setState({ connections: connections({ signing_in: true }) });

    renderSettings();

    expect(screen.getByText(/finish signing in in your browser/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeVisible();
  });

  it("surfaces a sign-in that failed instead of going quiet", () => {
    useSettings.setState({
      connections: connections({ error: "Port 1455 is already in use" }),
    });

    renderSettings();

    expect(screen.getByText(/Port 1455 is already in use/)).toBeVisible();
  });

  it("removes the demo placeholder when manually enabling ChatGPT models", async () => {
    useSettings.setState({
      connections: connections({ connected: true }, ["demo::demo-reliable", "openrouter::vendor/model"]),
    });

    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Choose enabled ChatGPT models" }));
    fireEvent.click(await screen.findByLabelText("Enable GPT-5.6-Sol"));
    fireEvent.click(screen.getByRole("button", { name: "Save enabled models" }));

    await waitFor(() =>
      expect(saveEnabledModels).toHaveBeenCalledWith(["openrouter::vendor/model", "codex::gpt-5.6-sol"]),
    );
  });

  it("keeps another provider's models when saving from this section", async () => {
    /** The enabled list is one list for every provider. Saving this section's choice must not
        be how someone loses the OpenRouter models they picked. */
    useSettings.setState({
      connections: connections({ connected: true }, ["openrouter::anthropic/claude-sonnet"]),
    });

    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Choose enabled ChatGPT models" }));
    fireEvent.click(await screen.findByLabelText("Enable GPT-5.5"));
    fireEvent.click(screen.getByRole("button", { name: "Save enabled models" }));

    await waitFor(() =>
      expect(saveEnabledModels).toHaveBeenCalledWith([
        "openrouter::anthropic/claude-sonnet",
        "codex::gpt-5.5",
      ]),
    );
  });

  it("does not let a model be enabled before anyone has signed in", () => {
    renderSettings();

    expect(screen.queryByRole("button", { name: "Choose enabled ChatGPT models" })).not.toBeInTheDocument();
  });
});

describe("Models the subscription serves but this app does not know", () => {
  const saveEnabledModels = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    saveEnabledModels.mockClear();
    useSettings.setState({
      connections: connections({ connected: true }),
      openRouterModels: [],
      modelOptionsLoading: false,
      modelPreferencesSaving: false,
      loadConnections: vi.fn().mockResolvedValue(undefined),
      loadOpenRouterModels: vi.fn().mockResolvedValue(undefined),
      loadKeyModels: vi.fn().mockResolvedValue(undefined),
      startCodexLogin: vi.fn().mockResolvedValue(undefined),
      signOutCodex: vi.fn().mockResolvedValue(undefined),
      saveEnabledModels,
      action: null,
      error: null,
    });
  });

  it("takes a model id by hand, because OpenAI publishes no list to read, and adds it only once", async () => {
    /** The seeded list is a guess that goes stale the day OpenAI ships a model. Without this
        the picker is what stops someone using what they are paying for. */
    renderSettings();
    const field = screen.getByLabelText("Add a ChatGPT model by id");

    fireEvent.change(field, { target: { value: "gpt-5.7-codex" } });
    fireEvent.click(screen.getByRole("button", { name: /Add/ }));

    await waitFor(() =>
      expect(saveEnabledModels).toHaveBeenCalledWith(["codex::gpt-5.6-sol", "codex::gpt-5.7-codex"]),
    );
    // Once it is enabled, the same id is not added again.
    saveEnabledModels.mockClear();
    act(() =>
      useSettings.setState({
        connections: connections({ connected: true }, ["codex::gpt-5.7-codex"]),
      }),
    );
    fireEvent.change(field, { target: { value: "gpt-5.7-codex" } });
    fireEvent.click(screen.getByRole("button", { name: /Add/ }));

    await waitFor(() => expect(field).toHaveValue(""));
    expect(saveEnabledModels).not.toHaveBeenCalled();
  });
});
