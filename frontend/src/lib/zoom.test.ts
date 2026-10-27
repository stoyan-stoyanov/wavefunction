import { beforeEach, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  document.documentElement.style.zoom = "";
});

it("starts new windows at 115% and resets to that default", async () => {
  const { zoomBy, setZoom, defaultZoom } = await import("./zoom");
  zoomBy(1);
  expect(localStorage.getItem("wavefunction.zoom")).toBe("1.3");
  setZoom(defaultZoom);
  expect(localStorage.getItem("wavefunction.zoom")).toBe("1.15");
  expect(document.documentElement.style.zoom).toBe("1.15");
  expect(document.documentElement.style.getPropertyValue("--css-zoom")).toBe("1.15");
});

it("preserves a saved 100% preference", async () => {
  localStorage.setItem("wavefunction.zoom", "1");
  const { zoomBy } = await import("./zoom");
  zoomBy(1);
  expect(localStorage.getItem("wavefunction.zoom")).toBe("1.15");
});

it("recovers an invalid preference without exceeding the zoom ladder", async () => {
  localStorage.setItem("wavefunction.zoom", "invalid");
  const { zoomBy } = await import("./zoom");
  zoomBy(1);
  expect(localStorage.getItem("wavefunction.zoom")).toBe("1.3");
  zoomBy(100);
  expect(localStorage.getItem("wavefunction.zoom")).toBe("2");
  zoomBy(-100);
  expect(localStorage.getItem("wavefunction.zoom")).toBe("0.75");
});
