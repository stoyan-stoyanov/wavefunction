import { useEffect, useRef, useState } from "react";
import { ExternalLink, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  gitHubSignInStatus,
  inspectLocalIntegration,
  installLocalIntegration,
  openExternal,
  startGitHubSignIn,
} from "@/lib/api";

/** Where GitHub's device flow asks for the one-time code. */
const DEVICE_PAGE = "https://github.com/login/device";
const CHECKING = "Checking your GitHub sign-in…";

/**
 * The GitHub CLI sign-in that git and gh use in Wavefunction's commands: install gh when it is
 * missing and sign in with GitHub's device flow. Entering the code at GitHub is the one step only
 * a person can take, so the code and the button that opens the page are here. `start` runs it at
 * once, for a task already waiting on it, and `onSignedIn` hears the account once gh has it.
 */
export function GitHubSignIn({
  start = false,
  onSignedIn,
}: {
  start?: boolean;
  onSignedIn?: (account: string) => Promise<unknown>;
}) {
  // Each run of the setup is an attempt; attempt 0 only reads who is signed in.
  const [attempt, setAttempt] = useState(start ? 1 : 0);
  const [step, setStep] = useState(CHECKING);
  const [code, setCode] = useState("");
  const [account, setAccount] = useState("");
  const [error, setError] = useState("");
  // Read when sign-in finishes, not when it starts: a new callback does not start it again.
  const notify = useRef(onSignedIn);
  useEffect(() => {
    notify.current = onSignedIn;
  });

  useEffect(() => {
    let alive = true;
    async function run() {
      try {
        let status = await inspectLocalIntegration("gh");
        if (!alive) return;
        if (!attempt) {
          setAccount(status.accounts[0]?.id ?? "");
          return;
        }
        if (!status.installed) {
          setStep("Installing the GitHub CLI…");
          await installLocalIntegration("gh");
          status = await inspectLocalIntegration("gh");
          if (!alive) return;
        }
        if (status.error) throw new Error(status.error);
        let login = status.accounts[0]?.id ?? "";
        if (!login) {
          setStep("Starting GitHub sign-in…");
          // Joins a sign-in already under way, so leaving and coming back keeps the same code.
          let state = await startGitHubSignIn();
          while (state.running) {
            if (!alive) return;
            setCode(state.code);
            if (state.code) setStep("Enter this code on GitHub to sign in");
            await new Promise((resolve) => setTimeout(resolve, 1000));
            state = await gitHubSignInStatus();
          }
          if (!state.account) throw new Error(state.error || "GitHub sign-in did not finish. Try again.");
          login = state.account;
        }
        if (!alive) return;
        setAccount(login);
        setCode("");
        if (notify.current) {
          setStep("Continuing the task…");
          await notify.current(login);
        }
      } catch (failure) {
        if (alive)
          setError(failure instanceof Error ? failure.message : "Could not sign in to GitHub. Try again.");
      } finally {
        if (alive) {
          setStep("");
          setCode("");
        }
      }
    }
    void run();
    return () => {
      alive = false;
    };
  }, [attempt]);

  const label = start ? "Try again" : account ? "Check again" : "Sign in to GitHub";
  return (
    <div className="space-y-2">
      <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
        {step ? <LoaderCircle aria-hidden className="size-3.5 shrink-0 animate-spin" /> : null}
        {step || (account ? `Signed in as ${account}` : "Not signed in")}
      </p>
      {code ? (
        <div className="flex flex-wrap items-center gap-2">
          <strong
            aria-label="One-time code"
            className="select-all rounded-md bg-muted px-2 py-1 font-mono text-sm tracking-widest"
          >
            {code}
          </strong>
          <Button
            size="xs"
            variant="secondary"
            onClick={() => {
              void openExternal(DEVICE_PAGE).catch(() => setError(`Open ${DEVICE_PAGE} in your browser.`));
            }}
          >
            <ExternalLink />
            Open GitHub
          </Button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="break-words text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {!step && (error || !start) ? (
        <Button
          size="xs"
          variant="secondary"
          onClick={() => {
            setError("");
            setStep(CHECKING);
            setAttempt((n) => n + 1);
          }}
        >
          {label}
        </Button>
      ) : null}
    </div>
  );
}
