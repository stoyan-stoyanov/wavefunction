import { ApiError, request, requestResponse, throwIfAborted } from "@/lib/transport";
import type { KeyProviderId } from "@/lib/models";
import { invoke } from "@tauri-apps/api/core";

import type {
  ComputerStatus,
  Connections,
  Connector,
  ConnectorProbe,
  GrantState,
  ModelSpec,
  OllamaConnection,
  OpenRouterModel,
  Run,
  RunEvent,
  Schemas,
  Skill,
} from "@/lib/types";

/** The keys Settings saves: model providers and web search. */
export type SecretKey = keyof Connections["secrets"];

export type CatalogEntry = Schemas["CatalogEntry"];
export function getCatalogSetup(id: string) {
  return request<Schemas["CatalogSetup"]>(`/api/connector-catalog/${encodeURIComponent(id)}/setup`);
}
export function listConnectorCatalog() {
  return request<CatalogEntry[]>("/api/connector-catalog");
}
export function connectCatalogEntry(id: string, setup: Record<string, string> = {}) {
  return request<Schemas["CatalogConnecting"]>(`/api/connector-catalog/${encodeURIComponent(id)}/connect`, {
    method: "POST",
    body: JSON.stringify(setup),
  });
}

export type CatalogConnectionState = Schemas["CatalogConnectionState"];
export function catalogConnectionStatus() {
  return request<Record<string, CatalogConnectionState>>("/api/connector-catalog/connections");
}
export function cancelCatalogConnection(id: string) {
  return request<Schemas["Cancelled"]>(`/api/connector-catalog/connections/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

type LocalIntegrationKind = "himalaya" | "gog" | "microsoft" | "gh";
export type LocalIntegrationStatus = Schemas["LocalIntegrationStatus"];

export function installLocalIntegration(kind: LocalIntegrationKind) {
  return request<Schemas["Installed"]>(`/api/local-integrations/${kind}/install`, { method: "POST" });
}
export function setupLocalEmail(payload: {
  email: string;
  password: string;
  imap_host: string;
  imap_port: number;
  smtp_host: string;
  smtp_port: number;
  smtp_starttls: boolean;
}) {
  return request<Schemas["EmailAccount"]>("/api/local-integrations/himalaya/setup", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
/** `foundAt` is a file the app found in Downloads; it is read there, not sent from here. */
export function startGoogleSignIn(
  email: string,
  credentials: string,
  services?: string[],
  allowWrite = true,
  foundAt = "",
) {
  return request<Schemas["SignInRunning"]>("/api/local-integrations/gog/sign-in", {
    method: "POST",
    body: JSON.stringify({
      email,
      allow_write: allowWrite,
      ...(foundAt ? { credentials_path: foundAt } : { credentials }),
      ...(services ? { services } : {}),
    }),
  });
}
export type DownloadedCredentials = Schemas["DownloadedCredentials"];
/** The Desktop client file Google's console just saved to Downloads, by name, if there is one. */
export function downloadedGoogleCredentials() {
  return request<Schemas["FoundCredentials"]>("/api/local-integrations/gog/credentials");
}
export function googleSignInStatus() {
  return request<Schemas["GoogleSignInState"]>("/api/local-integrations/gog/sign-in");
}
export function cancelGoogleSignIn() {
  return request<Schemas["SignInRunning"]>("/api/local-integrations/gog/sign-in", { method: "DELETE" });
}

export function inspectLocalIntegration(kind: LocalIntegrationKind) {
  return request<LocalIntegrationStatus>(`/api/local-integrations/${kind}`);
}

export async function connectLocalIntegration(
  kind: LocalIntegrationKind,
  account: string,
  allowWrite: boolean,
  mode: "mail" | "workspace" = "workspace",
  services?: string[],
) {
  return request<Schemas["SavedConnector"]>(`/api/local-integrations/${kind}/connect`, {
    method: "POST",
    body: JSON.stringify({
      account,
      allow_write: allowWrite,
      ...(kind === "microsoft" ? { mode } : {}),
      ...(services ? { services } : {}),
    }),
  });
}

export const api = {
  createSkill: (skill: { name: string; description: string; body: string }) =>
    request<Skill>("/api/skills", {
      method: "POST",
      body: JSON.stringify(skill),
    }),
  updateSkill: (name: string, skill: { description: string; body: string }) =>
    request<Skill>(`/api/skills/${name}`, {
      method: "PUT",
      body: JSON.stringify(skill),
    }),
  deleteSkill: (name: string) => request<void>(`/api/skills/${name}`, { method: "DELETE" }),
  listConnectors: () => request<Connector[]>("/api/connectors"),
  addConnector: (server: Record<string, unknown>) =>
    request<Schemas["SavedConnector"]>("/api/connectors", {
      method: "POST",
      body: JSON.stringify(server),
    }),
  updateConnector: (name: string, server: Record<string, unknown>) =>
    request<Schemas["SavedConnector"]>(`/api/connectors/${name}`, {
      method: "PUT",
      body: JSON.stringify(server),
    }),
  probeConnector: (url: string) =>
    request<ConnectorProbe>("/api/connectors/probe", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  setConnectorSecret: (name: string, value: string) =>
    request<void>(`/api/connectors/secrets/${name}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  clearConnectorSecret: (name: string) =>
    request<void>(`/api/connectors/secrets/${name}`, { method: "DELETE" }),
  refreshConnector: (name: string) =>
    request<Schemas["ConnectorCheck"]>(`/api/connectors/${name}/refresh`, {
      method: "POST",
    }),
  signInConnector: (name: string) =>
    request<Schemas["ConnectorCheck"]>(`/api/connectors/${name}/sign-in`, {
      method: "POST",
    }),
  signOutConnector: (name: string) => request<void>(`/api/connectors/${name}/sign-out`, { method: "POST" }),
  removeConnector: (name: string) => request<void>(`/api/connectors/${name}`, { method: "DELETE" }),
  decideApproval: (
    runId: string,
    decision: "approve" | "reject",
    feedback: string,
    expectedApproval?: number,
  ) =>
    request<Run>(`/api/runs/${runId}/approval`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        feedback,
        expected_approval: expectedApproval,
      }),
    }),
  cancelRun: (runId: string) => request<Run>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  runConnectorSetup: (runId: string) => request<Schemas["RunSetup"]>(`/api/runs/${runId}/connectors`),
  /** The GitHub card finished signing gh in; the task waiting on it carries on. */
  githubSignedIn: (runId: string) =>
    request<Schemas["GitHubAccount"]>(`/api/runs/${runId}/connectors/github`, {
      method: "POST",
    }),
  chooseRunFolder: (runId: string, path: string, setupId: string) =>
    request<Schemas["FolderChosen"]>(`/api/runs/${runId}/connectors/folder`, {
      method: "POST",
      body: JSON.stringify({ path, setup_id: setupId }),
    }),
  listGrants: () => request<GrantState>("/api/grants"),
  addGrant: (path: string, writable: boolean) =>
    request<Schemas["Grants"]>("/api/grants", {
      method: "POST",
      body: JSON.stringify({ path, writable }),
    }),
  revokeGrant: (path: string) =>
    request<Schemas["Grants"]>(`/api/grants?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),
  getConnections: () => request<Connections>("/api/connections"),
  modelReasoningOptions: (model: ModelSpec) =>
    request<Schemas["ReasoningOptions"]>("/api/models/reasoning-options", {
      method: "POST",
      body: JSON.stringify(model),
    }),
  setZeroDataRetention: (enabled: boolean) =>
    request<Schemas["ZeroDataRetention"]>("/api/connections/zdr", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  connectOllama: (base_url: string, num_ctx: number) =>
    request<OllamaConnection>("/api/connections/ollama", {
      method: "PUT",
      body: JSON.stringify({ base_url, num_ctx }),
    }),
  listOpenRouterModels: () =>
    request<Schemas["GatewayModels"]>("/api/openrouter/models").then((response) => response.models),
  listKeyProviderModels: (provider: KeyProviderId) =>
    request<{ models: OpenRouterModel[] }>(`/api/${provider}/models`).then((response) => response.models),
  updateDefaultModel: (model: ModelSpec) =>
    request<Schemas["DefaultModel"]>("/api/connections/default-model", {
      method: "PUT",
      body: JSON.stringify({ model }),
    }),
  updateEnabledModels: (models: string[]) =>
    request<Schemas["EnabledModels"]>("/api/connections/enabled-models", {
      method: "PUT",
      body: JSON.stringify({ models }),
    }),
  saveSecret: (key: SecretKey, value: string) =>
    request<void>(`/api/connections/secrets/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  deleteSecret: (key: SecretKey) => request<void>(`/api/connections/secrets/${key}`, { method: "DELETE" }),
  codexLogin: () =>
    request<Schemas["CodexLogin"]>("/api/codex/login", {
      method: "POST",
    }),
  codexCancelLogin: () => request<void>("/api/codex/login/cancel", { method: "POST" }),
  codexLogout: () => request<void>("/api/codex/logout", { method: "POST" }),
  computerStatus: (refresh = false) =>
    request<ComputerStatus>(`/api/computer${refresh ? "?refresh=true" : ""}`),
  buildComputerImage: () =>
    request<Schemas["ComputerBuild"]>("/api/computer/build", {
      method: "POST",
    }),
  manageDocker: (action: "start" | "restart") =>
    request<ComputerStatus>(`/api/computer/docker/${action}`, {
      method: "POST",
    }),
};

