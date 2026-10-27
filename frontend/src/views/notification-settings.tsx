import { useEffect, useState } from "react";
import { toast } from "sonner";
import { NativeSection, NativeView, ViewHeading } from "@/components/native-view";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  getNotificationSettings,
  notificationPermission,
  notificationsAvailable,
  saveNotificationSettings,
  sendTestNotification,
  type NotificationPermission,
  type NotificationSettings as Preferences,
} from "@/lib/notifications";
import { SettingsRow } from "./settings-row";

const permissionText: Record<NotificationPermission | "checking", string> = {
  checking: "Checking the macOS notification setting…",
  granted: "macOS allows notifications from Wavefunction. A Focus mode can still silence them.",
  not_requested: "macOS asks for permission when the first notification is due, or when you send a test.",
  denied: "macOS blocks notifications from Wavefunction. Allow them in System Settings › Notifications.",
  unsupported: "Notifications appear in the Wavefunction desktop app.",
  unavailable: "The macOS notification setting could not be read.",
};

export function NotificationSettings() {
  const [settings, setSettings] = useState<Preferences | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | "checking">("checking");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    getNotificationSettings()
      .then((loaded) => alive && setSettings(loaded))
      .catch(
        () => alive && setError("Notification settings could not be loaded. Reopen this page to try again."),
      );
    // Again on return from System Settings, where permission is changed.
    const check = () =>
      void notificationPermission()
        .then((answer) => alive && setPermission(answer))
        .catch(() => alive && setPermission("unavailable"));
    check();
    window.addEventListener("focus", check);
    return () => {
      alive = false;
      window.removeEventListener("focus", check);
    };
  }, []);

  async function save(change: Partial<Preferences>) {
    if (!settings) return;
    setSaving(true);
    setError("");
    try {
      if (change.desktop && notificationsAvailable()) {
        const answer = await notificationPermission(true);
        setPermission(answer);
        if (answer === "denied") throw new Error(permissionText.denied);
      }
      setSettings(await saveNotificationSettings({ ...settings, ...change }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    if (!settings) return;
    setError("");
    try {
      const answer = await notificationPermission(true);
      setPermission(answer);
      await sendTestNotification(settings.sound);
      toast.success("Test notification sent");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }

  const toggle = (title: string, description: string, key: keyof Preferences, disabled = false) => (
    <SettingsRow
      title={title}
      description={description}
      control={
        <Switch
          aria-label={title}
          checked={Boolean(settings?.[key])}
          disabled={saving || disabled}
          onCheckedChange={(checked) => void save({ [key]: checked })}
        />
      }
    />
  );

  return (
    <NativeView>
      <ViewHeading title="Notifications" />
      {error ? (
        <p role="alert" className="mb-4 text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {!settings ? (
        error ? null : (
          <p className="text-sm text-muted-foreground">Loading notification settings…</p>
        )
      ) : (
        <NativeSection
          title="Desktop notifications"
          description="For a chat you are not looking at: you have another chat open, or another app. Helpers don't notify; their chat does when it finishes."
        >
          <div className="divide-y divide-border overflow-hidden rounded-xl bg-card">
            {toggle(
              "When a chat finishes",
              "Its task completed or stopped with an error.",
              "desktop",
              permission === "unsupported",
            )}
            {toggle(
              "When a chat needs you",
              "It waits for an answer, an approval, a connection, or you on its desktop.",
              "input",
              !settings.desktop,
            )}
            {toggle("Sound", "Play the macOS notification sound.", "sound", !settings.desktop)}
            {toggle(
              "Show previews",
              "The chat's title and the start of its reply. When off, a notification only says that a task finished or needs you.",
              "previews",
              !settings.desktop,
            )}
          </div>
          <div className="mt-3 flex items-center justify-between gap-4">
            <p className="text-xs leading-5 text-muted-foreground">{permissionText[permission]}</p>
            <Button
              variant="outline"
              size="sm"
              className="shrink-0"
              disabled={!settings.desktop || permission === "unsupported" || permission === "denied"}
              onClick={() => void test()}
            >
              Send test notification
            </Button>
          </div>
        </NativeSection>
      )}
    </NativeView>
  );
}
