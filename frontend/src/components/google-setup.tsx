import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ExternalLink, FileJson, LoaderCircle } from "lucide-react";
import { ConnectorLogo } from "@/components/connector-brand";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  cancelGoogleSignIn,
  downloadedGoogleCredentials,
  googleSignInStatus,
  installLocalIntegration,
  startGoogleSignIn,
  connectLocalIntegration,
  inspectLocalIntegration,
  openExternal,
  type DownloadedCredentials,
  type LocalIntegrationStatus,
} from "@/lib/api";
import { useSettings } from "@/store/settings";

const choices = [
  { title: "Mail", services: ["gmail"] },
  { title: "Files", services: ["drive", "docs", "sheets"] },
  { title: "Calendar", services: ["calendar"] },
];
const apis: Record<string, string> = {
  gmail: "Gmail",
  drive: "Drive",
  docs: "Docs",
  sheets: "Sheets",
  calendar: "Calendar",
};
/** "Gmail, Drive and Calendar", for however many there are. */
const listed = (names: string[]) =>
  names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
const editScopes: Record<string, string> = {
  docs: "https://www.googleapis.com/auth/documents",
  sheets: "https://www.googleapis.com/auth/spreadsheets",
};

/** One step of the console setup: what to do, in a line, and the page it is done on. */
function Step({
  number,
  title,
  open,
  href,
  onOpen,
  children,
}: {
  number: number;
  title: string;
  open?: string;
  href?: string;
  onOpen?: (url: string) => void;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <span
        aria-hidden="true"
        className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-muted text-2xs font-medium tabular-nums"
      >
        {number}
      </span>
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">{title}</p>
          {open && href ? (
            <Button type="button" size="xs" variant="secondary" onClick={() => onOpen?.(href)}>
              <ExternalLink />
              {open}
            </Button>
          ) : null}
        </div>
        <div className="text-xs leading-5 text-muted-foreground">{children}</div>
      </div>
    </li>
  );
}

