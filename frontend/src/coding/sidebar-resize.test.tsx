import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResizableSidebar } from "./sidebar-resize";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.style.cursor = "";
  document.documentElement.style.userSelect = "";
  vi.stubGlobal(
    "PointerEvent",
    class extends MouseEvent {
      pointerId: number;
      constructor(type: string, init: PointerEventInit) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
      }
    },
  );
});

function open() {
  const view = render(
    <ResizableSidebar>
      <aside>Conversations</aside>
    </ResizableSidebar>,
  );
  const handle = screen.getByRole("separator", { name: "Resize sidebar" });
  handle.setPointerCapture = vi.fn();
  handle.hasPointerCapture = () => false;
  return { view, handle };
}

it("resizes with the keyboard, remembers the width, and resets on Home", () => {
  const { view, handle } = open();
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  expect(handle).toHaveAttribute("aria-valuenow", "266");
  view.unmount();
  const reopened = open().handle;
  expect(reopened).toHaveAttribute("aria-valuenow", "266");
  fireEvent.keyDown(reopened, { key: "Home" });
  expect(reopened).toHaveAttribute("aria-valuenow", "250");
});

it("paints a drag once per frame and commits on release, accounting for zoom", () => {
  const { view, handle } = open();
  const frame = handle.parentElement!;
  Object.defineProperty(frame, "offsetWidth", { value: 250 });
  Object.defineProperty(view.container, "clientWidth", { value: 1000 });
  vi.spyOn(view.container, "getBoundingClientRect").mockReturnValue({
    width: 1150,
  } as DOMRect);
  const frames = vi.spyOn(window, "requestAnimationFrame");
  fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: 287.5 });
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 402.5 });
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 402.5 });
  expect(frames).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem("harness.sidebarWidth")).toBeNull();
  fireEvent.pointerUp(handle, { pointerId: 1 });
  expect(handle).toHaveAttribute("aria-valuenow", "350");
  expect(localStorage.getItem("harness.sidebarWidth")).toBe("350");
  expect(document.documentElement.style.cursor).toBe("");
  expect(document.documentElement).not.toHaveAttribute("data-sidebar-resizing");
});

it("restores selection and the cursor when navigating away during a drag", () => {
  const { view, handle } = open();
  fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: 250 });
  expect(document.documentElement.style.cursor).toBe("col-resize");
  expect(document.documentElement).toHaveAttribute("data-sidebar-resizing");
  view.unmount();
  expect(document.documentElement.style.cursor).toBe("");
  expect(document.documentElement).not.toHaveAttribute("data-sidebar-resizing");
  expect(document.documentElement.style.userSelect).toBe("");
});

it("accounts for the other sidebar so resizing leaves room for the chat", () => {
  const view = render(
    <>
      <div className="coding-sidebar-frame" data-testid="left" />
      <ResizableSidebar side="right">
        <aside>Workbench</aside>
      </ResizableSidebar>
    </>,
  );
  const handle = screen.getByRole("separator", { name: "Resize workbench" });
  Object.defineProperty(view.container, "clientWidth", { value: 1100 });
  Object.defineProperty(screen.getByTestId("left"), "offsetWidth", {
    value: 350,
  });
  for (let index = 0; index < 30; index++) fireEvent.keyDown(handle, { key: "ArrowLeft" });
  expect(handle).toHaveAttribute("aria-valuenow", "390");
});
