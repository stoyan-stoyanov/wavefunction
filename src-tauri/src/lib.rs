use std::collections::VecDeque;
use std::error::Error;
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Manager, RunEvent, Theme};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

mod notifications;
mod quit;
#[cfg(target_os = "macos")]
mod terminate;

#[derive(Clone, Serialize)]
struct BackendInfo {
    base_url: String,
    token: String,
}

/// The one HTTP client for the app's own loopback engine. It never goes through a proxy, which
/// would see the session token, and never follows a redirect elsewhere. Each request sets its
/// own timeout.
fn engine_client() -> Option<&'static reqwest::Client> {
    static CLIENT: std::sync::OnceLock<Option<reqwest::Client>> = std::sync::OnceLock::new();
    CLIENT
        .get_or_init(|| {
            reqwest::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .ok()
        })
        .as_ref()
}

/// Whether the sidecar is still alive, and what it last said if it is not.
///
/// The page polls `/health` while the Python runtime loads, which takes several seconds even
/// on a warm start. A sidecar that has already died would keep it polling until the deadline,
/// then say only that nothing answered; the exit code and the last lines of stderr say why.
#[derive(Clone, Serialize, Default)]
struct BackendStatus {
    exited: Option<String>,
}

struct BackendState {
    info: BackendInfo,
    child: Mutex<Option<CommandChild>>,
    status: Arc<Mutex<BackendStatus>>,
}

#[tauri::command]
fn backend_info(state: tauri::State<'_, BackendState>) -> BackendInfo {
    state.info.clone()
}

/// Restart into an installed update: through the quit check, so running work is not cut off
/// without asking, and through the normal exit, so the engine stops before the app restarts.
#[tauri::command]
fn restart_app(app: tauri::AppHandle) {
    quit::request(&app, true);
}

#[tauri::command]
fn backend_status(state: tauri::State<'_, BackendState>) -> BackendStatus {
    state
        .status
        .lock()
        .map(|status| status.clone())
        .unwrap_or_default()
}

/// The window's native chrome on macOS: the blur behind the sidebar and the traffic lights.
///
/// AppKit objects belong to the main thread, so everything here runs there and the state lives
/// in a thread-local.
#[cfg(target_os = "macos")]
mod window_chrome {
    use std::cell::{Cell, RefCell};
    use std::ptr::NonNull;

    use block2::RcBlock;
    use objc2::rc::{Retained, Weak};
    use objc2::runtime::{NSObjectProtocol, ProtocolObject};
    use objc2::MainThreadMarker;
    use objc2_app_kit::{
        NSAutoresizingMaskOptions, NSColor, NSView, NSViewFrameDidChangeNotification,
        NSVisualEffectBlendingMode, NSVisualEffectMaterial, NSVisualEffectState,
        NSVisualEffectView, NSWindow, NSWindowButton, NSWindowDidExitFullScreenNotification,
        NSWindowDidResizeNotification, NSWindowOrderingMode, NSWindowStyleMask,
    };
    use objc2_foundation::{
        NSNotification, NSNotificationCenter, NSOperationQueue, NSPoint, NSRect, NSSize,
    };

    /// The close button's left edge, in window points.
    const BUTTON_X: f64 = 16.0;

    /// The buttons' centre until the page reports its toolbar: the middle of the 46px toolbar at
    /// the default 1.15 zoom. The page sends the real value once it has applied its zoom.
    const DEFAULT_CENTER: f64 = 23.0 * 1.15;

    /// How much of the window keeps the blur once the window paints the page's background.
    ///
    /// Only the sidebar is see-through, and it is at most 420 CSS px wide at 2x zoom: 840 points.
    /// Matching the window's 880-point minimum width keeps all of it on glass, and the strip a
    /// widening window uncovers always starts right of 880, where the solid colour is.
    const BACKDROP_WIDTH: f64 = 880.0;

    struct Chrome {
        window: Weak<NSWindow>,
        backdrop: Retained<NSVisualEffectView>,
        /// Where the buttons' centre belongs, in points from the top of the window.
        center: Cell<f64>,
        /// AppKit's distance between neighbouring buttons, read before anything moved them.
        spacing: f64,
        /// Set while the buttons move, because their own frame notifications re-enter.
        aligning: Cell<bool>,
        /// Set while a retry waits for AppKit to finish its layout pass.
        retrying: Cell<bool>,
        /// Kept for the window's lifetime, which is the app's.
        _observers: Vec<Retained<ProtocolObject<dyn NSObjectProtocol>>>,
    }

