import { PageContent } from "@/components/page-layout";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  ArrowLeft,
  CircleAlert,
  KeyRound,
  LoaderCircle,
  LogIn,
  LogOut,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConnectorCatalog } from "@/components/connector-catalog";
import { ConnectorLogo, LoginMethodTag } from "@/components/connector-brand";
import { EmailSetup } from "@/components/email-setup";
import { GoogleAccountSetup } from "@/components/google-setup";
import { MicrosoftSetup } from "@/components/microsoft-setup";
import {
  localAccountGroups,
  localConnectorAccount,
  localConnectorProducts,
  loginLabel,
} from "@/lib/connector-presentation";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { Connector, ConnectorProbe } from "@/lib/types";
import { listConnectorCatalog } from "@/lib/api";
import { useSettings } from "@/store/settings";
import "./connectors-view.css";

function ConnectorError() {
  const error = useSettings((state) => state.error);
  const clearError = useSettings((state) => state.clearError);
  return error ? (
    <div
      className="mb-4 flex items-center gap-3 rounded-lg border border-destructive p-3 text-xs text-destructive"
      role="alert"
    >
      <span className="flex-1">{error}</span>
      <Button size="xs" variant="ghost" aria-label="Dismiss connector error" onClick={clearError}>
        <X />
      </Button>
    </div>
  ) : null;
}
/** What a connector is reached through, in the words someone adding one would use. */
const TRANSPORTS = {
  stdio: "Runs on this Mac",
  http: "A URL",
  sse: "A URL (SSE)",
} as const;
function summary(connector: Connector): string {
  return connector.transport === "stdio"
    ? `${connector.command} ${connector.args.join(" ")}`.trim()
    : connector.url;
}
/**
 * A row editor for the two places a server takes name/value pairs: a stdio server's environment
 * and a URL server's headers. Both hold secrets, and both accept `$NAME` to read one from the
 * key store instead of writing it into settings.
 */
