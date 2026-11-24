//! Notifications for a chat's task that finished or needs the user while the chat is not on
//! screen: macOS's notification center, or the desktop's notification service on Linux. The
//! backend decides what is due and holds this one request open until something is; this shows
//! the result, except for the chat in view when the window has focus. A click opens that chat.

use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde::Deserialize;
use tauri::{Emitter, Manager};

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
    use std::ffi::{c_char, c_void, CStr, CString};
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
        super::clicked(&unsafe { CStr::from_ptr(id) }.to_string_lossy());
    }
    pub const NOT_ALLOWED: &str = "Allow Wavefunction in System Settings › Notifications first.";
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
#[cfg(target_os = "linux")]
mod native {
    use super::*;
    use std::collections::{BTreeMap, HashMap};
    use std::future::poll_fn;
    use std::pin::Pin;
    use zbus::export::futures_core::Stream;
    use zbus::zvariant::{DynamicType, Value};
    use zbus::{Connection, MatchRule, Message, MessageStream};

    const SERVICE: &str = "org.freedesktop.Notifications";
    const PATH: &str = "/org/freedesktop/Notifications";
    /// One connection for as long as the app runs. GNOME removes all of an app's notifications
    /// when the connection that sent its first one closes, and some servers (dunst) report a
    /// click only to the connection that sent the notification.
    static BUS: tokio::sync::OnceCell<Connection> = tokio::sync::OnceCell::const_new();
    /// Each chat's notification still on screen, by the id the service gave it. A server
    /// replaces only an id it gave, so a chat's next notification reuses that one.
    static SHOWN: Mutex<BTreeMap<String, u32>> = Mutex::new(BTreeMap::new());
    /// What `permission` answers when no notification service does.
    pub const NO_SERVICE: i32 = -2;
    pub const NOT_ALLOWED: &str =
        "No notification service answered. Start your desktop's notification service and try again.";

    /// There is nothing to set up: the service is asked each time.
    pub fn init() -> bool {
        true
    }
    /// Allowed whenever the desktop's notification service answers; it has nothing to ask.
    pub async fn permission(_: bool) -> Result<i32, String> {
        let answered = match bus().await {
            Ok(bus) => call(bus, "GetServerInformation", &()).await.is_ok(),
            Err(_) => false,
        };
        Ok(if answered { 2 } else { NO_SERVICE })
    }
    pub async fn send(id: &str, title: &str, body: &str, sound: bool) -> Result<(), String> {
        show(id, title, body, sound)
            .await
            .map_err(|e| e.to_string())
    }

    async fn show(id: &str, title: &str, body: &str, sound: bool) -> zbus::Result<()> {
        let bus = bus().await?;
        let capabilities: Vec<String> = call(bus, "GetCapabilities", &())
            .await?
            .body()
            .deserialize()?;
        // The body is plain text: a server that renders markup would show `<b>` as bold.
        let body = if capabilities
            .iter()
            .any(|capability| capability == "body-markup")
        {
            body.replace('&', "&amp;")
                .replace('<', "&lt;")
                .replace('>', "&gt;")
        } else {
            body.to_owned()
        };
        let hints = HashMap::from([if sound {
            ("sound-name", Value::from("message-new-instant"))
        } else {
            ("suppress-sound", Value::from(true))
        }]);
        let earlier = SHOWN.lock().unwrap().get(id).copied().unwrap_or(0);
        let actions = ["default", "Open"];
        let shown: u32 = call(
            bus,
            "Notify",
            &(
                "Wavefunction",
                earlier,
                "",
                title,
                body,
                actions.as_slice(),
                hints,
                -1_i32,
            ),
        )
        .await?
        .body()
        .deserialize()?;
        SHOWN.lock().unwrap().insert(id.to_owned(), shown);
        Ok(())
    }

    async fn call<B>(bus: &Connection, method: &str, body: &B) -> zbus::Result<Message>
    where
        B: serde::Serialize + DynamicType,
    {
        bus.call_method(Some(SERVICE), PATH, Some(SERVICE), method, body)
            .await
    }

    /// The session bus, connected on first use and listening for clicks from then on.
    async fn bus() -> zbus::Result<&'static Connection> {
        BUS.get_or_try_init(|| async {
            let bus = Connection::session().await?;
            let rule = MatchRule::builder()
                .msg_type(zbus::message::Type::Signal)
                .interface(SERVICE)?
                .build();
            let signals = MessageStream::for_match_rule(rule, &bus, None).await?;
            tauri::async_runtime::spawn(listen(signals));
            Ok::<_, zbus::Error>(bus)
        })
        .await
    }

    /// Opens a chat when its notification is clicked, and forgets one the server closed.
    async fn listen(mut signals: MessageStream) {
        while let Some(signal) = poll_fn(|cx| Pin::new(&mut signals).poll_next(cx)).await {
            let Ok(signal) = signal else { continue };
            let header = signal.header();
            match header.member().map(|name| name.as_str()) {
                Some("ActionInvoked") => {
                    let Ok((id, action)) = signal.body().deserialize::<(u32, String)>() else {
                        continue;
                    };
                    if action != "default" {
                        continue;
                    }
                    let thread = SHOWN
                        .lock()
                        .unwrap()
                        .iter()
                        .find(|(_, shown)| **shown == id)
                        .map(|(thread, _)| thread.clone());
                    if let Some(thread) = thread {
                        clicked(&thread);
                    }
                }
                Some("NotificationClosed") => {
                    let Ok((id, _)) = signal.body().deserialize::<(u32, u32)>() else {
                        continue;
                    };
                    SHOWN.lock().unwrap().retain(|_, shown| *shown != id);
                }
                _ => {}
            }
        }
    }
}
static AVAILABLE: OnceLock<bool> = OnceLock::new();

/// A click on a notification: bring the window forward and open its chat.
fn clicked(id: &str) {
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

#[tauri::command]
pub fn notification_context(thread: Option<String>, state: tauri::State<'_, NotificationState>) {
    *state.visible.lock().unwrap() = thread;
}

/// The system's answer for this app, asking first when `request` is set and it has not been asked.
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
        #[cfg(target_os = "linux")]
        native::NO_SERVICE => "unsupported",
        _ => "unavailable",
    }
    .into())
}

#[tauri::command]
pub async fn notification_test(sound: bool) -> Result<(), String> {
    if notification_permission(false).await? != "granted" {
        return Err(native::NOT_ALLOWED.into());
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
