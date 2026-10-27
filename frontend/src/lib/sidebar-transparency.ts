export type SidebarTheme = "light" | "dark";

const defaults = { light: 85, dark: 75 };
const storageKey = (theme: SidebarTheme) => `wavefunction.sidebar-transparency.${theme}`;

export function readSidebarTransparency(theme: SidebarTheme): number {
  try {
    const stored = window.localStorage.getItem(storageKey(theme));
    const value = stored?.trim() ? Number(stored) : NaN;
    if (Number.isFinite(value) && value >= 0 && value <= 100) return value;
  } catch {
    // Restricted previews can omit localStorage.
  }
  return defaults[theme];
}

function applyTransparency(theme: SidebarTheme, value: number): void {
  document.documentElement.style.setProperty(`--sidebar-${theme}-opacity`, `${100 - value}%`);
}

export function applySavedSidebarTransparency(): void {
  for (const theme of ["light", "dark"] as const) {
    applyTransparency(theme, readSidebarTransparency(theme));
  }
}

export function setSidebarTransparency(theme: SidebarTheme, value: number): void {
  applyTransparency(theme, value);
  try {
    window.localStorage.setItem(storageKey(theme), String(value));
  } catch {
    // The slider still applies immediately when storage is unavailable.
  }
}
