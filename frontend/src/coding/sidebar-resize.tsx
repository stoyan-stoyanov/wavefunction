import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

function savedWidth(storageKey: string, defaultWidth: number, minimumWidth: number, maximumWidth: number) {
  try {
    const value = Number(localStorage.getItem(storageKey));
    if (value >= minimumWidth && value <= maximumWidth) return value;
  } catch {
    // Browser previews can disable storage.
  }
  return defaultWidth;
}

type Drag = {
  pointerId: number;
  startX: number;
  startWidth: number;
  width: number;
  maximum: number;
  scale: number;
  handle: HTMLDivElement;
  frame: number | null;
  cursor: string;
  userSelect: string;
};

export function ResizableSidebar({
  children,
  side = "left",
  expanded = false,
  onResize,
}: {
  children: ReactNode;
  side?: "left" | "right";
  expanded?: boolean;
  onResize?: () => void;
}) {
  const defaultWidth = side === "right" ? 390 : 250;
  const minimumWidth = side === "right" ? 300 : 200;
  const maximumWidth = side === "right" ? 800 : 420;
  const storageKey = side === "right" ? "harness.workbenchWidth" : "harness.sidebarWidth";
  const direction = side === "right" ? -1 : 1;
  const [width, setWidth] = useState(() => savedWidth(storageKey, defaultWidth, minimumWidth, maximumWidth));
  const displayedWidth = expanded ? 640 : width;
  const container = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  function limit() {
    const parent = container.current?.parentElement;
    const available = parent?.clientWidth;
    const otherSidebar = parent?.querySelector<HTMLElement>(
      side === "right" ? ".coding-sidebar-frame:not(.coding-workbench-frame)" : ".coding-workbench-frame",
    );
    return Math.max(
      minimumWidth,
      Math.min(maximumWidth, available ? available - (otherSidebar?.offsetWidth ?? 0) - 360 : maximumWidth),
    );
  }
  function save(next: number) {
    const value = Math.max(minimumWidth, Math.min(limit(), next));
    container.current?.style.setProperty("--sidebar-preferred-width", `${value}px`);
    setWidth(value);
    onResize?.();
    try {
      localStorage.setItem(storageKey, String(value));
    } catch {
      // Resizing still works without persistence.
    }
  }
  function paint(current: Drag) {
    container.current?.style.setProperty("--sidebar-preferred-width", `${current.width}px`);
    current.handle.setAttribute("aria-valuenow", String(Math.round(current.width)));
  }
  function release(current: Drag) {
    if (current.frame !== null) cancelAnimationFrame(current.frame);
    const root = document.documentElement;
    root.style.cursor = current.cursor;
    root.style.userSelect = current.userSelect;
    root.removeAttribute("data-sidebar-resizing");
  }
  function finish(commit: boolean) {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    release(current);
    if (commit) save(current.width);
    else {
      container.current?.style.setProperty("--sidebar-preferred-width", `${displayedWidth}px`);
      current.handle.setAttribute("aria-valuenow", String(displayedWidth));
    }
    if (current.handle.hasPointerCapture(current.pointerId))
      current.handle.releasePointerCapture(current.pointerId);
  }
  useEffect(
    () => () => {
      const current = drag.current;
      if (current) release(current);
    },
    [],
  );

  return (
    <div
      ref={container}
      className={`coding-sidebar-frame${side === "right" ? " coding-workbench-frame" : ""}`}
      style={{ "--sidebar-preferred-width": `${displayedWidth}px` } as CSSProperties}
    >
      {children}
      <div
        className="coding-sidebar-resize"
        role="separator"
        aria-label={side === "right" ? "Resize workbench" : "Resize sidebar"}
        aria-orientation="vertical"
        aria-valuemin={minimumWidth}
        aria-valuemax={maximumWidth}
        aria-valuenow={displayedWidth}
        tabIndex={0}
        onMouseDown={(event) => event.stopPropagation()}
        onPointerDown={(event) => {
          const element = container.current;
          const parent = element?.parentElement;
          if (event.button !== 0 || !element || !parent || drag.current) return;
          event.preventDefault();
          const scale = parent.clientWidth ? parent.getBoundingClientRect().width / parent.clientWidth : 1;
          const initial = element.offsetWidth || displayedWidth;
          drag.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startWidth: initial,
            width: initial,
            maximum: limit(),
            scale: scale || 1,
            handle: event.currentTarget,
            frame: null,
            cursor: document.documentElement.style.cursor,
            userSelect: document.documentElement.style.userSelect,
          };
          event.currentTarget.setPointerCapture(event.pointerId);
          document.documentElement.style.cursor = "col-resize";
          document.documentElement.style.userSelect = "none";
          // Width changes every frame while dragging; transitions would chase it.
          document.documentElement.setAttribute("data-sidebar-resizing", "");
        }}
        onPointerMove={(event) => {
          const current = drag.current;
          if (!current || current.pointerId !== event.pointerId) return;
          current.width = Math.max(
            minimumWidth,
            Math.min(
              current.maximum,
              current.startWidth + (direction * (event.clientX - current.startX)) / current.scale,
            ),
          );
          if (current.frame === null)
            current.frame = requestAnimationFrame(() => {
              current.frame = null;
              paint(current);
            });
        }}
        onPointerUp={(event) => {
          if (drag.current?.pointerId === event.pointerId) finish(true);
        }}
        onPointerCancel={(event) => {
          if (drag.current?.pointerId === event.pointerId) finish(false);
        }}
        onLostPointerCapture={(event) => {
          if (drag.current?.pointerId === event.pointerId) finish(true);
        }}
        onDoubleClick={() => save(defaultWidth)}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
          event.preventDefault();
          const current = container.current?.offsetWidth || displayedWidth;
          save(
            event.key === "Home"
              ? defaultWidth
              : current + direction * (event.key === "ArrowRight" ? 16 : -16),
          );
        }}
      />
    </div>
  );
}
