use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use serde::Deserialize;
use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

use super::{BackendInfo, BackendState};

#[derive(Default)]
pub(crate) struct QuitGuard {
    approved: AtomicBool,
    pending: AtomicBool,
}

impl QuitGuard {
    pub(crate) fn approved(&self) -> bool {
        self.approved.load(Ordering::SeqCst)
    }

    fn begin(&self) -> bool {
        !self.pending.swap(true, Ordering::SeqCst)
    }

    fn finish(&self, approved: bool) {
        self.approved.store(approved, Ordering::SeqCst);
        self.pending.store(false, Ordering::SeqCst);
    }
}

#[derive(Deserialize)]
struct Activity {
    active: bool,
}

/// Whether the engine has work running, or `None` when it could not say.
async fn active_work(info: &BackendInfo) -> Option<bool> {
    let response = super::engine_client()?
        .get(format!("{}/api/activity", info.base_url))
        .bearer_auth(&info.token)
        // A wedged engine must not prevent the user quitting.
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .ok()?
        .error_for_status()
        .ok()?;
    Some(response.json::<Activity>().await.ok()?.active)
}

fn warning(active: Option<bool>) -> Option<&'static str> {
    match active {
        Some(false) => None,
        Some(true) => Some("Work is still running. Quitting stops active coding tasks. Saved chats and project data will remain available."),
        None => Some("Wavefunction couldn't check the local engine. Work may still be running, and quitting will stop it. Saved project data will remain available."),
    }
}

/// Ask before quitting (or restarting into an update) while work is running.
pub(crate) fn request(app: &tauri::AppHandle, restart: bool) {
    if !app.state::<QuitGuard>().begin() {
        return;
    }
    let app = app.clone();
    let info = app
        .try_state::<BackendState>()
        .map(|state| state.info.clone());
    tauri::async_runtime::spawn(async move {
        let active = match info {
            Some(info) => active_work(&info).await,
            None => None,
        };
        if let Some(message) = warning(active) {
            let callback_app = app.clone();
            app.dialog()
                .message(message)
                .title(if restart {
                    "Restart Wavefunction?"
                } else {
                    "Quit Wavefunction?"
                })
                .kind(MessageDialogKind::Warning)
                .buttons(MessageDialogButtons::OkCancelCustom(
                    if restart {
                        "Restart"
                    } else {
                        "Quit Wavefunction"
                    }
                    .into(),
                    "Keep Open".into(),
                ))
                .show(move |quit| conclude(&callback_app, quit, restart));
        } else {
            conclude(&app, true, restart);
        }
    });
}

/// Once approved, the exit runs as usual and stops the engine; a restart then starts the new app.
fn conclude(app: &tauri::AppHandle, quit: bool, restart: bool) {
    app.state::<QuitGuard>().finish(quit);
    // A quit from the menu, Dock, AppleScript or logout is waiting for this answer.
    #[cfg(target_os = "macos")]
    if super::terminate::reply(app, quit) {
        return;
    }
    if quit && restart {
        app.request_restart();
    } else if quit {
        app.exit(0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checks_engine_with_auth_and_treats_bad_responses_as_unknown() {
        use std::io::{Read, Write};
        use std::net::TcpListener;

        for (status, body, expected) in [
            ("200 OK", r#"{"active":true}"#, Some(true)),
            ("200 OK", r#"{"active":false}"#, Some(false)),
            ("200 OK", "{}", None),
            ("503 Service Unavailable", r#"{"active":false}"#, None),
        ] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let info = BackendInfo {
                base_url: format!("http://{}", listener.local_addr().unwrap()),
                token: "test-session".into(),
            };
            let server = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut request = Vec::new();
                let mut byte = [0];
                while !request.ends_with(b"\r\n\r\n") && request.len() < 8192 {
                    stream.read_exact(&mut byte).unwrap();
                    request.push(byte[0]);
                }
                let request = String::from_utf8(request).unwrap().to_lowercase();
                assert!(request.starts_with("get /api/activity http/1.1"));
                assert!(request.contains("authorization: bearer test-session\r\n"));
                write!(stream, "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            });
            assert_eq!(tauri::async_runtime::block_on(active_work(&info)), expected);
            server.join().unwrap();
        }
    }

    #[test]
    fn warns_for_active_or_unreachable_engine_only() {
        assert!(warning(Some(true)).is_some());
        assert!(warning(None).is_some());
        assert!(warning(Some(false)).is_none());
    }

    #[test]
    fn repeated_quit_requests_share_one_confirmation_and_cancel_can_retry() {
        let guard = QuitGuard::default();
        assert!(guard.begin());
        assert!(!guard.begin());
        assert!(!guard.approved());
        guard.finish(false);
        assert!(!guard.approved());
        assert!(guard.begin());
        guard.finish(true);
        assert!(guard.approved());
    }
}
