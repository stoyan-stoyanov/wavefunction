//! The native window buttons, for a design that draws its own (the page's `WindowControls`).

/// Show or hide the native window buttons, and answer whether they are hidden now: the page draws
/// its own only then. On macOS these are the traffic lights in the overlay title bar. Elsewhere
/// the window keeps its own frame and buttons, so nothing hides and the answer is false.
#[tauri::command]
pub async fn set_native_window_controls(
    window: tauri::WebviewWindow,
    visible: bool,
) -> Result<bool, String> {
    #[cfg(target_os = "macos")]
    {
        let (send, receive) = tokio::sync::oneshot::channel();
        let target = window.clone();
        window
            .run_on_main_thread(move || {
                let _ = send.send(macos::set_visible(&target, visible));
            })
            .map_err(|error| error.to_string())?;
        receive.await.map_err(|error| error.to_string())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (window, visible);
        Ok(false)
    }
}

#[cfg(target_os = "macos")]
mod macos {
    use objc2_app_kit::{NSWindow, NSWindowButton};

    /// Whether all three buttons are hidden afterwards.
    pub fn set_visible(window: &tauri::WebviewWindow, visible: bool) -> bool {
        let Ok(pointer) = window.ns_window() else {
            return false;
        };
        if pointer.is_null() {
            return false;
        }
        // Safety: Tauri hands back this window's own NSWindow, used here on the main thread.
        let window = unsafe { &*(pointer as *const NSWindow) };
        let mut hidden = !visible;
        for kind in [
            NSWindowButton::CloseButton,
            NSWindowButton::MiniaturizeButton,
            NSWindowButton::ZoomButton,
        ] {
            match window.standardWindowButton(kind) {
                Some(button) => button.setHidden(!visible),
                None => hidden = false,
            }
        }
        hidden
    }
}
