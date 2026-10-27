import { useEffect, useState } from "react";
import { Check, ChevronDown, Copy, ExternalLink, LoaderCircle, Search } from "lucide-react";
import { ConnectorLogo, InstalledTag, LoginMethodTag } from "@/components/connector-brand";
import { LocalIntegrations } from "@/components/local-integrations";
import { WebLink } from "@/components/web-link";
import {
  connectorCategories,
  localConnectorIds,
  loginLabel,
  matchesLocalIntegration,
} from "@/lib/connector-presentation";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  cancelCatalogConnection,
  catalogConnectionStatus,
  connectCatalogEntry,
  getCatalogSetup,
  listConnectorCatalog,
  type CatalogConnectionState,
  type CatalogEntry,
} from "@/lib/api";
import { slackManifest } from "@/lib/slack-manifest";
import { useSettings } from "@/store/settings";
export function ConnectorCatalog({
  onOpen,
  setupId,
  onClose,
}: {
  onOpen: (name: string) => void;
  /** Open the existing setup form directly from a chat request. */
  setupId?: string;
  onClose?: () => void;
}) {
  const connectors = useSettings((s) => s.connectors);
  const loadConnectors = useSettings((s) => s.loadConnectors);
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<CatalogEntry | null>(null);
  const [states, setStates] = useState<Record<string, CatalogConnectionState>>({});
  const [error, setError] = useState("");
  const [instanceUrl, setInstanceUrl] = useState("http://127.0.0.1:5678");
  const [apiKey, setApiKey] = useState("");
  const [setup, setSetup] = useState<Record<string, string>>({});
  const [loadingSetup, setLoadingSetup] = useState(false);
  const [setupFailed, setSetupFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    void listConnectorCatalog()
      .then((rows) => {
        if (!alive) return;
        setEntries(rows);
        if (setupId) {
          const entry = rows.find((row) => row.id === setupId);
          if (entry) {
            setLoadingSetup(Boolean(entry.setup));
            setSelected(entry);
          } else
            setError(
              "This connector is no longer in the catalog. Open Connectors in Settings to configure it.",
            );
        }
      })
      .catch(() => {
        if (alive) setError("Could not load the connector catalog. Reopen Connectors to try again.");
      });
    return () => {
      alive = false;
    };
  }, [setupId]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let previous = "";
    async function poll() {
      try {
        const found = await catalogConnectionStatus();
        if (!alive) return;
        setStates(found);
        const terminal = JSON.stringify(
          Object.entries(found).filter(([, state]) => state.state !== "connecting"),
        );
        if (previous && terminal !== previous) await loadConnectors();
        previous = terminal;
      } catch {
        /* Existing connectors remain usable during a transient status failure. */
      }
      if (alive) timer = setTimeout(() => void poll(), 1500);
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [loadConnectors]);
  useEffect(() => {
    if (!selected?.setup) return;
    let alive = true;
    void getCatalogSetup(selected.id)
      .then((saved) => {
        if (alive) setSetup({ ...saved, _entry_id: selected.id });
      })
      .catch(() => {
        if (alive) {
          setSetupFailed(true);
          setError("Could not load saved setup. Close this dialog and try again.");
        }
      })
      .finally(() => {
        if (alive) setLoadingSetup(false);
      });
    return () => {
      alive = false;
    };
  }, [selected]);
  const rows = entries.filter((entry) => {
    const matches = `${entry.title} ${entry.description}`.toLowerCase().includes(query.toLowerCase());
    return (
      matches &&
      (filter === "all" ||
        (filter === "oauth" && entry.auth === "oauth") ||
        (filter === "api_key" && entry.auth === "api_key") ||
        (filter === "public" && entry.auth === "none" && !entry.local) ||
        (filter === "local" && entry.local))
    );
  });
  const existing = selected
    ? connectors.find((c) => c.name === selected.id && (selected.setup || c.url === selected.url))
    : undefined;
  const state = selected ? states[selected.id] : undefined;
  // Once the connector exists, its live state: this session's last connect result stays
  // "connected" after the sign-in expires.
  const connected = existing ? existing.connected : state?.state === "connected";
  const working = busy || state?.state === "connecting";
  const setupDisabled =
    loadingSetup ||
    setupFailed ||
    (selected?.setup === "api_key" && !setup.api_key && setup.secret_saved !== "true") ||
    (selected?.setup === "registered_oauth" &&
      (!setup.url ||
        !setup.client_id ||
        !setup.redirect_uri ||
        ((setup.auth_method ?? selected.oauth_default_auth_method) !== "none" &&
          !setup.client_secret &&
          setup.secret_saved !== "true")));
  async function connect(entry: CatalogEntry) {
    setBusy(true);
    setError("");
    try {
      await connectCatalogEntry(
        entry.id,
        entry.id === "n8n"
          ? { url: instanceUrl, api_key: apiKey }
          : entry.id === "slack"
            ? // Slack's card has no scopes field: its sign-in asks for what the manifest lists.
              { ...setup, scope: entry.oauth_default_scope ?? setup.scope ?? "" }
            : entry.setup
              ? setup
              : {},
      );
      setApiKey("");
      setSetup((old) =>
        old._entry_id === entry.id
          ? {
              ...old,
              client_secret: "",
              api_key: "",
              secret_saved:
                entry.setup === "registered_oauth" || entry.setup === "api_key" ? "true" : "false",
            }
          : old,
      );
      setStates((old) => ({
        ...old,
        [entry.id]: { state: "connecting", error: "" },
      }));
      await loadConnectors();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not connect.");
    } finally {
      setBusy(false);
    }
  }
  function closeSetup() {
    setSelected(null);
    onClose?.();
  }
  return (
    <section className={setupId ? undefined : "mt-10 space-y-4"}>
      {!setupId ? (
        <>
          <h2 className="text-lg font-medium">Browse connectors</h2>
          <div className="flex flex-wrap gap-3">
            <div className="relative min-w-48 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label="Search connectors"
                placeholder="Search apps and services"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="pl-9"
              />
            </div>
            <div className="relative">
              <select
                aria-label="Filter connectors"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="h-8 appearance-none rounded-lg border border-input bg-transparent py-1 pl-3 pr-9 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
              >
                <option value="all">All connectors</option>
                <option value="oauth">Browser sign-in</option>
                <option value="api_key">API key</option>
                <option value="public">No account needed</option>
                <option value="local">Local setup</option>
              </select>
              <ChevronDown
                aria-hidden
                className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              />
            </div>
          </div>
          {error && !selected ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          {connectorCategories.map((category) => {
            if (category.title === "Email" || category.title === "Workspace")
              return (
                <LocalIntegrations
                  key={category.title}
                  section={category.title}
                  query={query}
                  filter={filter}
                />
              );
            const categoryRows = rows.filter((entry) => category.ids.includes(entry.id));
            if (!categoryRows.length) return null;
            return (
              <section key={category.title} aria-label={category.title} className="space-y-3 pt-4">
                <h3 className="text-sm font-medium text-muted-foreground">{category.title}</h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  {categoryRows.map((entry) => {
                    const installed = connectors.some((c) => c.name === entry.id);
                    const pending = states[entry.id]?.state === "connecting";
                    return (
                      <button
                        key={entry.id}
                        onClick={() => {
                          setSelected(entry);
                          setLoadingSetup(Boolean(entry.setup));
                          setSetupFailed(false);
                          setSetup({});
                          setError("");
                          setApiKey("");
                          setInstanceUrl(
                            connectors.find((c) => c.name === entry.id)?.env.N8N_BASE_URL ??
                              "http://127.0.0.1:5678",
                          );
                        }}
                        className="rounded-xl border border-hairline bg-card p-4 text-left transition-colors hover:bg-muted"
                      >
                        <span className="flex items-center gap-3">
                          <ConnectorLogo id={entry.id} />
                          <span className="font-medium text-sm">{entry.title}</span>
                          {installed ? (
                            <InstalledTag />
                          ) : pending ? (
                            <LoaderCircle aria-label="Connecting" className="ml-auto size-4 animate-spin" />
                          ) : null}
                        </span>
                        <span className="mt-3 block text-xs text-muted-foreground">{entry.description}</span>
                        <span className="mt-3 flex items-center gap-2">
                          <LoginMethodTag>{loginLabel(entry.auth)}</LoginMethodTag>
                          <span className="text-2xs text-muted-foreground">
                            {entry.local ? "Runs locally" : "Hosted service"}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
          {!rows.length &&
          entries.length &&
          !localConnectorIds.some((id) => matchesLocalIntegration(id, query, filter)) ? (
            <p className="text-sm text-muted-foreground">No connectors match your search.</p>
          ) : null}
        </>
      ) : null}
      {setupId && error && !selected ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {selected ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) closeSetup();
          }}
        >
          <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-3">
                <ConnectorLogo id={selected.id} />
                {selected.title}
              </DialogTitle>
              <DialogDescription>{selected.description}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4 text-sm">
              {selected.id === "slack" ? null : (
                <p className="text-muted-foreground">
                  {selected.id === "n8n"
                    ? "Connect to your n8n instance. The bundled adapter runs on your Mac."
                    : selected.local
                      ? `Connect to ${selected.title} running on your Mac.`
                      : `Connect to ${selected.title} so Wavefunction can use its tools.`}
                </p>
              )}
              {selected.id === "slack" ? (
                <SlackSteps
                  setup={setup}
                  scope={selected.oauth_default_scope ?? ""}
                  disabled={working || loadingSetup}
                  onChange={setSetup}
                />
              ) : (
                <div className="space-y-3 rounded-lg border border-hairline bg-muted p-3 text-xs">
                  <h3 className="font-medium">Set up {selected.title}</h3>
                  {selected.notes ? <p>{selected.notes}</p> : null}
                  <ol className="list-decimal space-y-2 pl-4">
                    {selected.id === "n8n" ? (
                      <>
                        <li>Start your n8n instance and create an API key in n8n Settings.</li>
                        <li>Enter your instance URL and API key below, then click Connect.</li>
                      </>
                    ) : selected.local ? (
                      <>
                        <li>Follow the {selected.title} setup guide below to enable its local MCP server.</li>
                        <li>Keep {selected.title} running, then click Connect to load its tools.</li>
                      </>
                    ) : selected.setup === "registered_oauth" ? (
                      <>
                        <li>
                          Follow the {selected.title} setup guide below to register your own OAuth
                          integration. You may need help from your account administrator.
                        </li>
                        <li>
                          Register the callback URL shown here in {selected.title}, then enter the integration
                          details below.
                        </li>
                        <li>
                          Click Connect {selected.title}, sign in in your browser, and review the requested
                          access.
                        </li>
                      </>
                    ) : selected.auth === "api_key" ? (
                      <>
                        <li>
                          Create an API key in your {selected.title} account using the setup guide below.
                        </li>
                        <li>Paste the key below, then click Connect to load your available tools.</li>
                      </>
                    ) : selected.auth === "oauth" ? (
                      <>
                        <li>
                          Have your {selected.title} account ready. Check the setup guide below for any
                          account or administrator requirements.
                        </li>
                        {selected.setup === "url" ? (
                          <li>Enter the MCP server URL for your {selected.title} workspace below.</li>
                        ) : null}
                        <li>
                          Click Connect {selected.title}, sign in in your browser, and review the requested
                          access.
                        </li>
                      </>
                    ) : (
                      <li>
                        Click Connect to load the available {selected.title} tools. No account sign-in is
                        needed.
                      </li>
                    )}
                    <li>
                      {setupId
                        ? "After connecting, your task will continue automatically."
                        : "After connecting, tools become available on your next task."}
                    </li>
                  </ol>
                  <WebLink href={selected.docs} className="inline-flex items-center gap-1 underline">
                    {selected.title} setup guide <ExternalLink className="size-3" />
                  </WebLink>
                </div>
              )}
              {!selected.local && selected.id !== "slack" ? (
                <p className="text-xs text-muted-foreground">
                  Tool requests are sent to {selected.title}. Tool results may also be sent to the model
                  provider of each chat that uses it.
                </p>
              ) : null}
              {selected.id === "n8n" ? (
                <div className="space-y-3">
                  <label className="block text-xs">
                    n8n instance URL
                    <Input
                      value={instanceUrl}
                      onChange={(e) => setInstanceUrl(e.target.value)}
                      disabled={working}
                    />
                  </label>
                  <label className="block text-xs">
                    n8n API key
                    <Input
                      type="password"
                      autoComplete="new-password"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      disabled={working}
                    />
                  </label>
                </div>
              ) : null}
              {selected.setup && selected.id !== "slack" ? (
                <div className="space-y-3">
                  {loadingSetup ? <p role="status">Loading saved setup…</p> : null}
                  {selected.setup !== "api_key" ? (
                    <label className="block text-xs">
                      MCP server URL
                      <Input
                        value={setup.url ?? ""}
                        disabled={
                          working || loadingSetup || selected.id === "hubspot" || selected.id === "asana"
                        }
                        placeholder={
                          selected.id === "snowflake"
                            ? "https://account.snowflakecomputing.com/api/v2/databases/DB/schemas/SCHEMA/mcp-servers/SERVER"
                            : selected.id === "salesforce"
                              ? "https://api.salesforce.com/platform/mcp/v1/SERVER-NAME"
                              : selected.url
                        }
                        onChange={(e) =>
                          setSetup({
                            ...setup,
                            url: e.target.value,
                            secret_saved: "false",
                          })
                        }
                      />
                    </label>
                  ) : (
                    <label className="block text-xs">
                      Render API key
                      <Input
                        type="password"
                        autoComplete="new-password"
                        value={setup.api_key ?? ""}
                        placeholder={
                          setup.secret_saved === "true" ? "Saved. Leave blank to keep it." : "API key"
                        }
                        disabled={working || loadingSetup}
                        onChange={(e) => setSetup({ ...setup, api_key: e.target.value })}
                      />
                    </label>
                  )}
                  {selected.setup === "registered_oauth" ? (
                    <>
                      <label className="block text-xs">
                        Callback URL
                        <Input
                          value={setup.redirect_uri ?? ""}
                          disabled={working || loadingSetup}
                          onChange={(e) => setSetup({ ...setup, redirect_uri: e.target.value })}
                        />
                      </label>
                      <p className="text-xs text-muted-foreground">
                        Copy this exact callback URL into your {selected.title} OAuth integration. It returns
                        sign-in to {"Wavefunction"} on this Mac.
                      </p>
                      <label className="block text-xs">
                        OAuth client ID
                        <Input
                          value={setup.client_id ?? ""}
                          disabled={working || loadingSetup}
                          onChange={(e) =>
                            setSetup({
                              ...setup,
                              client_id: e.target.value,
                              secret_saved: "false",
                            })
                          }
                        />
                      </label>
                      <label className="block text-xs">
                        Client authentication
                        <select
                          className="mt-1 block h-8 w-full rounded-lg border border-input bg-background px-3"
                          value={
                            setup.auth_method ?? selected.oauth_default_auth_method ?? "client_secret_basic"
                          }
                          disabled={working || loadingSetup}
                          onChange={(e) =>
                            setSetup({
                              ...setup,
                              auth_method: e.target.value,
                              client_secret: "",
                              secret_saved: "false",
                            })
                          }
                        >
                          {(selected.oauth_auth_methods ?? ["client_secret_basic", "client_secret_post"]).map(
                            (method) => (
                              <option key={method} value={method}>
                                {method === "none"
                                  ? "Public client (PKCE, no secret)"
                                  : method === "client_secret_basic"
                                    ? "HTTP Basic"
                                    : "Request body"}
                              </option>
                            ),
                          )}
                        </select>
                      </label>
                      {(setup.auth_method ?? selected.oauth_default_auth_method) !== "none" ? (
                        <>
                          <label className="block text-xs">
                            OAuth client secret
                            <Input
                              type="password"
                              autoComplete="new-password"
                              value={setup.client_secret ?? ""}
                              placeholder={
                                setup.secret_saved === "true"
                                  ? "Saved. Leave blank to keep it."
                                  : "Client secret"
                              }
                              disabled={working || loadingSetup}
                              onChange={(e) =>
                                setSetup({
                                  ...setup,
                                  client_secret: e.target.value,
                                })
                              }
                            />
                          </label>
                          <p className="text-xs text-muted-foreground">
                            Your client secret is saved in the local credential store.
                          </p>
                        </>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          {"Wavefunction"} handles PKCE and stores tokens locally. Your app does not need a
                          client secret for this flow.
                        </p>
                      )}
                      <label className="block text-xs">
                        OAuth scopes (optional)
                        <Input
                          value={setup.scope ?? ""}
                          placeholder="Use the provider’s advertised scopes"
                          disabled={working || loadingSetup}
                          onChange={(e) => setSetup({ ...setup, scope: e.target.value })}
                        />
                      </label>
                    </>
                  ) : null}
                </div>
              ) : null}
              {selected.url && !selected.setup ? (
                <p className="break-all font-mono text-2xs text-muted-foreground">{selected.url}</p>
              ) : null}
              {working ? (
                <p role="status" className="flex items-center gap-2">
                  <LoaderCircle className="size-4 animate-spin" />
                  {selected.auth === "oauth"
                    ? "Complete sign-in in your browser. You can close this dialog while it connects."
                    : "Checking the connection and loading tools…"}
                </p>
              ) : null}
              {error || state?.error ? (
                <p role="alert" className="text-xs text-destructive">
                  {error || state?.error}
                </p>
              ) : null}
              {connected ? (
                <p className="text-sm text-emerald-600">
                  {setupId
                    ? "Connected. Your task will continue once its connections are ready."
                    : "Connected. Open the connector to review its tools."}
                </p>
              ) : null}
              <div className="flex justify-end gap-2">
                {selected.setup && connected && !working ? (
                  <Button variant="secondary" onClick={() => onOpen(selected.id)}>
                    {setupId ? "Done" : "View tools"}
                  </Button>
                ) : null}
                <Button variant="ghost" onClick={closeSetup}>
                  Close
                </Button>
                {working ? (
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => {
                      void cancelCatalogConnection(selected.id)
                        .then(() =>
                          setStates((old) => ({
                            ...old,
                            [selected.id]: {
                              state: "cancelled",
                              error: "Sign-in cancelled.",
                            },
                          })),
                        )
                        .catch(() => setError("Could not cancel. Try again."));
                    }}
                  >
                    Cancel connection
                  </Button>
                ) : connected && !selected.setup ? (
                  <Button onClick={() => onOpen(selected.id)}>{setupId ? "Done" : "View tools"}</Button>
                ) : (
                  <Button
                    disabled={
                      Boolean(selected.setup && setupDisabled) ||
                      (selected.id === "n8n" &&
                        !connectors.some((c) => c.name === "n8n") &&
                        (!apiKey || !instanceUrl))
                    }
                    onClick={() => void connect(selected)}
                  >
                    {state?.state === "failed"
                      ? "Try again"
                      : selected.auth === "oauth"
                        ? `Connect ${selected.title}`
                        : "Connect"}
                  </Button>
                )}
              </div>
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </section>
  );
}

const SLACK_MANIFEST_GUIDE =
  "https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/#creating_apps";

/** Slack's MCP server only signs in through a Slack app of the user's own. Our manifest already
    holds its callback and scopes, so the user only creates it, installs it and pastes its ID. */
function SlackSteps({
  setup,
  scope,
  disabled,
  onChange,
}: {
  setup: Record<string, string>;
  scope: string;
  disabled: boolean;
  onChange: (setup: Record<string, string>) => void;
}) {
  const [copied, setCopied] = useState<boolean | null>(null);
  const manifest = slackManifest(setup.redirect_uri ?? "", scope);
  return (
    <div className="space-y-3 rounded-lg border border-hairline bg-muted p-3 text-xs">
      <h3 className="text-lg font-semibold">Set up Slack</h3>
      <ol className="list-decimal space-y-3 pl-4">
        <li className="space-y-2">
          <p>Create a Slack app from our manifest.</p>
          <WebLink href={SLACK_MANIFEST_GUIDE} className="inline-flex items-center gap-1 underline">
            How to create an app from a manifest <ExternalLink className="size-3" />
          </WebLink>
          <Button
            className="flex"
            disabled={disabled || !setup.redirect_uri}
            onClick={() => {
              void navigator.clipboard
                .writeText(manifest)
                .then(() => setCopied(true))
                // Shown instead, to copy by hand.
                .catch(() => setCopied(false));
            }}
          >
            {copied ? <Check /> : <Copy />}
            {copied ? "Manifest copied" : "Copy manifest"}
          </Button>
          {copied === false ? (
            <>
              <p>Copying didn't work here. Select the manifest and copy it.</p>
              <pre className="max-h-40 overflow-auto rounded-md bg-background p-2 whitespace-pre-wrap break-all select-all">
                {manifest}
              </pre>
            </>
          ) : null}
        </li>
        <li>Install it in your workspace. A workspace admin may need to approve it.</li>
        <li className="space-y-2">
          <p>Paste its client ID, from Basic Information › App Credentials.</p>
          <Input
            aria-label="Slack client ID"
            placeholder="Client ID"
            value={setup.client_id ?? ""}
            disabled={disabled}
            onChange={(e) => onChange({ ...setup, client_id: e.target.value, secret_saved: "false" })}
          />
        </li>
        <li>Sign in with Connect Slack.</li>
      </ol>
    </div>
  );
}