    thread_local! {
        static CHROME: RefCell<Option<Chrome>> = const { RefCell::new(None) };
    }

    /// Put the blur behind the page and take over the traffic lights' position.
    pub fn install(window: &NSWindow, mtm: MainThreadMarker) -> Option<()> {
        // A CSS backdrop-filter can only blur what is inside the page, so the sidebar's glass
        // comes from macOS. It covers the whole window until the page reports its background.
        let content = window.contentView()?;
        let backdrop = NSVisualEffectView::initWithFrame(mtm.alloc(), content.bounds());
        backdrop.setMaterial(NSVisualEffectMaterial::Sidebar);
        backdrop.setBlendingMode(NSVisualEffectBlendingMode::BehindWindow);
        backdrop.setState(NSVisualEffectState::FollowsWindowActiveState);
        backdrop.setAutoresizingMask(
            NSAutoresizingMaskOptions::ViewWidthSizable
                | NSAutoresizingMaskOptions::ViewHeightSizable,
        );
        content.addSubview_positioned_relativeTo(&backdrop, NSWindowOrderingMode::Below, None);

        let close = window.standardWindowButton(NSWindowButton::CloseButton)?;
        let miniaturize = window.standardWindowButton(NSWindowButton::MiniaturizeButton)?;
        let zoom = window.standardWindowButton(NSWindowButton::ZoomButton)?;
        // Safety: AppKit's own title bar views, observed and moved only on the main thread.
        let container = unsafe { close.superview()?.superview()? };
        let spacing = miniaturize.frame().origin.x - close.frame().origin.x;

        // AppKit lays the title bar out again on every resize step, theme change and full-screen
        // exit, and puts the buttons back where it wants them. Its frame notifications arrive
        // inside that layout pass, and the window's resize notification right after it, so the
        // buttons are back on our line before anything is drawn. Answering from the page instead
        // meant an asynchronous round trip per resize event, and the buttons flickered between
        // AppKit's position and ours.
        let on_layout = RcBlock::new(|_: NonNull<NSNotification>| realign());
        let center = NSNotificationCenter::defaultCenter();
        let views: [&NSView; 4] = [&container, &close, &miniaturize, &zoom];
        let mut observers = Vec::with_capacity(views.len() + 2);
        for view in views {
            view.setPostsFrameChangedNotifications(true);
            // Safety: the block is called on the posting thread, which for views is the main one.
            observers.push(unsafe {
                center.addObserverForName_object_queue_usingBlock(
                    Some(NSViewFrameDidChangeNotification),
                    Some(view),
                    None,
                    &on_layout,
                )
            });
        }
        // Safety: AppKit's notification names are immutable constants.
        let window_events = unsafe {
            [
                NSWindowDidResizeNotification,
                NSWindowDidExitFullScreenNotification,
            ]
        };
        for name in window_events {
            // Safety: as above; window notifications are posted on the main thread too.
            observers.push(unsafe {
                center.addObserverForName_object_queue_usingBlock(
                    Some(name),
                    Some(window),
                    None,
                    &on_layout,
                )
            });
        }

        CHROME.with(|chrome| {
            chrome.replace(Some(Chrome {
                window: Weak::new(window),
                backdrop,
                center: Cell::new(DEFAULT_CENTER),
                spacing,
                aligning: Cell::new(false),
                retrying: Cell::new(false),
                _observers: observers,
            }))
        });
        realign();
        Some(())
    }

    /// Move the buttons' centre line and report where the close button's centre ended up.
    pub fn set_traffic_light_center(center: f64) -> Option<f64> {
        let window = CHROME.with(|chrome| {
            let chrome = chrome.borrow();
            let chrome = chrome.as_ref()?;
            chrome.center.set(center);
            chrome.window.load()
        })?;
        realign();
        let close = window.standardWindowButton(NSWindowButton::CloseButton)?;
        let size = close.frame().size;
        if in_full_screen(&window) {
            // The buttons sit in AppKit's full-screen title bar for now; report where they return.
            return Some(button_top(&window, center, size.height) + size.height / 2.0);
        }
        // The content view is full height under `titleBarStyle: "Overlay"`, so window
        // coordinates and CSS pixels share an origin, except that AppKit measures up from the
        // bottom.
        let rect = close.convertRect_toView(close.bounds(), None);
        Some(window.frame().size.height - (rect.origin.y + size.height / 2.0))
    }

