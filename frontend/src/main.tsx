import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CodingApp } from "@/coding/app";
import { applySavedSidebarTransparency } from "@/lib/sidebar-transparency";
import "./index.css";

if ("__TAURI_INTERNALS__" in window) document.documentElement.classList.add("tauri");
applySavedSidebarTransparency();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CodingApp />
  </StrictMode>,
);
