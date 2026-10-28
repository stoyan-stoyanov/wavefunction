"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useReducedMotion } from "@/lib/use-reduced-motion";

// Each request is typed into the composer, then sent; the rest of the scene follows its send.
const FIRST = "Build a billing dashboard.";
const SECOND = "Also add a usage chart.";
const THIRD = "Use Stripe for the invoices.";
const KEYSTROKE = 45;
const FIRST_SENT = 1500;
const SECOND_SENT = FIRST_SENT + 2400;
// Sent while Billing API is still running, so the steer reaches a subagent at work.
const THIRD_SENT = SECOND_SENT + 2200;
const HELPERS = [
  { title: "Billing API", start: FIRST_SENT + 600, end: FIRST_SENT + 6400 },
  { title: "Dashboard UI", start: FIRST_SENT + 800, end: FIRST_SENT + 6000 },
  { title: "Integration tests", start: FIRST_SENT + 1000, end: FIRST_SENT + 6500 },
  { title: "Usage chart", start: SECOND_SENT + 800, end: FIRST_SENT + 7000 },
];
const FINISH = FIRST_SENT + 7300;
const START_DELAY = 1000;

/** The part of `text` typed by `clock`, finishing a short pause before `sent`; empty once sent. */
function typed(text: string, sent: number, clock: number) {
  if (clock >= sent) return "";
  const start = sent - 250 - text.length * KEYSTROKE;
  return text.slice(0, Math.max(0, Math.floor((clock - start) / KEYSTROKE)));
}

export function Timeline() {
  const instant = useReducedMotion();
  const [run, setRun] = useState(0);
  const [finished, setFinished] = useState(false);
  const finish = useCallback(() => setFinished(true), []);
  return (
    <div>
      {/* A new key mounts a fresh scene, which plays again from the start. */}
      <Scene key={run} replayed={run > 0} onFinished={finish} />
      {/* Reduced motion shows the finished scene, so there is nothing to replay. */}
      {!instant && (
        <div className="mt-3 flex justify-end sm:mt-5">
          <button
            type="button"
            onClick={() => {
              setFinished(false);
              setRun((count) => count + 1);
            }}
            disabled={!finished}
            className="inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm text-soft transition-[color,opacity] duration-300 hover:text-text disabled:pointer-events-none disabled:opacity-0"
          >
            <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M2.5 8a5.5 5.5 0 1 0 1.7-4M2.5 2v3h3" /></svg>
            Replay
          </button>
        </div>
      )}
    </div>
  );
}

