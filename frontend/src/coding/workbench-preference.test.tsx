import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const storageKey = "harness.workbench.enabled";

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
});

afterEach(() => vi.restoreAllMocks());

it("defaults to enabled and immediately shares persisted changes between consumers", async () => {
  const { useWorkbenchPreference } = await import("./workbench-preference");
  const first = renderHook(useWorkbenchPreference);
  const second = renderHook(useWorkbenchPreference);
  expect(first.result.current.enabled).toBe(true);
  expect(second.result.current.enabled).toBe(true);
  act(() => first.result.current.setEnabled(false));
  expect(first.result.current.enabled).toBe(false);
  expect(second.result.current.enabled).toBe(false);
  expect(localStorage.getItem(storageKey)).toBe("false");
  first.unmount();
  second.unmount();
  const reopened = renderHook(useWorkbenchPreference);
  expect(reopened.result.current.enabled).toBe(false);
  act(() => reopened.result.current.setEnabled(true));
  expect(localStorage.getItem(storageKey)).toBe("true");
});

it.each([false, true])(
  "retains shared changes when storage writes fail (reads blocked: %s)",
  async (readsBlocked) => {
    localStorage.setItem(storageKey, "true");
    if (readsBlocked) {
      vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
        throw new DOMException("Storage is unavailable", "SecurityError");
      });
    }
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("Storage is full", "QuotaExceededError");
    });
    const { useWorkbenchPreference } = await import("./workbench-preference");
    const first = renderHook(useWorkbenchPreference);
    const second = renderHook(useWorkbenchPreference);
    expect(first.result.current.enabled).toBe(true);
    act(() => first.result.current.setEnabled(false));
    expect(first.result.current.enabled).toBe(false);
    expect(second.result.current.enabled).toBe(false);
    first.unmount();
    const reopened = renderHook(useWorkbenchPreference);
    expect(reopened.result.current.enabled).toBe(false);
    act(() => reopened.result.current.setEnabled(true));
    expect(second.result.current.enabled).toBe(true);
  },
);
