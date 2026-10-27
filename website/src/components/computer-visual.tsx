"use client";

import { useEffect, useRef, useState } from "react";
import { SandboxIcon } from "@/components/icons";
import { useReducedMotion } from "@/lib/use-reduced-motion";

// What the app's desktop dock says at each moment, as in the real app, and when it changes (ms).
const PHASES = [
  { at: 0, step: "Looking at the screen" },
  { at: 900, step: "Moving the pointer" },
  { at: 2000, step: "Clicking" },
  { at: 2500, step: "Waiting for the page" },
  { at: 3400, step: null },
];
const DONE = PHASES.length - 1;
const START_DELAY = 600;
// Where the pointer starts, as fractions of the page area. It ends on the button, wherever the
// layout puts it.
const FROM = "translate(20%, 22%)";

/** The agent's own desktop. Plays once when it scrolls into view; reduced motion shows the end. */
export function ComputerVisual() {
  const ref = useRef<HTMLDivElement>(null);
  const page = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLSpanElement>(null);
  const instant = useReducedMotion();
  const [phase, setPhase] = useState(0);
  const [target, setTarget] = useState(FROM);
  const shown = instant ? DONE : phase;

  // The button's centre as fractions of the page area, which the pointer's translate is measured
  // against, so the pointer lands on it at any width.
  useEffect(() => {
    const area = page.current;
    const control = button.current;
    if (!area || !control) return;
    const aim = () =>
      setTarget(
        `translate(${((control.offsetLeft + control.offsetWidth / 2) / area.clientWidth) * 100}%, ${
          ((control.offsetTop + control.offsetHeight / 2) / area.clientHeight) *
          100
        }%)`,
      );
    aim();
    const resized = new ResizeObserver(aim);
    resized.observe(area);
    return () => resized.disconnect();
  }, []);

  useEffect(() => {
    if (instant) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const watcher = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        watcher.disconnect();
        PHASES.forEach(({ at }, index) => {
          if (index)
            timers.push(setTimeout(() => setPhase(index), START_DELAY + at));
        });
      },
      { threshold: 0.5 },
    );
    if (ref.current) watcher.observe(ref.current);
    return () => {
      watcher.disconnect();
      timers.forEach(clearTimeout);
    };
  }, [instant]);

  const moved = shown >= 1;
  const pressed = shown === 2;
  const clicked = shown >= 2;
  const done = shown === DONE;
  const step = PHASES[shown].step;

  return (
    <figure
      ref={ref}
      className="reveal min-w-0 rounded-[28px] bg-panel p-3 sm:p-6"
      role="img"
      aria-label="Wavefunction's own isolated desktop: Firefox open on a billing portal. The agent's pointer clicks Download report and the report downloads, while the dock below shows each step and a Take control button."
    >
      <div aria-hidden>
        <div className="overflow-hidden rounded-2xl border border-text/15 bg-[linear-gradient(160deg,#e3e9f1,#c7d3e2)] shadow-[0_20px_60px_-30px_rgb(27_38_56/0.35)]">
          {/* The desktop's top bar. */}
          <div className="flex h-7 items-center gap-2 bg-text/85 px-3 text-[10px] font-medium text-white/85">
            <SandboxIcon className="size-3 text-white/80" />
            <span>Isolated desktop</span>
            <span className="ml-auto tabular-nums">10:42</span>
          </div>
          <div className="relative p-3 sm:p-6">
            {/* Firefox. */}
            <div className="overflow-hidden rounded-lg bg-white shadow-[0_12px_30px_-14px_rgb(27_38_56/0.45)] ring-1 ring-text/10">
              <div className="flex items-center gap-2 border-b border-text/10 bg-panel/60 px-3 py-2">
                <span className="flex gap-1">
                  <span className="size-2 rounded-full bg-text/20" />
                  <span className="size-2 rounded-full bg-text/20" />
                  <span className="size-2 rounded-full bg-text/20" />
                </span>
                <span className="min-w-0 flex-1 truncate rounded-md bg-white px-2.5 py-1 font-mono text-[10px] text-soft ring-1 ring-text/10 sm:text-[11px]">
                  billing.example.com/invoices
                </span>
              </div>
              <div
                ref={page}
                className="relative flex h-[230px] flex-col overflow-hidden p-4 lg:h-[270px]"
              >
                <span className="block h-2.5 w-2/5 rounded-full bg-text/25" />
                <div className="mt-5 space-y-2.5">
                  {[
                    ["Mar 2026", "$1,240"],
                    ["Feb 2026", "$1,180"],
                    ["Jan 2026", "$1,215"],
                  ].map(([month, amount], index) => (
                    <div
                      key={month}
                      className={`flex items-center justify-between rounded-md px-2.5 py-2 text-[11px] ${index === 0 ? "bg-accent/10 font-medium" : "bg-panel/70 text-soft"}`}
                    >
                      <span>{month}</span>
                      <span className="tabular-nums">{amount}</span>
                    </div>
                  ))}
                </div>
                {/* Under the invoices, level with their right edge. */}
                <div className="mt-auto flex justify-end">
                  <span
                    ref={button}
                    className={`relative flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[10.5px] font-medium text-white transition-[transform,background-color] duration-150 sm:text-[11px] ${
                      done
                        ? "bg-done"
                        : pressed
                          ? "scale-90 bg-accent"
                          : "bg-text"
                    }`}
                  >
                    {done ? (
                      <svg
                        viewBox="0 0 16 16"
                        className="size-3"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="m3 8 3 3 7-7" />
                      </svg>
                    ) : null}
                    {done ? "Downloaded" : "Download report"}
                    {clicked ? (
                      <>
                        <span className="computer-ripple absolute left-1/2 top-1/2 size-10 rounded-full border-2 border-accent" />
                        <span
                          className="computer-ripple absolute left-1/2 top-1/2 size-10 rounded-full bg-accent/25"
                          style={{ animationDelay: "120ms" }}
                        />
                      </>
                    ) : null}
                  </span>
                </div>
                <span
                  className="pointer-events-none absolute inset-0 transition-transform duration-[1000ms] ease-[cubic-bezier(0.45,0,0.25,1)]"
                  style={{ transform: moved ? target : FROM }}
                >
                  <svg
                    viewBox="0 0 16 16"
                    className={`size-4 -translate-x-[2px] -translate-y-[1px] drop-shadow-[0_1px_1px_rgb(27_38_56/0.35)] transition-transform duration-150 ${pressed ? "scale-75" : ""}`}
                  >
                    <path
                      d="M2 1.5v11l3-2.6 2 4.6 2-.9-2-4.5H11z"
                      fill="#1b2d47"
                      stroke="#fff"
                      strokeWidth="1.1"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
              </div>
            </div>
            {/* The download, as a desktop notification. */}
            <div
              className={`absolute right-4 top-4 flex items-center gap-2.5 rounded-xl bg-white px-3 py-2 text-[11px] shadow-[0_10px_30px_-12px_rgb(27_38_56/0.45)] ring-1 ring-text/10 transition-[opacity,transform] duration-500 sm:right-7 sm:top-7 ${
                done ? "translate-y-0 opacity-100" : "-translate-y-2 opacity-0"
              }`}
            >
              <svg
                viewBox="0 0 16 16"
                className="size-4 text-accent"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.3"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M4 1.5h5l3 3v10H4zM9 1.5v3h3M8 7v5m-2-2 2 2 2-2" />
              </svg>
              <span>
                <span className="block font-medium">Report-March.pdf</span>
                <span className="block text-faint">Downloads</span>
              </span>
            </div>
          </div>
        </div>
        {/* The app's desktop dock: the current step, as in the app, and Take control. */}
        <div className="mt-3 flex items-center gap-3 rounded-xl border border-text/10 bg-white px-3.5 py-2.5 sm:mt-4">
          {step ? (
            <span className="flex min-w-0 items-center gap-2 text-xs font-medium">
              <span className="size-1.5 shrink-0 rounded-full bg-[#ef4444]" />
              <span className="truncate">{step}</span>
            </span>
          ) : (
            <span className="min-w-0">
              <span className="block text-xs font-medium">Desktop</span>
              <span className="block truncate text-[11px] text-faint">
                Still open for follow-ups
              </span>
            </span>
          )}
          <span className="ml-auto shrink-0 rounded-full bg-text px-3 py-1.5 text-[11px] font-medium text-white">
            Take control
          </span>
        </div>
      </div>
    </figure>
  );
}
