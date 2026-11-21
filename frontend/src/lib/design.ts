import { create } from "zustand";

import { request } from "@/lib/transport";
import type { Schemas } from "@/lib/types";

/**
 * Designs: themes that replace the app's look with CSS, images and fonts only, never code.
 *
 * A theme is a folder with `theme.json` and `theme.css` (see `themes/README.md`). Bundled ones are
 * in `src/themes/` and compiled in; a person's own are in the engine's themes folder, which hands
 * their stylesheet over with its assets inlined. The active one is a single <style> element at
 * the end of <body>, after every stylesheet the app or a lazy chunk puts in <head>, so switching
 * replaces its text and nothing reloads.
 */

export type Appearance = "light" | "dark";
export type Design = {
  /** "default", a bundled theme's folder, or "user:" and the folder of a person's own. */
  id: string;
  name: string;
  description: string;
  appearances: Appearance[];
  /** "theme" draws the app's own minimize, maximize and close buttons for the theme to style. */
  windowControls: "native" | "theme";
  /** Why a person's theme cannot be used. */
  error: string | null;
};

type Manifest = Partial<Pick<Design, "description" | "appearances" | "windowControls">> & { name: string };

const manifests = import.meta.glob<Manifest>("../themes/*/theme.json", { eager: true, import: "default" });
const stylesheets = import.meta.glob<string>("../themes/*/theme.css", {
  query: "?inline",
  import: "default",
});
const bundledPath = (id: string) => `../themes/${id}/theme.css`;

export const defaultDesign: Design = {
  id: "default",
  name: "Default",
  description: "Wavefunction's own look.",
  appearances: ["light", "dark"],
  windowControls: "native",
  error: null,
};

export const bundledDesigns: Design[] = [
  defaultDesign,
  ...Object.entries(manifests).map(([path, manifest]) => ({
    id: path.split("/").at(-2)!,
    name: manifest.name,
    description: manifest.description ?? "",
    appearances: manifest.appearances ?? defaultDesign.appearances,
    windowControls: manifest.windowControls ?? "native",
    error: null,
  })),
];

const USER = "user:";
const STORAGE_KEY = "wavefunction.design";
const STYLE_ID = "wavefunction-design";

function userDesign(theme: Schemas["Theme"]): Design {
  return {
    id: USER + theme.id,
    name: theme.name,
    description: theme.description,
    appearances: theme.appearances,
    windowControls: theme.window_controls,
    error: theme.error,
  };
}

type DesignState = {
  active: Design;
  /** A person's own themes, from the engine's themes folder; null until listed. */
  userDesigns: Design[] | null;
  folder: string | null;
  /** Why the last choice could not be applied. */
  error: string | null;
  /** List the themes folder again, so a theme added or fixed there shows up. */
  loadUserDesigns: () => Promise<Design[]>;
  /** Apply a design and remember it. Choosing a person's theme reads it from disk again. */
  select: (id: string) => Promise<void>;
};

let selection = 0;

export const useDesign = create<DesignState>((set, get) => ({
  active: defaultDesign,
  userDesigns: null,
  folder: null,
  error: null,
  loadUserDesigns: async () => {
    const listed = await request<Schemas["Themes"]>("/api/themes");
    const userDesigns = listed.themes.map(userDesign);
    set({ userDesigns, folder: listed.folder });
    return userDesigns;
  },
  select: async (id) => {
    const current = ++selection;
    try {
      const design = await findDesign(id, get);
      const css = await stylesheet(design);
      // A slower earlier choice must not land over a later one.
      if (current !== selection) return;
      applyStylesheet(design.id, css);
      set({ active: design, error: null });
      remember(design.id);
    } catch (error) {
      if (current !== selection) return;
      set({ error: error instanceof Error ? error.message : String(error) });
      throw error;
    }
  },
}));

async function findDesign(id: string, get: () => DesignState): Promise<Design> {
  const bundled = bundledDesigns.find((design) => design.id === id);
  if (bundled) return bundled;
  const userDesigns = id.startsWith(USER) ? await get().loadUserDesigns() : [];
  const design = userDesigns.find((item) => item.id === id);
  if (!design) throw new Error("That theme is no longer in the themes folder.");
  if (design.error) throw new Error(design.error);
  return design;
}

async function stylesheet(design: Design): Promise<string> {
  if (design.id === defaultDesign.id) return "";
  if (design.id.startsWith(USER)) {
    const folder = encodeURIComponent(design.id.slice(USER.length));
    return (await request<Schemas["ThemeStylesheet"]>(`/api/themes/${folder}/stylesheet`)).css;
  }
  return await stylesheets[bundledPath(design.id)]();
}

function applyStylesheet(id: string, css: string): void {
  let style = document.getElementById(STYLE_ID);
  if (!css) style?.remove();
  else {
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
    }
    style.textContent = css;
    if (style.parentElement !== document.body || style !== document.body.lastElementChild)
      document.body.append(style);
  }
  if (id === defaultDesign.id) delete document.documentElement.dataset.design;
  else document.documentElement.dataset.design = id;
}

function remember(id: string): void {
  try {
    if (id === defaultDesign.id) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // The choice still applies for this session when storage is unavailable.
  }
}

function saved(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * Apply the saved design at startup. A bundled one is applied before the first render, so the app
 * never shows the default look first; the caller awaits the returned promise for that. A person's
 * own needs the engine, so it is applied once the engine answers. One that has gone or broken
 * since falls back to the default look.
 */
export function restoreDesign(): Promise<void> {
  const id = saved();
  if (!id) return Promise.resolve();
  // `select` rejects only while it is still the latest choice, so this never undoes a later one.
  const restored = useDesign
    .getState()
    .select(id)
    .catch(() => {
      applyStylesheet(defaultDesign.id, "");
      useDesign.setState({ active: defaultDesign });
      remember(defaultDesign.id);
    });
  return id.startsWith(USER) ? Promise.resolve() : restored;
}

/** The one appearance a design supports, which the app then shows whatever the setting says. */
export function forcedAppearance(state: Pick<DesignState, "active">): Appearance | undefined {
  return state.active.appearances.length === 1 ? state.active.appearances[0] : undefined;
}