    /// Paint the page's background colour behind the page and narrow the blur to the sidebar.
    ///
    /// The page draws a resize a moment after AppKit has grown the window. Until it does, the new
    /// area showed straight through to the blur, so the window flashed glass before going solid.
    ///
    /// `everywhere` keeps the blur across the whole window instead, for the setup screen, whose
    /// left column grows with the window and spans it when the window is narrow.
    pub fn paint_background([red, green, blue]: [u8; 3], everywhere: bool) {
        CHROME.with(|chrome| {
            let chrome = chrome.borrow();
            let Some(chrome) = chrome.as_ref() else {
                return;
            };
            let Some(window) = chrome.window.load() else {
                return;
            };
            let color = NSColor::colorWithSRGBRed_green_blue_alpha(
                f64::from(red) / 255.0,
                f64::from(green) / 255.0,
                f64::from(blue) / 255.0,
                1.0,
            );
            window.setBackgroundColor(Some(&color));
            let mut frame = chrome.backdrop.frame();
            frame.origin.x = 0.0;
            let width = if everywhere {
                window
                    .contentView()
                    .map(|content| content.bounds().size.width)
            } else {
                None
            };
            frame.size.width = width.unwrap_or(BACKDROP_WIDTH);
            chrome.backdrop.setAutoresizingMask(
                NSAutoresizingMaskOptions::ViewHeightSizable
                    | if width.is_some() {
                        NSAutoresizingMaskOptions::ViewWidthSizable
                    } else {
                        NSAutoresizingMaskOptions::ViewMaxXMargin
                    },
            );
            chrome.backdrop.setFrame(frame);
        });
    }

    fn realign() {
        CHROME.with(|chrome| {
            let Ok(chrome) = chrome.try_borrow() else {
                return;
            };
            let Some(chrome) = chrome.as_ref() else {
                return;
            };
            if chrome.aligning.replace(true) {
                return;
            }
            let placed = chrome
                .window
                .load()
                .is_none_or(|window| place_buttons(&window, chrome.center.get(), chrome.spacing));
            chrome.aligning.set(false);
            // AppKit ignores a move made from inside the notification about that same view's
            // move, so the last button it lays out can stay where AppKit put it. A resize is
            // followed by the window's resize notification, which fixes that in time; anything
            // else gets one retry after AppKit's layout pass.
            if !placed && !chrome.retrying.replace(true) {
                let retry = RcBlock::new(|| {
                    CHROME.with(|chrome| {
                        if let Ok(chrome) = chrome.try_borrow() {
                            if let Some(chrome) = chrome.as_ref() {
                                chrome.retrying.set(false);
                            }
                        }
                    });
                    realign();
                });
                // Safety: the main queue runs the block on the main thread, which owns AppKit.
                unsafe { NSOperationQueue::mainQueue().addOperationWithBlock(&retry) };
            }
        });
    }

    /// Move the buttons onto the centre line. False when AppKit did not let one of them move.
    fn place_buttons(window: &NSWindow, center: f64, spacing: f64) -> bool {
        // In full screen AppKit moves the title bar into a window of its own; leave that alone.
        if in_full_screen(window) {
            return true;
        }
        let buttons = [
            NSWindowButton::CloseButton,
            NSWindowButton::MiniaturizeButton,
            NSWindowButton::ZoomButton,
        ]
        .map(|kind| window.standardWindowButton(kind));
        let Some(close) = buttons[0].as_ref() else {
            return true;
        };
        // Safety: as in `install`.
        let Some(container) = (unsafe { close.superview().and_then(|bar| bar.superview()) }) else {
            return true;
        };
        // The geometry below is in this window's coordinates; AppKit can still be handing the
        // title bar back from full screen.
        if !container
            .window()
            .is_some_and(|host| std::ptr::eq(&*host, window))
        {
            return true;
        }
        let size = close.frame().size;
        let top = button_top(window, center, size.height);
        // The container hangs from the top of the window with the buttons centred in it. It has
        // to enclose them, or clicks on their lower half miss.
        let height = top * 2.0 + size.height;
        let frame = container.frame();
        let wanted = NSRect::new(
            NSPoint::new(frame.origin.x, window.frame().size.height - height),
            NSSize::new(frame.size.width, height),
        );
        let mut placed = true;
        if !near(frame.origin, wanted.origin) || (frame.size.height - height).abs() >= 0.01 {
            container.setFrame(wanted);
            placed &= near(container.frame().origin, wanted.origin);
        }
        for (index, button) in buttons.iter().enumerate() {
            let Some(button) = button else { continue };
            let origin = NSPoint::new(BUTTON_X + index as f64 * spacing, top);
            if !near(button.frame().origin, origin) {
                button.setFrameOrigin(origin);
                placed &= near(button.frame().origin, origin);
            }
        }
        placed
    }

