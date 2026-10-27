//! Quitting from the app menu, Cmd+Q, the Dock, AppleScript or logout asks the same question
//! as closing the window. Each sends -[NSApplication terminate:], which tao answers by exiting
//! at once: its app delegate has no applicationShouldTerminate:, so Tauri never reports an
//! ExitRequested for the run loop's quit check. This gives tao's delegate class that method.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

use objc2::runtime::{AnyClass, AnyObject, Imp, Sel};
use objc2::{class, ffi, msg_send, sel};
use tauri::Manager;

use super::quit::{self, QuitGuard};

static APP: OnceLock<tauri::AppHandle> = OnceLock::new();
/// A terminate: is waiting for `reply`: logout and shutdown resume only once it is answered.
static PENDING: AtomicBool = AtomicBool::new(false);

// NSApplicationTerminateReply.
const TERMINATE_NOW: usize = 1;
const TERMINATE_LATER: usize = 2;

extern "C-unwind" fn should_terminate(_: *mut AnyObject, _: Sel, _: *mut AnyObject) -> usize {
    match APP.get() {
        Some(app) if !app.state::<QuitGuard>().approved() => {
            PENDING.store(true, Ordering::SeqCst);
            quit::request(app, false);
            TERMINATE_LATER
        }
        _ => TERMINATE_NOW,
    }
}

/// Answer a deferred terminate: with the user's decision. Returns false when none is waiting,
/// for the caller to exit through Tauri instead.
pub(crate) fn reply(app: &tauri::AppHandle, quit: bool) -> bool {
    if !PENDING.swap(false, Ordering::SeqCst) {
        return false;
    }
    let _ = app.run_on_main_thread(move || {
        // Safety: the main thread, answering the applicationShouldTerminate: that returned Later.
        unsafe {
            let application: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
            let _: () = msg_send![application, replyToApplicationShouldTerminate: objc2::runtime::Bool::new(quit)];
        }
    });
    true
}

/// Route macOS quit requests through `quit::request`. Call from setup, on the main thread,
/// after tao has installed its delegate. Returns whether the method was added.
pub(crate) fn install(app: &tauri::AppHandle) -> bool {
    let _ = APP.set(app.clone());
    // Safety: plain AppKit messages on the main thread. The method is added to the class of the
    // delegate tao installed, which defines no applicationShouldTerminate: of its own, with the
    // type encoding of `- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)`.
    unsafe {
        let application: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
        let delegate: *mut AnyObject = msg_send![application, delegate];
        if delegate.is_null() {
            return false;
        }
        let class = (*delegate).class() as *const AnyClass as *mut AnyClass;
        let method: extern "C-unwind" fn(*mut AnyObject, Sel, *mut AnyObject) -> usize =
            should_terminate;
        let imp: Imp = std::mem::transmute(method);
        ffi::class_addMethod(
            class,
            sel!(applicationShouldTerminate:),
            imp,
            c"Q@:@".as_ptr(),
        )
        .as_bool()
    }
}
