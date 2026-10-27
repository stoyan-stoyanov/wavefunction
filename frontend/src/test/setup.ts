import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node 25's unset localStorage global can shadow jsdom's own Storage object.
class BrowserStorage implements Storage {
  private values = new Map<string, string>();
  get length() {
    return this.values.size;
  }
  clear() {
    this.values.clear();
  }
  getItem(key: string) {
    return this.values.get(String(key)) ?? null;
  }
  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.values.delete(String(key));
  }
  setItem(key: string, value: string) {
    this.values.set(String(key), String(value));
  }
}
const browserStorage = window.localStorage ?? new BrowserStorage();
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: browserStorage,
});
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: browserStorage,
});

afterEach(() => cleanup());

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, "ResizeObserver", {
  configurable: true,
  writable: true,
  value: TestResizeObserver,
});

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

Object.defineProperty(Element.prototype, "scrollIntoView", {
  writable: true,
  value: () => undefined,
});

Object.defineProperty(Element.prototype, "scrollTo", {
  writable: true,
  value: () => undefined,
});

// jsdom does not implement browser blob URLs used by attachment previews.
URL.createObjectURL ??= () => "blob:test-preview";
URL.revokeObjectURL ??= () => undefined;