    /// The buttons' top edge for a centre line, on whole device pixels so they stay sharp.
    fn button_top(window: &NSWindow, center: f64, button_height: f64) -> f64 {
        let scale = window.backingScaleFactor();
        ((center - button_height / 2.0) * scale).round() / scale
    }

    fn in_full_screen(window: &NSWindow) -> bool {
        window.styleMask().contains(NSWindowStyleMask::FullScreen)
    }

    fn near(a: NSPoint, b: NSPoint) -> bool {
        (a.x - b.x).abs() < 0.01 && (a.y - b.y).abs() < 0.01
    }
}

/// Keep the native buttons on the same centre line as the zoomed web toolbar.
///
/// The page calls this when its zoom changes; AppKit keeps them there through resizes.
#[tauri::command]
async fn align_window_controls(
    window: tauri::WebviewWindow,
    center_y: f64,
) -> Result<Option<f64>, String> {
    if !center_y.is_finite() || !(16.0..=128.0).contains(&center_y) {
        return Err("Invalid window toolbar position".into());
    }
    #[cfg(target_os = "macos")]
    {
        let (send, receive) = tokio::sync::oneshot::channel();
        window
            .run_on_main_thread(move || {
                let _ = send.send(window_chrome::set_traffic_light_center(center_y));
            })
            .map_err(|error| error.to_string())?;
        receive.await.map_err(|error| error.to_string())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = window;
        Ok(None)
    }
}

/// Keep the native window in step with the in-app theme.
///
/// The vibrancy layer takes its colour from the window's NSAppearance, which otherwise follows
/// the system setting rather than the theme chosen in the app. With the system dark and the app
/// set to light, the blur behind the sidebar stayed dark under a light tint — a muddy flat panel
/// instead of glass.
///
/// `background` is the page's own background colour in sRGB, which the window paints behind the
/// page so that a resize looks solid before the page has drawn the new area. `glass_everywhere`
/// keeps the blur across the whole window rather than only under the sidebar.
#[tauri::command]
fn set_window_theme(
    window: tauri::WebviewWindow,
    dark: bool,
    background: Option<[u8; 3]>,
    glass_everywhere: Option<bool>,
) -> Result<(), String> {
    window
        .set_theme(Some(if dark { Theme::Dark } else { Theme::Light }))
        .map_err(|error| error.to_string())?;
    #[cfg(target_os = "macos")]
    if let Some(background) = background {
        window
            .run_on_main_thread(move || {
                window_chrome::paint_background(background, glass_everywhere.unwrap_or(false))
            })
            .map_err(|error| error.to_string())?;
    }
    #[cfg(not(target_os = "macos"))]
    let _ = (background, glass_everywhere);
    Ok(())
}

/// Start macOS dictation, as Edit › Start Dictation does.
///
/// The page focuses the message box first. The action goes to the first responder, the web view,
/// and WebKit types what is said where the caret is. Dictation runs on the Mac, under the user's
/// own Keyboard settings; macOS offers to turn it on when it is off.
#[tauri::command]
async fn start_dictation(window: tauri::WebviewWindow) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let (send, receive) = tokio::sync::oneshot::channel();
        window
            .run_on_main_thread(move || {
                use objc2::runtime::{AnyObject, Bool};
                use objc2::{class, msg_send, sel};
                // SAFETY: on the main thread; a nil target sends the action up the responder chain.
                let handled: Bool = unsafe {
                    let application: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
                    msg_send![application, sendAction: sel!(startDictation:),
                        to: std::ptr::null_mut::<AnyObject>(), from: std::ptr::null_mut::<AnyObject>()]
                };
                let _ = send.send(handled.as_bool());
            })
            .map_err(|error| error.to_string())?;
        match receive.await {
            Ok(true) => Ok(()),
            Ok(false) => Err(
                "Dictation could not start here. Click in the message box and try again.".into(),
            ),
            Err(error) => Err(error.to_string()),
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = window;
        Err("Dictation needs macOS".into())
    }
}

