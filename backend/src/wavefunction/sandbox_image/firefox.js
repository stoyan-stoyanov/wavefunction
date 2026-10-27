// Firefox's settings on the desktop, for every profile, including the one an agent keeps from one
// desktop to the next: Debian's Firefox reads every file in /etc/firefox-esr, outside that home.
//
// A desktop is removed by stopping it, which Firefox takes for a crash. On its own it then reopens
// the last desktop's tabs, and from the second such stop shows "Restore Session" instead. Each
// desktop opens on one clean window, as it did when every desktop was new. Sign-ins a site keeps
// after the browser closes stay; one that ends with the browser session ends here too.
pref("browser.sessionstore.resume_from_crash", false);
// Nor, in their place, a bar that offers to open the previous tabs.
pref("browser.startup.couldRestoreSession.count", -1);
// No saved passwords. The profile stays with the agent from one desktop to the next, and the agent
// could open about:logins and read one; a site's sign-in cookie is what keeps it signed in.
pref("signon.rememberSignons", false);
// The page cache lives outside the kept home, which it would otherwise grow toward a gigabyte.
pref("browser.cache.disk.parent_directory", "/tmp/firefox-cache");