function Scene({ replayed, onFinished }: { replayed: boolean; onFinished: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const instant = useReducedMotion();
  const [seen, setSeen] = useState(false);
  const playing = seen || instant;
  const [typing, setTyping] = useState("");

  useEffect(() => {
    if (instant) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // A replay starts at once, a moment after mounting so the transitions run from the start.
    if (replayed) {
      timer = setTimeout(() => setSeen(true), 50);
      return () => clearTimeout(timer);
    }
    // Start once the window is mostly in view, then pause so the reader settles on it first.
    const watcher = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          timer = setTimeout(() => setSeen(true), START_DELAY);
          watcher.disconnect();
        }
      },
      { threshold: 0.6 },
    );
    if (ref.current) watcher.observe(ref.current);
    return () => {
      watcher.disconnect();
      clearTimeout(timer);
    };
  }, [instant, replayed]);

  useEffect(() => {
    if (!seen) return;
    const timer = setTimeout(onFinished, FINISH + 400);
    return () => clearTimeout(timer);
  }, [seen, onFinished]);

  // Drive the composer's typing; everything else runs on CSS transition delays from the same start.
  useEffect(() => {
    if (!seen || instant) return;
    const started = performance.now();
    const ticker = setInterval(() => {
      const elapsed = performance.now() - started;
      // Same text bails out of rendering, so only keystrokes and sends re-render the scene.
      setTyping(
        typed(FIRST, FIRST_SENT, elapsed) ||
          typed(SECOND, SECOND_SENT, elapsed) ||
          typed(THIRD, THIRD_SENT, elapsed),
      );
      if (elapsed >= THIRD_SENT) clearInterval(ticker);
    }, KEYSTROKE / 2);
    return () => clearInterval(ticker);
  }, [seen, instant]);
  const draft = instant ? "" : typing;

  // Play once on entry. CSS handles the sequence; reduced motion shows the finished scene.
  const shown = (delay: number): CSSProperties => ({
    opacity: playing ? 1 : 0,
    transition: instant ? undefined : `opacity 400ms ease-out ${delay}ms`,
  });

  return (
    <div ref={ref} className="overflow-hidden rounded-2xl border border-text/15 bg-white shadow-[0_20px_60px_-30px_rgb(27_38_56/0.3)] [zoom:0.6] min-[400px]:[zoom:0.75] sm:[zoom:1]" role="img" aria-label="Wavefunction desktop example: Billing dashboard is selected in the sidebar, with Billing API, Dashboard UI, and Integration tests nested beneath it as subagents, each on its own branch. The conversation stays open: you ask for a usage chart and a fourth subagent starts, then you ask for Stripe invoices and the message goes to the Billing API subagent while it works. All four complete for review.">
      {/* Keep the app window in one row; scale the whole illustration to fit phones. */}
      <div aria-hidden className="grid grid-cols-[210px_minmax(0,1fr)] lg:grid-cols-[248px_minmax(0,1fr)]">
        <div className="flex min-h-[570px] min-w-0 flex-col border-r border-text/10 bg-panel">
          <div className="flex h-12 items-center gap-1.5 px-5">
            <span className="size-2.5 rounded-full bg-[#ed6a5e]" />
            <span className="size-2.5 rounded-full bg-[#f4bf50]" />
            <span className="size-2.5 rounded-full bg-[#61c554]" />
            <svg viewBox="0 0 20 20" className="ml-auto size-4 text-faint" fill="none" stroke="currentColor" strokeWidth="1.2"><rect x="3" y="4" width="14" height="12" rx="2" /><path d="M8 4v12" /></svg>
          </div>
          <div className="px-3 pb-5 pt-2 text-[13px]">
            <div className="flex items-center gap-2.5 px-2 py-2 text-soft">
              <svg viewBox="0 0 20 20" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><path d="M9 4H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-5M12 3l5 5M8 12l1-4 6-6 3 3-6 6-4 1Z" /></svg>
              New project
            </div>
            <div className="mb-3 mt-6 px-2 text-xs font-medium text-faint">Projects</div>
            <div className="flex items-center gap-2 px-2 py-2.5 font-medium">
              <svg viewBox="0 0 12 12" className="size-3 text-faint" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><path d="m3 4 3 3 3-3" /></svg>
              <svg viewBox="0 0 20 20" className="size-[18px] text-accent" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><path d="m10 2 8 4-8 4-8-4 8-4Zm-8 8 8 4 8-4M2 14l8 4 8-4" /></svg>
              Acme
            </div>
            <ul className="space-y-1">
              <li>
                <div className="flex items-center gap-2 rounded-lg bg-text/8 py-2.5 pl-7 pr-3 font-medium">
                  <span className="min-w-0 flex-1 truncate">Billing dashboard</span>
                  <TaskStatus playing={playing} instant={instant} start={FIRST_SENT} end={FINISH} />
                </div>
                <ul className="pt-1">
                  {HELPERS.map((helper) => (
                    <li key={helper.title} className="grid" style={{
                      gridTemplateRows: playing ? "1fr" : "0fr",
                      opacity: playing ? 1 : 0,
                      transition: instant ? undefined : `grid-template-rows 350ms ease-out ${helper.start}ms, opacity 350ms ease-out ${helper.start}ms`,
                    }}>
                      <div className="min-h-0 overflow-hidden">
                        <div className="flex items-center gap-2 py-2.5 pl-7 pr-3 text-soft">
                          {/* A branch: each subagent works in a worktree of its own. */}
                          <svg viewBox="0 0 16 16" className="size-3 shrink-0 text-faint/70" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><circle cx="4.5" cy="3.5" r="1.5" /><circle cx="4.5" cy="12.5" r="1.5" /><circle cx="11.5" cy="5" r="1.5" /><path d="M4.5 5v6M11.5 6.5c0 2.6-2.2 3.8-5.3 4.6" /></svg>
                          <span className="min-w-0 flex-1 truncate text-xs">{helper.title}</span>
                          <TaskStatus playing={playing} instant={instant} start={helper.start} end={helper.end} />
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </li>
              <li className="truncate py-2.5 pl-7 pr-3 text-faint">Update the docs</li>
            </ul>
          </div>
          <div className="mt-auto flex items-center gap-2 px-5 pb-5 text-xs text-faint">
            <svg viewBox="0 0 20 20" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"><path d="M3 6h14M3 14h14" /><circle cx="7" cy="6" r="2" fill="var(--panel)" /><circle cx="13" cy="14" r="2" fill="var(--panel)" /></svg>
            Settings
          </div>
        </div>

        <div className="flex min-w-0 flex-col">
          <div className="flex h-12 items-center gap-2 border-b border-text/8 px-5 text-xs font-medium text-soft sm:text-[13px]">
            <svg viewBox="0 0 20 20" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H7l-4 3V5a1 1 0 0 1 1-1Z" /></svg>
            Billing dashboard
          </div>
          <div className="flex flex-1 flex-col gap-5 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
            <div className="ml-auto max-w-[90%] rounded-2xl rounded-br-md bg-panel px-4 py-2.5 text-[13px] leading-relaxed sm:text-sm" style={shown(FIRST_SENT)}>
              {FIRST}
            </div>
            <p className="max-w-[40ch] text-[13px] leading-relaxed text-soft sm:text-sm" style={shown(FIRST_SENT + 350)}>
              Starting three subagents for the API, interface, and tests, each in its own worktree.
            </p>
            <div className="ml-auto max-w-[90%] rounded-2xl rounded-br-md bg-accent/10 px-4 py-2.5 text-[13px] leading-relaxed sm:text-sm" style={shown(SECOND_SENT)}>
              {SECOND}
            </div>
            <p className="text-[13px] leading-relaxed text-soft sm:text-sm" style={shown(SECOND_SENT + 500)}>
              On it. A fourth subagent takes the chart; the rest keep moving.
            </p>
            <div className="ml-auto max-w-[90%] rounded-2xl rounded-br-md bg-accent/10 px-4 py-2.5 text-[13px] leading-relaxed sm:text-sm" style={shown(THIRD_SENT)}>
              {THIRD}
            </div>
            <p className="text-[13px] leading-relaxed text-soft sm:text-sm" style={shown(THIRD_SENT + 500)}>
              Passed to Billing API. It’s adjusting without starting over.
            </p>
            <div className="flex items-center gap-2 text-xs text-done sm:text-[13px]" style={shown(FINISH)}>
              <Check /> All four tasks are ready to review.
            </div>
            <div className="mt-auto flex items-center justify-between gap-3 rounded-xl border border-text/10 bg-panel/50 px-3 py-3 text-xs text-faint sm:text-[13px]">
              {draft ? (
                <span className="min-w-0 truncate text-text">
                  {draft}
                  <span className="ml-px inline-block h-[1.1em] w-px translate-y-[0.2em] bg-text" />
                </span>
              ) : (
                "Ask anything, anytime"
              )}
              <span className="grid size-6 shrink-0 place-items-center rounded-full bg-text text-white">
                <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 12V4m-4 4 4-4 4 4" /></svg>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function TaskStatus({ playing, instant, start, end }: { playing: boolean; instant: boolean; start: number; end: number }) {
  const transition = instant ? undefined : `opacity 300ms ease-out ${end}ms`;
  return (
    <span className="relative size-3.5 shrink-0">
      <span className="absolute inset-0 text-accent" style={{
        opacity: playing ? 0 : 1,
        transform: `rotate(${playing ? 1080 : 0}deg)`,
        transition: instant ? undefined : `${transition}, transform ${end - start}ms linear ${start}ms`,
      }}>
        <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"><circle cx="8" cy="8" r="5.5" opacity="0.15" /><path d="M8 2.5A5.5 5.5 0 0 1 13.5 8" /></svg>
      </span>
      <span className="absolute inset-0 text-done" style={{ opacity: playing ? 1 : 0, transition }}><Check /></span>
    </span>
  );
}

function Check() {
  return (
    <svg viewBox="0 0 16 16" className="size-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="m3 8 3 3 7-7" />
    </svg>
  );
}
