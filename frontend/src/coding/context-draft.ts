import type { CodingThread, TaskContext } from "./api";

export const emptyContext = (): TaskContext => ({
  files: [],
  skills: [],
  command: null,
});
export function contextDraftKey(thread: CodingThread) {
  return `harness.context.${thread.id}.${thread.folder}`;
}
export function readContextDraft(thread: CodingThread): TaskContext {
  try {
    const value = JSON.parse(localStorage.getItem(contextDraftKey(thread)) ?? "null") as TaskContext | null;
    if (
      value &&
      Array.isArray(value.files) &&
      Array.isArray(value.skills) &&
      (value.command === null || typeof value.command === "string")
    )
      return value;
  } catch {
    /* Ignore invalid saved data. */
  }
  return emptyContext();
}
