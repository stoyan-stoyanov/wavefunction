import { useEffect, useId, useState } from "react";
import { Check, LoaderCircle } from "lucide-react";
import { ConnectorLogo } from "@/components/connector-brand";
import { MicrosoftSetup } from "@/components/microsoft-setup";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  connectLocalIntegration,
  inspectLocalIntegration,
  installLocalIntegration,
  setupLocalEmail,
  openExternal,
  type LocalIntegrationStatus,
} from "@/lib/api";
import { useSettings } from "@/store/settings";

type Provider = "gmail" | "microsoft" | "other";

type EmailSetupProps = {
  close: () => void;
  onConnected?: (name: string) => Promise<void>;
  initialAccount?: string;
  initialAllowWrite?: boolean;
  initialProvider?: Provider;
};

export function EmailSetup(props: EmailSetupProps) {
  const [provider, setProvider] = useState<Provider | null>(props.initialProvider ?? null);
  // Previously connected IMAP mailboxes keep their saved identity and tools.
  if (props.initialAccount && provider !== "gmail") return <ImapEmailSetup {...props} />;
  if (provider === "gmail") return <ImapEmailSetup {...props} initialProvider="gmail" />;
  if (provider === "microsoft")
    return (
      <MicrosoftSetup
        close={props.close}
        onConnected={props.onConnected}
        initialAllowWrite={props.initialAllowWrite}
        mode="mail"
      />
    );
  if (provider === "other") return <ImapEmailSetup {...props} />;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) props.close();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Connect Email</DialogTitle>
          <DialogDescription>Choose your email provider. Credentials stay on this Mac.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 sm:grid-cols-3">
          {(
            [
              ["gmail", "Gmail", "gmail"],
              ["microsoft", "Outlook", "outlook"],
              ["other", "Email", "email"],
            ] as const
          ).map(([id, title, icon]) => (
            <Button
              type="button"
              key={id}
              variant="outline"
              className="h-auto whitespace-normal py-4"
              onClick={() => setProvider(id)}
            >
              <ConnectorLogo id={icon} />
              {title}
            </Button>
          ))}
        </div>
        <div className="flex justify-end">
          <Button variant="ghost" onClick={props.close}>
            Cancel
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ImapEmailSetup({
  close,
  onConnected,
  initialAccount = "",
  initialAllowWrite = true,
  initialProvider,
}: EmailSetupProps) {
  const gmail = initialProvider === "gmail";
  const [status, setStatus] = useState<LocalIntegrationStatus | null>(null);
  const [updateLogin, setUpdateLogin] = useState(false);
  const accountList = useId();
  const [email, setEmail] = useState(initialAccount);
  const [password, setPassword] = useState("");
  const [imapHost, setImapHost] = useState(gmail ? "imap.gmail.com" : "");
  const [smtpHost, setSmtpHost] = useState(gmail ? "smtp.gmail.com" : "");
  const [imapPort, setImapPort] = useState(993);
  const [smtpPort, setSmtpPort] = useState(465);
  const [starttls, setStarttls] = useState(false);
  const [allowWrite, setAllowWrite] = useState(initialAllowWrite);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const loadConnectors = useSettings((s) => s.loadConnectors);

  useEffect(() => {
    let alive = true;
    void inspectLocalIntegration("himalaya")
      .then((found) => {
        if (alive) {
          setStatus(found);
        }
      })
      .catch(() => {
        if (alive) setError("Could not check email setup. Close this window and try again.");
      });
    return () => {
      alive = false;
    };
  }, [initialAccount]);

  const saved = status?.accounts.find((row) => row.id.toLowerCase() === email.trim().toLowerCase());
  const existing = Boolean(saved) && !updateLogin;

  async function connect() {
    setError("");
    try {
      if (!status?.installed) {
        setBusy("Installing email support…");
        await installLocalIntegration("himalaya");
        setStatus(await inspectLocalIntegration("himalaya"));
      }
      let selected = saved?.id ?? email.trim();
      if (!existing) {
        setBusy("Verifying your mailbox…");
        selected = email.trim();
        await setupLocalEmail({
          email: selected,
          password: gmail ? password.replace(/\s/g, "") : password,
          imap_host: imapHost.trim(),
          imap_port: imapPort,
          smtp_host: smtpHost.trim(),
          smtp_port: smtpPort,
          smtp_starttls: starttls,
        });
        setPassword("");
        // Keep a verified mailbox selectable if discovery or connecting the waiting task needs a retry.
        setUpdateLogin(false);
        setStatus((old) => ({
          installed: true,
          version: old?.version ?? "",
          error: "",
          accounts: [
            ...(old?.accounts.filter((row) => row.id !== selected) ?? []),
            { id: selected, label: selected },
          ],
        }));
      }
      setBusy("Connecting your account…");
      const connector = await connectLocalIntegration("himalaya", selected, allowWrite);
      if (!connector.connected)
        throw new Error(
          connector.error || "The mailbox was saved, but its connection failed. Try connecting again.",
        );
      await onConnected?.(connector.name);
      await loadConnectors();
      close();
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "Could not connect your email. Please try again.",
      );
    } finally {
      setBusy("");
    }
  }

  function help(url: string, title: string) {
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
        {title}
      </a>
    );
  }

  const servers = (
    <div className="space-y-3">
      <div className="grid grid-cols-[1fr_5rem] gap-2">
        <label className="text-xs">
          IMAP server
          <input
            required
            value={imapHost}
            onChange={(e) => setImapHost(e.target.value)}
            className="mt-1 w-full rounded-lg border bg-background p-2"
          />
        </label>
        <label className="text-xs">
          IMAP port
          <input
            required
            type="number"
            min="1"
            max="65535"
            value={imapPort}
            onChange={(e) => setImapPort(Number(e.target.value))}
            className="mt-1 w-full rounded-lg border bg-background p-2"
          />
        </label>
        <label className="text-xs">
          SMTP server
          <input
            required
            value={smtpHost}
            onChange={(e) => setSmtpHost(e.target.value)}
            className="mt-1 w-full rounded-lg border bg-background p-2"
          />
        </label>
        <label className="text-xs">
          SMTP port
          <input
            required
            type="number"
            min="1"
            max="65535"
            value={smtpPort}
            onChange={(e) => setSmtpPort(Number(e.target.value))}
            className="mt-1 w-full rounded-lg border bg-background p-2"
          />
        </label>
      </div>
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={starttls}
          onChange={(e) => {
            setStarttls(e.target.checked);
            setSmtpPort(e.target.checked ? 587 : 465);
          }}
        />
        SMTP uses STARTTLS (usually port 587)
      </label>
    </div>
  );
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) close();
      }}
    >
      <DialogContent className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ConnectorLogo id={gmail ? "gmail" : "email"} className="size-5" />
            {initialAccount ? "Reconnect" : "Connect"} {gmail ? "Gmail" : "Email"}
          </DialogTitle>
          <DialogDescription>Use your own email account. Credentials stay on this Mac.</DialogDescription>
        </DialogHeader>
        <form
          className="flex min-h-0 flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <div className="min-h-0 overflow-y-auto px-1 pb-1">
            <fieldset disabled={Boolean(busy)} className="space-y-4 disabled:opacity-60">
              {/* Text throughout, as a saved mailbox can go by a name such as "work": switching to it
              once a saved address was picked from the suggestions took WebKit's suggestion picker
              down mid-pick and crashed the page. The address is checked when it is saved. */}
              <label className="block text-sm">
                Email address
                <input
                  required
                  type="text"
                  inputMode="email"
                  autoComplete="email"
                  list={accountList}
                  readOnly={Boolean(initialAccount)}
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  className="mt-2 block w-full rounded-lg border bg-background p-2"
                />
              </label>
              <datalist id={accountList}>
                {status?.accounts.map((row) => (
                  <option key={row.id} value={row.id} />
                ))}
              </datalist>
              {existing ? (
                <div className="space-y-1 rounded-lg bg-muted p-3 text-xs leading-5">
                  <p>A mailbox is saved for this address. Connect to verify it and load its tools.</p>
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    className="h-auto p-0 text-foreground"
                    onClick={() => setUpdateLogin(true)}
                  >
                    {gmail ? "Update app password" : "Update login details"}
                  </Button>
                </div>
              ) : (
                <>
                  {gmail ? (
                    <>
                      <label className="block text-sm">
                        Google app password
                        <input
                          required
                          type="password"
                          autoComplete="new-password"
                          value={password}
                          onChange={(event) => setPassword(event.target.value)}
                          className="mt-2 block w-full rounded-lg border bg-background p-2"
                        />
                      </label>
                      <p className="text-xs leading-5">
                        {help("https://myaccount.google.com/apppasswords", "Create an app password")} named
                        “Wavefunction” and paste the 16-character code above.
                      </p>
                      <p className="text-xs leading-5 text-muted-foreground">
                        Requires{" "}
                        {help(
                          "https://myaccount.google.com/signinoptions/two-step-verification",
                          "2-Step Verification",
                        )}
                        . {help("https://support.google.com/accounts/answer/185833", "Can’t create one?")}
                      </p>
                    </>
                  ) : (
                    <>
                      <p className="text-xs leading-5 text-muted-foreground">
                        Use your provider’s IMAP and SMTP settings and a password or app password. Both
                        servers use your full email address and the same password.
                      </p>
                      <label className="block text-sm">
                        Password or app password
                        <input
                          required
                          type="password"
                          autoComplete="new-password"
                          value={password}
                          onChange={(event) => setPassword(event.target.value)}
                          className="mt-2 block w-full rounded-lg border bg-background p-2"
                        />
                      </label>
                      {servers}
                    </>
                  )}
                </>
              )}
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={allowWrite}
                    onChange={(event) => setAllowWrite(event.target.checked)}
                  />
                  Include sending and moving email
                </label>
                <p className="text-xs leading-5 text-muted-foreground">
                  Wavefunction can use this mailbox after connecting. Email it reads may be sent to your
                  chosen model provider.
                </p>
              </div>
              {status?.installed ? (
                <p className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Check className="size-3" />
                  Email support installed
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Wavefunction installs Himalaya email support automatically when you connect. No Terminal
                  needed.
                </p>
              )}
            </fieldset>
          </div>
          {error ? (
            <p role="alert" className="shrink-0 break-words text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex shrink-0 items-center justify-end gap-2">
            {busy ? (
              <p role="status" className="mr-auto flex items-center gap-2 text-xs text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" />
                {busy}
              </p>
            ) : null}
            <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={close}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={
                Boolean(busy) ||
                !status ||
                !email.trim() ||
                (!existing && (!password || !imapHost.trim() || !smtpHost.trim()))
              }
            >
              Connect account
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
