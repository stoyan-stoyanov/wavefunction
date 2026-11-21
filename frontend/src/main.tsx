import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CodingApp } from "@/coding/app";
import { linuxDesktop } from "@/lib/platform";
import { applySavedSidebarTransparency } from "@/lib/sidebar-transparency";
import "./index.css";

// The macOS window: see-through behind the page, with the traffic lights over the toolbar. The
// Linux window is a plain one, painted like the browser's.
if ("__TAURI_INTERNALS__" in window && !linuxDesktop()) document.documentElement.classList.add("tauri");
applySavedSidebarTransparency();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CodingApp />
  </StrictMode>,
);