export function GoogleAccountSetup({
  close,
  services,
  initialAccount = "",
  initialAllowWrite = true,
  onConnected,
}: {
  close: () => void;
  services?: string[];
  initialAccount?: string;
  initialAllowWrite?: boolean;
  onConnected?: (name: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState(services ?? ["drive", "docs", "sheets", "calendar"]);
  const [email, setEmail] = useState(initialAccount);
  const [credentials, setCredentials] = useState("");
  const [chosenName, setChosenName] = useState("");
  // The file Google's console just saved. Offered so nobody has to find it in a file chooser.
  const [found, setFound] = useState<DownloadedCredentials | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [allowWrite, setAllowWrite] = useState(initialAllowWrite);
  const [googleStatus, setGoogleStatus] = useState<LocalIntegrationStatus | null>(null);
  const [signInAgain, setSignInAgain] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const autoConnect = useRef(false);
  const callbacks = useRef({ close, onConnected });
  useEffect(() => {
    callbacks.current = { close, onConnected };
  }, [close, onConnected]);
  const loadConnectors = useSettings((state) => state.loadConnectors);
  const workspaceServices = selected;
  const hasWorkspace = workspaceServices.length > 0;
  const hasFiles = workspaceServices.some((service) => ["drive", "docs", "sheets"].includes(service));
  const hasMail = selected.includes("gmail");
  const workspaceTitle = listed(
    choices.filter((choice) => selected.includes(choice.services[0])).map((choice) => choice.title),
  );
  const savedGoogle = googleStatus?.accounts.find(
    (row) => row.id.toLowerCase() === email.trim().toLowerCase(),
  );
  const grantedScopes = savedGoogle?.scopes;
  const missingEditConsent =
    allowWrite &&
    grantedScopes &&
    !grantedScopes.some((scope) =>
      ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/drive.file"].includes(scope),
    ) &&
    workspaceServices.some((service) => editScopes[service] && !grantedScopes.includes(editScopes[service]));
  const needsSignIn =
    signInAgain ||
    !savedGoogle ||
    missingEditConsent ||
    (savedGoogle.services && workspaceServices.some((service) => !savedGoogle.services!.includes(service)));
  const locked = Boolean(busy) || signingIn;

  useEffect(() => {
    if (!needsSignIn || credentials || signingIn) return;
    let alive = true;
    const look = () =>
      void downloadedGoogleCredentials()
        .then((result) => {
          if (alive) setFound(result.found);
        })
        .catch(() => {});
    look();
    const timer = setInterval(look, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [needsSignIn, credentials, signingIn]);

  useEffect(() => {
    if (!hasWorkspace) return;
    let alive = true;
    void inspectLocalIntegration("gog")
      .then((found) => {
        if (alive) setGoogleStatus(found);
      })
      .catch(() => {
        if (alive) setError("Could not check Google setup. Close this window and try again.");
      });
    void googleSignInStatus()
      .then((found) => {
        if (alive && found.running) setSigningIn(true);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [hasWorkspace]);

  const finish = useCallback(async () => {
    await loadConnectors();
    callbacks.current.close();
  }, [loadConnectors]);

  const connectWorkspace = useCallback(
    async (account: string) => {
      setBusy(`Connecting ${workspaceTitle}…`);
      const connector = await connectLocalIntegration(
        "gog",
        account,
        allowWrite,
        "workspace",
        workspaceServices,
      );
      if (!connector.connected)
        throw new Error(connector.error || "Google signed in, but its tools could not load. Try again.");
      await callbacks.current.onConnected?.(connector.name);
      await finish();
    },
    [allowWrite, workspaceServices, workspaceTitle, finish],
  );

  useEffect(() => {
    if (!signingIn) return;
    let alive = true;
    let polling = false;
    const timer = setInterval(() => {
      if (polling) return;
      polling = true;
      void googleSignInStatus()
        .then(async (result) => {
          if (!alive || result.running) return;
          if (result.error) {
            setError(result.error);
            setSigningIn(false);
            return;
          }
          const found = await inspectLocalIntegration("gog");
          if (!alive) return;
          setGoogleStatus(found);
          setSigningIn(false);
          setSignInAgain(false);
          const account = found.accounts.find((row) => row.id.toLowerCase() === email.trim().toLowerCase());
          if (autoConnect.current) {
            autoConnect.current = false;
            if (!account)
              throw new Error(
                "Sign-in finished, but this account was not found. Sign in with the email address above.",
              );
            await connectWorkspace(account.id);
          }
        })
        .catch((failure) => {
          setSigningIn(false);
          setError(
            failure instanceof Error ? failure.message : "Could not finish Google sign-in. Please try again.",
          );
        })
        .finally(() => {
          polling = false;
          setBusy("");
        });
    }, 1500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [signingIn, email, connectWorkspace]);

  async function submit() {
    setError("");
    try {
      if (!needsSignIn && savedGoogle) return await connectWorkspace(savedGoogle.id);
      if (!googleStatus?.installed) {
        setBusy("Installing Google support…");
        await installLocalIntegration("gog");
        setGoogleStatus(await inspectLocalIntegration("gog"));
      }
      setBusy("Opening Google sign-in…");
      // A file chosen by hand is sent as it always was. One found in Downloads is read there.
      await (credentials
        ? startGoogleSignIn(email.trim(), credentials, workspaceServices, allowWrite)
        : startGoogleSignIn(email.trim(), "", workspaceServices, allowWrite, found?.path ?? ""));
      autoConnect.current = true;
      setCredentials("");
      setChosenName("");
      setSigningIn(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not connect Google. Please try again.");
    } finally {
      setBusy("");
    }
  }

  function openPage(url: string) {
    void openExternal(url).catch(() =>
      setError(`Could not open your browser. Open this address manually: ${url}`),
    );
  }

  function helpLink(url: string, title: string) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="underline"
        onClick={(event) => {
          event.preventDefault();
          void openExternal(url).catch(() =>
            setError(`Could not open your browser. Open this address manually: ${url}`),
          );
        }}
      >
        {title}
      </a>
    );
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !locked) close();
      }}
    >
      <DialogContent className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ConnectorLogo id="google-workspace" className="size-5" />
            Connect Google
          </DialogTitle>
          <DialogDescription>
            Connect your mail, files and calendar. Credentials stay on this Mac.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex min-h-0 flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="min-h-0 overflow-y-auto px-1 pb-1">
            <fieldset disabled={locked} className="space-y-4 disabled:opacity-60">
              <label className="block text-sm">
                Email address
                <input
                  required
                  type="email"
                  autoComplete="email"
                  readOnly={Boolean(initialAccount)}
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    setError("");
                  }}
                  className="mt-2 block w-full rounded-lg border bg-background p-2"
                />
              </label>
              {!services ? (
                <fieldset className="flex gap-2">
                  <legend className="sr-only">Services</legend>
                  {choices.map((choice) => (
                    <label
                      key={choice.title}
                      className="flex flex-1 items-center justify-center gap-2 rounded-lg border p-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={selected.includes(choice.services[0])}
                        onChange={(event) =>
                          setSelected((current) =>
                            event.target.checked
                              ? [...current, ...choice.services]
                              : current.filter((service) => !choice.services.includes(service)),
                          )
                        }
                      />
                      {choice.title}
                    </label>
                  ))}
                </fieldset>
              ) : null}
              {hasWorkspace ? (
                <section aria-label={`${workspaceTitle} setup`} className="space-y-3 rounded-lg border p-3">
                  <p className="text-sm font-medium">{workspaceTitle}</p>
                  {needsSignIn ? (
                    <>
                      <p className="text-xs leading-5 text-muted-foreground">
                        Google asks for a small app of your own, once. It is free and takes about two minutes.
                      </p>
                      <ol aria-label="Setup steps" className="space-y-4">
                        <Step
                          number={1}
                          title="Create a project"
                          open="Open Google Cloud"
                          href="https://console.cloud.google.com/projectcreate"
                          onOpen={openPage}
                        >
                          Create a project, then enable the{" "}
                          {listed(
                            Object.keys(apis)
                              .filter((service) => selected.includes(service))
                              .map((service) => apis[service]),
                          )}{" "}
                          {selected.length === 1 ? "API" : "APIs"} in the API library. No billing account is
                          needed.
                        </Step>
                        <Step
                          number={2}
                          title="Create the app"
                          open="Open app setup"
                          href="https://console.cloud.google.com/auth/overview"
                          onOpen={openPage}
                        >
                          Press <b>Get started</b>. App name <b>Wavefunction local</b>, your own email for
                          support. Audience <b>Internal</b> if it is offered, otherwise <b>External</b>. Your
                          email as contact, then <b>Create</b>.
                        </Step>
                        <Step
                          number={3}
                          title="Create an OAuth client"
                          open="Open Clients"
                          href="https://console.cloud.google.com/auth/clients"
                          onOpen={openPage}
                        >
                          Press <b>Create client</b>, choose <b>Desktop app</b>, then <b>Create</b> and{" "}
                          <b>Download JSON</b>. If you chose External, also add your email under Audience,
                          Test users.
                        </Step>
                        <Step number={4} title="Choose the downloaded file">
                          {credentials || found ? (
                            <span className="flex items-center gap-1.5 text-foreground">
                              <Check className="size-3.5 shrink-0 text-success" />
                              <span className="truncate">{credentials ? chosenName : found!.name}</span>
                              <span className="shrink-0 text-muted-foreground">
                                {credentials ? "" : "found in Downloads"}
                              </span>
                            </span>
                          ) : (
                            <span>
                              It lands in Downloads as <code>client_secret_….json</code>. This form picks it
                              up by itself.
                            </span>
                          )}
                          <span className="mt-1.5 flex">
                            <Button
                              type="button"
                              size="xs"
                              variant={credentials || found ? "ghost" : "secondary"}
                              onClick={() => fileInput.current?.click()}
                            >
                              <FileJson />
                              {credentials || found ? "Choose a different file" : "Choose file"}
                            </Button>
                          </span>
                        </Step>
                      </ol>
                      <p className="text-xs">
                        {helpLink(
                          "https://github.com/openclaw/gogcli/blob/main/docs/quickstart.md",
                          "Full setup guide",
                        )}
                      </p>
                      <input
                        ref={fileInput}
                        key={signingIn ? "signing" : "ready"}
                        aria-label="Google credentials JSON"
                        type="file"
                        accept=".json,application/json"
                        className="sr-only"
                        tabIndex={-1}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          setCredentials("");
                          setChosenName("");
                          if (file && file.size <= 20000)
                            void file
                              .text()
                              .then((text) => {
                                setCredentials(text);
                                setChosenName(file.name ?? "Chosen file");
                              })
                              .catch(() => setError("Could not read that file."));
                          else if (file) setError("Choose a credentials JSON file smaller than 20 KB.");
                        }}
                      />
                    </>
                  ) : (
                    <p className="text-xs leading-5">
                      Google sign-in is saved.{" "}
                      <button
                        type="button"
                        className="underline"
                        onClick={() => {
                          setSignInAgain(true);
                          setError("");
                        }}
                      >
                        Sign in again
                      </button>
                    </p>
                  )}
                </section>
              ) : null}
              {hasFiles ? (
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={allowWrite}
                    onChange={(event) => setAllowWrite(event.target.checked)}
                  />
                  Allow editing Docs and Sheets
                </label>
              ) : null}
              {hasWorkspace ? (
                <p className="text-xs text-muted-foreground">
                  {hasMail ? "Mail is read-only. " : ""}Drive and Calendar are read-only. Docs and Sheets
                  support editing.
                </p>
              ) : null}
              <p className="text-xs leading-5 text-muted-foreground">
                Wavefunction can use the selected services after connecting. Content it reads may be sent to
                your chosen model provider.
              </p>
            </fieldset>
          </div>
          {error ? (
            <p role="alert" className="shrink-0 break-words text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {busy || signingIn ? (
              <p role="status" className="mr-auto flex items-center gap-2 text-xs text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" />
                {busy || "Finish Google sign-in in your browser."}
              </p>
            ) : null}
            {signingIn ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  void cancelGoogleSignIn()
                    .then(() => {
                      autoConnect.current = false;
                      setSigningIn(false);
                    })
                    .catch(() => setError("Could not cancel sign-in. Please try again."));
                }}
              >
                Cancel sign-in
              </Button>
            ) : (
              <>
                <Button type="button" variant="ghost" disabled={locked} onClick={close}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={
                    locked ||
                    !email.trim() ||
                    !selected.length ||
                    (hasWorkspace && (!googleStatus || (needsSignIn && !credentials && !found)))
                  }
                >
                  Connect account
                </Button>
              </>
            )}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
