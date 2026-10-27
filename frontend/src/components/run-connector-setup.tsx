import { Check, Folder, LoaderCircle, Monitor, Plug, RefreshCw, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { ConnectorLogo } from "@/components/connector-brand";
import { ConnectorCatalog } from "@/components/connector-catalog";
import { GitHubSignIn } from "@/components/github-setup";
import { RestartDockerDialog } from "@/components/restart-docker-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, cancelCatalogConnection, connectCatalogEntry, listConnectorCatalog } from "@/lib/api";
import { pendingRunConnectors } from "@/lib/pending-run-connectors";
import type { RunConnector, RunEvent } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { AddConnector, ConnectorDetail } from "@/views/connectors-view";

/** The folder card, shown when the agent asks which folder it works in. The card makes the
    change itself, with the same device request Settings › Access uses. */
const repository = {
  title: "Repository folder",
  icon: Folder,
  description:
    "The agent asked which folder to work in. Choose it in the folder picker; the agent can change files there and works there again next time.",
};

/** And when it asks for a folder a task needs, such as one to save a file to. */
const agentFolder = {
  title: "Folder access",
  icon: Folder,
  description:
    "The agent asked for access to a folder. Choose it in the folder picker; you can remove it later in Settings › Access.",
};

function settingFor(row: RunConnector) {
  if (row.id === "repository-folder") return repository;
  if (row.local_kind === "folder") return agentFolder;
  return undefined;
}

const labels: Record<RunConnector["state"], string> = {
  ready: "Ready",
  setup: "Setup needed",
  sign_in: "Sign-in required",
  connecting: "Checking connection…",
  error: "Connection failed",
  disabled: "Disabled",
};

export function RunConnectorSetup({ runId, events }: { runId: string; events: RunEvent[] }) {
  const initial = pendingRunConnectors(events);
  return initial ? <ConnectorSetup key={runId} id={runId} initial={initial} /> : null;
}

function ConnectorSetup({ id, initial }: { id: string; initial: RunConnector[] }) {
  const [rows, setRows] = useState(initial);
  const [setupId, setSetupId] = useState<string | null>(null);
  const [customName, setCustomName] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [restarting, setRestarting] = useState<RunConnector | null>(null);
  const [error, setError] = useState("");
  const [finished, setFinished] = useState(false);
  const connectors = useSettings((s) => s.connectors);
  const loadConnectors = useSettings((s) => s.loadConnectors);
  const custom = connectors.find((c) => c.name === customName);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api.runConnectorSetup(id);
        if (!alive) return;
        if (result.status !== "waiting_connector") {
          setFinished(true);
          return;
        } else setRows(result.connectors);
      } catch {
        if (alive)
          setError("Could not check the connection. Setup is saved and will resume when the app reconnects.");
      }
      if (alive) timer = setTimeout(() => void poll(), 1500);
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id]);

  async function setup(row: RunConnector) {
    setError("");
    setBusy(row.id);
    try {
      if (row.catalog_id && row.state !== "disabled") {
        const entry = (await listConnectorCatalog()).find((entry) => entry.id === row.catalog_id);
        if (entry && !entry.setup && !entry.local && entry.id !== "n8n" && row.state !== "error") {
          await connectCatalogEntry(entry.id);
          setRows((old) =>
            old.map((item) =>
              item.id === row.id
                ? {
                    ...item,
                    state: "connecting",
                    signing_in: row.auth === "oauth",
                    error: "",
                  }
                : item,
            ),
          );
        } else setSetupId(row.catalog_id);
      } else {
        await loadConnectors();
        setCustomName(row.id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start connector setup.");
    } finally {
      setBusy(null);
    }
  }

  /** Allow the folder a row asks for. The next check reads the grants themselves. */
  /** gh is signed in: the task carries on once the app has seen it too. */
  async function signedIn() {
    await api.githubSignedIn(id);
    setRows((old) =>
      old.map((item) => (item.local_kind === "github" ? { ...item, state: "connecting" } : item)),
    );
  }

  async function chooseFolder(row: RunConnector) {
    setBusy(row.id);
    setError("");
    try {
      const workspace = useSettings.getState();
      // The system picker, as in Settings: macOS attributes the choice to a real user action.
      const { open } = await import("@tauri-apps/plugin-dialog");
      const chosen = await open({
        directory: true,
        multiple: false,
        defaultPath: row.path || undefined,
      });
      if (typeof chosen !== "string") return;
      // Never less than the folder has: saving a read request over a writable folder made it
      // read-only for every chat that writes there.
      if (!workspace.grants) await workspace.loadGrants();
      const existing = useSettings.getState().grants?.grants.find((grant) => grant.path === chosen);
      await workspace.addGrant(chosen, Boolean(row.writable) || Boolean(existing?.writable));
      // The folder picked answers the card, whichever it named: waiting for the named one left a
      // task whose user picked another waiting for ever. So a folder that already had the access
      // completes it too, and the repository card is how the agent learns where it works. The
      // folder it named needs only the access, which can carry the task on before it is told.
      if (chosen !== row.path) await api.chooseRunFolder(id, chosen, row.id);
      setRows((old) => old.map((item) => (item.id === row.id ? { ...item, state: "connecting" } : item)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change this setting. Try again.");
    } finally {
      setBusy(null);
    }
  }

  /** The steps Settings › Computer use offers, in order: start (or restart) Docker, then build
   * the desktop. */
  async function prepareComputer(row: RunConnector, docker: "start" | "restart" = "start") {
    setBusy(row.id);
    setError("");
    try {
      // Docker may have been started or the desktop built since: look again first.
      let status = await api.computerStatus(true);
      if (status.docker_desktop_available && (docker === "restart" || status.docker_problem))
        status = await api.manageDocker(docker);
      // The backend refuses a build without a running Docker and says why.
      if (!status.image_ready) await api.buildComputerImage();
      setRows((old) =>
        old.map((item) =>
          item.id === row.id ? { ...item, state: "connecting", error: "", build_log: [] } : item,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not set up computer use.");
    } finally {
      setBusy(null);
    }
  }

  /** Look at Docker again, as Settings' Check again does; the card's poll shows what changed. */
  async function checkComputer(row: RunConnector) {
    setBusy(row.id);
    setError("");
    try {
      await api.computerStatus(true);
    } catch {
      setError("Could not check Docker.");
    } finally {
      setBusy(null);
    }
  }

  async function retry(connectorId: string) {
    setBusy(connectorId);
    setError("");
    try {
      await api.refreshConnector(connectorId);
      const result = await api.runConnectorSetup(id);
      if (result.status === "waiting_connector") setRows(result.connectors);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not retry the connection.");
    } finally {
      setBusy(null);
    }
  }

  const building = rows.length > 0 && rows.every((row) => row.local_kind === "computer");

  async function cancel() {
    setBusy("cancel");
    setError("");
    try {
      await api.cancelRun(id);
    } catch {
      setError("Could not cancel this task. Try again.");
    } finally {
      setBusy(null);
    }
  }

  // A setting on this Mac is named for itself: nothing is being connected.
  const asked = new Set(rows.map((row) => settingFor(row)));
  const only = asked.size === 1 ? [...asked][0] : undefined;
  const mixed = asked.size > 1 && !asked.has(undefined);

  function settingRow(row: RunConnector) {
    const Icon = settingFor(row)!.icon;
    // What is being allowed, in the status line where the other rows say where they stand.
    const status =
      row.state === "ready"
        ? labels.ready
        : row.path
          ? `${row.writable ? "Read and write" : "Read"} access to ${row.path}`
          : `Choose a folder for ${row.writable ? "read and write" : "read"} access`;
    return (
      <div key={row.id} className="flex items-start gap-3">
        <Icon className="mt-1 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{row.title}</p>
          <p role="status" className="mt-0.5 break-words text-xs text-muted-foreground">
            {status}
          </p>
          {row.error ? <p className="mt-1 break-words text-xs text-destructive">{row.error}</p> : null}
        </div>
        {row.state === "ready" ? (
          <Check aria-label={`${row.title} ready`} className="mt-1 size-4 text-success" />
        ) : row.state === "connecting" || busy === row.id ? (
          <LoaderCircle aria-label={`Checking ${row.title}`} className="mt-1 size-4 animate-spin" />
        ) : (
          <Button
            size="xs"
            variant="secondary"
            disabled={Boolean(busy)}
            onClick={() => void chooseFolder(row)}
          >
            Choose folder
          </Button>
        )}
      </div>
    );
  }

  if (finished) return null;

  return (
    <section aria-label="Agent connector setup" className="rounded-xl border border-hairline bg-card p-4">
      <p className="flex items-center gap-2 text-sm font-medium">
        <Plug className="size-4" />
        {building
          ? "Setting up computer use"
          : only
            ? only.title
            : mixed
              ? "Finish setting up"
              : "Finish connecting"}
      </p>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {building
          ? "This task carries on by itself once the desktop is ready."
          : only
            ? only.description
            : mixed
              ? "This task carries on by itself once these are set up."
              : "This task is waiting for its connections. Sign in or finish setup below, and it will continue automatically."}
      </p>
      <div className="mt-4 space-y-3">
        {rows.map((row) =>
          settingFor(row) ? (
            settingRow(row)
          ) : row.local_kind === "computer" ? (
            <div key={row.id} className="flex items-start gap-3">
              <Monitor className="mt-1 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{row.title}</p>
                <p role="status" className="mt-0.5 text-xs text-muted-foreground">
                  {row.state === "ready"
                    ? "Ready"
                    : row.state === "connecting" && row.docker_action === "restart"
                      ? "Restarting Docker…"
                      : row.state === "connecting" && (row.docker_action || row.docker_startable)
                        ? "Starting Docker…"
                        : row.state === "error" && row.build_log?.length && !row.docker_startable
                          ? "The build did not finish"
                          : row.state === "error" || row.state === "setup"
                            ? labels.setup
                            : "Building the desktop. This downloads a few hundred megabytes and takes a few minutes."}
                </p>
                {row.error ? <p className="mt-1 break-words text-xs text-destructive">{row.error}</p> : null}
                {/* Docker is stopped: what Settings › Computer use offers, here where the task waits. */}
                {(row.state === "error" || row.state === "setup") && row.docker_startable ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button size="xs" disabled={Boolean(busy)} onClick={() => void prepareComputer(row)}>
                      Start Docker
                    </Button>
                    <Button
                      size="xs"
                      variant="secondary"
                      disabled={Boolean(busy)}
                      onClick={() => setRestarting(row)}
                    >
                      <RotateCcw /> Restart Docker
                    </Button>
                    <Button
                      size="xs"
                      variant="secondary"
                      disabled={Boolean(busy)}
                      onClick={() => void checkComputer(row)}
                    >
                      <RefreshCw /> Check again
                    </Button>
                  </div>
                ) : null}
                {row.state !== "ready" && row.build_log?.length ? (
                  <pre
                    aria-label="Build output"
                    className="mt-2 max-h-32 overflow-auto rounded-lg bg-muted/60 p-2 font-mono text-2xs leading-4 text-muted-foreground"
                  >
                    {row.build_log.slice(-8).join("\n")}
                  </pre>
                ) : null}
              </div>
              {row.state === "ready" ? (
                <Check aria-label="Computer use ready" className="mt-1 size-4 text-success" />
              ) : busy === row.id ? (
                <LoaderCircle aria-label="Setting up computer use" className="mt-1 size-4 animate-spin" />
              ) : row.state === "error" || row.state === "setup" ? (
                // A stopped Docker's buttons sit under its message.
                row.docker_startable ? null : (
                  <Button
                    size="xs"
                    variant={!row.error ? "default" : "secondary"}
                    disabled={Boolean(busy)}
                    onClick={() => void prepareComputer(row)}
                  >
                    {row.build_log?.length ? (
                      <>
                        <RefreshCw /> Build again
                      </>
                    ) : row.error ? (
                      <>
                        <RefreshCw /> Check again
                      </>
                    ) : row.image_update ? (
                      "Update desktop"
                    ) : (
                      "Build the desktop"
                    )}
                  </Button>
                )
              ) : (
                <LoaderCircle aria-label="Building the desktop" className="mt-1 size-4 animate-spin" />
              )}
            </div>
          ) : row.local_kind === "github" && row.state !== "ready" ? (
            <div key={row.id} className="flex items-start gap-3">
              <ConnectorLogo id="github" className="mt-1 size-5" />
              <div className="min-w-0 flex-1">
                <p className="mb-0.5 text-sm font-medium">{row.title}</p>
                {/* Runs by itself; the one thing shown to the person is the code GitHub asks for. */}
                <GitHubSignIn start onSignedIn={signedIn} />
              </div>
            </div>
          ) : (
            <div key={row.id} className="flex items-start gap-3">
              <ConnectorLogo id={row.id} className="mt-1 size-5" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{row.title}</p>
                <p role="status" className="mt-0.5 text-xs text-muted-foreground">
                  {row.signing_in ? "Complete sign-in in your browser" : labels[row.state]}
                </p>
                {row.error ? <p className="mt-1 break-words text-xs text-destructive">{row.error}</p> : null}
              </div>
              {row.state === "ready" ? (
                <Check aria-label={`${row.title} ready`} className="mt-1 size-4 text-success" />
              ) : row.state === "connecting" || busy === row.id ? (
                <div className="flex items-center gap-2">
                  <LoaderCircle aria-label={`Checking ${row.title}`} className="size-4 animate-spin" />
                  {row.signing_in ? (
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={() => {
                        void cancelCatalogConnection(row.id).catch(() =>
                          setError("Could not cancel sign-in. Try again."),
                        );
                      }}
                    >
                      Cancel sign-in
                    </Button>
                  ) : null}
                </div>
              ) : (
                <div className="flex shrink-0 flex-wrap justify-end gap-1">
                  {row.state === "error" ? (
                    <Button
                      size="xs"
                      variant="secondary"
                      disabled={Boolean(busy)}
                      onClick={() => void retry(row.id)}
                    >
                      <RefreshCw />
                      Retry
                    </Button>
                  ) : null}
                  <Button
                    size="xs"
                    variant={row.state === "error" ? "ghost" : "secondary"}
                    disabled={Boolean(busy)}
                    onClick={() => void setup(row)}
                  >
                    {row.state === "sign_in"
                      ? `Sign in to ${row.title}`
                      : row.state === "setup"
                        ? `Connect ${row.title}`
                        : "Open setup"}
                  </Button>
                </div>
              )}
            </div>
          ),
        )}
      </div>
      {error ? (
        <p role="alert" className="mt-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <div className="mt-3 flex justify-end">
        <Button variant="ghost" size="xs" disabled={Boolean(busy)} onClick={() => void cancel()}>
          Cancel this task
        </Button>
      </div>
      <RestartDockerDialog
        open={Boolean(restarting)}
        onOpenChange={(open) => {
          if (!open) setRestarting(null);
        }}
        onRestart={() => {
          if (restarting) void prepareComputer(restarting, "restart");
          setRestarting(null);
        }}
      />
      {setupId ? (
        <ConnectorCatalog
          key={setupId}
          setupId={setupId}
          onClose={() => setSetupId(null)}
          onOpen={() => setSetupId(null)}
        />
      ) : null}
      {custom ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) setCustomName(null);
          }}
        >
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>Set up {custom.name}</DialogTitle>
              <DialogDescription>The task will continue after this connector is ready.</DialogDescription>
            </DialogHeader>
            <ConnectorDetail connector={custom} onBack={() => setCustomName(null)} />
          </DialogContent>
        </Dialog>
      ) : null}
      {customName && !custom ? (
        <AddConnector key={customName} initialName={customName} open onDone={() => setCustomName(null)} />
      ) : null}
    </section>
  );
}
