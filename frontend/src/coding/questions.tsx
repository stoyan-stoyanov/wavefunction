import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { RunEvent } from "@/lib/types";
import { codingApi } from "./api";
import { useCoding } from "./store";

type Group = {
  id: string;
  questions: Array<{
    id: string;
    title: string;
    options: Array<{ label: string; description: string }>;
  }>;
};
/** Per question: the option chosen, or an answer written instead. Never both. */
type Draft = { choices: Record<string, string>; texts: Record<string, string> };

function savedDraft(
  key: string,
  questions: Array<{ key: string; options: Array<{ label: string }> }>,
): Draft {
  try {
    const saved = JSON.parse(localStorage.getItem(key) ?? "{}");
    if (saved.choices || saved.texts) return { choices: saved.choices ?? {}, texts: saved.texts ?? {} };
    // Older builds saved one answer per question: a chosen option's label or the written text.
    const draft: Draft = { choices: {}, texts: {} };
    for (const question of questions) {
      const answer = saved[question.key];
      if (typeof answer !== "string") continue;
      if (question.options.some((option) => option.label === answer)) draft.choices[question.key] = answer;
      else draft.texts[question.key] = answer;
    }
    return draft;
  } catch {
    return { choices: {}, texts: {} };
  }
}

export function QuestionCard({
  threadId,
  runId,
  event,
}: {
  threadId: string;
  runId: string;
  event: RunEvent;
}) {
  const groups = (event.payload.groups ?? []) as Group[];
  const questions = groups.flatMap((group) =>
    group.questions.map((question) => ({ ...question, key: `${group.id}:${question.id}` })),
  );
  const key = `harness.answers.${threadId}.${runId}.${event.sequence}`;
  const [draft, setDraft] = useState<Draft>(() => savedDraft(key, questions));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(draft));
    } catch {
      /* Keep the draft in memory. */
    }
  }, [key, draft]);
  const answer = (id: string) => draft.texts[id]?.trim() || draft.choices[id] || "";
  const choose = (id: string, label: string) =>
    setDraft(({ choices, texts }) => ({
      choices: { ...choices, [id]: label },
      texts: { ...texts, [id]: "" },
    }));
  const write = (id: string, text: string) =>
    setDraft(({ choices, texts }) => ({
      choices: Object.fromEntries(Object.entries(choices).filter(([question]) => question !== id)),
      texts: { ...texts, [id]: text },
    }));
  async function finish(dismiss: boolean) {
    setBusy(true);
    setError("");
    try {
      if (dismiss) await codingApi.dismissQuestions(threadId, runId, event.sequence);
      else
        await codingApi.answerQuestions(
          threadId,
          runId,
          event.sequence,
          Object.fromEntries(questions.map((question) => [question.key, answer(question.key)])),
        );
      await Promise.all([useCoding.getState().load(threadId), useCoding.getState().refresh()]);
      localStorage.removeItem(key);
    } catch (reason) {
      if (alive.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  const ready = questions.length > 0 && questions.every((question) => answer(question.key));
  return (
    <div className="question-card" role="group" aria-label="Questions from the agent">
      {questions.map((question) => (
        <fieldset key={question.key}>
          <legend>{question.title}</legend>
          {/* One choice per line, then the user's own answer as one more row of the same size. */}
          <div className="question-options">
            {question.options.map((option) => (
              <button
                key={option.label}
                aria-pressed={draft.choices[question.key] === option.label}
                disabled={busy}
                onClick={() => choose(question.key, option.label)}
              >
                <span className="question-radio" aria-hidden="true" />
                <span className="question-option-text">
                  <strong>{option.label}</strong>
                  {option.description ? <small>{option.description}</small> : null}
                </span>
              </button>
            ))}
            <Textarea
              className={`question-own${!question.options.length || question.options.some((option) => option.description) ? " question-own-tall" : ""}`}
              aria-label={`Answer: ${question.title}`}
              placeholder={question.options.length ? "Or write your own answer…" : "Write your answer…"}
              value={draft.texts[question.key] ?? ""}
              maxLength={4000}
              rows={1}
              disabled={busy}
              onChange={(e) => write(question.key, e.target.value)}
            />
          </div>
        </fieldset>
      ))}
      {error && (
        <p role="alert" className="workbench-error">
          {error}
        </p>
      )}
      <div className="question-actions">
        <Button size="sm" disabled={busy || !ready} onClick={() => void finish(false)}>
          {busy ? "Submitting…" : "Continue with answers"}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void finish(true)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
