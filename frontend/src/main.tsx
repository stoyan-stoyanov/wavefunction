import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CodingApp } from "@/coding/app";
import { restoreDesign } from "@/lib/design";
import { applySavedSidebarTransparency } from "@/lib/sidebar-transparency";
import "./index.css";

if ("__TAURI_INTERNALS__" in window) document.documentElement.classList.add("tauri");
applySavedSidebarTransparency();

// A bundled design is in place before the first paint; a broken one leaves the default look.
void restoreDesign().finally(() =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <CodingApp />
    </StrictMode>,
  ),
);
