import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  Archive,
  ArrowUpCircle,
  ChevronDown,
  CircleAlert,
  Clock,
  CornerDownRight,
  GitFork,
  LoaderCircle,
  MessagesSquare,
  MoreHorizontal,
  Pencil,
  Pin,
  Plus,
  Plug,
  Settings2,
  SquarePen,
  Trash2,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { confirmAction } from "@/lib/confirm";
import { beginWindowDrag } from "@/lib/titlebar";
import { chatTitle, codingApi, isActive, routineStatus, type CodingProject, type CodingThread } from "./api";
import { installUpdate, restartToUpdate, useAppUpdate } from "@/lib/app-update";
import { useCoding } from "./store";
import { NavigationHeader } from "./navigation-header";
import { ProjectIcon } from "./project-appearance";
import { ForkDialog } from "./checkpoint-actions";
import { RoutineSettings } from "./routine-settings";
import "./sidebar.css";
const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Replaces a chat's title in place: Enter or leaving saves, Escape cancels. */
export function TitleEditor({
  thread,
  className,
  onDone,
}: {
  thread: CodingThread;
  className?: string;
  onDone: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const finished = useRef(false);
  const title = chatTitle(thread);
  useLayoutEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  const finish = (save: boolean) => {
    if (finished.current) return;
    finished.current = true;
    const value = input.current?.value.trim() ?? "";
    onDone();
    if (save && value && value !== title && value !== thread.title)
      // The store reports a failed save; the title stays as it was.
      void useCoding
        .getState()
        .patch(thread.id, { title: value })
        .catch(() => undefined);
  };
  return (
    <input
      ref={input}
      className={`title-editor ${className ?? ""}`}
      aria-label={`Rename ${title}`}
      defaultValue={title}
      // The API's limit for a chat title (ThreadPatch in coding_workspace.py).
      maxLength={160}
      spellCheck={false}
      onBlur={() => finish(true)}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Enter") {
          event.preventDefault();
          finish(true);
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          finish(false);
        }
      }}
    />
  );
}