/// How long the engine gets to shut down on quit before it is killed outright.
// Frozen terminal review helpers need time to save their last patches after
// stopping PTYs. Keep the engine alive until its owned jobs have been reaped.
const QUIT_GRACE: Duration = Duration::from_secs(15);

/// Ask a process to stop, and kill it only if it has not within `grace`.
///
/// The engine answers SIGTERM by running its own shutdown: it cancels runs, which stops the
/// commands they started — each in a process group of its own that a SIGKILL of the engine
/// would leave running — and removes their desktops.
fn stop_gracefully(pid: u32, grace: Duration, exited: impl Fn() -> bool, kill: impl FnOnce()) {
    // Safety: a plain signal to the child process this app started.
    if unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) } == 0 {
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if exited() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
    kill();
}

fn available_port() -> Result<u16, Box<dyn Error>> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    Ok(listener.local_addr()?.port())
}

fn start_backend(app: &tauri::AppHandle) -> Result<BackendState, Box<dyn Error>> {
    let port = available_port()?;
    let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let parent_pid = std::process::id().to_string();
    // A development build can use another folder, so `npm run desktop:dev` need not share the
    // installed app's data. A release build always uses the app's own.
    let data_dir = match std::env::var_os("WAVEFUNCTION_DEV_DATA_DIR") {
        Some(folder) if cfg!(debug_assertions) => std::path::PathBuf::from(folder),
        _ => app.path().app_data_dir()?,
    };
    std::fs::create_dir_all(&data_dir)?;

    // The frozen executable and its runtime stay together in the app's resources,
    // avoiding a fresh extraction of Python and its libraries on every launch.
    let backend = app.path().resource_dir()?.join("backend").join(format!(
        "wavefunction-backend{}",
        std::env::consts::EXE_SUFFIX
    ));
    let (mut events, child) = app
        .shell()
        .command(backend)
        .args([
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
            "--token",
            &token,
            "--data-dir",
            &data_dir.to_string_lossy(),
            "--parent-pid",
            &parent_pid,
        ])
        .spawn()?;

    let status = Arc::new(Mutex::new(BackendStatus::default()));
    let watched = Arc::clone(&status);
    tauri::async_runtime::spawn(async move {
        // The tail of stderr: a Python traceback ends with the line that names the problem.
        let mut recent: VecDeque<String> = VecDeque::new();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    println!("[wavefunction-backend] {}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Stderr(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).trim_end().to_string();
                    eprintln!("[wavefunction-backend] {line}");
                    if !line.trim().is_empty() {
                        if recent.len() == 5 {
                            recent.pop_front();
                        }
                        recent.push_back(line);
                    }
                }
                CommandEvent::Error(error) => {
                    eprintln!("[wavefunction-backend] {error}");
                }
                CommandEvent::Terminated(payload) => {
                    let how = match (payload.code, payload.signal) {
                        (Some(code), _) => format!("exit code {code}"),
                        (None, Some(signal)) => format!("signal {signal}"),
                        (None, None) => "no exit code".to_string(),
                    };
                    let tail = recent.iter().cloned().collect::<Vec<_>>().join("\n");
                    eprintln!("[wavefunction-backend] stopped ({how})");
                    if let Ok(mut guard) = watched.lock() {
                        guard.exited = Some(if tail.is_empty() {
                            how
                        } else {
                            format!("{how}\n{tail}")
                        });
                    }
                }
                _ => {}
            }
        }
    });

    Ok(BackendState {
        info: BackendInfo {
            base_url: format!("http://127.0.0.1:{port}"),
            token,
        },
        child: Mutex::new(Some(child)),
        status,
    })
}