/** Open a URL in the user's own browser rather than in the app's webview.
 *
 * The ChatGPT sign-in has to land where they are already signed in to ChatGPT, which is their
 * real browser — inside the webview they would be asked for a password, and the redirect back
 * to localhost:1455 would be caught by the app instead of the listener waiting for it.
 *
 * `window.open` is the fallback for `npm run dev` in an ordinary browser, where there is no
 * shell to ask.
 */
export async function openExternal(url: string): Promise<void> {
  if ("__TAURI_INTERNALS__" in window) {
    await invoke("plugin:opener|open_url", { url });
    return;
  }
  window.open(url, "_blank", "noopener");
}

export async function fetchBlobUrl(path: string, init: RequestInit = {}): Promise<string> {
  return requestResponse(
    path,
    async (response, signal) => {
      const blob = await response.blob();
      throwIfAborted(signal);
      return URL.createObjectURL(blob);
    },
    init,
  );
}

/** Shared SSE frame parser: split on blank lines, keep `data:` payloads and the `id:` cursor. */
async function consumeEventStream(
  body: ReadableStream<Uint8Array>,
  onData: (data: string, id?: number) => void,
  signal: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const cancel = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener("abort", cancel, { once: true });
  const decoder = new TextDecoder();
  let buffer = "";
  let carry = "";
  try {
    for (;;) {
      throwIfAborted(signal);
      const { done, value } = await reader.read();
      throwIfAborted(signal);
      const text = carry + decoder.decode(value, { stream: !done });
      // A read can end between the "\r" and "\n" of a line end: keep that "\r" for the next one.
      carry = !done && text.endsWith("\r") ? "\r" : "";
      buffer += text.slice(0, text.length - carry.length).replaceAll("\r\n", "\n");
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const lines = block.split("\n");
        const data = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        const idLine = lines.find((line) => line.startsWith("id:"));
        const id = idLine ? Number(idLine.slice(3).trim()) : undefined;
        throwIfAborted(signal);
        if (data) onData(data, Number.isFinite(id) ? id : undefined);
      }
      if (done) {
        const trailing = buffer.trim();
        if (trailing.startsWith("data:")) onData(trailing.slice(5).trimStart());
        break;
      }
    }
  } finally {
    signal.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function streamRunEvents(
  runId: string,
  after: number,
  onEvent: (event: RunEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return requestResponse(
    `/api/runs/${runId}/events?after=${after}`,
    async (response, combinedSignal) => {
      if (!response.body) throw new ApiError("Could not follow this run", response.status);
      await consumeEventStream(
        response.body,
        (data) => onEvent(JSON.parse(data) as RunEvent),
        combinedSignal,
      );
    },
    {
      headers: { Accept: "text/event-stream" },
      signal,
    },
  );
}

export function startMicrosoftSignIn(payload: {
  email: string;
  account_type: "personal" | "work";
  mode: "mail" | "workspace";
  allow_write: boolean;
  services?: string[];
}) {
  return request<Schemas["MicrosoftAttempt"]>("/api/local-integrations/microsoft/sign-in", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
export function microsoftSignInStatus(id: string) {
  return request<Schemas["MicrosoftSignInState"]>(
    `/api/local-integrations/microsoft/sign-in/${encodeURIComponent(id)}`,
  );
}
export function cancelMicrosoftSignIn(id: string) {
  return request<Schemas["Cancelled"]>(
    `/api/local-integrations/microsoft/sign-in/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

/** `gh auth login` with GitHub's device flow. Starting it again joins the one under way. */
type GitHubSignInState = Schemas["GitHubSignInState"];
export function startGitHubSignIn() {
  return request<GitHubSignInState>("/api/local-integrations/gh/sign-in", {
    method: "POST",
  });
}
export function gitHubSignInStatus() {
  return request<GitHubSignInState>("/api/local-integrations/gh/sign-in");
}
