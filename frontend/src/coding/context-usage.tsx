// How full the chat's context is, as its last request reported it: a ring at the message box's
// bottom left that fills toward the point where the conversation is compacted, and what fills it.
import { useMemo, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { isActive, type CodingRun } from "./api";

const parts = [
  { key: "system_prompt", label: "System prompt", shade: "bg-foreground/80" },
  { key: "tools", label: "Tool descriptions", shade: "bg-foreground/50" },
  { key: "documents", label: "Attached documents", shade: "bg-foreground/30" },
  { key: "conversation", label: "Conversation", shade: "bg-foreground/15" },
] as const;
type Breakdown = Record<(typeof parts)[number]["key"], number>;

interface ContextUsage {
  used: number;
  /** Where the conversation is summarised. */
  compactAt: number;
  breakdown: Breakdown;
}

const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;
const isBreakdown = (value: unknown): value is Breakdown =>
  typeof value === "object" &&
  value !== null &&
  parts.every(({ key }) => count((value as Record<string, unknown>)[key]));
const tokens = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

/** The context as of the chat's last request, or of a compaction since, when only the summary
 *  is left of the conversation. Null before the first request. */
function contextUsage(runs: CodingRun[]): ContextUsage | null {
  let summary: number | undefined;
  for (const run of [...runs].reverse()) {
    for (const event of [...run.events].reverse()) {
      const { payload } = event;
      if (
        summary === undefined &&
        run.kind === "compact" &&
        event.event_type === "summarization_finished" &&
        count(payload.summary_tokens)
      ) {
        summary = payload.summary_tokens;
        continue;
      }
      if (
        event.event_type !== "context_budget" ||
        !isBreakdown(payload.breakdown) ||
        !count(payload.compact_at)
      )
        continue;
      // The prompt and tools stay as they were; the summary replaced everything else.
      const breakdown =
        summary === undefined
          ? payload.breakdown
          : { ...payload.breakdown, documents: 0, conversation: summary };
      const used = parts.reduce((total, { key }) => total + breakdown[key], 0);
      return { used, compactAt: payload.compact_at, breakdown };
    }
  }
  return null;
}

function Ring({ share, warn }: { share: number; warn: boolean }) {
  const radius = 6;
  const around = 2 * Math.PI * radius;
  return (
    <svg viewBox="0 0 16 16" className="size-4 -rotate-90" aria-hidden>
      <circle cx="8" cy="8" r={radius} fill="none" strokeWidth="2" className="stroke-foreground/15" />
      {share > 0 ? (
        <circle
          cx="8"
          cy="8"
          r={radius}
          fill="none"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={around}
          strokeDashoffset={around * (1 - share)}
          className={warn ? "stroke-warning" : "stroke-foreground/70"}
        />
      ) : null}
    </svg>
  );
}

export function ContextIndicator({
  runs,
  busy,
  onCompact,
}: {
  runs: CodingRun[];
  /** A task is running or waiting: it has to finish before the chat can be compacted. */
  busy: boolean;
  onCompact: () => Promise<void>;
}) {
  const usage = useMemo(() => contextUsage(runs), [runs]);
  const compacting = runs.some((run) => run.kind === "compact" && isActive(run.status));
  const [requested, setRequested] = useState(false);
  const share = usage ? Math.min(1, usage.used / usage.compactAt) : 0;
  const percent = Math.round(share * 100);
  const label = usage ? `Context ${percent}% full` : "Context";
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={label} title={label}>
          <Ring share={share} warn={share >= 0.9} />
        </Button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-80 p-4 text-xs" aria-label="Context">
        <div className="flex items-baseline justify-between gap-4">
          <p className="font-medium">Context</p>
          {usage ? <p className="tabular-nums text-muted-foreground">{percent}% full</p> : null}
        </div>
        {usage ? (
          <>
            <p className="mt-1 text-muted-foreground">
              <span className="tabular-nums text-foreground">{tokens.format(usage.used)}</span> of{" "}
              <span className="tabular-nums">{tokens.format(usage.compactAt)}</span> tokens before it is
              compacted
            </p>
            <div className="mt-3 flex h-1.5 overflow-hidden rounded-full bg-foreground/10" aria-hidden>
              {parts.map(({ key, shade }) => (
                <span
                  key={key}
                  className={shade}
                  style={{ width: `${(usage.breakdown[key] / usage.compactAt) * 100}%` }}
                />
              ))}
            </div>
            <dl className="mt-3 space-y-2">
              {parts.map(({ key, label: name, shade }) => (
                <div key={key} className="flex items-center justify-between gap-4">
                  <dt className="flex items-center gap-2 text-muted-foreground">
                    <span className={cn("size-2 rounded-full", shade)} />
                    {name}
                  </dt>
                  <dd className="tabular-nums">{tokens.format(usage.breakdown[key])}</dd>
                </div>
              ))}
            </dl>
          </>
        ) : (
          <p className="mt-1 text-muted-foreground">
            What fills the model's context shows here after the chat's first request.
          </p>
        )}
        <Button
          className="mt-3 w-full"
          variant="secondary"
          size="sm"
          disabled={!usage || busy || requested}
          title={busy && !compacting ? "Wait for the current task to finish" : undefined}
          onClick={() => {
            setRequested(true);
            void onCompact().finally(() => setRequested(false));
          }}
        >
          {compacting || requested ? <LoaderCircle className="animate-spin" /> : null}
          {compacting ? "Compacting…" : "Compact now"}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
