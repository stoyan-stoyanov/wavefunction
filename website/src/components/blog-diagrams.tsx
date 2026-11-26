import type { ReactNode } from "react";

// Diagrams for the parallel-execution post, drawn in HTML so they reflow on a phone instead of
// shrinking. They use the site's own tokens: panels, slate text and the accent blue.

function Box({ title, children, accent = false }: { title: string; children?: ReactNode; accent?: boolean }) {
  return (
    <div className={`rounded-xl px-4 py-3 ${accent ? "bg-text text-white" : "bg-bg ring-1 ring-text/10"}`}>
      <p className="text-[14px] font-semibold">{title}</p>
      {children && <div className={`mt-1 text-[13px] leading-5 ${accent ? "text-white/70" : "text-soft"}`}>{children}</div>}
    </div>
  );
}

function Tag({ children, tone = "plain" }: { children: ReactNode; tone?: "plain" | "accent" }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 font-mono text-[11px] ${tone === "accent" ? "bg-accent/15 text-accent" : "bg-panel text-soft"}`}
    >
      {children}
    </span>
  );
}

/** An arrow between two columns: down on a phone, right from tablet width up. */
function Arrow({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-1 text-faint sm:flex-col sm:py-0" aria-hidden>
      <svg viewBox="0 0 24 24" className="size-5 rotate-90 sm:rotate-0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 12h15M14 7l5 5-5 5" />
      </svg>
      {label && <span className="text-[11px] sm:max-w-[8ch] sm:text-center">{label}</span>}
    </div>
  );
}

export function Column({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.08em] text-faint">{label}</p>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

export { Arrow, Box, Tag };

// How RepeatHoldMiddleware breaks a loop: the same non-read action comes back with the same result
// three times, so the next request leaves that tool out until the model does something else.
const HOLD_STEPS = [
  { call: "edit_file(report.py)", result: "No match for the old text" },
  { call: "edit_file(report.py)", result: "No match for the old text" },
  { call: "edit_file(report.py)", result: "No match for the old text" },
  { hold: "Three identical results: edit_file is left out of the next request, with a note saying why" },
  { call: "read_file(report.py)", result: "Reads are never held, and don’t lift the hold" },
  { call: "write_file(report.py)", result: "A different action: edit_file comes back" },
] as const;

export function HoldDiagram() {
  let count = 0;
  return (
    <ol className="space-y-2">
      {HOLD_STEPS.map((step, index) =>
        "hold" in step ? (
          <li key={index} className="rounded-xl bg-text px-4 py-3 text-[13px] font-medium text-white">
            {step.hold}
          </li>
        ) : (
          <li
            key={index}
            className="flex flex-col gap-1 rounded-xl bg-bg px-4 py-3 ring-1 ring-text/10 sm:flex-row sm:items-center sm:gap-4"
          >
            <span className="w-6 shrink-0 font-mono text-[12px] text-faint">{++count}</span>
            <code className="min-w-0 flex-1 font-mono text-[13px]">{step.call}</code>
            <span className="text-[13px] text-soft">{step.result}</span>
          </li>
        ),
      )}
    </ol>
  );
}

/** One chat splitting work across helpers: the real scenario from the orchestration check. */
export function HelpersDiagram() {
  return (
    <div className="grid items-center gap-3 sm:grid-cols-[1fr_auto_1fr_auto_1.15fr]">
      <Column label="You">
        <Box title="Work on billing, usage and exporting in parallel">Three independent deliverables</Box>
        <Box title="Also implement returns.py">Sent while it works</Box>
      </Column>
      <Arrow />
      <Column label="One chat">
        <Box title="Parent agent" accent>
          Splits the work, starts helpers, does its own part, then checks and combines the
          results
        </Box>
      </Column>
      <Arrow label="spawn_agent" />
      <Column label="Helpers">
        <Box title="billing.py">
          <Tag>shared checkout</Tag>
        </Box>
        <Box title="usage.py">
          <Tag>shared checkout</Tag>
        </Box>
        <Box title="exporting.py">
          <Tag tone="accent">own worktree</Tag>
        </Box>
        <Box title="returns.py">
          <Tag>started mid-run</Tag>
        </Box>
      </Column>
    </div>
  );
}

/** Where a message sent mid-task goes. */
export function SteeringDiagram() {
  return (
    <div className="grid items-center gap-3 sm:grid-cols-[0.9fr_auto_1fr_auto_1.2fr]">
      <Column label="You, mid-task">
        <Box title="“The free shipping threshold is 7500 cents, not 5000”">A correction to billing, sent while a helper builds it</Box>
      </Column>
      <Arrow />
      <Column label="Delivered at the next">
        <Box title="model call">or after a final answer, or at the end of the run</Box>
      </Column>
      <Arrow />
      <Column label="The parent">
        <Box title="Changes its own part">when the message is about work it’s doing</Box>
        <Box title="send_message" accent>
          passes it to the helper working on that part
        </Box>
        <Box title="spawn_agent">starts a helper for a new, independent task</Box>
      </Column>
    </div>
  );
}

const SEGMENTS = [
  { label: "System prompt", note: "the same every turn", grow: "1.3", cached: true },
  { label: "Tools", note: "sorted; new ones go last", grow: "1.6", cached: true },
  { label: "Conversation so far", note: "only ever appended to", grow: "3", cached: true },
  { label: "New", note: "this turn", grow: "0.8", cached: false },
] as const;

/** What a request looks like to the provider's prompt cache. */
export function CacheDiagram() {
  return (
    <div>
      <div className="flex flex-col gap-1.5 sm:flex-row sm:gap-[3px]">
        {SEGMENTS.map((segment) => (
          <div
            key={segment.label}
            style={{ flexGrow: segment.grow }}
            className={`basis-0 rounded-lg px-3 py-3 ${segment.cached ? "bg-text text-white" : "bg-bg ring-1 ring-text/15"}`}
          >
            <p className="text-[13px] font-semibold">{segment.label}</p>
            <p className={`text-[12px] ${segment.cached ? "text-white/65" : "text-soft"}`}>{segment.note}</p>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px] text-soft">
        <span className="flex items-center gap-2">
          <span className="size-3 rounded-[3px] bg-text" aria-hidden /> Same bytes as the last request: read from the
          cache
        </span>
        <span className="flex items-center gap-2">
          <span className="size-3 rounded-[3px] bg-bg ring-1 ring-text/20" aria-hidden /> Billed in full
        </span>
      </div>
      <p className="mt-4 rounded-xl bg-bg px-4 py-3 font-mono text-[12.5px] text-soft ring-1 ring-text/10">
        prompt_cache_key = the conversation’s id. Each helper is a conversation of its own.
      </p>
    </div>
  );
}

// Helper start-up against other agents: an emphasis chart, Wavefunction in blue (darker now,
// lighter before the fixes) and every other agent in grey. Colours checked with the dataviz
// validator; the grey falls under 3:1 on white, so every bar carries its value and a table follows.
const TONES = { now: "#2a78d6", before: "#86b6ef", other: "#9aa3b0" } as const;

type Row = { label: string; value: number; display: string; tone: keyof typeof TONES };

function BarChart({ title, rows, max, ticks }: { title: string; rows: Row[]; max: number; ticks: number[] }) {
  return (
    <div>
      <p className="text-[14px] font-semibold">{title}</p>
      <div className="relative mt-3">
        <div className="pointer-events-none absolute inset-y-0 left-[8.5rem] right-16 sm:left-[11rem]" aria-hidden>
          {ticks.map((tick) => (
            <span key={tick} className="absolute inset-y-0 w-px bg-text/10" style={{ left: `${(tick / max) * 100}%` }} />
          ))}
        </div>
        <ul className="relative space-y-2">
          {rows.map((row) => (
            <li key={row.label} className="flex items-center" title={`${row.label}: ${row.display}`}>
              <span
                className={`w-[8.5rem] shrink-0 pr-3 text-[13px] leading-4 sm:w-[11rem] ${row.tone === "other" ? "text-soft" : "font-medium text-text"}`}
              >
                {row.label}
              </span>
              <span className="flex min-w-0 flex-1 items-center pr-16">
                <span
                  className="h-[18px] shrink-0 rounded-r-[4px]"
                  style={{ width: `${(row.value / max) * 100}%`, background: TONES[row.tone] }}
                />
                <span className="-mr-16 w-16 shrink-0 whitespace-nowrap pl-2 text-[12.5px] font-medium tabular-nums text-text">{row.display}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="relative ml-[8.5rem] mr-16 mt-2 h-4 sm:ml-[11rem]" aria-hidden>
        {ticks.map((tick, index) => (
          <span
            key={tick}
            // A phone only has room for the two ends of the scale.
            className={`absolute -translate-x-1/2 text-[11px] tabular-nums text-faint ${index > 0 && index < ticks.length - 1 ? "hidden sm:block" : ""}`}
            style={{ left: `${(tick / max) * 100}%` }}
          >
            {tick.toLocaleString("en-US")}
          </span>
        ))}
      </div>
    </div>
  );
}

const SHARED: Row[] = [
  { label: "Terminal agent A", value: 44, display: "44 ms", tone: "other" },
  { label: "Terminal agent B", value: 113, display: "113 ms", tone: "other" },
  { label: "Terminal agent C", value: 144, display: "144 ms", tone: "other" },
  { label: "Wavefunction now", value: 334, display: "334 ms", tone: "now" },
  { label: "Wavefunction, Oct 31", value: 454, display: "454 ms", tone: "before" },
];

const ISOLATED: Row[] = [
  { label: "Terminal agent A", value: 182, display: "182 ms", tone: "other" },
  { label: "Wavefunction now", value: 998, display: "998 ms", tone: "now" },
  { label: "Wavefunction, Oct 31", value: 1499, display: "1,499 ms", tone: "before" },
];

export function HelperStartChart() {
  return (
    <div className="space-y-8">
      <BarChart title="Shared helper ready, milliseconds" rows={SHARED} max={500} ticks={[0, 100, 200, 300, 400, 500]} />
      <BarChart title="Isolated helper ready, milliseconds" rows={ISOLATED} max={1600} ticks={[0, 400, 800, 1200, 1600]} />
      <details className="text-[13px] text-soft">
        <summary className="cursor-pointer text-faint">Show the data</summary>
        <table className="mt-3 w-full text-left tabular-nums">
          <thead className="text-faint">
            <tr>
              <th className="py-1 pr-3 font-medium">Agent</th>
              <th className="py-1 pr-3 font-medium">Shared helper</th>
              <th className="py-1 font-medium">Isolated helper</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["Terminal agent A", "44 ms", "182 ms"],
              ["Terminal agent B", "113 ms", "No worktree helpers"],
              ["Terminal agent C", "144 ms", "No worktree helpers"],
              ["Terminal agent D", "No built-in helpers", "No built-in helpers"],
              ["Wavefunction now", "332–334 ms", "973–998 ms"],
              ["Wavefunction, Oct 31", "454 ms", "1,499 ms"],
            ].map((row) => (
              <tr key={row[0]} className="border-t border-text/10">
                {row.map((cell, index) => (
                  <td key={index} className="py-1.5 pr-3">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