function PairEditor({
  label,
  hint,
  pairs,
  onChange,
}: {
  label: string;
  hint: string;
  pairs: Array<[string, string]>;
  onChange: (pairs: Array<[string, string]>) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-2xs uppercase tracking-[0.07em] text-muted-foreground">{label}</span>
        <Button type="button" size="xs" variant="ghost" onClick={() => onChange([...pairs, ["", ""]])}>
          <Plus /> Add
        </Button>
      </div>
      {pairs.map(([key, value], index) => (
        <div key={index} className="flex gap-2">
          <Input
            value={key}
            onChange={(event) =>
              onChange(pairs.map((p, i) => (i === index ? [event.target.value, p[1]] : p)))
            }
            placeholder="Name"
            aria-label={`${label} name ${index + 1}`}
            className="h-9 flex-1 bg-background font-mono text-xs dark:bg-background"
          />
          <Input
            value={value}
            onChange={(event) =>
              onChange(pairs.map((p, i) => (i === index ? [p[0], event.target.value] : p)))
            }
            placeholder="Value, or $SECRET_NAME"
            aria-label={`${label} value ${index + 1}`}
            className="h-9 flex-1 bg-background font-mono text-xs dark:bg-background"
          />
          <Button
            type="button"
            size="xs"
            variant="ghost"
            aria-label={`Remove ${label} row ${index + 1}`}
            onClick={() => onChange(pairs.filter((_, i) => i !== index))}
          >
            <X />
          </Button>
        </div>
      ))}
      <p className="text-2xs text-muted-foreground">{hint}</p>
    </div>
  );
}
/** The transport-specific fields, shared by the add form and the detail panel. */
function ConnectorFields({ draft, setDraft }: { draft: Draft; setDraft: (draft: Draft) => void }) {
  return (
    <div className="space-y-4">
      <div className="flex gap-1">
        {(Object.keys(TRANSPORTS) as Array<keyof typeof TRANSPORTS>).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setDraft({ ...draft, transport: option })}
            className={cn(
              "rounded-md px-2.5 py-1 text-2xs transition-colors",
              draft.transport === option
                ? "bg-accent font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {TRANSPORTS[option]}
          </button>
        ))}
      </div>

      {draft.transport === "stdio" ? (
        <>
          <Input
            value={draft.command}
            onChange={(event) => setDraft({ ...draft, command: event.target.value })}
            placeholder="Command, e.g. npx"
            aria-label="Command"
            className="h-9 bg-background font-mono text-xs dark:bg-background"
          />
          <Input
            value={draft.args}
            onChange={(event) => setDraft({ ...draft, args: event.target.value })}
            placeholder="Arguments, space separated"
            aria-label="Arguments"
            className="h-9 bg-background font-mono text-xs dark:bg-background"
          />
          {/* How every credentialed stdio server takes its token — on the command line it would
                show up in `ps`, so servers read it from the environment instead. */}
          <PairEditor
            label="Environment"
            hint="Most servers take their token here, e.g. GITHUB_PERSONAL_ACCESS_TOKEN."
            pairs={draft.env}
            onChange={(env) => setDraft({ ...draft, env })}
          />
          <Input
            value={draft.cwd}
            onChange={(event) => setDraft({ ...draft, cwd: event.target.value })}
            placeholder="Working directory (optional)"
            aria-label="Working directory"
            className="h-9 bg-background font-mono text-xs dark:bg-background"
          />
        </>
      ) : (
        <>
          <Input
            value={draft.url}
            onChange={(event) => setDraft({ ...draft, url: event.target.value })}
            placeholder="https://…"
            aria-label="Server URL"
            className="h-9 bg-background font-mono text-xs dark:bg-background"
          />
          <div className="flex gap-1">
            {(["none", "oauth"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setDraft({ ...draft, auth: option })}
                className={cn(
                  "rounded-md px-2.5 py-1 text-2xs transition-colors",
                  draft.auth === option
                    ? "bg-accent font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {option === "none" ? "A key or no auth" : "Sign in (OAuth)"}
              </button>
            ))}
          </div>
          {draft.auth === "oauth" ? (
            <p className="text-2xs text-muted-foreground">
              Connect, then press Sign in. A browser opens, you approve, and the connector keeps the token.
            </p>
          ) : (
            <PairEditor
              label="Headers"
              hint="Write $NAME to read the value from a saved key instead of storing it here."
              pairs={draft.headers}
              onChange={(headers) => setDraft({ ...draft, headers })}
            />
          )}
        </>
      )}
    </div>
  );
}
interface Draft {
  name: string;
  transport: "stdio" | "http" | "sse";
  command: string;
  args: string;
  env: Array<[string, string]>;
  cwd: string;
  url: string;
  auth: Connector["auth"];
  headers: Array<[string, string]>;
}
function draftFrom(connector: Connector): Draft {
  return {
    name: connector.name,
    transport: connector.transport,
    command: connector.command,
    args: connector.args.join(" "),
    env: Object.entries(connector.env ?? {}),
    cwd: connector.cwd ?? "",
    url: connector.url,
    auth: connector.auth,
    headers: Object.entries(connector.headers ?? {}),
  };
}
function pairsToObject(pairs: Array<[string, string]>): Record<string, string> {
  return Object.fromEntries(pairs.filter(([key]) => key.trim()).map(([k, v]) => [k.trim(), v]));
}
function draftToServer(draft: Draft): Record<string, unknown> {
  const stdio = draft.transport === "stdio";
  return {
    name: draft.name.trim(),
    transport: draft.transport,
    command: stdio ? draft.command.trim() : "",
    args: stdio
      ? draft.args
          .split(" ")
          .map((a) => a.trim())
          .filter(Boolean)
      : [],
    env: stdio ? pairsToObject(draft.env) : {},
    cwd: stdio ? draft.cwd.trim() : "",
    url: stdio ? "" : draft.url.trim(),
    auth: stdio ? "none" : draft.auth,
    headers: !stdio && draft.auth === "none" ? pairsToObject(draft.headers) : {},
  };
}
/**
 * Adding a connector: one address, then whatever that particular server turns out to need.
 *
 * Someone adding a connector knows a URL. Whether it speaks streamable HTTP or SSE, what its
 * path is, and whether it wants a token or a browser round trip are all things the server itself
 * will say — so they are asked, not put to the user as settings to go and look up. The modal
 * only closes on a connector that is connected.
 */
