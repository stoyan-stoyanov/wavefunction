// A routine's settings, from its chat's menu: what it is asked, when, where it reports, and
// whether it runs. A timing reads the same way here as the agent says it ("on weekdays at
// 09:00").
import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { confirmAction } from "@/lib/confirm";
import { cn } from "@/lib/utils";
import { codingApi, routineStatus, type CodingThread, type RoutineTiming } from "./api";
import { Field } from "./folder-field";
import { useCoding } from "./store";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
type Mode = "once" | "every" | "daily" | "weekly" | "monthly";
type Unit = "minutes" | "hours" | "days";
const MODES: Array<{ id: Mode; label: string }> = [
  { id: "once", label: "Once" },
  { id: "every", label: "Every…" },
  { id: "daily", label: "Daily" },
  { id: "weekly", label: "Weekly" },
  { id: "monthly", label: "Monthly" },
];
const UNIT_SECONDS: Record<Unit, number> = { minutes: 60, hours: 3_600, days: 86_400 };
const pad = (value: number) => String(value).padStart(2, "0");
const isoDay = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

interface Draft {
  mode: Mode;
  time: string;
  date: string;
  count: number;
  unit: Unit;
  days: number[];
  day: number;
  weekdaysOnly: boolean;
}

function draftOf(timing: RoutineTiming, now: Date): Draft {
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const draft: Draft = {
    mode: "daily",
    time: "09:00",
    date: isoDay(tomorrow),
    count: 1,
    unit: "hours",
    days: [(now.getDay() + 6) % 7],
    day: 1,
    weekdaysOnly: false,
  };
  if (timing.kind === "interval") {
    const unit =
      (["days", "hours", "minutes"] as const).find(
        (name) => timing.every_seconds % UNIT_SECONDS[name] === 0,
      ) ?? "minutes";
    return {
      ...draft,
      mode: "every",
      unit,
      count: Math.max(1, Math.round(timing.every_seconds / UNIT_SECONDS[unit])),
    };
  }
  if (timing.kind === "once")
    return { ...draft, mode: "once", date: timing.at.slice(0, 10), time: timing.at.slice(11, 16) };
  if (timing.kind === "monthly") return { ...draft, mode: "monthly", day: timing.day, time: timing.time };
  const days = [...timing.days].sort((a, b) => a - b);
  const key = days.join("");
  if (key === "0123456" || key === "01234")
    return { ...draft, mode: "daily", weekdaysOnly: key === "01234", time: timing.time, days };
  return { ...draft, mode: "weekly", days, time: timing.time };
}

function timingOf(draft: Draft): RoutineTiming | null {
  if (draft.mode === "every")
    return {
      kind: "interval",
      every_seconds: Math.max(1, Math.round(draft.count)) * UNIT_SECONDS[draft.unit],
    };
  if (!/^\d{2}:\d{2}$/.test(draft.time)) return null;
  if (draft.mode === "once")
    return /^\d{4}-\d{2}-\d{2}$/.test(draft.date)
      ? { kind: "once", at: `${draft.date}T${draft.time}` }
      : null;
  if (draft.mode === "monthly") return { kind: "monthly", time: draft.time, day: draft.day };
  if (draft.mode === "daily")
    return {
      kind: "weekly",
      time: draft.time,
      days: draft.weekdaysOnly ? [0, 1, 2, 3, 4] : [0, 1, 2, 3, 4, 5, 6],
    };
  return draft.days.length
    ? { kind: "weekly", time: draft.time, days: [...draft.days].sort((a, b) => a - b) }
    : null;
}

