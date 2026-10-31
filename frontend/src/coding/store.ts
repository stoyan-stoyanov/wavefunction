import { create } from "zustand";
import { api, streamRunEvents } from "@/lib/api";
import type { ProjectFile, RunEvent, Usage } from "@/lib/types";
import {
  codingApi,
  isActive,
  type CodingSnapshot,
  type CodingThread,
  type CodingRun,
  type ThreadDetail,
  type TaskContext,
  type MessageOptions,
  type ForkOptions,
  type ChatDraft,
} from "./api";
import { contextDraftKey, emptyContext } from "./context-draft";

interface CodingState {
  workspace: CodingSnapshot | null;
  selected: string | null;
  details: Record<string, ThreadDetail>;
  loading: boolean;
  error: string | null;
  sending: Record<string, boolean>;
  restoring: Record<string, boolean>;
  uploading: Record<string, boolean>;
  /** A message on its way, shown at the end of its chat until the chat's history includes it. */
  pending: Record<string, PendingMessage>;
  /** The last message each chat could not send, for the composer to put back in the box. */
  unsent: Record<string, { text: string; context?: TaskContext }>;
  initialize: () => Promise<void>;
  refresh: () => Promise<void>;
  select: (id: string, reload?: boolean) => Promise<void>;
  load: (id: string) => Promise<void>;
  loadOlder: (id: string) => Promise<void>;
  sync: (id: string) => Promise<void>;
  newThread: (projectId?: string | null) => Promise<void>;
  patch: (id: string, changes: Partial<CodingThread>) => Promise<void>;
  orderProjects: (ids: string[]) => Promise<void>;
  remove: (id: string, deleteWorktree?: boolean) => Promise<void>;
  send: (id: string, text: string, context?: TaskContext, options?: MessageOptions) => Promise<void>;
  /** Summarise the chat's conversation now, in a run of its own. */
  compact: (id: string) => Promise<void>;
  fork: (id: string, options: ForkOptions) => Promise<void>;
  rewind: (id: string, checkpointId: string) => Promise<void>;
  stop: (id: string) => Promise<void>;
  clearError: () => void;
}
export interface PendingMessage {
  text: string;
  context?: TaskContext;
  files: ProjectFile[];
  at: string;
  /** The backend has it: a history read from now on includes it. */
  accepted?: boolean;
}
const following = new Map<string, AbortController>();
const retryAfter = new Map<string, number>();
const loadSequence = new Map<string, number>();
const deleted = new Set<string>();
// Chats being deleted, with their helpers: off the list once deletion is confirmed, as the
// backend can take a while to clean up a chat with many helpers.
const removing = new Set<string>();
const listed = (workspace: CodingSnapshot) =>
  removing.size
    ? { ...workspace, threads: workspace.threads.filter((thread) => !removing.has(thread.id)) }
    : workspace;
const loadingOlder = new Set<string>();
let refreshSequence = 0;
// The latest project move. Snapshots requested before it was saved may still list the old order.
let projectOrder: { ids: string[]; saved: number | null } | null = null;
/** Agent tools that add, archive or remove what the sidebar lists. */
const SIDEBAR_TOOLS = new Set(["create_project", "archive_chats", "remove_project"]);

