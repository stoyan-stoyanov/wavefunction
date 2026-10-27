import { fetchBlobUrl } from "@/lib/api";
import { request, requestResponse } from "@/lib/transport";
import type { ModelSpec, ProjectFile, Run, RunEvent, Schemas } from "@/lib/types";

export type CodingProject = Schemas["Project"];
export type RoutineTiming = Schemas["Routine"]["timing"];
/** A routine: this chat, sent `instruction` on a schedule while the app is open. */
export type Routine = Schemas["Routine"];
export type RoutineChanges = Partial<Pick<Routine, "instruction" | "timing" | "enabled" | "report_to">>;
/** When a routine runs next, in a few words. */
export function routineStatus(routine: Routine) {
  if (routine.finished) return "Done";
  if (!routine.enabled) return "Paused";
  if (!routine.next_run_at) return "No next run";
  const at = new Date(routine.next_run_at);
  return at.getTime() <= Date.now()
    ? "Due now"
    : `Next ${at.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
}
type SidebarFields = Omit<Schemas["SidebarThread"], keyof Schemas["Thread"]>;
/** A chat. The sidebar's copy also carries its routine and the state of its runs. */
export type CodingThread = Schemas["Thread"] & Partial<SidebarFields>;
export type WorktreeCleanupSettings = Schemas["WorktreeCleanup"];
export type ChatCheckpoint = Schemas["Checkpoint"];
/** A run in a chat's history. One just sent, or followed live, has no history position or
 *  checkpoints yet. */
export type CodingRun = Run & { events: RunEvent[] } & Partial<
    Omit<Schemas["ChatRun"], keyof Run | "events">
  >;
export type FilePreview = Schemas["FilePreview"];
export type ThreadDetail = CodingThread & {
  runs: CodingRun[];
  attachments: ProjectFile[];
  older_cursor?: string | null;
};
export type CodingSnapshot = Schemas["WorkspaceSnapshot"];
export type AnalyticsMetric = Schemas["AnalyticsMetric"];
export type AnalyticsStats = Schemas["AnalyticsStats"];
export type CodingAnalytics = Schemas["Analytics"];
export interface FileReference {
  path: string;
  start_line: number;
  end_line: number | null;
  version: string;
}
export interface TaskContext {
  files: FileReference[];
  skills: string[];
  command: string | null;
}
export interface ChatDraft {
  content: string;
  context: TaskContext;
}
export interface MessageOptions {
  delivery?: "steer" | "queue";
  worktree?: boolean;
  worktree_base?: string;
}
export interface ForkOptions {
  checkpoint_id?: string;
  worktree: boolean;
}
export type GitFile = Schemas["GitFile"];
export type GitState = Schemas["GitState"];
export type GitBranches = Schemas["GitBranches"];
export type GitPatch = Schemas["GitDiff"];
export type Workflow = Schemas["Workflow"];
export type TreeEntry = Schemas["TreeEntry"];
type FileContent = Schemas["FileText"];
export type DiscoveredSkill = Schemas["DiscoveredSkill"];
/** A skill on the Settings page: where it applies and whether it wins there. */
export type CatalogSkill = Schemas["CatalogSkill"];
export type SkillInventory = Schemas["SkillInventory"];
export type TerminalSession = Schemas["TerminalSession"];
type TerminalOutput = Schemas["TerminalOutput"];
const base = "/api/coding";
const json = (method: string, data?: unknown) => ({
  method,
  ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
});
const threadUrl = (id: string) => `${base}/threads/${encodeURIComponent(id)}`;
export const codingApi = {
  worktreeCleanup: (signal?: AbortSignal) =>
    request<WorktreeCleanupSettings>(`${base}/worktree-cleanup`, { signal }),
  saveWorktreeCleanup: (settings: WorktreeCleanupSettings) =>
    request<WorktreeCleanupSettings>(`${base}/worktree-cleanup`, json("PUT", settings)),
  analytics: (signal?: AbortSignal) => request<CodingAnalytics>(`${base}/analytics`, { signal }),
  skillCatalog: () => request<Schemas["SkillCatalog"]>(`${base}/skill-catalog`),
  createProjectSkill: (projectId: string, skill: { name: string; description: string; body: string }) =>
    request<Schemas["SkillFile"]>(`${base}/projects/${projectId}/skills`, json("POST", skill)),
  updateProjectSkill: (
    projectId: string,
    skill: { path: string; digest: string; description: string; body: string },
  ) => request<Schemas["SkillFile"]>(`${base}/projects/${projectId}/skills`, json("PUT", skill)),
  deleteProjectSkill: (projectId: string, path: string, digest: string) =>
    request<void>(
      `${base}/projects/${projectId}/skills?path=${encodeURIComponent(path)}&digest=${encodeURIComponent(digest)}`,
      { method: "DELETE" },
    ),
  skills: (threadId?: string) =>
    request<SkillInventory>(`${base}/skills${threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ""}`),
  terminals: (id: string, signal?: AbortSignal) =>
    request<TerminalSession[]>(`${threadUrl(id)}/terminals`, { signal }),
  createTerminal: (id: string, title = "Terminal") =>
    request<TerminalSession>(`${threadUrl(id)}/terminals`, json("POST", { title })),
  terminalOutput: (id: string, terminalId: string, cursor: number, signal?: AbortSignal) =>
    request<TerminalOutput>(
      `${threadUrl(id)}/terminals/${encodeURIComponent(terminalId)}/output?cursor=${cursor}&wait=20`,
      { signal },
    ),
  terminalInput: (id: string, terminalId: string, text: string, signal?: AbortSignal) =>
    request<void>(`${threadUrl(id)}/terminals/${encodeURIComponent(terminalId)}/input`, {
      ...json("POST", { text }),
      signal,
    }),
  terminalSize: (id: string, terminalId: string, cols: number, rows: number, signal?: AbortSignal) =>
    request<void>(`${threadUrl(id)}/terminals/${encodeURIComponent(terminalId)}/size`, {
      ...json("PUT", { cols, rows }),
      signal,
    }),
  renameTerminal: (id: string, terminalId: string, title: string) =>
    request<TerminalSession>(
      `${threadUrl(id)}/terminals/${encodeURIComponent(terminalId)}`,
      json("PATCH", { title }),
    ),
  closeTerminal: (id: string, terminalId: string) =>
    request<void>(`${threadUrl(id)}/terminals/${encodeURIComponent(terminalId)}`, json("DELETE")),
  configureModel: (model: ModelSpec) => request<ModelSpec>(`${base}/models`, json("PUT", model)),
  search: (query: string) =>
    request<Schemas["SearchResult"][]>(`${base}/search?query=${encodeURIComponent(query)}`),
  fork: (id: string, options: ForkOptions) =>
    request<Schemas["ForkedThread"]>(`${threadUrl(id)}/fork`, json("POST", options)),
  rewind: (id: string, checkpoint_id: string) =>
    request<Schemas["Rewound"]>(`${threadUrl(id)}/rewind`, json("POST", { checkpoint_id })),
  workspace: () => request<CodingSnapshot>(`${base}/workspace`),
  createThread: (data: Partial<CodingThread> = {}) =>
    request<Schemas["Thread"]>(`${base}/threads`, json("POST", data)),
  touchThread: (id: string) => request<void>(`${threadUrl(id)}/activity`, json("POST")),
  thread: (id: string) => request<Schemas["ThreadDetail"]>(threadUrl(id)),
  history: (id: string, before: string) =>
    request<Schemas["RunPage"]>(`${threadUrl(id)}/history?before=${encodeURIComponent(before)}`),
  patch: (id: string, data: Partial<CodingThread>) =>
    request<Schemas["Thread"]>(threadUrl(id), json("PATCH", data)),
  remove: (id: string, deleteWorktree = false) =>
    request<void>(`${threadUrl(id)}${deleteWorktree ? "?delete_worktree=true" : ""}`, json("DELETE")),
  compact: (id: string) => request<Run>(`${threadUrl(id)}/compact`, json("POST")),
  updateRoutine: (id: string, changes: RoutineChanges) =>
    request<Routine>(`${threadUrl(id)}/routine`, json("PATCH", changes)),
  stopRoutine: (id: string) => request<void>(`${threadUrl(id)}/routine`, json("DELETE")),
  send: (id: string, content: string, context?: TaskContext, options?: MessageOptions) =>
    request<Run>(
      `${threadUrl(id)}/messages`,
      json("POST", { content, ...(context ? { context } : {}), ...options }),
    ),
  contextFiles: (id: string, query = "") =>
    request<Schemas["ContextFiles"]>(`${threadUrl(id)}/context/files?query=${encodeURIComponent(query)}`),
  workflow: (id: string) => request<Workflow>(`${threadUrl(id)}/workflow`),
  gitState: (id: string) => request<GitState>(`${threadUrl(id)}/git/state`),
  gitBranches: (id: string) => request<GitBranches>(`${threadUrl(id)}/git/branches`),
  gitDiff: (id: string, path: string, staged: boolean, combined = false) =>
    request<GitPatch>(
      `${threadUrl(id)}/git/diff?path=${encodeURIComponent(path)}&staged=${staged}&combined=${combined}`,
    ),
  answerQuestions: (id: string, runId: string, sequence: number, answers: Record<string, string>) =>
    request<Run>(`${threadUrl(id)}/runs/${runId}/answers`, json("POST", { sequence, answers })),
  /** Close the agent's questions without answering them; the task carries on. */
  dismissQuestions: (id: string, runId: string, sequence: number) =>
    request<Run>(`${threadUrl(id)}/runs/${runId}/answers`, json("POST", { sequence, dismiss: true })),
  project: (data: Partial<CodingProject>, id?: string) =>
    request<CodingProject>(`${base}/projects${id ? `/${id}` : ""}`, json(id ? "PATCH" : "POST", data)),
  orderProjects: (ids: string[]) => request<CodingProject[]>(`${base}/projects/order`, json("PUT", { ids })),
  removeProject: (id: string) => request<void>(`${base}/projects/${id}`, json("DELETE")),
  upload: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Schemas["UploadedFile"]>(`${threadUrl(id)}/attachments`, {
      method: "POST",
      body,
    });
  },
  /** A file a chat links, checked against that chat's folders. */
  filePreview: (id: string, path: string) =>
    request<FilePreview>(`${threadUrl(id)}/preview?path=${encodeURIComponent(path)}`),
  /** The linked file as a blob URL (the caller revokes it), as bytes, or as text. */
  filePreviewUrl: (id: string, path: string, signal?: AbortSignal) =>
    fetchBlobUrl(`${threadUrl(id)}/preview/content?path=${encodeURIComponent(path)}`, { signal }),
  filePreviewBytes: (id: string, path: string, signal?: AbortSignal) =>
    requestResponse(
      `${threadUrl(id)}/preview/content?path=${encodeURIComponent(path)}`,
      (response) => response.arrayBuffer(),
      { signal },
    ),
  filePreviewText: (id: string, path: string, signal?: AbortSignal) =>
    requestResponse(
      `${threadUrl(id)}/preview/content?path=${encodeURIComponent(path)}`,
      (response) => response.text(),
      { signal },
    ),
  openFile: (id: string, path: string, target: "finder" | "app") =>
    request<void>(`${threadUrl(id)}/preview/open`, json("POST", { path, target })),
  /** An attached image as a blob URL; the caller revokes it. */
  attachmentPreview: (id: string, fileId: string) =>
    fetchBlobUrl(`${threadUrl(id)}/attachments/${encodeURIComponent(fileId)}`),
  removeAttachment: (id: string, fileId: string) =>
    request<void>(`${threadUrl(id)}/attachments/${fileId}`, json("DELETE")),
  tree: (id: string, path = "") =>
    request<TreeEntry[]>(`${threadUrl(id)}/tree?path=${encodeURIComponent(path)}`),
  file: (id: string, path: string, folder?: string) =>
    request<FileContent>(
      `${threadUrl(id)}/file?path=${encodeURIComponent(path)}${folder === undefined ? "" : `&folder=${encodeURIComponent(folder)}`}`,
    ),
  save: (id: string, path: string, content: string, version: string, folder?: string) =>
    request<FileContent>(`${threadUrl(id)}/file`, json("PUT", { path, content, version, folder })),
  access: (access: CodingSnapshot["access"]) =>
    request<Schemas["AccessSetting"]>(`${base}/access`, json("PUT", { access })),
  defaultMode: (mode: CodingThread["mode"]) =>
    request<Schemas["DefaultMode"]>(`${base}/default-mode`, json("PUT", { mode })),
};
/** The title a chat shows; new sessions keep the default until their first message names them.
 * Earlier defaults show as today's. */
export const chatTitle = (thread: CodingThread) =>
  ["New conversation", "New chat"].includes(thread.title) ? "New session" : thread.title;
export const isActive = (status: string) =>
  ["queued", "running", "waiting_approval", "waiting_connector", "waiting_computer"].includes(status);
