import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CodingApp } from "@/coding/app";
import { restoreDesign } from "@/lib/design";
import { has } from "@/lib/platform";
import { applySavedSidebarTransparency } from "@/lib/sidebar-transparency";
import "./index.css";

// The macOS window: see-through behind the page, with the traffic lights over the toolbar. The
// Linux window is a plain one, painted like the browser's.
if (has.windowChrome()) document.documentElement.classList.add("tauri");
applySavedSidebarTransparency();

// A bundled design is in place before the first paint; a broken one leaves the default look.
void restoreDesign().finally(() =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <CodingApp />
    </StrictMode>,
  ),
);