function withProjectOrder(workspace: CodingSnapshot, ids: string[]) {
  const rank = new Map(ids.map((id, index) => [id, index]));
  const projects = [...workspace.projects].sort(
    (a, b) => (rank.get(a.id) ?? Infinity) - (rank.get(b.id) ?? Infinity),
  );
  return { ...workspace, projects };
}
let initialization: Promise<void> | undefined;
// The chat list from the last launch, shown while the engine starts. The engine's answer replaces it.
const SAVED_WORKSPACE = "wavefunction.workspace";
const SAVED_CHATS = 500;
function savedWorkspace(): CodingSnapshot | null {
  try {
    const saved = JSON.parse(localStorage.getItem(SAVED_WORKSPACE) ?? "null") as CodingSnapshot | null;
    return saved?.projects?.length && Array.isArray(saved.threads) ? saved : null;
  } catch {
    return null;
  }
}
let lastSaved = "";
// Until the engine answers. If it can't, a saved list isn't a workspace the app can use.
let showingSaved = false;
function saveWorkspace(workspace: CodingSnapshot) {
  showingSaved = false;
  try {
    const threads = workspace.threads.filter((thread) => !thread.archived).slice(0, SAVED_CHATS);
    const text = JSON.stringify({ ...workspace, threads });
    if (text !== lastSaved) localStorage.setItem(SAVED_WORKSPACE, text);
    lastSaved = text;
  } catch {
    /* The next launch waits for the engine, as a first launch does. */
  }
  return workspace;
}
export const startingFromSaved = () => showingSaved;
function startingState() {
  const saved = savedWorkspace();
  const last = saved?.threads.find((thread) => thread.id === remembered() && !thread.archived);
  showingSaved = Boolean(saved && last);
  return saved && last
    ? { workspace: saved, selected: last.id, loading: false, error: null }
    : { loading: true, error: null };
}
const historyOrder = (a: CodingRun, b: CodingRun) =>
  a.history_order !== undefined && b.history_order !== undefined
    ? a.history_order - b.history_order
    : a.started_at.localeCompare(b.started_at);
function remembered() {
  try {
    return localStorage.getItem("harness.thread");
  } catch {
    return null;
  }
}
function writeDraft(thread: CodingThread, draft?: ChatDraft | null) {
  localStorage.setItem(`harness.draft.${thread.id}`, draft?.content ?? "");
  localStorage.setItem(contextDraftKey(thread), JSON.stringify(draft?.context ?? emptyContext()));
}
/** Whether `match` holds for a chat or for the chat it helps. */
function inChain(threads: CodingThread[], match: (thread: CodingThread) => boolean) {
  const byId = new Map(threads.map((thread) => [thread.id, thread]));
  return (id: string) => {
    for (
      let current = byId.get(id), depth = 0;
      current && depth < 64;
      current = current.parent_thread_id ? byId.get(current.parent_thread_id) : undefined, depth++
    )
      if (match(current)) return true;
    return false;
  };
}
function threadChanged(detail: ThreadDetail, summary?: CodingThread) {
  if (!summary) return false;
  const active =
    detail.runs.find((run) => isActive(run.status) && run.status !== "queued") ??
    detail.runs.find((run) => run.status === "queued");
  return Boolean(
    (summary.last_run_id && !detail.runs.some((run) => run.id === summary.last_run_id)) ||
    summary.conversation_id !== detail.conversation_id ||
    summary.folder !== detail.folder ||
    summary.project_id !== detail.project_id ||
    summary.worktree_status !== detail.worktree_status ||
    summary.updated_at !== detail.updated_at ||
    (summary.status !== undefined && summary.status !== (active?.status ?? "idle")) ||
    (summary.active_run_id !== undefined && summary.active_run_id !== (active?.id ?? null)) ||
    // One pause can follow another, as when a helper's card is answered from its main chat.
    (summary.pause_sequence != null &&
      !active?.events.some((event) => event.sequence === summary.pause_sequence)),
  );
}
/** The run's total so far, which a model call's finishing (or failing) event carries. */
function runUsage(event: RunEvent): Usage | undefined {
  const total = event.payload?.run_usage;
  return total && typeof total === "object" ? (total as Usage) : undefined;
}