type Stage = "address" | "auth" | "local" | "done";
export function AddConnector({
  open,
  onDone,
  initialName = "",
}: {
  open: boolean;
  onDone: () => void;
  initialName?: string;
}) {
  const probeConnector = useSettings((state) => state.probeConnector);
  const addConnector = useSettings((state) => state.addConnector);
  const updateConnector = useSettings((state) => state.updateConnector);
  const signInConnector = useSettings((state) => state.signInConnector);
  const setConnectorSecret = useSettings((state) => state.setConnectorSecret);
  const action = useSettings((state) => state.action);
  const [stage, setStage] = useState<Stage>("address");
  const [name, setName] = useState(initialName);
  const [address, setAddress] = useState("");
  const [probe, setProbe] = useState<ConnectorProbe | null>(null);
  const [token, setToken] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState("");
  const [connected, setConnected] = useState<Connector | null>(null);
  // The connector this dialog saved. Trying again after a failed connect changes it: adding the
  // same name a second time is refused.
  const [saved, setSaved] = useState("");
  async function save(server: Record<string, unknown> & { name: string }) {
    if (saved === server.name) {
      await updateConnector(server.name, server);
    } else {
      await addConnector(server);
      setSaved(server.name);
    }
  }
  // Local servers are the other half of MCP — Playwright and the filesystem server have no URL
  // to type — so the door stays open, one click off the main path rather than beside it.
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  /** On to "Connected" only when it did connect. A connector saved but not connected (a bad
   * command or token, a sign-in refused or left unfinished) stays on this step with its error. */
  const settle = (saved: string, failed: string) => {
    // Read through, not from the render this closure was made in: the list was refreshed by the
    // action that just ran, and the captured copy is the one from before it.
    const found = useSettings.getState().connectors.find((c) => c.name === saved);
    if (!found?.connected) {
      setProblem(found?.error ? `${failed} ${found.error}` : failed);
      return;
    }
    setConnected(found);
    setStage("done");
  };
  /** The name as it will be saved: what they typed, in the shape a tool name can use. */
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!slug || !address.trim()) return;
    setProblem("");
    setBusy("Asking the server what it needs…");
    try {
      const found = await probeConnector(address);
      setProbe(found);
      setStage("auth");
      // Nothing to ask for, so do not stop to ask: connect and show what turned up.
      if (found.auth === "none") {
        setBusy("Connecting…");
        await save({
          name: slug,
          transport: found.transport,
          url: found.url,
          auth: "none",
        });
        settle(slug, "That did not connect.");
      }
    } catch {
      setProblem("That address could not be checked. Try again, or connect anyway below.");
      setStage("auth");
    } finally {
      setBusy("");
    }
  }
  /** Save with the auth the user settled on. `manual` is the connect-anyway path. */
  async function connect(auth: "none" | "oauth" | "token") {
    if (!probe && auth !== "none") return;
    setProblem("");
    setBusy("Connecting…");
    const url = probe?.url || address.trim();
    const transport = probe?.transport ?? "http";
    try {
      let headers: Record<string, string> = {};
      if (auth === "token") {
        const base = slug.replace(/[^a-zA-Z0-9]+/g, "_").toUpperCase();
        // A `$NAME` starts with a letter; `1password` would otherwise never be read back.
        const key = `${/^[A-Z]/.test(base) ? "" : "MCP_"}${base}_TOKEN`;
        // The token goes to the key store first, so what lands in settings is the reference.
        await setConnectorSecret(key, token.trim());
        headers = { Authorization: `Bearer $${key}` };
      }
      await save({
        name: slug,
        transport,
        url,
        auth: auth === "oauth" ? "oauth" : "none",
        headers,
      });
      if (auth === "oauth") {
        setBusy("Finish signing in in your browser…");
        await signInConnector(slug);
      }
      settle(
        slug,
        auth === "oauth"
          ? "The sign-in did not finish."
          : "That did not connect. Check the address and the token.",
      );
    } catch {
      setProblem(
        auth === "oauth"
          ? "The sign-in did not finish."
          : "That did not connect. Check the address and the token.",
      );
    } finally {
      setBusy("");
    }
  }
  async function addLocal(event: FormEvent) {
    event.preventDefault();
    if (!command.trim() || !slug) return;
    setProblem("");
    setBusy("Starting it…");
    try {
      await save({
        name: slug,
        transport: "stdio",
        command: command.trim(),
        args: args
          .split(" ")
          .map((a) => a.trim())
          .filter(Boolean),
      });
      settle(slug, "That command did not start a server.");
    } catch {
      setProblem("That command did not start a server.");
    } finally {
      setBusy("");
    }
  }
  const working = Boolean(busy) || Boolean(action);
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{stage === "done" ? "Connected" : "Custom connector"}</DialogTitle>
          <DialogDescription>
            {stage === "address"
              ? "An MCP server brings tools the agent can use. Name it and give its address."
              : stage === "local"
                ? "A server that runs on this Mac, started by a command."
                : stage === "done"
                  ? `${connected?.name ?? "The server"} is ready for Wavefunction to use.`
                  : probe?.url || address}
          </DialogDescription>
        </DialogHeader>

        {stage === "address" ? (
          <form onSubmit={ask} className="space-y-4">
            <Input
              value={name}
              readOnly={Boolean(initialName)}
              onChange={(event) => setName(event.target.value)}
              placeholder="Name, e.g. agentcouch"
              aria-label="Connector name"
              className="h-10 bg-background text-sm dark:bg-background"
              autoFocus
            />
            <Input
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              placeholder="mcp.agentcouch.com"
              aria-label="Server address"
              className="h-10 bg-background font-mono text-xs dark:bg-background"
            />
            <p className="text-2xs text-muted-foreground">
              The address is used as given. If nothing answers there we will look nearby and tell you what we
              found.
            </p>
            {problem ? <Problem text={problem} /> : null}
            <div className="flex items-center gap-2">
              <Button type="submit" size="sm" disabled={!slug || !address.trim() || working}>
                {working ? <LoaderCircle className="animate-spin" /> : null}
                {busy || "Continue"}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={onDone}>
                Cancel
              </Button>
              <button
                type="button"
                onClick={() => setStage("local")}
                className="ml-auto text-2xs text-muted-foreground underline-offset-2 hover:underline"
              >
                Run a local server instead
              </button>
            </div>
          </form>
        ) : null}

        {stage === "auth" ? (
          <div className="space-y-4">
            {/* Never swap the typed address silently: say what answered instead. */}
            {probe?.guessed ? (
              <p className="text-2xs text-muted-foreground">
                Nothing answered at <span className="font-mono">{probe.typed_url}</span>, so this is{" "}
                <span className="font-mono">{probe.url}</span>.
              </p>
            ) : null}

            {probe?.auth === "oauth" ? (
              <>
                <div className="flex items-start gap-3 rounded-xl bg-card p-4">
                  <KeyRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <p className="text-xs text-muted-foreground">
                    This server wants you to sign in. A browser opens, you approve there, and the token comes
                    back here — there is nothing to paste.
                  </p>
                </div>
                <Button size="sm" onClick={() => void connect("oauth")} disabled={working}>
                  {working ? <LoaderCircle className="animate-spin" /> : <LogIn />}
                  {busy || "Sign in"}
                </Button>
              </>
            ) : null}

            {probe?.auth === "token" ? (
              <>
                <div className="flex items-start gap-3 rounded-xl bg-card p-4">
                  <KeyRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <p className="text-xs text-muted-foreground">
                    This server wants an access token. Paste one from its settings page — it is kept in your
                    key store, not in the connector.
                  </p>
                </div>
                <Input
                  type="password"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder="Access token"
                  aria-label="Access token"
                  className="h-10 bg-background font-mono text-xs dark:bg-background"
                  autoFocus
                />
                <Button size="sm" onClick={() => void connect("token")} disabled={!token.trim() || working}>
                  {working ? <LoaderCircle className="animate-spin" /> : null}
                  {busy || "Connect"}
                </Button>
              </>
            ) : null}

            {/* Detection is a helper, not a gate. A server we could not read is still a server
                the user may know how to reach, so every option stays available here. */}
            {probe?.auth === "unreachable" || !probe ? (
              <>
                <Problem
                  text={`Nothing answered at ${probe?.typed_url ?? address}. ${probe?.detail ?? ""} You can still connect if you know what it needs.`}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void connect("none")}
                    disabled={working}
                  >
                    Connect anyway
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void connect("oauth")}
                    disabled={working}
                  >
                    <LogIn /> Sign in instead
                  </Button>
                </div>
                <Input
                  type="password"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder="…or paste an access token"
                  aria-label="Access token"
                  className="h-10 bg-background font-mono text-xs dark:bg-background"
                />
                {token.trim() ? (
                  <Button size="sm" onClick={() => void connect("token")} disabled={working}>
                    Connect with this token
                  </Button>
                ) : null}
              </>
            ) : null}

            {problem ? <Problem text={problem} /> : null}
            <Button size="sm" variant="ghost" onClick={() => setStage("address")}>
              Back
            </Button>
          </div>
        ) : null}

        {stage === "local" ? (
          <form onSubmit={addLocal} className="space-y-3">
            <Input
              value={name}
              readOnly={Boolean(initialName)}
              onChange={(event) => setName(event.target.value)}
              placeholder="Name, e.g. playwright"
              aria-label="Connector name"
              className="h-9 bg-background text-sm dark:bg-background"
              autoFocus
            />
            <Input
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              placeholder="Command, e.g. npx"
              aria-label="Command"
              className="h-9 bg-background font-mono text-xs dark:bg-background"
            />
            <Input
              value={args}
              onChange={(event) => setArgs(event.target.value)}
              placeholder="Arguments, e.g. -y @playwright/mcp@latest"
              aria-label="Arguments"
              className="h-9 bg-background font-mono text-xs dark:bg-background"
            />
            {problem ? <Problem text={problem} /> : null}
            <div className="flex items-center gap-2">
              <Button type="submit" size="sm" disabled={!command.trim() || !slug || working}>
                {working ? <LoaderCircle className="animate-spin" /> : null}
                {busy || "Connect"}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setStage("address")}>
                Back
              </Button>
            </div>
          </form>
        ) : null}

        {stage === "done" ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 rounded-xl bg-card px-4 py-3">
              <span className="size-1.5 shrink-0 rounded-full bg-emerald-500" />
              <p className="text-xs">
                {connected?.tools.length ?? 0} {connected?.tools.length === 1 ? "tool" : "tools"}{" "}
                {"available to Wavefunction on your next task."}
              </p>
            </div>
            {connected?.tools.length ? (
              <div className="flex flex-wrap gap-1.5">
                {connected.tools.map((tool) => (
                  <span
                    key={tool.name}
                    className="rounded-md bg-background px-2 py-1 font-mono text-2xs text-muted-foreground"
                  >
                    {tool.name}
                  </span>
                ))}
              </div>
            ) : null}
            <Button size="sm" onClick={onDone}>
              Done
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
function Problem({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2">
      <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" />
      <p className="text-2xs text-muted-foreground">{text}</p>
    </div>
  );
}
function StatusDot({ connector }: { connector: Connector }) {
  return (
    <span
      aria-label={
        connector.checking ? "Checking connection" : connector.connected ? "Connected" : "Not connected"
      }
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        connector.checking
          ? "bg-muted-foreground animate-pulse"
          : connector.connected
            ? "bg-emerald-500"
            : "bg-destructive",
      )}
    />
  );
}
function microsoftAccount(connector: Connector) {
  if (connector.env?.WAVEFUNCTION_LOCAL_INTEGRATION !== "microsoft") return null;
  const accountIndex = connector.args.indexOf("--account");
  const modeIndex = connector.args.indexOf("--mode");
  if (accountIndex < 0 || modeIndex < 0) return null;
  const mode = connector.args[modeIndex + 1] === "mail" ? ("mail" as const) : ("workspace" as const);
  return {
    email: connector.args[accountIndex + 1],
    mode,
    services: connector.args.includes("--services")
      ? connector.args[connector.args.indexOf("--services") + 1].split(",")
      : mode === "mail"
        ? ["mail"]
        : ["mail", "files", "calendar"],
    title: mode === "mail" ? "Outlook" : "Microsoft 365",
  };
}
function ConnectorCard({ connector, onOpen }: { connector: Connector; onOpen: () => void }) {
  const microsoft = microsoftAccount(connector);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full flex-col gap-3 rounded-xl bg-card p-4 text-left shadow-xs transition-colors hover:bg-accent/40"
    >
      <div className="flex items-start gap-2.5">
        <ConnectorLogo id={connector.name} />
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 truncate text-sm font-medium">
            {microsoft?.title ?? connector.name}
            <StatusDot connector={connector} />
          </p>
          <p className="mt-0.5 truncate font-mono text-2xs text-muted-foreground">
            {microsoft?.email ?? summary(connector)}
          </p>
        </div>
      </div>

      <div>
        <LoginMethodTag>
          {microsoft
            ? "OAuth"
            : connector.name.startsWith("mail-")
              ? "Password / app password"
              : connector.name.startsWith("google-")
                ? "OAuth"
                : connector.auth !== "none"
                  ? loginLabel(connector.auth)
                  : connector.secrets.length
                    ? "API key"
                    : connector.transport === "stdio"
                      ? "Local setup"
                      : "No login"}
        </LoginMethodTag>
      </div>

      <div className="flex items-center gap-2 text-2xs text-muted-foreground">
        {connector.checking ? (
          <>
            <LoaderCircle className="size-3 animate-spin" /> Checking connection…
          </>
        ) : connector.connected ? (
          <>
            <Wrench className="size-3" />
            {connector.tools.length} {connector.tools.length === 1 ? "tool" : "tools"}
          </>
        ) : (
          <>
            <CircleAlert className="size-3 text-destructive" />
            <span className="truncate">{connector.error || "Not connected"}</span>
          </>
        )}
      </div>
    </button>
  );
}
/**
 * The values behind the `$NAME`s this connector's settings refer to.
 *
 * Written here rather than into the settings above, so the token lands in the credentials file
 * instead of the connector list. Once set, the value is never read back — there is nothing to
 * show but whether it is there.
 */
