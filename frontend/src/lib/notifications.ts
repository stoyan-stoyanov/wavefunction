import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { request } from "@/lib/transport";
import type { Schemas } from "@/lib/types";

/** Notify when a chat's task finishes, or needs the user, while that chat is not on screen.
 *  `input`: also when a task waits for an answer, an approval, a connection or the desktop.
 *  `previews`: the chat's title and the opening of its reply; generic text otherwise. */
export type NotificationSettings = Schemas["NotificationSettings"];
export type NotificationPermission = "granted" | "denied" | "not_requested" | "unsupported" | "unavailable";

/** Only the desktop app shows notifications; the browser development build has no shell. */
export const notificationsAvailable = () => "__TAURI_INTERNALS__" in window;

export const getNotificationSettings = () => request<NotificationSettings>("/api/notifications/settings");
export const saveNotificationSettings = (settings: NotificationSettings) =>
  request<NotificationSettings>("/api/notifications/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
export const notificationPermission = (ask = false) =>
  notificationsAvailable()
    ? invoke<NotificationPermission>("notification_permission", { request: ask })
    : Promise.resolve<NotificationPermission>("unsupported");
export const sendTestNotification = (sound: boolean) => invoke<void>("notification_test", { sound });

/** The chat on screen, which gets no notifications while the window has focus. */
export function reportVisibleChat(thread: string | null) {
  if (notificationsAvailable()) void invoke("notification_context", { thread }).catch(() => undefined);
}

/** Calls `open` with the chat whose notification was clicked. Returns the unsubscribe. */
export function onNotificationOpen(open: (thread: string) => void) {
  if (!notificationsAvailable()) return () => undefined;
  const unlisten = listen<string>("notification-open", (event) => open(event.payload));
  return () => void unlisten.then((stop) => stop()).catch(() => undefined);
}