export const useCoding = create<CodingState>((set, get) => {
  const lastOpened = new Map<string, number>();
  let openSequence = 0;
  const cacheDetail = (id: string, detail: ThreadDetail) => {
    const state = get();
    const details = { ...state.details, [id]: detail };
    // Keep five recently viewed idle chats, plus any chat that still owns work.
    const idle = Object.keys(details).filter(
      (key) =>
        !state.sending[key] &&
        !state.restoring[key] &&
        !details[key].runs.some((run) => isActive(run.status)),
    );
    idle.sort((a, b) =>
      a === state.selected
        ? -1
        : b === state.selected
          ? 1
          : (lastOpened.get(b) ?? 0) - (lastOpened.get(a) ?? 0),
    );
    for (const key of idle.slice(5)) {
      delete details[key];
      lastOpened.delete(key);
    }
    set({ details });
  };
  const fail = (error: unknown) => {
    set({ error: error instanceof Error ? error.message : String(error) });
  };
  // Drop a chat's pending message, or only `message` when another has replaced it since.
  const settle = (id: string, message?: PendingMessage) => {
    const pending = { ...get().pending };
    if (!pending[id] || (message && pending[id] !== message)) return;
    delete pending[id];
    set({ pending });
  };
  const follow = (threadId: string, runId: string, after: number) => {
    if (get().restoring[threadId] || following.has(runId) || (retryAfter.get(runId) ?? 0) > Date.now())
      return;
    const controller = new AbortController();
    const conversationId = get().details[threadId]?.conversation_id;
    let failed = false;
    following.set(runId, controller);
    void streamRunEvents(
      runId,
      after,
      (event) => {
        const detail = get().details[threadId];
        if (!detail || controller.signal.aborted || detail.conversation_id !== conversationId) return;
        // A finished model call carries the run's total so far: its token count follows live.
        const total = runUsage(event);
        const usage = total ? { usage: total } : {};
        set({
          details: {
            ...get().details,
            [threadId]: {
              ...detail,
              runs: detail.runs.map((run) =>
                run.id === runId
                  ? {
                      ...run,
                      ...usage,
                      events: [...run.events.filter((e) => e.sequence !== event.sequence), event].sort(
                        (a, b) => a.sequence - b.sequence,
                      ),
                    }
                  : run,
              ),
            },
          },
        });
        // A message steering this run is in the transcript now; its placeholder goes.
        if (event.event_type === "steering_requested") settle(threadId);
        // The agent changed the sidebar: show it now rather than when the task ends.
        if (event.event_type === "tool_completed" && SIDEBAR_TOOLS.has(String(event.payload.tool)))
          void get().refresh().catch(fail);
      },
      controller.signal,
    )
      .catch((error) => {
        failed = true;
        if (!controller.signal.aborted) {
          retryAfter.set(runId, Date.now() + 5000);
          fail(error);
        }
      })
      .finally(() => {
        if (following.get(runId) === controller) following.delete(runId);
        if (!failed && !controller.signal.aborted && !deleted.has(threadId)) {
          retryAfter.delete(runId);
          // The sidebar shows the workspace's status for each chat: settle it with the stream.
          void Promise.all([get().load(threadId), get().refresh()]).catch(fail);
        }
      });
  };
  const stopThreadStreams = (id: string) => {
    for (const run of get().details[id]?.runs ?? []) {
      following.get(run.id)?.abort();
      following.delete(run.id);
      retryAfter.delete(run.id);
    }
  };
  return {
    workspace: null,
    selected: null,
    details: {},
    loading: true,
    error: null,
    sending: {},
    restoring: {},
    uploading: {},
    pending: {},
    unsent: {},
    clearError: () => set({ error: null }),
    refresh: async () => {
      const sequence = ++refreshSequence;
      let workspace = await codingApi.workspace();
      if (sequence === refreshSequence) {
        if (projectOrder && (projectOrder.saved === null || sequence <= projectOrder.saved))
          workspace = withProjectOrder(workspace, projectOrder.ids);
        else projectOrder = null;
        workspace = saveWorkspace(listed(workspace));
        const previous = get().workspace;
        set({ workspace });
        const selected = get().selected;
        const summary = workspace.threads.find((thread) => thread.id === selected);
        const detail = selected ? get().details[selected] : undefined;
        // A task paused for the user has no open stream, and a queued follow-up's carries none of its
        // helpers' news: that arrives with a reload.
        const helpers = (snapshot: CodingSnapshot | null) =>
          JSON.stringify(
            snapshot?.threads
              .filter((thread) => thread.parent_thread_id === selected)
              .map((thread) => [
                thread.id,
                thread.status,
                thread.pause_sequence,
                thread.last_finished_run_id,
              ]),
          );
        const helpersMoved =
          previous !== null &&
          helpers(previous) !== helpers(workspace) &&
          !detail?.runs.some((run) => run.status === "running" && following.has(run.id));
        if (selected && detail && (threadChanged(detail, summary) || helpersMoved))
          await get().load(selected);
      }
    },
    initialize: async () => {
      if (initialization) return initialization;
      initialization = (async () => {
        set(startingState());
        try {
          await get().refresh();
          const threads = get().workspace!.threads;
          const thread =
            threads.find((t) => t.id === remembered() && !t.archived) ?? threads.find((t) => !t.archived);
          if (thread) {
            const opening = get().select(thread.id);
            // Navigation is ready once workspace metadata arrives. The selected
            // chat owns its loading state, so a slow history cannot hide another chat.
            set({ loading: false });
            await opening;
          } else if (get().workspace!.projects.length) {
            await get().newThread();
          } // Without a project there is nothing to start a chat in: setup asks for one first.
        } catch (error) {
          if (showingSaved) set({ workspace: null, selected: null });
          fail(error);
          initialization = undefined;
        } finally {
          set({ loading: false });
          // Done once a chat is open. A start that could not open one (the first project's folder
          // was moved, say) runs again from Try again instead of handing back this attempt.
          if (!get().selected) initialization = undefined;
        }
      })();
      return initialization;
    },
    select: async (id, reload = false) => {
      lastOpened.set(id, ++openSequence);
      set({ selected: id, error: null });
      // Record an explicit open without making history wait on a housekeeping request.
      void codingApi.touchThread(id).catch(() => undefined);
      try {
        localStorage.setItem("harness.thread", id);
      } catch {
        /* Session still works. */
      }
      try {
        const cached = get().details[id];
        const summary = get().workspace?.threads.find((thread) => thread.id === id);
        if (cached) cacheDetail(id, cached);
        if (reload || !cached || threadChanged(cached, summary)) await get().load(id);
        // Cleanup can change a cached checkout between workspace polls. Keep the
        // visible history, but check current metadata before reusing its controls.
        else await get().refresh();
      } catch (error) {
        if (get().selected === id) fail(error);
      }
    },
    load: async (id) => {
      if (deleted.has(id) || get().restoring[id]) return;
      const sequence = (loadSequence.get(id) ?? 0) + 1;
      loadSequence.set(id, sequence);
      const pending = get().pending[id];
      const detail: ThreadDetail = await codingApi.thread(id);
      if (deleted.has(id) || get().restoring[id] || loadSequence.get(id) !== sequence) return;
      // Events may arrive while the HTTP snapshot is in flight. Keep those newer pixels/text.
      const stored = get().details[id];
      const previous = stored?.conversation_id === detail.conversation_id ? stored : undefined;
      if (stored && !previous) stopThreadStreams(id);
      detail.runs = detail.runs.map((run) => {
        const events = [
          ...new Map(
            [...run.events, ...(previous?.runs.find((r) => r.id === run.id)?.events ?? [])].map((event) => [
              event.sequence,
              event,
            ]),
          ).values(),
        ].sort((a, b) => a.sequence - b.sequence);
        // A total streamed after this snapshot was taken is newer than the one it carries.
        const taken = Math.max(0, ...run.events.map((event) => event.sequence));
        const live = [...events].reverse().find((event) => event.sequence > taken && runUsage(event));
        return { ...run, events, ...(live ? { usage: runUsage(live) } : {}) };
      });
      // Keep loaded pages only when the latest old message overlaps this page. If
      // more than a page arrived while away, restart pagination to avoid a hidden gap.
      const previousLast = previous?.runs.at(-1)?.id;
      // An old active owner can precede the real page. It is not evidence that
      // the intervening messages were loaded; the cursor identifies the page start.
      const pageStart = detail.older_cursor
        ? detail.runs.findIndex((run) => run.id === detail.older_cursor)
        : 0;
      const overlap = detail.runs.findIndex((run) => run.id === previousLast);
      if (previous?.older_cursor !== undefined && pageStart >= 0 && overlap >= pageStart) {
        const recent = new Set(detail.runs.map((run) => run.id));
        detail.runs = [...previous.runs.filter((run) => !recent.has(run.id)), ...detail.runs].sort(
          historyOrder,
        );
        detail.older_cursor = previous.older_cursor;
      }
      cacheDetail(id, detail);
      // Read after the backend accepted the pending message, this history shows it.
      if (pending?.accepted) settle(id, pending);
      for (const run of detail.runs) {
        if (["running", "queued"].includes(run.status)) {
          follow(id, run.id, Math.max(0, ...run.events.map((e) => e.sequence)));
        }
      }
    },
    loadOlder: async (id) => {
      const cursor = get().details[id]?.older_cursor;
      const conversationId = get().details[id]?.conversation_id;
      if (!cursor || loadingOlder.has(id) || deleted.has(id) || get().restoring[id]) return;
      loadingOlder.add(id);
      try {
        const page = await codingApi.history(id, cursor);
        const detail = get().details[id];
        if (
          !detail ||
          deleted.has(id) ||
          get().restoring[id] ||
          detail.conversation_id !== conversationId ||
          detail.older_cursor !== cursor
        )
          return;
        const existing = new Set(detail.runs.map((run) => run.id));
        cacheDetail(id, {
          ...detail,
          older_cursor: page.older_cursor,
          runs: [...page.runs.filter((run) => !existing.has(run.id)), ...detail.runs].sort(historyOrder),
        });
      } catch (error) {
        if (get().details[id]?.conversation_id === conversationId && !get().restoring[id] && !deleted.has(id))
          fail(error);
      } finally {
        loadingOlder.delete(id);
      }
    },
    sync: async (id) => {
      await get().refresh();
      for (const run of get().details[id]?.runs ?? []) {
        if (["running", "queued"].includes(run.status))
          follow(id, run.id, Math.max(0, ...run.events.map((event) => event.sequence)));
      }
    },
    newThread: async (projectId = null) => {
      try {
        const state = get();
        const selectedThread = state.workspace?.threads.find((item) => item.id === state.selected);
        const project =
          state.workspace?.projects.find((item) => item.id === selectedThread?.project_id) ??
          state.workspace?.projects[0];
        const thread = await codingApi.createThread({
          project_id: projectId ?? project?.id ?? null,
        });
        await get().refresh();
        await get().select(thread.id);
      } catch (error) {
        fail(error);
      }
    },
    patch: async (id, changes) => {
      try {
        await codingApi.patch(id, changes);
        await Promise.all([get().refresh(), get().load(id)]);
        // The sidebar keeps the open chat listed, so an archived one would stay until another
        // chat was opened. The next one opens now, as after deleting.
        // Only when this chat is the open one, or the one it helps: an archived chat being read
        // stays open while others are archived.
        const threads = get().workspace?.threads ?? [];
        const selected = get().selected;
        if (changes.archived && selected && inChain(threads, (thread) => thread.id === id)(selected)) {
          const off = inChain(threads, (thread) => thread.archived);
          const next = threads.find((thread) => !off(thread.id));
          if (next) await get().select(next.id);
          else await get().newThread();
        }
      } catch (error) {
        fail(error);
        throw error;
      }
    },
    orderProjects: async (ids) => {
      const workspace = get().workspace;
      if (!workspace) return;
      const previous = workspace.projects.map((project) => project.id);
      const move: { ids: string[]; saved: number | null } = { ids, saved: null };
      projectOrder = move;
      set({ workspace: withProjectOrder(workspace, ids) });
      try {
        await codingApi.orderProjects(ids);
        move.saved = refreshSequence;
      } catch (error) {
        // A later move already shows its own order.
        if (projectOrder === move) {
          projectOrder = null;
          const current = get().workspace;
          if (current) set({ workspace: withProjectOrder(current, previous) });
        }
        void get()
          .refresh()
          .catch(() => undefined);
        throw error;
      }
    },
    remove: async (id, deleteWorktree = false) => {
      const workspace = get().workspace;
      const threads = workspace?.threads ?? [];
      // Its helpers are deleted with it.
      const ids = new Set([
        id,
        ...threads.filter((thread) => thread.parent_thread_id === id).map((thread) => thread.id),
      ]);
      ids.forEach((chat) => removing.add(chat));
      if (workspace) set({ workspace: listed(workspace) });
      const off = inChain(threads, (thread) => thread.archived || ids.has(thread.id));
      const next = threads.find((thread) => !off(thread.id));
      const selected = get().selected;
      if (selected && ids.has(selected) && next) await get().select(next.id);
      try {
        await codingApi.remove(id, deleteWorktree);
      } catch (error) {
        // Not deleted, or only in part: listed again with the error.
        ids.forEach((chat) => removing.delete(chat));
        fail(error);
        await get()
          .refresh()
          .catch(() => undefined);
        return;
      }
      const details = { ...get().details };
      for (const chat of ids) {
        deleted.add(chat);
        loadSequence.set(chat, (loadSequence.get(chat) ?? 0) + 1);
        stopThreadStreams(chat);
        delete details[chat];
        lastOpened.delete(chat);
      }
      set({ details });
      try {
        await get().refresh();
        if (get().selected && ids.has(get().selected!)) await get().newThread();
      } catch (error) {
        fail(error);
      } finally {
        ids.forEach((chat) => removing.delete(chat));
      }
    },
    send: async (id, text, context, options) => {
      const owner = get().details[id];
      // The message shows in the chat at once, with the files it takes; the backend can take a
      // moment to start its task.
      const message: PendingMessage = {
        text: text || (context?.command ? `/${context.command}` : ""),
        context,
        files: owner?.attachments ?? [],
        at: new Date().toISOString(),
      };
      set({
        sending: { ...get().sending, [id]: true },
        pending: { ...get().pending, [id]: message },
        error: null,
      });
      // The composer empties as the message leaves. Its saved draft goes too, for a composer that
      // mounts while it is sent (another chat opened and back, or a worktree start remounting it).
      try {
        if (localStorage.getItem(`harness.draft.${id}`) === text)
          localStorage.removeItem(`harness.draft.${id}`);
        if (owner && localStorage.getItem(contextDraftKey(owner)) === JSON.stringify(context))
          localStorage.removeItem(contextDraftKey(owner));
      } catch {
        /* Nothing saved to clear. */
      }
      let accepted = false;
      try {
        await codingApi.send(id, text, context, options);
        accepted = true;
        if (get().pending[id] === message)
          set({ pending: { ...get().pending, [id]: { ...message, accepted: true } } });
        await Promise.all([get().load(id), get().refresh()]);
      } catch (error) {
        if (!accepted) {
          // Refused: the message goes back in the box, unless something else was written there.
          try {
            if (!localStorage.getItem(`harness.draft.${id}`))
              localStorage.setItem(`harness.draft.${id}`, text);
            const saved = owner && localStorage.getItem(contextDraftKey(owner));
            if (owner && context && (!saved || saved === JSON.stringify(emptyContext())))
              localStorage.setItem(contextDraftKey(owner), JSON.stringify(context));
          } catch {
            /* The mounted composer still gets it back. */
          }
          set({ unsent: { ...get().unsent, [id]: { text, context } } });
        }
        // Worktree creation can succeed before message validation fails. Keep
        // the draft, but show the actual folder before the user retries.
        if (options?.worktree) {
          // The chips are saved per folder: carry them to the new worktree's key before the
          // composer remounts there and reads it.
          const owner = get().details[id];
          const moved = await codingApi
            .workspace()
            .then((snapshot) => snapshot.threads.find((thread) => thread.id === id))
            .catch(() => undefined);
          try {
            const saved = owner && localStorage.getItem(contextDraftKey(owner));
            if (saved && moved && localStorage.getItem(contextDraftKey(moved)) === null)
              localStorage.setItem(contextDraftKey(moved), saved);
          } catch {
            /* The text is kept either way. */
          }
          await Promise.allSettled([get().load(id), get().refresh()]);
        }
        fail(error);
        throw error;
      } finally {
        set({ sending: { ...get().sending, [id]: false } });
        settle(id);
        const detail = get().details[id];
        if (detail) cacheDetail(id, detail);
      }
    },
    compact: async (id) => {
      try {
        await codingApi.compact(id);
        await Promise.all([get().load(id), get().refresh()]);
      } catch (error) {
        fail(error);
      }
    },
    fork: async (id, options) => {
      const forked = await codingApi.fork(id, options);
      if (forked.draft) {
        try {
          writeDraft(forked, forked.draft);
        } catch {
          fail(new Error("Chat forked, but its draft could not be saved in this browser."));
        }
      }
      await get().refresh();
      await get().select(forked.id);
    },
    rewind: async (id, checkpointId) => {
      if (get().restoring[id] || get().sending[id])
        throw new Error("Wait for the current message operation before rewinding.");
      set({ restoring: { ...get().restoring, [id]: true }, error: null });
      loadSequence.set(id, (loadSequence.get(id) ?? 0) + 1);
      refreshSequence++;
      stopThreadStreams(id);
      try {
        const result = await codingApi.rewind(id, checkpointId);
        // Responses requested before the continuation changed must not restore it.
        refreshSequence++;
        loadSequence.set(id, (loadSequence.get(id) ?? 0) + 1);
        const owner = get().details[id];
        // The response has the chat's stored folder, null for a chat on its project's folder.
        // Drafts are keyed by the folder the composer shows, which a rewind never changes.
        const thread = owner
          ? { ...result.thread, folder: owner.folder, configured_folder: owner.configured_folder }
          : result.thread;
        try {
          if (owner) {
            const draft = localStorage.getItem(`harness.draft.${id}`);
            const context = localStorage.getItem(contextDraftKey(owner));
            if (draft !== null) localStorage.setItem(`harness.draft.${result.archived_thread_id}`, draft);
            if (context !== null)
              localStorage.setItem(contextDraftKey({ ...owner, id: result.archived_thread_id }), context);
          }
          writeDraft(thread, result.draft);
        } catch {
          fail(new Error("Chat restored, but its draft could not be saved in this browser."));
        }
        cacheDetail(id, { ...thread, runs: [], attachments: [] });
      } catch (error) {
        fail(error);
        throw error;
      } finally {
        set({ restoring: { ...get().restoring, [id]: false } });
        await Promise.all([get().load(id), get().refresh()]).catch(fail);
      }
    },
    stop: async (id) => {
      try {
        const owner = Object.values(get().details).find((detail) =>
          detail.runs.some((run) => run.id === id),
        )?.id;
        await api.cancelRun(id);
        if (owner) await get().load(owner);
        await get().refresh();
      } catch (error) {
        fail(error);
      }
    },
  };
});
export function stopFollowing() {
  for (const controller of following.values()) controller.abort();
  following.clear();
}
