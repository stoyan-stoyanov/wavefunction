import { Button } from "@/components/ui/button";
import type { SecretKey } from "@/lib/api";
import { useSettings } from "@/store/settings";

/** "Connected", with the way back: Disconnect forgets the saved key. Its models stay enabled, as
 * after signing out of ChatGPT, and run again once a key is back. */
export function KeyStatus({ secret, connected }: { secret: SecretKey; connected: boolean }) {
  if (!connected) return <span className="text-xs text-muted-foreground">Not connected</span>;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-xs text-success">Connected</span>
      <ForgetKeyButton secret={secret} label="Disconnect" />
    </span>
  );
}
export function ForgetKeyButton({ secret, label }: { secret: SecretKey; label: string }) {
  const forgetKey = useSettings((state) => state.forgetKey);
  const busy = useSettings((state) => state.action === `forgetting-${secret}`);
  return (
    <Button
      type="button"
      variant="ghost"
      size="xs"
      disabled={busy}
      // The banner above carries a failure, such as a key set in the app's environment.
      onClick={() => void forgetKey(secret).catch(() => undefined)}
    >
      {busy ? "Removing…" : label}
    </Button>
  );
}
