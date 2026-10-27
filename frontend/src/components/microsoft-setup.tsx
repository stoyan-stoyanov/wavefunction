import { useEffect, useId, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { ConnectorLogo } from "@/components/connector-brand";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  cancelMicrosoftSignIn,
  connectLocalIntegration,
  inspectLocalIntegration,
  installLocalIntegration,
  microsoftSignInStatus,
  openExternal,
  startMicrosoftSignIn,
  type LocalIntegrationStatus,
} from "@/lib/api";
import { useSettings } from "@/store/settings";

export function MicrosoftSetup({
  close,
  onConnected,
  initialAccount = "",
  initialAllowWrite = true,
  mode = "workspace",
  initialServices,
}: {
  close: () => void;
  onConnected?: (name: string) => Promise<void>;
  initialAccount?: string;
  initialAllowWrite?: boolean;
  mode?: "mail" | "workspace";
  initialServices?: string[];
}) {
  const accountList = useId();
  const [status, setStatus] = useState<LocalIntegrationStatus | null>(null);
  const [email, setEmail] = useState(initialAccount);
  const [accountType, setAccountType] = useState<"personal" | "work">("personal");
  const [allowWrite, setAllowWrite] = useState(initialAllowWrite);
  const [services, setServices] = useState(
    initialServices ?? (mode === "mail" ? ["mail"] : ["files", "calendar"]),
  );
  const [busy, setBusy] = useState("");
  const [attaching, setAttaching] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState("");
  const [deviceCode, setDeviceCode] = useState("");
  const savedAccount = status?.accounts.find((row) => row.id.toLowerCase() === email.trim().toLowerCase());
  const effectiveAccountType = savedAccount?.account_type ?? accountType;
  const attemptRef = useRef("");
  const alive = useRef(true);
  const callbacks = useRef({ onConnected, close });
  const loadConnectors = useSettings((state) => state.loadConnectors);
  useEffect(() => {
    callbacks.current = { onConnected, close };
  }, [onConnected, close]);

  useEffect(() => {
    alive.current = true;
    void inspectLocalIntegration("microsoft")
      .then((found) => {
        if (!alive.current) return;
        setStatus(found);
      })
      .catch(() => {
        if (alive.current) setError("Could not check Microsoft setup. Close this window and try again.");
      });
    return () => {
      alive.current = false;
      if (attemptRef.current) void cancelMicrosoftSignIn(attemptRef.current).catch(() => {});
    };
  }, [initialAccount, mode]);

  useEffect(() => {
    if (!attempt) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await microsoftSignInStatus(attempt);
        if (!active) return;
        setDeviceCode(result.device_code ?? "");
        if (result.running) {
          timer = setTimeout(() => void poll(), 1000);
          return;
        }
        attemptRef.current = "";
        if (result.error) throw new Error(result.error);
        await finish(result.account);
      } catch (failure) {
        if (active) {
          setError(failure instanceof Error ? failure.message : "Could not complete Microsoft sign-in.");
          setAttempt("");
          setBusy("");
          setAttaching(false);
        }
      }
    }
    async function finish(account: string) {
      setAttaching(true);
      setBusy("Adding this account…");
      const connector = await connectLocalIntegration("microsoft", account, allowWrite, mode);
      if (!connector.connected)
        throw new Error(
          connector.error || "Microsoft connected, but its tools could not be loaded. Try again.",
        );
      if (!active) return;
      await callbacks.current.onConnected?.(connector.name);
      await loadConnectors();
      if (active) callbacks.current.close();
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [attempt, allowWrite, mode, loadConnectors]);

  async function signIn() {
    setError("");
    setDeviceCode("");
    try {
      if (!status?.installed) {
        setBusy("Installing Microsoft support…");
        await installLocalIntegration("microsoft");
        if (!alive.current) return;
        setStatus(await inspectLocalIntegration("microsoft"));
      }
      if (!alive.current) return;
      setBusy(
        savedAccount
          ? "Connecting your saved Microsoft account…"
          : "Complete Microsoft sign-in in your browser…",
      );
      const started = await startMicrosoftSignIn({
        email: email.trim(),
        account_type: effectiveAccountType,
        mode,
        allow_write: allowWrite,
        services,
      });
      if (!alive.current) {
        await cancelMicrosoftSignIn(started.id);
        return;
      }
      attemptRef.current = started.id;
      setAttempt(started.id);
    } catch (failure) {
      if (alive.current) {
        setError(failure instanceof Error ? failure.message : "Could not start Microsoft sign-in.");
        setBusy("");
      }
    }
  }

  async function cancel() {
    if (attaching) return;
    try {
      if (attemptRef.current) await cancelMicrosoftSignIn(attemptRef.current);
      attemptRef.current = "";
      setAttempt("");
      close();
    } catch {
      setError("Could not cancel Microsoft sign-in. Try again.");
    }
  }

  function help(url: string, text: string) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="underline"
        onClick={(event) => {
          event.preventDefault();
          void openExternal(url).catch(() => setError(`Open this address in your browser: ${url}`));
        }}
      >
        {text}
      </a>
    );
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) void cancel();
      }}
    >
      <DialogContent showCloseButton={!attaching} className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ConnectorLogo id={mode === "mail" ? "outlook" : "microsoft-workspace"} className="size-5" />
            Connect {mode === "mail" ? "Outlook" : "Microsoft 365"}
          </DialogTitle>
          <DialogDescription>
            Sign in to your own Microsoft account. Microsoft’s official tools run on this Mac and keep sign-in
            credentials in its secure local cache.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void signIn();
          }}
        >
          <fieldset disabled={Boolean(busy)} className="space-y-4 disabled:opacity-60">
            <label className="block text-sm">
              Microsoft email address
              <input
                required
                type="email"
                autoComplete="email"
                list={accountList}
                readOnly={Boolean(initialAccount)}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="mt-2 block w-full rounded-lg border bg-background p-2"
              />
            </label>
            <datalist id={accountList}>
              {[...new Set(status?.accounts.map((row) => row.id) ?? [])].map((account) => (
                <option key={account} value={account} />
              ))}
            </datalist>
            <label className="block text-sm">
              Account type
              <select
                aria-label="Account type"
                disabled={Boolean(savedAccount)}
                value={effectiveAccountType}
                onChange={(event) => setAccountType(event.target.value as "personal" | "work")}
                className="mt-2 block w-full rounded-lg border bg-background p-2"
              >
                <option value="personal">Personal · Outlook.com, Hotmail, Live</option>
                <option value="work">Work or school · Microsoft 365</option>
              </select>
            </label>
            {savedAccount ? (
              <p className="rounded-lg bg-muted p-3 text-xs leading-5">
                Microsoft sign-in is saved for this address. Outlook and Microsoft 365 share it. Microsoft may
                ask you to approve additional access.
              </p>
            ) : null}
            {mode === "workspace" ? (
              <fieldset className="space-y-2">
                <legend className="mb-2 text-sm font-medium">Choose services</legend>
                {(
                  [
                    ["mail", "Mail", "Outlook email"],
                    [
                      "files",
                      "Files",
                      effectiveAccountType === "work"
                        ? "OneDrive, SharePoint, and Excel"
                        : "OneDrive and Office files",
                    ],
                    ["calendar", "Calendar", "Outlook calendar"],
                  ] as const
                )
                  .filter(([id]) => id !== "mail" || initialServices?.includes("mail"))
                  .map(([id, title, description]) => (
                    <label key={id} className="flex items-center gap-3 rounded-lg border p-3 text-sm">
                      <input
                        type="checkbox"
                        checked={services.includes(id)}
                        onChange={(event) =>
                          setServices((current) =>
                            event.target.checked ? [...current, id] : current.filter((value) => value !== id),
                          )
                        }
                      />
                      <span>
                        <span className="block font-medium">{title}</span>
                        <span className="text-xs text-muted-foreground">{description}</span>
                      </span>
                    </label>
                  ))}
              </fieldset>
            ) : null}
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={allowWrite}
                onChange={(event) => setAllowWrite(event.target.checked)}
              />
              {mode === "mail"
                ? "Include sending and moving email"
                : "Allow changes to the selected services"}
            </label>
            <div className="space-y-2 rounded-lg bg-muted p-3 text-xs leading-5">
              <p>
                Microsoft’s consent screen calls this app <strong>Microsoft Graph Command Line Tools</strong>.
                Wavefunction uses Microsoft’s official SDK; you do not need to register an app or enter a
                client ID.
              </p>
              {mode === "workspace" ? (
                <p>
                  Only the selected services will be enabled. SharePoint and editing Excel cell ranges require
                  a work or school account. Access depends on your Microsoft license and permissions.
                </p>
              ) : (
                <p>This connects your Outlook mailbox only.</p>
              )}
              {effectiveAccountType === "work" ? (
                <p>
                  Your organization may require an administrator to approve Microsoft Graph Command Line Tools
                  and the requested permissions, or block access.{" "}
                  {help(
                    "https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/user-admin-consent-overview",
                    "Microsoft’s consent guidance",
                  )}
                </p>
              ) : null}
              <p>
                {help(
                  "https://learn.microsoft.com/en-us/powershell/microsoftgraph/authentication-commands",
                  "Microsoft’s sign-in documentation",
                )}
              </p>
            </div>
            <p className="text-xs text-muted-foreground">
              {status?.installed
                ? "Microsoft support is installed."
                : "Microsoft support will be downloaded and verified automatically. No Terminal needed."}{" "}
              Wavefunction can use this account after connecting. Content it reads may be sent to your chosen
              model provider.
            </p>
          </fieldset>
          {deviceCode ? (
            <div role="status" className="rounded-lg border p-3 text-sm">
              Enter <strong className="select-all font-mono">{deviceCode}</strong> on{" "}
              {help("https://microsoft.com/devicelogin", "Microsoft’s sign-in page")}.
            </div>
          ) : null}
          {error ? (
            <p role="alert" className="break-words text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex items-center justify-end gap-2">
            {busy ? (
              <p role="status" className="mr-auto flex items-center gap-2 text-xs text-muted-foreground">
                <LoaderCircle className="size-4 shrink-0 animate-spin" />
                {busy}
              </p>
            ) : null}
            <Button type="button" variant="ghost" disabled={attaching} onClick={() => void cancel()}>
              Cancel
            </Button>
            <Button type="submit" disabled={Boolean(busy) || !status || !email.trim() || !services.length}>
              {savedAccount ? "Connect account" : "Sign in with Microsoft"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
