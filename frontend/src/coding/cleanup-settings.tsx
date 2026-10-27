import { useEffect, useId, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { NativeSection } from "@/components/native-view";
import { Button } from "@/components/ui/button";
import { codingApi, type WorktreeCleanupSettings as CleanupSettings } from "./api";

export function WorktreeCleanupSettings() {
  const id = useId();
  const [saved, setSaved] = useState<CleanupSettings | null>(null);
  const [days, setDays] = useState("14");
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    codingApi
      .worktreeCleanup(controller.signal)
      .then((settings) => {
        if (controller.signal.aborted) return;
        setSaved(settings);
        setEnabled(settings.retention_days !== null);
        setDays(String(settings.retention_days ?? 14));
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [retry]);

  const retention = enabled ? Number(days) : null;
  const valid = !enabled || (/^\d+$/.test(days) && Number(days) >= 1 && Number(days) <= 3650);
  const changed = saved !== null && saved.retention_days !== retention;

  async function save() {
    if (!saved || !valid || !changed || saving) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const settings = await codingApi.saveWorktreeCleanup({ retention_days: retention });
      setSaved(settings);
      setEnabled(settings.retention_days !== null);
      setDays(String(settings.retention_days ?? 14));
      setNotice("Cleanup settings saved");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <NativeSection title="Worktree cleanup">
      <form
        className="rounded-xl bg-card p-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <p id={`${id}-description`} className="text-xs leading-5 text-muted-foreground">
          Delete inactive clean managed worktrees to free disk space. Chats and Git branches stay available.
          Worktrees with unsaved changes are kept.
        </p>
        {loading ? (
          <p role="status" className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
            <LoaderCircle className="size-3.5 animate-spin" /> Loading cleanup settings…
          </p>
        ) : saved ? (
          <>
            <fieldset disabled={saving} className="mt-4 flex flex-wrap items-end gap-4">
              <label className="flex flex-col gap-2 text-xs" htmlFor={`${id}-mode`}>
                Delete unused worktrees
                <select
                  id={`${id}-mode`}
                  aria-describedby={`${id}-description`}
                  className="h-9 rounded-md bg-background px-3 text-sm focus-visible:outline focus-visible:outline-ring"
                  value={enabled ? "after" : "never"}
                  onChange={(event) => {
                    setEnabled(event.target.value === "after");
                    setNotice(null);
                  }}
                >
                  <option value="after">After inactivity</option>
                  <option value="never">Never</option>
                </select>
              </label>
              {enabled ? (
                <label className="flex flex-col gap-2 text-xs" htmlFor={`${id}-days`}>
                  Days of inactivity
                  <input
                    id={`${id}-days`}
                    type="number"
                    min={1}
                    max={3650}
                    step={1}
                    required
                    className="h-9 w-28 rounded-md bg-background px-3 text-sm focus-visible:outline focus-visible:outline-ring"
                    value={days}
                    aria-invalid={!valid}
                    aria-describedby={!valid ? `${id}-validation` : undefined}
                    onChange={(event) => {
                      setDays(event.target.value);
                      setNotice(null);
                    }}
                  />
                </label>
              ) : null}
              <Button type="submit" variant="secondary" size="sm" disabled={!changed || !valid || saving}>
                {saving ? <LoaderCircle className="size-3.5 animate-spin" /> : null}
                {saving ? "Saving…" : "Save cleanup settings"}
              </Button>
            </fieldset>
            {!valid ? (
              <p id={`${id}-validation`} className="mt-3 text-xs text-destructive">
                Enter a whole number from 1 to 3650 days.
              </p>
            ) : null}
          </>
        ) : null}
        {error ? (
          <div role="alert" className="mt-3 flex items-center gap-3 text-xs text-destructive">
            <span>{error}</span>
            {!saved && !loading ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  setLoading(true);
                  setError(null);
                  setRetry((attempt) => attempt + 1);
                }}
              >
                Retry
              </Button>
            ) : null}
          </div>
        ) : null}
        {notice ? (
          <p role="status" className="mt-3 text-xs text-muted-foreground">
            {notice}
          </p>
        ) : null}
      </form>
    </NativeSection>
  );
}
