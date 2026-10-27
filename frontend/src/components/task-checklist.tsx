import { Check, Circle, LoaderCircle } from "lucide-react";
import type { RunEvent } from "@/lib/types";
import { runTasks, type Task } from "@/lib/task-checklist";

function TaskChecklist({ tasks, active }: { tasks: Task[]; active: boolean }) {
  if (!tasks.length) return null;
  const completed = tasks.filter((task) => task.status === "completed").length;
  return (
    <details
      open={active}
      className="min-w-0 rounded-lg border bg-card/40 p-3 text-xs"
      aria-label="Task checklist"
    >
      <summary className="cursor-pointer text-muted-foreground">
        Plan · {completed}/{tasks.length} complete
      </summary>
      {/* Every task in view: a scrolling list hid the last ones, with no scrollbar on macOS. */}
      <ul className="mt-2 space-y-2">
        {tasks.map((task, index) => {
          const Icon =
            task.status === "completed"
              ? Check
              : task.status === "in_progress" && active
                ? LoaderCircle
                : Circle;
          return (
            <li key={index} className="flex items-start gap-2">
              <Icon
                aria-hidden="true"
                className={`mt-0.5 size-3.5 shrink-0 ${task.status === "in_progress" && active ? "animate-spin" : ""}`}
              />
              <span className="min-w-0 break-words">
                <span className="sr-only">
                  {task.status === "in_progress" && !active ? "Unfinished" : task.status.replace("_", " ")}
                  :{" "}
                </span>
                {task.content}
              </span>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

export function RunChecklist({ events, active }: { events: RunEvent[]; active: boolean }) {
  return <TaskChecklist tasks={runTasks(events)} active={active} />;
}
