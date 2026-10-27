import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { RunEvent, Usage } from "@/lib/types";

function reported(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function callUsage(event: RunEvent): Usage[] | undefined {
  const value = event.payload.usage;
  const items = event.event_type === "summarization_finished" ? [value] : value;
  return Array.isArray(items) &&
    items.length > 0 &&
    items.every((item) => typeof item === "object" && item !== null)
    ? (items as Usage[])
    : undefined;
}

function inputTotal(usages: Usage[] | undefined): number | undefined {
  if (!usages || usages.some((usage) => reported(usage.input_tokens) === undefined)) return undefined;
  return usages.reduce((total, usage) => total + usage.input_tokens!, 0);
}

function requestCounts(events: RunEvent[], usage: Usage) {
  let lastInput: number | undefined;
  let inputs = 0;
  let cached = 0;
  let calls = 0;
  let complete = true;
  for (const event of events) {
    if (
      event.event_type !== "model_call_finished" &&
      event.event_type !== "summarization_finished" &&
      !(event.event_type === "model_call_failed" && Array.isArray(event.payload.usage))
    )
      continue;
    const usages = callUsage(event);
    const input = inputTotal(usages);
    if (event.event_type === "model_call_finished") lastInput = input;
    calls++;
    if (input === undefined || !usages) {
      complete = false;
      continue;
    }
    inputs += input;
    for (const item of usages) {
      const cache = reported(item.input_token_details?.cache_read);
      if (cache === undefined || cache > item.input_tokens!) complete = false;
      else cached += cache;
    }
  }
  // A saved total can include calls without detailed events, including model calls inside
  // tools. A partial cache report cannot establish an exact uncached count for the run.
  const recordedCache = reported(usage.input_token_details?.cache_read);
  const covered =
    complete &&
    calls > 0 &&
    inputs === reported(usage.input_tokens) &&
    (recordedCache === undefined || recordedCache === cached);
  return {
    lastInput,
    cached: recordedCache ?? (covered ? cached : undefined),
    uncached: covered ? inputs - cached : undefined,
  };
}

const count = (value: number | undefined) => (value === undefined ? "Unknown" : value.toLocaleString());
const dollars = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
});

export function RunUsage({
  usage = {},
  helpers,
  events,
}: {
  usage?: Usage;
  helpers?: Usage;
  events: RunEvent[];
}) {
  const input = reported(usage.input_tokens);
  const output = reported(usage.output_tokens);
  const total = reported(usage.total_tokens);
  const reasoning = reported(usage.output_token_details?.reasoning);
  const cost = reported(usage.cost);
  // The helpers it started run in chats of their own; their spending is part of this task's.
  const helperTotal = reported(helpers?.total_tokens) || undefined;
  const helperCost = reported(helpers?.cost);
  const incomplete = events.some(
    (event) => event.event_type === "model_call_failed" && event.payload.usage_status === "unknown",
  );
  const { lastInput, cached, uncached } = requestCounts(events, usage);
  if (
    !incomplete &&
    [input, output, total, reasoning, cost, lastInput, cached, helperTotal, helperCost].every(
      (value) => value === undefined,
    )
  )
    return null;
  // Unknown stays unknown: helpers' tokens alone are not this task's total.
  const shown = total === undefined ? undefined : total + (helperTotal ?? 0);

  const rows = [
    ["Input", count(input)],
    ["Cached input (reported)", count(cached)],
    ["Uncached input", count(uncached)],
    ["Output", count(output)],
    ["Reasoning output (reported)", count(reasoning)],
    ["Reported cost (USD)", cost === undefined ? "Unknown" : dollars.format(cost)],
  ];
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Recorded run usage"
          className="rounded-sm tabular-nums hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          {shown === undefined ? "Usage" : `${count(shown)} tokens`}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-80 p-4 text-xs" aria-label="Recorded run usage">
        <p className="font-medium">Recorded run usage</p>
        <p className="mt-1 text-muted-foreground">Totals across recorded requests in this run.</p>
        {helperTotal !== undefined || helperCost !== undefined ? (
          <p className="mt-2">
            {"Helpers spent "}
            {[
              helperTotal === undefined ? "" : `${count(helperTotal)} tokens`,
              helperCost === undefined ? "" : dollars.format(helperCost),
            ]
              .filter(Boolean)
              .join(", ")}
            {helperTotal === undefined ? "." : ", included in the total."}
            {" The rows below are this task's own."}
          </p>
        ) : null}
        {incomplete ? (
          <p className="mt-2 text-muted-foreground">
            Some interrupted requests have unknown usage. These totals are incomplete.
          </p>
        ) : null}
        <dl className="mt-3 space-y-2">
          {rows.map(([label, value]) => (
            <div key={label} className="flex justify-between gap-4">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-muted-foreground">
          Cached input and reasoning are included in the input and output totals. Unknown means the provider
          or saved history did not supply enough detail.
        </p>
        <dl className="mt-3 border-t border-border pt-3">
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">Last agent request · input</dt>
            <dd className="tabular-nums">{count(lastInput)}</dd>
          </div>
        </dl>
        <p className="mt-1 text-muted-foreground">Latest completed request, excluding summaries.</p>
      </PopoverContent>
    </Popover>
  );
}
