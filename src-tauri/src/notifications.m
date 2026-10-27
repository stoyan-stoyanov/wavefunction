#import <Foundation/Foundation.h>
#import <UserNotifications/UserNotifications.h>

// Owned for the life of the app. Callbacks copy their C strings before returning.
typedef void (*ClickCallback)(const char *);
typedef void (*ResultCallback)(void *, int);
static ClickCallback clicked;
@interface WavefunctionNotifications : NSObject <UNUserNotificationCenterDelegate>
@end
@implementation WavefunctionNotifications
- (void)userNotificationCenter:(UNUserNotificationCenter *)center didReceiveNotificationResponse:(UNNotificationResponse *)response withCompletionHandler:(void (^)(void))completion {
    if (clicked && [response.actionIdentifier isEqualToString:UNNotificationDefaultActionIdentifier]) clicked(response.notification.request.identifier.UTF8String);
    completion();
}
// Shown while the app is in front too: the chat that finished is not the one on screen.
- (void)userNotificationCenter:(UNUserNotificationCenter *)center willPresentNotification:(UNNotification *)notification withCompletionHandler:(void (^)(UNNotificationPresentationOptions))completion {
    completion(UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList | UNNotificationPresentationOptionSound);
}
@end
static WavefunctionNotifications *delegate;
// Only an app bundle has a notification center: outside one, as under `tauri dev`, asking raises.
bool wf_notifications_init(ClickCallback callback) {
    NSBundle *bundle = NSBundle.mainBundle;
    if (!bundle.bundleIdentifier || ![bundle.bundlePath.pathExtension isEqualToString:@"app"]) return false;
    clicked = callback;
    delegate = [WavefunctionNotifications new];
    [UNUserNotificationCenter currentNotificationCenter].delegate = delegate;
    return true;
}
void wf_notifications_permission(bool request, void *context, ResultCallback callback) {
    UNUserNotificationCenter *center = [UNUserNotificationCenter currentNotificationCenter];
    if (request) {
        [center requestAuthorizationWithOptions:(UNAuthorizationOptionAlert | UNAuthorizationOptionSound) completionHandler:^(BOOL allowed, NSError *error) { callback(context, error ? -1 : allowed ? 2 : 1); }];
    } else {
        [center getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings) { callback(context, (int)settings.authorizationStatus); }];
    }
}
void wf_notifications_send(const char *identifier, const char *title, const char *body, bool sound, void *context, ResultCallback callback) {
    UNMutableNotificationContent *content = [UNMutableNotificationContent new];
    content.title = [NSString stringWithUTF8String:title];
    content.body = [NSString stringWithUTF8String:body];
    if (sound) content.sound = UNNotificationSound.defaultSound;
    UNNotificationRequest *request = [UNNotificationRequest requestWithIdentifier:[NSString stringWithUTF8String:identifier] content:content trigger:nil];
    [[UNUserNotificationCenter currentNotificationCenter] addNotificationRequest:request withCompletionHandler:^(NSError *error) { callback(context, error ? -1 : 0); }];
}
