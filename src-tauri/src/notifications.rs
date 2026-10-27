//! macOS notifications for a chat's task that finished or needs the user while the chat is not
//! on screen. The backend decides what is due and holds this one request open until something
//! is; this shows the result, except for the chat in view when the window has focus. A click
//! opens that chat.

use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde::Deserialize;
use tauri::Manager;

/// The chat the page shows, or none on Settings and Connectors.
#[derive(Default)]
pub struct NotificationState {
    visible: Mutex<Option<String>>,
}
static APP: OnceLock<tauri::AppHandle> = OnceLock::new();
/// What a notification's id is when it opens no chat.
const TEST: &str = "test";

#[cfg(target_os = "macos")]
mod native {
    use super::*;
    use std::ffi::{c_char, c_void, CStr, CString};
    use tauri::Emitter;
    type Sender = tokio::sync::oneshot::Sender<i32>;
    extern "C" {
        fn wf_notifications_init(callback: extern "C" fn(*const c_char)) -> bool;
        fn wf_notifications_permission(
            request: bool,
            context: *mut c_void,
            callback: extern "C" fn(*mut c_void, i32),
        );
        fn wf_notifications_send(
            id: *const c_char,
            title: *const c_char,
            body: *const c_char,
            sound: bool,
            context: *mut c_void,
            callback: extern "C" fn(*mut c_void, i32),
        );
    }
    extern "C" fn result(context: *mut c_void, value: i32) {
        // The Objective-C completion runs exactly once and returns ownership of this sender.
        let sender = unsafe { Box::from_raw(context as *mut Sender) };
        let _ = sender.send(value);
    }
    extern "C" fn clicked(id: *const c_char) {
        let id = unsafe { CStr::from_ptr(id) }.to_string_lossy().into_owned();
        if let Some(app) = APP.get() {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
            if id != TEST {
                let _ = app.emit("notification-open", id);
            }
        }
    }
    pub fn init() -> bool {
        unsafe { wf_notifications_init(clicked) }
    }
    pub async fn permission(request: bool) -> Result<i32, String> {
        let (tx, rx) = tokio::sync::oneshot::channel::<i32>();
        unsafe {
            wf_notifications_permission(request, Box::into_raw(Box::new(tx)) as *mut c_void, result)
        };
        rx.await.map_err(|e| e.to_string())
    }
    pub async fn send(id: &str, title: &str, body: &str, sound: bool) -> Result<(), String> {
        let (id, title, body) = (
            CString::new(id).map_err(|e| e.to_string())?,
            CString::new(title.replace('\0', "")).map_err(|e| e.to_string())?,
            CString::new(body.replace('\0', "")).map_err(|e| e.to_string())?,
        );
        let (tx, rx) = tokio::sync::oneshot::channel::<i32>();
        unsafe {
            wf_notifications_send(
                id.as_ptr(),
                title.as_ptr(),
                body.as_ptr(),
                sound,
                Box::into_raw(Box::new(tx)) as *mut c_void,
                result,
            )
        };
        if rx.await.map_err(|e| e.to_string())? == 0 {
            Ok(())
        } else {
            Err("macOS could not show the notification.".into())
        }
    }
}
#[cfg(not(target_os = "macos"))]
mod native {
    pub fn init() -> bool {
        false
    }
    pub async fn permission(_: bool) -> Result<i32, String> {
        Ok(-2)
    }
    pub async fn send(_: &str, _: &str, _: &str, _: bool) -> Result<(), String> {
        Err("Notifications need macOS.".into())
    }
}
static AVAILABLE: OnceLock<bool> = OnceLock::new();

#[tauri::command]
pub fn notification_context(thread: Option<String>, state: tauri::State<'_, NotificationState>) {
    *state.visible.lock().unwrap() = thread;
}

/// macOS's answer for this app, asking first when `request` is set and it has not been asked.
#[tauri::command]
pub async fn notification_permission(request: bool) -> Result<String, String> {
    if AVAILABLE.get() != Some(&true) {
        return Ok("unsupported".into());
    }
    let mut status = native::permission(false).await?;
    if request && status == 0 {
        status = native::permission(true).await?;
    }
    Ok(match status {
        0 => "not_requested",
        1 => "denied",
        2..=4 => "granted",
        _ => "unavailable",
    }
    .into())
}

#[tauri::command]
pub async fn notification_test(sound: bool) -> Result<(), String> {
    if notification_permission(false).await? != "granted" {
        return Err("Allow Wavefunction in System Settings › Notifications first.".into());
    }
    native::send(
        TEST,
        "Wavefunction",
        "Notifications are on. You'll hear when a chat finishes while you're away from it.",
        sound,
    )
    .await
}

#[derive(Deserialize)]
struct Pending {
    cursor: String,
    sound: bool,
    items: Vec<Notice>,
}
#[derive(Deserialize)]
struct Notice {
    thread_id: String,
    title: String,
    body: String,
}

/// How long the backend holds a request open while nothing is due.
const WAIT_SECONDS: u64 = 25;

pub fn start(app: &tauri::AppHandle, base_url: String, token: String) {
    let _ = APP.set(app.clone());
    let available = native::init();
    let _ = AVAILABLE.set(available);
    if !available {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let Some(client) = crate::engine_client() else {
            return;
        };
        let Ok(endpoint) = reqwest::Url::parse(&format!("{base_url}/api/notifications/pending"))
        else {
            return;
        };
        let mut cursor: Option<String> = None;
        loop {
            let mut url = endpoint.clone();
            if let Some(after) = &cursor {
                url.query_pairs_mut()
                    .append_pair("after", after)
                    .append_pair("wait", &WAIT_SECONDS.to_string());
            }
            // Answered when a task ends or pauses, or empty after the wait; then asked again at once.
            let Some(pending) = fetch(client, url, &token).await else {
                // The backend may still be starting, or restarting into an update.
                tokio::time::sleep(Duration::from_secs(3)).await;
                continue;
            };
            cursor = Some(pending.cursor);
            // The chat in view now: the window may have changed while the request waited.
            let visible = in_view(&app);
            let items: Vec<Notice> = pending
                .items
                .into_iter()
                .filter(|notice| Some(&notice.thread_id) != visible.as_ref())
                .collect();
            if items.is_empty() {
                continue;
            }
            // macOS asks once, when the first notification is due.
            if notification_permission(true).await.as_deref() != Ok("granted") {
                continue;
            }
            for notice in items {
                // One per chat: a newer one replaces the chat's earlier notification.
                if let Err(error) = native::send(
                    &notice.thread_id,
                    &notice.title,
                    &notice.body,
                    pending.sound,
                )
                .await
                {
                    eprintln!("[notifications] {error}");
                }
            }
        }
    });
}

async fn fetch(client: &reqwest::Client, url: reqwest::Url, token: &str) -> Option<Pending> {
    client
        .get(url)
        .bearer_auth(token)
        .timeout(Duration::from_secs(WAIT_SECONDS + 15))
        .send()
        .await
        .ok()?
        .error_for_status()
        .ok()?
        .json::<Pending>()
        .await
        .ok()
}

/// The chat on screen, when the window is in front.
fn in_view(app: &tauri::AppHandle) -> Option<String> {
    let window = app.get_webview_window("main")?;
    let front = window.is_focused().unwrap_or(false)
        && window.is_visible().unwrap_or(false)
        && !window.is_minimized().unwrap_or(false);
    if !front {
        return None;
    }
    app.state::<NotificationState>()
        .visible
        .lock()
        .unwrap()
        .clone()
}