function TimingEditor({
  value,
  onChange,
}: {
  value: RoutineTiming;
  onChange: (timing: RoutineTiming | null) => void;
}) {
  const [draft, setDraft] = useState(() => draftOf(value, new Date()));
  const change = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));
  const timing = timingOf(draft);
  const past = timing?.kind === "once" && new Date(timing.at) <= new Date();
  const wrong =
    draft.mode === "weekly" && !draft.days.length
      ? "Choose at least one day."
      : past
        ? "That time has already passed."
        : timing
          ? ""
          : "Choose a time.";
  const key = timing && !wrong ? JSON.stringify(timing) : "";
  useEffect(() => {
    onChange(key ? (JSON.parse(key) as RoutineTiming) : null);
    // `onChange` is the caller's setter; only a different timing is worth telling it about.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const time = (
    <input
      type="time"
      required
      aria-label="Time"
      value={draft.time}
      className="w-28 tabular-nums"
      onChange={(event) => change({ time: event.target.value })}
    />
  );
  return (
    <div className="space-y-2.5">
      <div
        role="radiogroup"
        aria-label="Repeats"
        className="flex flex-wrap gap-1 rounded-lg bg-muted/60 p-0.5"
      >
        {MODES.map((mode) => (
          <button
            key={mode.id}
            type="button"
            role="radio"
            aria-checked={draft.mode === mode.id}
            onClick={() => change({ mode: mode.id })}
            className={cn(
              "h-7 flex-1 rounded-md px-2.5 text-xs outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50",
              draft.mode === mode.id
                ? "bg-background font-medium text-foreground shadow-xs"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {mode.label}
          </button>
        ))}
      </div>
      <div className="routine-timing flex flex-wrap items-center gap-2 text-sm">
        {draft.mode === "once" ? (
          <>
            <span className="text-muted-foreground">On</span>
            <input
              type="date"
              required
              aria-label="Date"
              value={draft.date}
              min={isoDay(new Date())}
              onChange={(event) => change({ date: event.target.value })}
            />
            <span className="text-muted-foreground">at</span>
            {time}
          </>
        ) : null}
        {draft.mode === "every" ? (
          <>
            <span className="text-muted-foreground">Every</span>
            <input
              type="number"
              min={1}
              step={1}
              required
              aria-label="How many"
              value={draft.count}
              className="w-20 tabular-nums"
              onChange={(event) =>
                change({ count: Math.max(1, Math.floor(Number(event.target.value)) || 1) })
              }
            />
            <select
              aria-label="Unit"
              value={draft.unit}
              onChange={(event) => change({ unit: event.target.value as Unit })}
            >
              <option value="minutes">{draft.count === 1 ? "minute" : "minutes"}</option>
              <option value="hours">{draft.count === 1 ? "hour" : "hours"}</option>
              <option value="days">{draft.count === 1 ? "day" : "days"}</option>
            </select>
          </>
        ) : null}
        {draft.mode === "daily" ? (
          <>
            <span className="text-muted-foreground">At</span>
            {time}
            <label className="ml-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={draft.weekdaysOnly}
                onChange={(event) => change({ weekdaysOnly: event.target.checked })}
              />
              Weekdays only
            </label>
          </>
        ) : null}
        {draft.mode === "weekly" ? (
          <>
            <div role="group" aria-label="Days" className="flex gap-1">
              {DAYS.map((name, day) => {
                const chosen = draft.days.includes(day);
                return (
                  <button
                    key={name}
                    type="button"
                    aria-label={name}
                    aria-pressed={chosen}
                    title={name}
                    onClick={() =>
                      change({
                        days: chosen ? draft.days.filter((item) => item !== day) : [...draft.days, day],
                      })
                    }
                    className={cn(
                      "size-8 rounded-full text-xs outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50",
                      chosen
                        ? "bg-primary font-medium text-primary-foreground"
                        : "bg-background text-muted-foreground ring-1 ring-hairline hover:text-foreground",
                    )}
                  >
                    {name.slice(0, 2)}
                  </button>
                );
              })}
            </div>
            <span className="text-muted-foreground">at</span>
            {time}
          </>
        ) : null}
        {draft.mode === "monthly" ? (
          <>
            <span className="text-muted-foreground">On the</span>
            <select
              aria-label="Day of the month"
              value={draft.day}
              onChange={(event) => change({ day: Number(event.target.value) })}
            >
              {Array.from({ length: 30 }, (_, index) => index + 1).map((day) => (
                <option key={day} value={day}>
                  {day}
                </option>
              ))}
              <option value={31}>last day</option>
            </select>
            <span className="text-muted-foreground">at</span>
            {time}
          </>
        ) : null}
      </div>
      {wrong ? (
        <p role="alert" className="text-xs text-destructive">
          {wrong}
        </p>
      ) : null}
    </div>
  );
}

export function RoutineSettings({ thread, onDone }: { thread: CodingThread; onDone: () => void }) {
  const routine = thread.routine!;
  // The chats it can report to: its project's, and the current one even while archived. One
  // since moved to another project shows as Nobody, which is what saving can keep.
  const chats = useCoding((s) => s.workspace?.threads ?? []).filter(
    (chat) =>
      chat.project_id === thread.project_id &&
      chat.id !== thread.id &&
      !chat.parent_thread_id &&
      (!chat.archived || chat.id === routine.report_to),
  );
  const [instruction, setInstruction] = useState(routine.instruction);
  const [timing, setTiming] = useState<RoutineTiming | null>(routine.timing);
  const [reportTo, setReportTo] = useState(
    chats.some((chat) => chat.id === routine.report_to) ? routine.report_to! : "",
  );
  const [enabled, setEnabled] = useState(routine.enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await useCoding.getState().refresh();
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onDone();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Routine settings</DialogTitle>
          <DialogDescription>
            This chat is sent its instruction {routine.summary} while Wavefunction is open.{" "}
            {routineStatus(routine)}.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!timing || !instruction.trim()) return;
            void run(() =>
              codingApi.updateRoutine(thread.id, {
                instruction: instruction.trim(),
                timing,
                enabled,
                report_to: reportTo || null,
              }),
            );
          }}
        >
          <Field label="Instruction">
            <textarea
              rows={4}
              aria-label="Routine instruction"
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
            />
          </Field>
          <div className="coding-field">
            <span>Schedule</span>
            <TimingEditor value={routine.timing} onChange={setTiming} />
          </div>
          <Field label="Report each run to">
            <select
              aria-label="Report each run to"
              value={reportTo}
              onChange={(event) => setReportTo(event.target.value)}
            >
              <option value="">Nobody</option>
              {chats.map((chat) => (
                <option key={chat.id} value={chat.id}>
                  {chat.title}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            Running
          </label>
          {routine.last_error ? (
            <p className="text-xs text-muted-foreground">
              Last time it could not start: {routine.last_error}
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex items-center justify-between gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() =>
                void confirmAction("Stop this routine? Its chat and history stay as an ordinary chat.")
                  .then((confirmed) => (confirmed ? run(() => codingApi.stopRoutine(thread.id)) : undefined))
                  .catch((cause) => toast.error(String(cause)))
              }
            >
              Stop routine
            </Button>
            <Button type="submit" disabled={busy || !timing || !instruction.trim()}>
              {busy ? <LoaderCircle className="animate-spin" /> : null}Save
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
