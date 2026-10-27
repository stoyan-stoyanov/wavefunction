import type { RunEvent } from "@/lib/types";
import type { CodingRun } from "./api";

type AssistantMessage = { kind: "assistant"; id: string; text: string; complete: boolean; partial?: boolean };
/** The user's reply to the agent's questions, or their closing them unanswered (no text). */
type Answer = { kind: "answer"; id: string; label: string; text: string; at: string };
type TranscriptItem =
  | AssistantMessage
  | { kind: "steering"; id: string; event: RunEvent; label?: string }
  | { kind: "plan"; id: string }
  /** A helper the task started, where it started it. */
  | { kind: "helper"; id: string; agentId: string; name: string; task: string }
  /** The conversation compacted mid-task, where it happened. */
  | { kind: "compaction"; id: string; state: "running" | "done" | "failed" }
  | Answer;
type QuestionGroup = { id: string; questions: Array<{ id: string; title: string }> };

/** The pause a run is waiting on: its latest question set or approval request. */
export function latestPause(events: RunEvent[]): RunEvent | undefined {
  return [...events]
    .reverse()
    .find((event) => event.event_type === "run_questions" || event.event_type === "run_waiting_for_approval");
}

function answerLabel(titles: string[]) {
  return titles.length === 1 ? `Answer · ${titles[0]}` : "Answers";
}

/** Keep one live plan and each message at its first position in the event stream. */
export function runTranscript(run: CodingRun): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  const messages = new Map<string, AssistantMessage>();
  const planCalls = new Set<string>();
  // Question titles by the sequence of the event that asked them.
  const asked = new Map<number, Map<string, string>>();
  let planPlaced = false;
  let streaming: AssistantMessage | undefined;
  let compaction: Extract<TranscriptItem, { kind: "compaction" }> | undefined;

  for (const event of run.events) {
    const { payload, event_type: type } = event;
    if (type === "run_questions") {
      const titles = new Map<string, string>();
      for (const group of (payload.groups ?? []) as QuestionGroup[])
        for (const question of group.questions) titles.set(`${group.id}:${question.id}`, question.title);
      asked.set(event.sequence, titles);
    } else if (type === "steering_requested") {
      const titles = typeof payload.reply_to === "number" ? asked.get(payload.reply_to) : undefined;
      items.push({
        kind: "steering",
        id: event.id,
        event,
        ...(titles ? { label: answerLabel([...titles.values()]) } : {}),
      });
    } else if (type === "questions_answered" && !payload.replied) {
      // A reply typed in the composer is shown as that message instead.
      const titles = asked.get(Number(payload.sequence)) ?? new Map<string, string>();
      const answers = Object.entries((payload.answers ?? {}) as Record<string, string>);
      items.push(
        payload.dismissed
          ? {
              kind: "answer",
              id: event.id,
              label: "Questions closed without an answer",
              text: "",
              at: event.created_at,
            }
          : {
              kind: "answer",
              id: event.id,
              at: event.created_at,
              label: answerLabel(answers.map(([key]) => titles.get(key) ?? key)),
              text:
                answers.length === 1
                  ? answers[0][1]
                  : answers.map(([key, value]) => `${titles.get(key) ?? key}: ${value}`).join("\n"),
            },
      );
    } else if (type === "delegation_started" && !payload.followup && payload.agent_id) {
      items.push({
        kind: "helper",
        id: event.id,
        agentId: String(payload.agent_id),
        name: String(payload.agent_name ?? "Helper"),
        task: String(payload.task ?? ""),
      });
    } else if (type === "summarization_started") {
      compaction = { kind: "compaction", id: event.id, state: "running" };
      items.push(compaction);
    } else if ((type === "summarization_finished" || type === "summarization_failed") && compaction) {
      compaction.state = type === "summarization_finished" ? "done" : "failed";
    } else if (type === "tool_started" && payload.tool === "write_todos") {
      planCalls.add(String(payload.id));
    } else if (type === "tool_completed" && planCalls.has(String(payload.id)) && !planPlaced) {
      items.push({ kind: "plan", id: event.id });
      planPlaced = true;
    } else if (type === "assistant_delta" || type === "assistant_message" || type === "assistant_started") {
      const text = String(payload.text ?? "");
      if (!text && type !== "assistant_started") continue;
      const messageId = typeof payload.message_id === "string" ? payload.message_id : undefined;
      // Providers can replace a temporary stream id with their response id at
      // completion. Older saved events have no id at all. Both finish the open
      // bubble in place, including when steering arrived mid-response.
      const id = messageId || streaming?.id || event.id;
      let message = messages.get(id) ?? (type === "assistant_message" ? streaming : undefined);
      if (!message) {
        message = { kind: "assistant", id, text: "", complete: false };
        items.push(message);
      }
      messages.set(id, message);
      if (type === "assistant_message") {
        message.text = text;
        message.complete = true;
        if (payload.partial === true) {
          message.partial = true;
          message.id = `${event.id}-interrupted`;
          // A retry may reuse a provider's response ID. Its answer must not overwrite
          // the interrupted attempt or append to an already closed bubble.
          for (const [key, value] of messages) if (value === message) messages.delete(key);
        } else delete message.partial;
        if (streaming === message) streaming = undefined;
      } else if (!message.complete) {
        message.text += text;
        streaming = message;
      }
    }
  }
  // A tool-only model response may stream whitespace without a saved message.
  // Filter only after assembling messages so leading newlines, indentation and
  // whitespace-only chunks inside a real response retain their meaning.
  const visible = items.filter((item) => item.kind !== "assistant" || item.text.trim());
  const last = [...visible].reverse().find((item) => item.kind === "assistant");
  // Older/structured runs can have an output without a matching message event.
  if (run.output?.text?.trim() && run.output.text !== last?.text) {
    visible.push({ kind: "assistant", id: `${run.id}-output`, text: run.output.text, complete: true });
  }
  return visible;
}