function ThreadRow({
  thread,
  selected,
  onSelect,
  onEdit,
  unread,
  depth,
  pastHelpers,
  helpersExpanded,
  onToggleHelpers,
  needsInput,
}: {
  thread: CodingThread;
  selected: boolean;
  onSelect: () => void;
  onEdit: () => void;
  unread: boolean;
  depth: number;
  pastHelpers: number;
  helpersExpanded: boolean;
  onToggleHelpers: () => void;
  needsInput: NeedsInput;
}) {
  const title = chatTitle(thread);
  const patch = useCoding((s) => s.patch);
  const remove = useCoding((s) => s.remove);
  const threads = useCoding((s) => s.workspace?.threads);
  const helpers = threads?.filter((other) => other.parent_thread_id === thread.id) ?? [];
  const active = useCoding((s) =>
    thread.status !== undefined
      ? isActive(thread.status)
      : Boolean(s.details[thread.id]?.runs.some((run) => isActive(run.status))),
  );
  const [forking, setForking] = useState(false);
  const [routineOpen, setRoutineOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const renameRequested = useRef(false);
  const rowRef = useRef<HTMLDivElement>(null);
  // Opened from elsewhere, such as a helper's card in its main chat: the row comes into view.
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [selected]);
  const branch = depth ? (
    <CornerDownRight className="thread-branch" strokeWidth={1} aria-hidden="true" />
  ) : null;
  const indent = depth ? { paddingLeft: 16 + depth * 12 } : undefined;
  return (
    <div ref={rowRef} className={`coding-thread-row ${selected ? "selected" : ""}`}>
      {renaming ? (
        <div className="thread-select" style={indent}>
          {branch}
          <TitleEditor thread={thread} className="thread-title" onDone={() => setRenaming(false)} />
        </div>
      ) : (
        <button
          className="thread-select"
          style={indent}
          aria-current={selected ? "page" : undefined}
          // The second click of a double-click renames instead of selecting again.
          onClick={(event) => {
            if (event.detail < 2) onSelect();
          }}
          onDoubleClick={() => setRenaming(true)}
        >
          {branch}
          {thread.routine ? (
            <Clock
              className={`thread-routine size-3.5 shrink-0 ${thread.routine.enabled && !thread.routine.finished ? "" : "opacity-50"}`}
              role="img"
              aria-label={`Routine, ${thread.routine.summary}. ${routineStatus(thread.routine)}`}
            />
          ) : null}
          <span className="thread-title">{title}</span>
          {needsInput || active || unread || thread.pinned ? (
            <span className="thread-status">
              {needsInput ? (
                <CircleAlert
                  className="thread-needs-input size-3 shrink-0"
                  role="img"
                  aria-label={needsInput === "helper" ? "A helper needs your input" : "Needs your input"}
                />
              ) : active ? (
                <LoaderCircle className="size-3 shrink-0 animate-spin" aria-label="Chat running" />
              ) : unread ? (
                <span className="unread-dot" role="img" aria-label="Unread response" />
              ) : thread.pinned ? (
                <Pin className="size-3 shrink-0" />
              ) : null}
            </span>
          ) : null}
        </button>
      )}
      {pastHelpers ? (
        <Button
          className="thread-helpers"
          variant="ghost"
          size="icon-xs"
          aria-label={`${helpersExpanded ? "Hide" : "Show"} helpers for ${title}`}
          aria-expanded={helpersExpanded}
          title={`${helpersExpanded ? "Hide" : "Show"} ${pastHelpers} past ${pastHelpers === 1 ? "helper" : "helpers"}`}
          onClick={onToggleHelpers}
        >
          <Workflow />
        </Button>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button className="thread-more" variant="ghost" size="icon-xs" aria-label={`Options for ${title}`}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="min-w-44"
          onCloseAutoFocus={(event) => {
            // Rename once the menu has closed, so its focus handling leaves the title field alone.
            if (!renameRequested.current) return;
            renameRequested.current = false;
            event.preventDefault();
            setRenaming(true);
          }}
        >
          <DropdownMenuItem onSelect={() => (renameRequested.current = true)}>
            <Pencil />
            Rename
          </DropdownMenuItem>
          {thread.routine ? (
            <DropdownMenuItem onClick={() => setRoutineOpen(true)}>
              <Clock />
              Routine settings
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem onClick={onEdit}>
            <Settings2 />
            Chat settings
          </DropdownMenuItem>
          <DropdownMenuItem disabled={active || forking} onClick={() => setForking(true)}>
            <GitFork />
            Fork chat
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => void patch(thread.id, { pinned: !thread.pinned }).catch(() => undefined)}
          >
            <Pin />
            {thread.pinned ? "Unpin" : "Pin"}
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => void patch(thread.id, { archived: !thread.archived }).catch(() => undefined)}
          >
            <Archive />
            {thread.archived ? "Restore" : "Archive"}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-destructive"
            onClick={() => {
              // Its helpers are deleted with it, their worktrees included.
              const worktrees = Boolean(thread.worktree) || helpers.some((helper) => helper.worktree);
              const also = helpers.length
                ? ` Its ${helpers.length} ${helpers.length === 1 ? "helper is" : "helpers are"} deleted too.`
                : "";
              void confirmAction(
                (worktrees
                  ? `Delete this chat, its history, and its managed worktree${helpers.length ? "s" : ""}? Uncommitted, untracked, and ignored files in ${helpers.length ? "them" : "the worktree"} will be permanently deleted. ${helpers.length ? "Git branches" : "Its Git branch"} will be kept.`
                  : "Delete this chat and its history?") + also,
              )
                .then((confirmed) => (confirmed ? remove(thread.id, worktrees) : undefined))
                .catch((e) => toast.error(errorText(e)));
            }}
          >
            <Trash2 />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {forking ? <ForkDialog thread={thread} onClose={() => setForking(false)} /> : null}
      {routineOpen && thread.routine ? (
        <RoutineSettings thread={thread} onDone={() => setRoutineOpen(false)} />
      ) : null}
    </div>
  );
}

/** A paused chat waits on the user, not the model: its own card, or one of its helpers'. */
type NeedsInput = "self" | "helper" | null;
// A helper's question is its parent's to answer; its approval cards, sign-ins and takeovers are the user's.
const waitingOnUser = (thread: CodingThread) =>
  (thread.status?.startsWith("waiting_") ?? false) && !(thread.parent_thread_id && thread.asks_questions);

function nestedThreads(
  threads: CodingThread[],
  expanded: ReadonlySet<string>,
  viewing: ReadonlySet<string> = new Set(),
) {
  const byId = new Map(threads.map((thread) => [thread.id, thread]));
  const children = new Map<string | null, CodingThread[]>();
  const visibleByDefault = new Set<string>();
  for (const thread of threads) {
    const parent = thread.parent_thread_id ? byId.get(thread.parent_thread_id) : undefined;
    const parentId = parent?.id ?? null;
    if (!children.has(parentId)) children.set(parentId, []);
    children.get(parentId)!.push(thread);
    if (
      !parent ||
      thread.pinned ||
      viewing.has(thread.id) ||
      isActive(thread.status ?? "") ||
      (isActive(parent.status ?? "") && parent.active_run_id && thread.parent_run_id === parent.active_run_id)
    ) {
      // Keep ancestors visible too, so active descendants cannot disappear behind a settled row.
      let current: CodingThread | undefined = thread;
      while (current && !visibleByDefault.has(current.id)) {
        visibleByDefault.add(current.id);
        current = current.parent_thread_id ? byId.get(current.parent_thread_id) : undefined;
      }
    }
  }
  const helperWaits = (id: string, seen = new Set<string>()): boolean => {
    if (seen.has(id)) return false;
    seen.add(id);
    return (children.get(id) ?? []).some((child) => waitingOnUser(child) || helperWaits(child.id, seen));
  };
  const result: Array<{ thread: CodingThread; depth: number; pastHelpers: number; needsInput: NeedsInput }> =
    [];
  const visited = new Set<string>();
  function append(thread: CodingThread, depth: number, visible: boolean) {
    if (visited.has(thread.id)) return;
    visited.add(thread.id);
    const helpers = children.get(thread.id) ?? [];
    if (visible)
      result.push({
        thread,
        depth,
        pastHelpers: helpers.filter((child) => !visibleByDefault.has(child.id)).length,
        needsInput: waitingOnUser(thread) ? "self" : helperWaits(thread.id) ? "helper" : null,
      });
    for (const child of helpers) {
      append(child, depth + 1, visible && (visibleByDefault.has(child.id) || expanded.has(thread.id)));
    }
  }
  for (const thread of children.get(null) ?? []) append(thread, 0, true);
  for (const thread of threads) if (!visited.has(thread.id)) append(thread, 0, true);
  return result;
}
/**
 * Drag a project, with its chats, to another place in the list. The drag works on the DOM
 * directly, so moving the pointer re-renders nothing; only the drop changes the store.
 */
function useProjectReorder() {
  const listRef = useRef<HTMLDivElement>(null);
  const indicatorRef = useRef<HTMLDivElement>(null);
  // The click that ends a drag must not also collapse the project.
  const draggedRef = useRef(false);
  const begin = useCallback((event: ReactPointerEvent<HTMLElement>, projectId: string) => {
    const root = listRef.current;
    const line = indicatorRef.current;
    const handle = event.currentTarget;
    if (event.button !== 0 || !event.isPrimary || !root || !line) return;
    const source = handle.closest<HTMLElement>(".sidebar-project");
    const { pointerId, clientX: startX, clientY: startY } = event;
    let dragging = false;
    let slot = -1;
    let frame = 0;
    let pointerY = startY;
    const place = () => {
      frame = 0;
      const sections = Array.from(root.querySelectorAll<HTMLElement>(":scope > .sidebar-project"));
      let next = sections.findIndex((section) => {
        const rect = section.getBoundingClientRect();
        return pointerY < rect.top + rect.height / 2;
      });
      if (next < 0) next = sections.length;
      if (next === slot || !sections.length) return;
      slot = next;
      const last = sections[sections.length - 1];
      // Halfway into the space between two projects.
      const top =
        next < sections.length ? sections[next].offsetTop - 4 : last.offsetTop + last.offsetHeight + 4;
      line.style.transform = `translateY(${top - 1}px)`;
      line.dataset.visible = "true";
    };
    const release = () => {
      window.setTimeout(() => {
        draggedRef.current = false;
      });
    };
    const finish = (commit: boolean) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", key, true);
      if (!dragging) return;
      if (frame) {
        cancelAnimationFrame(frame);
        place();
      }
      source?.classList.remove("dragging");
      root.classList.remove("reordering");
      delete line.dataset.visible;
      if (!commit) return;
      const ids = (useCoding.getState().workspace?.projects ?? []).map((project) => project.id);
      const from = ids.indexOf(projectId);
      if (from < 0 || slot < 0 || slot === from || slot === from + 1) return;
      const order = ids.filter((id) => id !== projectId);
      order.splice(slot > from ? slot - 1 : slot, 0, projectId);
      void useCoding
        .getState()
        .orderProjects(order)
        .catch((error) => toast.error(`The project order was not saved: ${errorText(error)}`));
    };
    const move = (next: PointerEvent) => {
      if (next.pointerId !== pointerId) return;
      pointerY = next.clientY;
      if (!dragging) {
        if (Math.hypot(next.clientX - startX, next.clientY - startY) < 4) return;
        dragging = true;
        draggedRef.current = true;
        source?.classList.add("dragging");
        root.classList.add("reordering");
        try {
          handle.setPointerCapture(pointerId);
        } catch {
          // Window listeners still follow the pointer.
        }
      }
      next.preventDefault();
      if (!frame) frame = requestAnimationFrame(place);
    };
    const up = (next: PointerEvent) => {
      if (next.pointerId !== pointerId) return;
      finish(true);
      release();
    };
    const cancel = (next: PointerEvent) => {
      if (next.pointerId !== pointerId) return;
      finish(false);
      release();
    };
    const key = (next: KeyboardEvent) => {
      if (next.key !== "Escape" || !dragging) return;
      next.preventDefault();
      next.stopPropagation();
      finish(false);
      // The button released after Escape still clicks the project.
      window.addEventListener("pointerup", release, { once: true });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key, true);
  }, []);
  return { listRef, indicatorRef, draggedRef, begin };
}
function SidebarActions({
  onSession,
  onConnectors,
  connectorsSelected,
}: {
  onSession: () => void;
  onConnectors?: () => void;
  connectorsSelected: boolean;
}) {
  return (
    <div className="sidebar-new-project">
      <button className="new-project" onClick={onSession}>
        <SquarePen className="size-5 shrink-0" />
        <span>New session</span>
      </button>
      <button
        className="new-project"
        aria-current={connectorsSelected ? "page" : undefined}
        onClick={onConnectors}
      >
        <Plug className="size-5 shrink-0" />
        <span>Connectors</span>
      </button>
    </div>
  );
}
/** The projects' title, with a button for a new project while the pointer is on it. */
function ProjectsHeading({ onNew }: { onNew: () => void }) {
  return (
    <div className="sidebar-section sidebar-projects-heading">
      <h2>Projects</h2>
      <Button size="icon-xs" variant="ghost" aria-label="New project" title="New project" onClick={onNew}>
        <Plus />
      </Button>
    </div>
  );
}
export function Sidebar({
  onSettings,
  onConnectors,
  connectorsSelected = false,
  unreadThreads = new Set<string>(),
  onChat,
  onProject,
  onThread,
  onToggleSidebar,
}: {
  onSettings: () => void;
  onConnectors?: () => void;
  connectorsSelected?: boolean;
  unreadThreads?: ReadonlySet<string>;
  onChat?: () => void;
  onProject: (project?: CodingProject) => void;
  onThread: (thread: CodingThread) => void;
  onToggleSidebar?: () => void;
}) {
  const workspace = useCoding((s) => s.workspace);
  const selected = useCoding((s) => s.selected);
  const select = useCoding((s) => s.select);
  const newThread = useCoding((s) => s.newThread);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [expandedHelpers, setExpandedHelpers] = useState<ReadonlySet<string>>(new Set());
  // Projects showing their archived chats instead of their active ones.
  const [showArchived, setShowArchived] = useState<Record<string, boolean>>({});
  const { listRef, indicatorRef, draggedRef, begin: beginReorder } = useProjectReorder();
  const projects = workspace?.projects ?? [];
  // An archived chat's helpers go with it (a rewind sets aside the helpers' parent too), not
  // to the top of the list as if they had no parent.
  const byId = new Map((workspace?.threads ?? []).map((thread) => [thread.id, thread]));
  const setAside = (thread: CodingThread) => {
    for (
      let current: CodingThread | undefined = thread, depth = 0;
      current && depth < 64;
      current = current.parent_thread_id ? byId.get(current.parent_thread_id) : undefined, depth++
    )
      if (current.archived) return true;
    return false;
  };
  // The chat being viewed stays listed, archived or not, so opening a helper shows where it is.
  const viewing = new Set<string>();
  for (
    let current = selected ? byId.get(selected) : undefined, depth = 0;
    current && depth < 64;
    current = current.parent_thread_id ? byId.get(current.parent_thread_id) : undefined, depth++
  )
    viewing.add(current.id);
  const threads = workspace?.threads.filter((thread) => !setAside(thread) || viewing.has(thread.id)) ?? [];
  // Opening a chat in a collapsed project expands it, once: the user can collapse it again.
  const viewedProject = selected ? byId.get(selected)?.project_id : undefined;
  const [expandedFor, setExpandedFor] = useState(viewedProject);
  if (viewedProject !== expandedFor) {
    setExpandedFor(viewedProject);
    if (viewedProject && collapsed[viewedProject]) setCollapsed({ ...collapsed, [viewedProject]: false });
  }
  // A project's archived chats, with the chats they belong to, so a helper stays under its parent.
  const archivedThreads = (projectId: string) => {
    const shown = new Set<string>();
    for (const thread of workspace?.threads ?? []) {
      if (thread.project_id !== projectId || !setAside(thread)) continue;
      for (
        let current: CodingThread | undefined = thread, depth = 0;
        current && !shown.has(current.id) && depth < 64;
        current = current.parent_thread_id ? byId.get(current.parent_thread_id) : undefined, depth++
      )
        shown.add(current.id);
    }
    return (workspace?.threads ?? []).filter((thread) => shown.has(thread.id));
  };
  const startChat = (project?: CodingProject) => {
    if (!project) {
      onProject();
      return;
    }
    onChat?.();
    setCollapsed((current) => ({
      ...current,
      [project.id]: false,
    }));
    void newThread(project.id);
  };
  // Archived chats are listed in full: every helper under its parent, with no toggles.
  const rows = (items: CodingThread[], archived = false) =>
    nestedThreads(items, archived ? new Set(items.map((thread) => thread.id)) : expandedHelpers, viewing).map(
      ({ thread, depth, pastHelpers, needsInput }) => (
        <ThreadRow
          key={thread.id}
          thread={thread}
          depth={depth}
          pastHelpers={archived ? 0 : pastHelpers}
          needsInput={needsInput}
          helpersExpanded={expandedHelpers.has(thread.id)}
          onToggleHelpers={() =>
            setExpandedHelpers((current) => {
              const next = new Set(current);
              if (next.has(thread.id)) next.delete(thread.id);
              else next.add(thread.id);
              return next;
            })
          }
          selected={!connectorsSelected && selected === thread.id}
          unread={unreadThreads.has(thread.id)}
          onSelect={() => {
            onChat?.();
            void select(thread.id);
          }}
          onEdit={() => onThread(thread)}
        />
      ),
    );
  return (
    <aside
      className="coding-sidebar"
      aria-label="Projects sidebar"
      data-tauri-drag-region
      onMouseDown={beginWindowDrag}
    >
      <NavigationHeader onToggle={onToggleSidebar} />
      <SidebarActions
        // In the project of the session open now, which the new session's page can change.
        onSession={() => startChat(projects.find((project) => project.id === viewedProject) ?? projects[0])}
        onConnectors={onConnectors}
        connectorsSelected={connectorsSelected}
      />
      <div className="sidebar-scroll" ref={listRef}>
        <ProjectsHeading onNew={() => onProject()} />
        {projects.map((project) => (
          <section key={project.id} className="sidebar-project" aria-label={`${project.name} project`}>
            <div className="project-group">
              <button
                className="min-w-0 flex-1"
                aria-expanded={!collapsed[project.id]}
                aria-description="Drag to reorder projects"
                onPointerDown={(event) => beginReorder(event, project.id)}
                onClick={() => {
                  if (draggedRef.current) return;
                  setCollapsed((current) => ({
                    ...current,
                    [project.id]: !current[project.id],
                  }));
                }}
              >
                <ChevronDown className={`project-chevron ${collapsed[project.id] ? "-rotate-90" : ""}`} />
                <ProjectIcon icon={project.icon} color={project.icon_color} className="project-icon" />
                <span className="truncate">{project.name}</span>
                {showArchived[project.id] ? <span className="project-view">Archived</span> : null}
              </button>
              <div className="project-actions">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon-xs" aria-label={`Settings for ${project.name}`}>
                      <MoreHorizontal />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={() => onProject(project)}>
                      <Settings2 />
                      Settings
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        setShowArchived((current) => ({ ...current, [project.id]: !current[project.id] }));
                        setCollapsed((current) => ({ ...current, [project.id]: false }));
                      }}
                    >
                      {showArchived[project.id] ? <MessagesSquare /> : <Archive />}
                      {showArchived[project.id] ? "Show active" : "Show archived"}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() =>
                        void confirmAction("Remove this project? Its chat history will remain searchable.")
                          .then(async (confirmed) => {
                            if (!confirmed) return;
                            await codingApi.removeProject(project.id);
                            await useCoding.getState().refresh();
                          })
                          .catch((e) => toast.error(errorText(e)))
                      }
                    >
                      <Trash2 />
                      Remove
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label={`New chat in ${project.name}`}
                  onClick={() => startChat(project)}
                >
                  <Plus />
                </Button>
              </div>
            </div>
            {collapsed[project.id] ? null : (
              <div className="sidebar-project-chats">
                {showArchived[project.id] ? (
                  archivedThreads(project.id).length ? (
                    rows(archivedThreads(project.id), true)
                  ) : (
                    <p className="empty-project">No archived chats</p>
                  )
                ) : (
                  <>
                    {/* Routines first: they are the project's standing work. */}
                    {rows([
                      ...threads.filter((thread) => thread.project_id === project.id && thread.routine),
                      ...threads.filter((thread) => thread.project_id === project.id && !thread.routine),
                    ])}
                    {!threads.some((thread) => thread.project_id === project.id) ? (
                      <button
                        className="empty-project"
                        aria-label={`Start a chat in ${project.name}`}
                        onClick={() => startChat(project)}
                      >
                        Start a chat
                      </button>
                    ) : null}
                  </>
                )}
              </div>
            )}
          </section>
        ))}
        <div className="project-drop-indicator" ref={indicatorRef} aria-hidden="true" />
      </div>
      <div className="sidebar-bottom">
        <UpdateButton />
        <button onClick={onSettings}>
          <Settings2 className="size-4" />
          Settings
        </button>
      </div>
    </aside>
  );
}

/** Shown only when a newer release is available: installs it and restarts into it. */
function UpdateButton() {
  const update = useAppUpdate();
  if (update.status !== "available" && update.status !== "installing" && update.status !== "ready")
    return null;
  return (
    <button
      className="sidebar-update"
      disabled={update.status === "installing"}
      onClick={() => void (update.status === "ready" ? restartToUpdate() : installUpdate())}
    >
      {update.status === "installing" ? (
        <LoaderCircle className="size-4 animate-spin" />
      ) : (
        <ArrowUpCircle className="size-4" />
      )}
      {update.status === "installing"
        ? `Updating to ${update.next}…`
        : update.status === "ready"
          ? "Restart to finish updating"
          : `Update to ${update.next}`}
    </button>
  );
}