pub fn run() {
    let app = tauri::Builder::default()
        .manage(quit::QuitGuard::default())
        .manage(notifications::NotificationState::default())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            backend_info,
            backend_status,
            restart_app,
            align_window_controls,
            set_window_theme,
            start_dictation,
            notifications::notification_context,
            notifications::notification_permission,
            notifications::notification_test
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            if let Some(window) = app.get_webview_window("main") {
                use objc2_app_kit::NSWindow;

                let pointer = window.ns_window().unwrap_or(std::ptr::null_mut());
                match objc2::MainThreadMarker::new() {
                    Some(mtm) if !pointer.is_null() => {
                        // Safety: Tauri hands back this window's own NSWindow, and setup runs on
                        // the main thread that owns it.
                        let ns_window = unsafe { &*(pointer as *const NSWindow) };
                        if window_chrome::install(ns_window, mtm).is_none() {
                            eprintln!(
                                "[wavefunction] window chrome: AppKit title bar views not found"
                            );
                        }
                        // The page is see-through only when Tauri makes the webview transparent,
                        // which takes both `transparent` and `macOSPrivateApi` in tauri.conf.json
                        // and also leaves the window non-opaque. It says nothing when they are
                        // not set, which is how the sidebar stayed solid through several rebuilds.
                        if ns_window.isOpaque() {
                            eprintln!("[wavefunction] window is opaque; vibrancy will not show");
                        }
                    }
                    _ => eprintln!("[wavefunction] window chrome: no native window"),
                }
            }

            #[cfg(target_os = "macos")]
            if !terminate::install(app.handle()) {
                eprintln!("[wavefunction] quit check: no app delegate; Cmd+Q will not ask");
            }

            let backend = app.state::<BackendState>();
            notifications::start(
                app.handle(),
                backend.info.base_url.clone(),
                backend.info.token.clone(),
            );
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Wavefunction desktop app");

    // Before any window: the build above ran the single-instance check, so a second launch
    // has exited by now and never starts a second engine on the same data.
    let backend = start_backend(app.handle()).expect("failed to start the Wavefunction engine");
    app.manage(backend);

    app.run(|app, event| {
        match &event {
            RunEvent::WindowEvent {
                label,
                event: tauri::WindowEvent::CloseRequested { api, .. },
                ..
            } if label == "main" => {
                if !app.state::<quit::QuitGuard>().approved() {
                    api.prevent_close();
                    quit::request(app, false);
                }
            }
            RunEvent::ExitRequested { api, .. } if !app.state::<quit::QuitGuard>().approved() => {
                api.prevent_exit();
                quit::request(app, false);
            }
            _ => {}
        }
        if let RunEvent::Exit = event {
            if let Some(state) = app.try_state::<BackendState>() {
                let child = state.child.lock().ok().and_then(|mut guard| guard.take());
                if let Some(child) = child {
                    let status = Arc::clone(&state.status);
                    stop_gracefully(
                        child.pid(),
                        QUIT_GRACE,
                        || status.lock().map(|s| s.exited.is_some()).unwrap_or(true),
                        || {
                            let _ = child.kill();
                        },
                    );
                }
            }
        }
    });
}

#[cfg(test)]
mod quit_tests {
    use super::*;
    use std::process::{Child, Command};

    fn exited(child: &Mutex<Child>) -> bool {
        child.lock().unwrap().try_wait().unwrap().is_some()
    }

    #[test]
    fn an_engine_that_stops_when_asked_is_not_killed() {
        let child = Mutex::new(Command::new("sleep").arg("30").spawn().unwrap());
        let pid = child.lock().unwrap().id();
        let mut killed = false;
        stop_gracefully(
            pid,
            Duration::from_secs(3),
            || exited(&child),
            || killed = true,
        );
        assert!(!killed);
        assert!(exited(&child));
    }

    #[test]
    fn an_engine_that_ignores_the_request_is_killed_after_the_grace() {
        let child = Mutex::new(
            Command::new("sh")
                .args(["-c", "trap '' TERM; exec sleep 30"])
                .spawn()
                .unwrap(),
        );
        let pid = child.lock().unwrap().id();
        // Let the shell install its trap before it is asked to stop.
        std::thread::sleep(Duration::from_millis(200));
        let started = Instant::now();
        stop_gracefully(
            pid,
            Duration::from_millis(300),
            || exited(&child),
            || {
                child.lock().unwrap().kill().unwrap();
            },
        );
        assert!(started.elapsed() >= Duration::from_millis(300));
        child.lock().unwrap().wait().unwrap();
    }
}