function ConnectorSecrets({ connector }: { connector: Connector }) {
  const setConnectorSecret = useSettings((state) => state.setConnectorSecret);
  const clearConnectorSecret = useSettings((state) => state.clearConnectorSecret);
  const action = useSettings((state) => state.action);
  const [values, setValues] = useState<Record<string, string>>({});
  return (
    <div className="space-y-3 rounded-xl bg-card p-4 shadow-xs">
      <p className="font-mono text-2xs uppercase tracking-[0.07em] text-muted-foreground">
        Keys this connector asks for
      </p>
      {connector.secrets.map((secret) => (
        <div key={secret.name} className="flex items-center gap-2">
          <KeyRound
            className={cn("size-3.5 shrink-0", secret.set ? "text-emerald-500" : "text-muted-foreground")}
          />
          <span className="w-48 shrink-0 truncate font-mono text-2xs">${secret.name}</span>
          {secret.set ? (
            <>
              <span className="flex-1 text-2xs text-muted-foreground">Saved</span>
              <Button
                size="xs"
                variant="ghost"
                disabled={Boolean(action)}
                onClick={() => void clearConnectorSecret(secret.name)}
              >
                Clear
              </Button>
            </>
          ) : (
            <>
              <Input
                type="password"
                value={values[secret.name] ?? ""}
                onChange={(event) => setValues({ ...values, [secret.name]: event.target.value })}
                placeholder="Paste the value"
                aria-label={`Value for $${secret.name}`}
                className="h-8 flex-1 bg-background font-mono text-xs dark:bg-background"
              />
              <Button
                size="xs"
                variant="secondary"
                disabled={!values[secret.name]?.trim() || Boolean(action)}
                onClick={async () => {
                  try {
                    await setConnectorSecret(secret.name, values[secret.name]);
                    setValues({ ...values, [secret.name]: "" });
                  } catch {
                    // The persistent error banner carries the reason.
                  }
                }}
              >
                Save
              </Button>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
export function ConnectorDetail({ connector, onBack }: { connector: Connector; onBack: () => void }) {
  const microsoft = microsoftAccount(connector);
  const localAccount = localConnectorAccount(connector);
  // A catalog connector's settings are the catalog's: editing them could only break it, so the
  // settings form is for connectors added by hand, including one that only shares a catalog name.
  // Recognised as the catalog's own the way its catalog card does. Unknown until the catalog loads.
  const [custom, setCustom] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    void listConnectorCatalog()
      .then(
        (entries) =>
          alive &&
          setCustom(
            !entries.some(
              (entry) =>
                entry.id === connector.name &&
                entry.transport === "http" &&
                connector.transport === "http" &&
                (entry.setup !== undefined || connector.url === entry.url),
            ),
          ),
      )
      .catch(() => alive && setCustom(true));
    return () => {
      alive = false;
    };
  }, [connector.name, connector.transport, connector.url]);
  const [localSetupOpen, setLocalSetupOpen] = useState(false);
  const updateConnector = useSettings((state) => state.updateConnector);
  const refreshConnector = useSettings((state) => state.refreshConnector);
  const removeConnector = useSettings((state) => state.removeConnector);
  const signInConnector = useSettings((state) => state.signInConnector);
  const signOutConnector = useSettings((state) => state.signOutConnector);
  const action = useSettings((state) => state.action);
  // Serialised, so "has this changed" and "has the server changed under me" are the same
  // comparison. After a save the settings come back from the server and the draft follows them.
  const saved = useMemo(() => JSON.stringify(draftFrom(connector)), [connector]);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(connector));
  const [base, setBase] = useState(saved);
  if (base !== saved) {
    setBase(saved);
    setDraft(draftFrom(connector));
  }
  const changed = JSON.stringify(draft) !== saved;
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await updateConnector(connector.name, draftToServer(draft));
    } catch {
      // The persistent error banner carries the reason.
    }
  }
  return (
    <div className="space-y-6">
      <ConnectorError />
      <div className="flex items-start gap-3">
        <Button size="xs" variant="ghost" onClick={onBack} aria-label="Back to connectors">
          <ArrowLeft />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="flex items-center gap-2 text-lg font-medium">
            {microsoft?.title ?? connector.name}
            <StatusDot connector={connector} />
          </h1>
          <p className="mt-0.5 truncate font-mono text-2xs text-muted-foreground">
            {microsoft?.email ?? summary(connector)}
          </p>
        </div>
        <Button
          size="xs"
          variant="ghost"
          disabled={Boolean(action)}
          onClick={() => void refreshConnector(connector.name)}
        >
          {action === "connecting" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
          Test
        </Button>
      </div>

      {!connector.connected ? (
        <div className="flex items-start gap-2 rounded-xl bg-destructive/8 px-4 py-3">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" />
          <p className="text-2xs text-muted-foreground">
            {connector.error || "Not connected."} Its tools are unavailable until the connection is restored.
          </p>
        </div>
      ) : null}

      {localAccount ? (
        <div className="flex items-center gap-3 rounded-xl bg-card px-4 py-3 shadow-xs">
          <KeyRound className="size-4 shrink-0 text-muted-foreground" />
          <p className="flex-1 text-xs text-muted-foreground">
            This account is connected locally. Reconnect to renew sign-in or update its permissions.
          </p>
          <Button
            size="xs"
            variant="secondary"
            disabled={Boolean(action) || !connector.enabled}
            onClick={() => setLocalSetupOpen(true)}
          >
            <LogIn />
            Reconnect
          </Button>
        </div>
      ) : null}
      {localSetupOpen && microsoft ? (
        <MicrosoftSetup
          close={() => setLocalSetupOpen(false)}
          initialAccount={microsoft.email}
          initialServices={microsoft.services}
          mode={microsoft.mode}
          initialAllowWrite={connector.args.includes("--allow-write")}
          onConnected={async (name) => {
            if (name !== connector.name)
              throw new Error("Choose the Microsoft account this connector already uses.");
          }}
        />
      ) : null}

      {localSetupOpen && localAccount && !microsoft ? (
        connector.env.WAVEFUNCTION_LOCAL_INTEGRATION === "gog" ? (
          <GoogleAccountSetup
            close={() => setLocalSetupOpen(false)}
            initialAccount={localAccount.email}
            initialAllowWrite={connector.args.includes("--allow-write")}
            services={
              connector.args.includes("--allow-tool")
                ? connector.args[connector.args.indexOf("--allow-tool") + 1].split(",")
                : ["drive", "docs", "sheets", "calendar"]
            }
            onConnected={async (name) => {
              if (name !== connector.name)
                throw new Error("Choose the Google account this connector already uses.");
            }}
          />
        ) : (
          <EmailSetup
            close={() => setLocalSetupOpen(false)}
            initialAccount={localAccount.email}
            initialProvider={localAccount.provider === "google" ? "gmail" : "other"}
            initialAllowWrite={connector.args.includes("--allow-write")}
            onConnected={async (name) => {
              if (name !== connector.name) throw new Error("Choose the mailbox this connector already uses.");
            }}
          />
        )
      ) : null}

      {connector.auth === "oauth" ? (
        <div className="flex items-center gap-3 rounded-xl bg-card px-4 py-3 shadow-xs">
          <KeyRound className="size-4 shrink-0 text-muted-foreground" />
          <p className="flex-1 text-xs text-muted-foreground">
            {connector.connected
              ? "Signed in. The token is kept in your key store."
              : "This server wants you to sign in. A browser opens and you approve there."}
          </p>
          <Button
            size="xs"
            variant={connector.connected ? "ghost" : "secondary"}
            disabled={Boolean(action)}
            onClick={() =>
              void (connector.connected ? signOutConnector(connector.name) : signInConnector(connector.name))
            }
          >
            {action === "signing-in" ? (
              <LoaderCircle className="animate-spin" />
            ) : connector.connected ? (
              <LogOut />
            ) : (
              <LogIn />
            )}
            {connector.connected ? "Sign out" : "Sign in"}
          </Button>
        </div>
      ) : null}

      {connector.secrets?.length ? <ConnectorSecrets connector={connector} /> : null}

      {custom && !localAccount ? (
        <form onSubmit={save} className="space-y-4 rounded-xl bg-card p-4 shadow-xs">
          <p className="font-mono text-2xs uppercase tracking-[0.07em] text-muted-foreground">Settings</p>
          <ConnectorFields draft={draft} setDraft={setDraft} />
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={!changed || Boolean(action)}>
              {action === "connecting" ? <LoaderCircle className="animate-spin" /> : null}
              Save and test
            </Button>
            {changed ? (
              <Button type="button" size="sm" variant="ghost" onClick={() => setDraft(draftFrom(connector))}>
                Discard
              </Button>
            ) : null}
          </div>
        </form>
      ) : null}

      {connector.connected ? (
        <div className="rounded-xl bg-card p-4 shadow-xs">
          <p className="mb-3 font-mono text-2xs uppercase tracking-[0.07em] text-muted-foreground">
            {connector.tools.length} {connector.tools.length === 1 ? "tool" : "tools"}{" "}
            {"available to Wavefunction"}
          </p>
          <div className="space-y-2">
            {connector.tools.map((tool) => (
              <div key={tool.name} className="rounded-lg bg-background px-3 py-2">
                <p className="font-mono text-2xs">
                  {connector.name}__{tool.name}
                </p>
                {tool.description ? (
                  <p className="mt-1 text-2xs text-muted-foreground">{tool.description}</p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex items-center gap-3 rounded-xl bg-card px-4 py-3 shadow-xs">
        <p className="flex-1 text-2xs text-muted-foreground">
          {"Removing this connector makes its tools unavailable to future tasks."}
        </p>
        <Button
          size="xs"
          variant="ghost"
          className="text-destructive"
          disabled={Boolean(action)}
          onClick={async () => {
            try {
              await removeConnector(connector.name);
              onBack();
            } catch {
              // Keep this connector open so its error is visible and removal can be retried.
            }
          }}
        >
          <Trash2 /> Remove
        </Button>
      </div>
    </div>
  );
}
export function ConnectorsView() {
  const connectors = useSettings((state) => state.connectors);
  const loadConnectors = useSettings((state) => state.loadConnectors);
  const action = useSettings((state) => state.action);
  const clearError = useSettings((state) => state.clearError);
  const [adding, setAdding] = useState(false);
  const [openName, setOpenName] = useState<string | null>(null);
  useEffect(() => {
    clearError();
    void loadConnectors();
  }, [clearError, loadConnectors]);
  useEffect(() => {
    if (!connectors.some((connector) => connector.checking)) return;
    const timer = window.setTimeout(() => void loadConnectors(), 1000);
    return () => window.clearTimeout(timer);
  }, [connectors, loadConnectors]);
  const opened = connectors.find((connector) => connector.name === openName) ?? null;
  const accountGroups = localAccountGroups(connectors);
  const otherConnectors = connectors.filter((connector) => !localConnectorAccount(connector));
  return (
    <ScrollArea className="connectors-page h-full min-h-0 bg-background">
      <PageContent>
        {opened ? (
          <ConnectorDetail key={opened.name} connector={opened} onBack={() => setOpenName(null)} />
        ) : (
          <>
            <ConnectorError />
            <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
              <h2 className="text-lg font-medium">Installed</h2>
              <Button size="sm" onClick={() => setAdding(true)} disabled={Boolean(action)}>
                <Plus /> Add custom connector
              </Button>
            </div>
            {connectors.length ? (
              <div className="grid gap-3 sm:grid-cols-2">
                {accountGroups.map((group) => (
                  <div
                    key={`${group.product}:${group.email.toLowerCase()}`}
                    className="rounded-xl bg-card p-4 shadow-xs"
                  >
                    <div className="mb-3 flex items-center gap-3">
                      <ConnectorLogo id={localConnectorProducts[group.product].logo} />
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{localConnectorProducts[group.product].title}</p>
                        <p className="truncate text-xs text-muted-foreground">{group.email}</p>
                      </div>
                    </div>
                    <div className="mb-3 flex flex-wrap gap-1.5">
                      {group.services.map((service) => (
                        <span key={service} className="rounded-md bg-primary/10 px-2 py-1 text-2xs">
                          {service}
                        </span>
                      ))}
                    </div>
                    <div className="space-y-1">
                      {group.connectors.map((connector) => (
                        <button
                          key={connector.name}
                          aria-label={`${connector.connected ? "Manage" : "Reconnect"} ${localConnectorAccount(connector)?.services.join(" and ")} for ${group.email}`}
                          onClick={() => setOpenName(connector.name)}
                          className="flex w-full items-center gap-2 rounded-lg p-2 text-left text-xs hover:bg-muted"
                        >
                          <StatusDot connector={connector} />
                          <span>{localConnectorAccount(connector)?.services.join(" · ")}</span>
                          <span className="ml-auto text-muted-foreground">
                            {connector.checking ? "Checking…" : connector.connected ? "Manage" : "Reconnect"}{" "}
                            →
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
                {otherConnectors.map((connector) => (
                  <ConnectorCard
                    key={connector.name}
                    connector={connector}
                    onOpen={() => setOpenName(connector.name)}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-hairline p-4 text-center">
                <Plug className="mx-auto mb-3 size-5 text-muted-foreground" />
                <p className="text-sm font-medium">Nothing connected yet</p>
                <p className="mx-auto mt-2 max-w-md text-xs text-muted-foreground">
                  Choose an app from the catalog below, or add a custom MCP server.
                </p>
              </div>
            )}
            <ConnectorCatalog onOpen={setOpenName} />
          </>
        )}

        {/* Keyed so each open starts from a blank form rather than resetting one in an effect. */}
        <AddConnector key={String(adding)} open={adding} onDone={() => setAdding(false)} />
      </PageContent>
    </ScrollArea>
  );
}
