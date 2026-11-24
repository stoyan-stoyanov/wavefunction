import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CodingApp } from "@/coding/app";
import { restoreDesign } from "@/lib/design";
import { installLinuxKeys } from "@/lib/linux-keys";
import { has, readLinuxPackage } from "@/lib/platform";
import { applySavedSidebarTransparency } from "@/lib/sidebar-transparency";
import "./index.css";

// The macOS window: see-through behind the page, with the traffic lights over the toolbar. The
// Linux window is a plain one, painted like the browser's.
if (has.windowChrome()) document.documentElement.classList.add("tauri");
applySavedSidebarTransparency();
// Quit and full screen, which the macOS menu bar gives the Mac window.
if (!has.systemMenu()) installLinuxKeys();

// Before the first paint: a bundled design, where a broken one leaves the default look, and the
// Linux package, which decides whether the app offers updates.
void Promise.allSettled([restoreDesign(), readLinuxPackage()]).then(() =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <CodingApp />
    </StrictMode>,
  ),
);
