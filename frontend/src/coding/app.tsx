import { Sidebar, TitleEditor } from "./sidebar";
import { Composer } from "./composer";
import { AttachmentPreviews } from "./attachment-preview";
import { FilePreviewPane } from "./file-preview";
import type { ProjectFile } from "@/lib/types";
import { Welcome } from "./welcome";
import { Field, FolderField } from "./folder-field";
import { Onboarding } from "./onboarding";
import { SidebarToggle } from "./navigation-header";
import { ProjectEditor } from "./project-editor";
import { QuestionCard } from "./questions";
import { CheckpointActions } from "./checkpoint-actions";
import { latestPause, runTranscript } from "./run-transcript";
import { runActivity } from "./run-status";
import { RunUsage } from "./run-usage";
import {
  lazy,
  memo,
  useCallback,
  Suspense,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentType,
  type RefObject,
} from "react";
import {
  ArrowDown,
  Archive,
  Check,
  ChevronDown,
  Clock,
  Copy,
  LoaderCircle,
  MessageSquare,
  PanelRight,
  Paperclip,
  Pin,
  Plug,
  Search,
  X,
} from "lucide-react";
import { ThemeProvider, useTheme } from "next-themes";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { CodingMarkdown } from "./markdown";
import { FileLinks } from "./file-links";
import { RunChecklist } from "@/components/task-checklist";
import { RunActivity } from "@/components/trace-event";
import { groupRunActivity } from "@/lib/run-activity";
import { DesktopDock } from "./live-screen";
import { startUpdateChecks } from "@/lib/app-update";
import { onNotificationOpen, reportVisibleChat } from "@/lib/notifications";
import { api } from "@/lib/api";
import { beginWindowDrag, syncWindowTheme } from "@/lib/titlebar";
import { useSettings } from "@/store/settings";
import { modelSetupDone } from "@/lib/models";
import { useZoom } from "@/lib/zoom";
import {
  chatTitle,
  codingApi,
  isActive,
  type CodingProject,
  type CodingRun,
  type CodingThread,
  type ChatCheckpoint,
  type TaskContext,
} from "./api";
import { useCoding } from "./store";
import { useThreadActivity } from "./thread-activity";
import type { WorkbenchTab } from "./workbench";
import { ResizableSidebar } from "./sidebar-resize";
import { useWorkbenchPreference } from "./workbench-preference";
import "./coding.css";
import "./shell-layout.css";

function preloadableScreen<Props extends object>(load: () => Promise<{ default: ComponentType<Props> }>) {
  let loaded: ComponentType<Props> | undefined;
  const preload = () =>
    load().then((module) => {
      loaded = module.default;
      return module;
    });
  const Lazy = lazy(preload);
  function Screen(props: Props) {
    // React.lazy still suspends on its first render after a cached import. Mount
    // the warmed component directly, keeping that choice stable while it is open.
    const [Component] = useState(() => loaded ?? Lazy);
    return <Component {...props} />;
  }
  return { Screen, preload };
}
const { Screen: SettingsView, preload: loadSettings } = preloadableScreen(() => import("./settings"));
const { Screen: ConnectorsView, preload: loadConnectors } = preloadableScreen(async () => ({
  default: (await import("@/views/connectors-view")).ConnectorsView,
}));
const { Screen: Workbench, preload: loadWorkbench } = preloadableScreen(async () => ({
  default: (await import("./workbench")).Workbench,
}));
const { Screen: RunConnectorSetup, preload: loadConnectorSetup } = preloadableScreen(async () => ({
  default: (await import("@/components/run-connector-setup")).RunConnectorSetup,
}));
const preloadScreens = [loadWorkbench, loadConnectors, loadConnectorSetup, loadSettings];
type Page = "chat" | "settings" | "connectors";
const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));
function ThreadSettings({ thread, onDone }: { thread: CodingThread; onDone: () => void }) {
  const projects = useCoding((s) => s.workspace?.projects ?? []);
  const [title, setTitle] = useState(thread.title);
  const [folder, setFolder] = useState(
    thread.configured_folder ?? (thread.project_id ? "" : (thread.folder ?? "")),
  );
  const [instructions, setInstructions] = useState(thread.instructions);
  const [projectId, setProjectId] = useState(thread.project_id ?? "");
  const [mode, setMode] = useState(thread.mode);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value) onDone();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Chat settings</DialogTitle>
          <DialogDescription>Choose where to work and what this chat should know.</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            void useCoding
              .getState()
              .patch(thread.id, {
                title,
                folder: folder || null,
                instructions,
                project_id: projectId || null,
                mode,
              })
              .then(onDone)
              .catch((e) => setError(errorText(e)))
              .finally(() => setBusy(false));
          }}
        >
          <Field label="Title">
            <input
              required
              aria-label="Chat title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <Field label="Project">
            <select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="" disabled>
                Choose a project
              </option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Agent mode">
            <select
              aria-label="Agent mode"
              value={mode}
              onChange={(event) => setMode(event.target.value as CodingThread["mode"])}
            >
              <option value="auto">Code</option>
              <option value="plan">Plan</option>
              <option value="full">Full access</option>
            </select>
          </Field>
          <FolderField value={folder} onChange={setFolder} />
          <Field label="Instructions">
            <textarea
              rows={4}
              aria-label="Conversation instructions"
              placeholder="Additional context for this chat…"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
          </Field>
          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <Button className="float-right" type="submit" disabled={busy || !title.trim()}>
            {busy ? <LoaderCircle className="animate-spin" /> : null}Save
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
function SearchDialog({ onDone, onChoose }: { onDone: () => void; onChoose: (id: string) => void }) {
  const threads = useCoding((s) => s.workspace?.threads ?? []);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CodingThread[] | null>(null);
  useEffect(() => {
    let alive = true;
    const timer = window.setTimeout(() => {
      void codingApi
        .search(query)
        .then((items) => {
          if (alive) setResults(items);
        })
        .catch(() => {
          if (alive) setResults(null);
        });
    }, 150);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [query]);
  const matches =
    query && results
      ? results
      : threads.filter(
          (t) =>
            t.title.toLowerCase().includes(query.toLowerCase()) ||
            t.instructions.toLowerCase().includes(query.toLowerCase()),
        );
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value) onDone();
      }}
    >
      <DialogContent className="max-w-xl gap-0 p-0">
        <DialogHeader className="sr-only">
          <DialogTitle>Search chats</DialogTitle>
          <DialogDescription>Search chat titles, instructions, and message history.</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-3 border-b border-border px-5 py-4">
          <Search className="size-4 text-muted-foreground" />
          <input
            autoFocus
            className="w-full bg-transparent text-sm outline-none"
            aria-label="Search chats"
            placeholder="Search chats…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="max-h-80 overflow-auto p-2">
          {matches.length ? (
            matches.map((thread) => (
              <button
                key={thread.id}
                className="search-result"
                onClick={() => {
                  onChoose(thread.id);
                  onDone();
                }}
              >
                <MessageSquare className="size-4" />
                <span className="flex-1 truncate">{thread.title}</span>
                {thread.archived ? <Archive className="size-3" /> : null}
                <span className="text-[10px]">{new Date(thread.updated_at).toLocaleDateString()}</span>
              </button>
            ))
          ) : (
            <p className="p-6 text-center text-sm text-muted-foreground">No conversations found.</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
// When a message was sent or a response finished, beside its actions: hours and minutes only.
const messageTime = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
function MessageTime({ at }: { at?: string | null }) {
  const date = at ? new Date(at) : null;
  if (!date || Number.isNaN(date.getTime())) return null;
  return (
    <time className="run-time" dateTime={date.toISOString()} title={date.toLocaleString()}>
      {messageTime.format(date)}
    </time>
  );
}
function CopyMessage({ text, label }: { text: string; label: string }) {
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      onClick={() =>
        void navigator.clipboard
          .writeText(text)
          .then(() => toast.success("Copied"))
          .catch((e) => toast.error(errorText(e)))
      }
    >
      <Copy />
    </Button>
  );
}
function UserMessage({
  threadId,
  text,
  files,
  context,
  checkpoint,
  label,
  at,
  name = "Your message",
}: {
  threadId: string;
  text: string;
  files?: ProjectFile[];
  context?: Partial<TaskContext>;
  checkpoint?: ChatCheckpoint;
  label?: string;
  at?: string;
  name?: string;
}) {
  return (
    <div className="user-message" role="group" aria-label={name}>
      {files?.length ? (
        <AttachmentPreviews className="message-attachments" threadId={threadId} files={files} />
      ) : null}
      <div className="user-message-bubble">
        {label ? <p className="mb-1 text-[10px] text-muted-foreground">{label}</p> : null}
        <div className="whitespace-pre-wrap break-words text-[15px] leading-7">{text}</div>
        {context ? (
          <div className="context-chips">
            {(context.files ?? []).map((file) => (
              <span key={file.path}>
                {file.path}:{file.start_line}–{file.end_line}
              </span>
            ))}
            {(context.skills ?? []).map((skill) => (
              <span key={skill}>Skill: {skill}</span>
            ))}
          </div>
        ) : null}
      </div>
      <div className="message-actions">
        <MessageTime at={at} />
        {text ? <CopyMessage text={text} label="Copy message" /> : null}
        <CheckpointActions threadId={threadId} checkpoint={checkpoint} />
      </div>
    </div>
  );
}

/** A routine's report on a finished run: sent as a message so it starts a turn here, shown as the
 *  routine's, with its answer rendered like any reply. */
function RoutineReport({
  threadId,
  report,
  text,
  at,
  checkpoint,
}: {
  threadId: string;
  report: NonNullable<CodingRun["input"]["routine_report"]>;
  text: string;
  at?: string;
  checkpoint?: ChatCheckpoint;
}) {
  const outcome =
    report.status === "completed" ? "finished" : report.status === "cancelled" ? "was stopped" : "failed";
  return (
    <div className="routine-report" role="group" aria-label={`Report from routine ${report.title}`}>
      <div className="routine-report-card">
        <p className="routine-report-header">
          <Clock className="size-3.5 shrink-0" aria-hidden />
          <span>
            Report from routine{" "}
            <button
              type="button"
              className="routine-report-link"
              onClick={() => void useCoding.getState().select(report.thread_id)}
            >
              {report.title}
            </button>{" "}
            · {outcome}
          </span>
        </p>
        <div className="coding-answer">
          <CodingMarkdown>{text}</CodingMarkdown>
        </div>
      </div>
      <div className="message-actions">
        <MessageTime at={at} />
        {text ? <CopyMessage text={text} label="Copy report" /> : null}
        <CheckpointActions threadId={threadId} checkpoint={checkpoint} />
      </div>
    </div>
  );
}

/** Each message's own attachments, keyed by run ID and by steering event ID. */
function sentFiles(run: CodingRun) {
  const sent = new Map<string, ProjectFile[]>();
  // Older runs predate per-message attachments and only record what they could read.
  const own = run.input.initial_reference_files ?? run.input.reference_files ?? [];
  if (own.length) sent.set(run.id, own);
  for (const event of run.events)
    if (
      event.event_type === "steering_requested" &&
      (event.payload.reference_files as ProjectFile[] | undefined)?.length
    )
      sent.set(String(event.id), event.payload.reference_files as ProjectFile[]);
  return sent;
}

/** A Compact now run: no message and no answer, only where the conversation was summarised. */
function CompactionNotice({ run }: { run: CodingRun }) {
  const active = isActive(run.status);
  const text = active
    ? "Compacting the conversation…"
    : run.status === "completed"
      ? run.output?.text || "Context compacted."
      : run.status === "cancelled"
        ? "Compaction stopped."
        : `Compaction failed. ${run.error ?? ""}`.trim();
  return (
    <div className="context-compacted" role="status">
      {active ? <LoaderCircle className="size-3 animate-spin" /> : null}
      <span>{text}</span>
    </div>
  );
}

/** What a paused run waits on the user for: its questions, an approval or a connector to set up.
 *  `threadId` is the run's chat; `chatId` the chat showing the card, which is the helper's main
 *  chat for a helper's copy of it. */
function PauseCard({
  run,
  threadId,
  chatId,
}: {
  run: Pick<CodingRun, "id" | "status" | "events">;
  threadId: string;
  chatId: string;
}) {
  const [busy, setBusy] = useState(false);
  // Helpers follow their main chat's access, so "for this chat" sets it there: the chat showing a
  // helper's copy of the card, or the helper's main chat when it is shown in the helper's own.
  const parentChat = useCoding(
    (s) => s.workspace?.threads.find((thread) => thread.id === threadId)?.parent_thread_id,
  );
  const accessChat = chatId !== threadId ? chatId : (parentChat ?? threadId);
  // For the rest of the chat: full access first, so this command and every later one the agent
  // asks to run outside the sandbox go ahead without a card.
  async function decide(decision: "approve" | "reject", wholeChat = false) {
    setBusy(true);
    try {
      if (wholeChat) await codingApi.patch(accessChat, { mode: "full" });
      await api.decideApproval(
        run.id,
        decision,
        "",
        approval?.event_type === "run_waiting_for_approval" ? approval.sequence : undefined,
      );
      await Promise.all([wholeChat && useCoding.getState().refresh(), useCoding.getState().load(chatId)]);
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const approval = latestPause(run.events);
  const requests =
    (approval?.payload.requests as
      | Array<{
          kind?: string;
          description?: string;
          command?: string;
          reason?: string;
          cwd?: string;
          danger?: string;
          outside_sandbox?: boolean;
        }>
      | undefined) ?? [];
  const folderRequests = requests.filter((request) => request.kind === "folder_access");
  const outside = requests.find((request) => request.kind === "outside_sandbox");
  const irreversible = requests.find((request) => request.kind === "irreversible");
  // One decision covers every request in the pause, so the card shows them all.
  const batch = requests.map((request, index) =>
    request.command === undefined ? (
      <p key={index} className="break-words">
        {request.description}
      </p>
    ) : (
      <div key={index} className="space-y-1">
        {request.danger || request.kind === "outside_sandbox" || request.outside_sandbox ? (
          <p className="break-words">
            {request.danger}
            {request.kind === "outside_sandbox" || request.outside_sandbox
              ? " It runs outside the sandbox, on this Mac."
              : null}
          </p>
        ) : null}
        {request.reason ? <p className="break-words text-muted-foreground">{request.reason}</p> : null}
        <pre className="whitespace-pre-wrap break-all">{request.command}</pre>
        <p className="text-xs text-muted-foreground">In {request.cwd}.</p>
      </div>
    ),
  );
  return (
    <>
      {run.status === "waiting_approval" && approval?.event_type === "run_questions" ? (
        <QuestionCard
          key={`${run.id}:${approval.sequence}`}
          threadId={threadId}
          runId={run.id}
          event={approval}
        />
      ) : run.status === "waiting_approval" && irreversible ? (
        // Asked in every mode, a chat with full access included, so it has no "for this chat".
        <div className="approval-card" role="group" aria-label="Can't be undone">
          <p className="font-medium text-destructive">This can&apos;t be undone</p>
          {batch}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="destructive" disabled={busy} onClick={() => void decide("approve")}>
              Run it
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void decide("reject")}>
              Don&apos;t run
            </Button>
          </div>
        </div>
      ) : run.status === "waiting_approval" && outside ? (
        <div className="approval-card" role="group" aria-label="Run outside the sandbox">
          <p className="font-medium">Run outside the sandbox</p>
          {batch}
          <p className="text-xs text-muted-foreground">It runs on this Mac as in your own terminal.</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy} onClick={() => void decide("approve")}>
              <Check />
              Allow once
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => void decide("approve", true)}
            >
              Allow for this chat
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void decide("reject")}>
              Don&apos;t run
            </Button>
          </div>
        </div>
      ) : run.status === "waiting_approval" ? (
        <div className="approval-card">
          <p className="font-medium">{folderRequests.length ? "Folder access" : "Review this action"}</p>
          {folderRequests.length ? (
            folderRequests.map((request, index) => (
              <p key={index} className="break-words">
                {request.description}
              </p>
            ))
          ) : (
            <pre>{JSON.stringify(approval?.payload ?? {}, null, 2)}</pre>
          )}
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={() => void decide("approve")}>
              <Check />
              {folderRequests.length ? "Allow folder" : "Approve"}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void decide("reject")}>
              Reject
            </Button>
          </div>
        </div>
      ) : null}
      {run.status === "waiting_connector" ? (
        <Suspense
          fallback={
            <p role="status" className="text-xs text-muted-foreground">
              Opening connector setup…
            </p>
          }
        >
          <RunConnectorSetup runId={run.id} events={run.events} />
        </Suspense>
      ) : null}
    </>
  );
}

type HelperPause = {
  agent_id: string;
  title: string;
  run_id: string;
  status: CodingRun["status"];
  pause: CodingRun["events"][number];
};
/** Helpers paused on the user, from the main task's own events: open until each is resolved. */
function openHelperRequests(events: CodingRun["events"]): HelperPause[] {
  const latest = new Map<string, CodingRun["events"][number]>();
  for (const event of events)
    if (event.event_type === "helper_waiting" || event.event_type === "helper_resolved")
      latest.set(String(event.payload.run_id), event);
  return [...latest.values()]
    .filter((event) => event.event_type === "helper_waiting")
    .map((event) => event.payload as unknown as HelperPause);
}

/** A helper's card in its main chat: answered here or in the helper's chat, either closes both. */
function HelperRequest({ request, chatId }: { request: HelperPause; chatId: string }) {
  const run = { id: request.run_id, status: request.status, events: [request.pause] };
  return (
    <div className="helper-request" role="group" aria-label={`${request.title} needs you`}>
      <p className="flex items-center gap-2 text-xs font-medium">
        <span className="min-w-0 truncate">Helper · {request.title}</span>
        <Button size="xs" variant="ghost" onClick={() => void useCoding.getState().select(request.agent_id)}>
          Open helper
        </Button>
      </p>
      {request.status === "waiting_computer" ? (
        <p className="text-xs text-muted-foreground">
          It handed you its computer. Open the helper to take over.
        </p>
      ) : (
        <PauseCard run={run} threadId={request.agent_id} chatId={chatId} />
      )}
    </div>
  );
}
const RunMessage = memo(function RunMessage({
  run,
  threadId,
  latest,
}: {
  run: CodingRun;
  threadId: string;
  latest: boolean;
}) {
  const files = sentFiles(run);
  const [trace, setTrace] = useState(false);
  const transcript = runTranscript(run);
  const lastMessage = [...transcript].reverse().find((item) => item.kind === "assistant");
  const active = isActive(run.status);
  const events = run.events.filter((e) => e.event_type !== "assistant_delta");
  const activity = groupRunActivity(events);
  const applied = new Set(
    events.filter((event) => event.event_type === "steering_applied").map((event) => event.payload.event_id),
  );
  const status =
    run.status === "queued"
      ? "Queued"
      : run.status.startsWith("waiting")
        ? "Waiting for you"
        : runActivity(events);
  // Activity and usage on the left, actions on the right: under the last response, or at the
  // foot of a run that has none. Shown on hover; always on the newest response, and kept while
  // running, open, or focused.
  const details =
    lastMessage || events.length || run.checkpoints?.length || Object.keys(run.usage ?? {}).length ? (
      <div className={`run-details${active || trace || latest ? " pinned" : ""}`}>
        <div className="run-details-info">
          {events.length ? (
            <button onClick={() => setTrace(!trace)} className="trace-toggle" aria-expanded={trace}>
              <ChevronDown className={`size-3 ${trace ? "" : "-rotate-90"}`} />
              {active ? "Activity" : "View activity"}{" "}
              <span>
                {activity.toolCount} {activity.toolCount === 1 ? "tool" : "tools"}
              </span>
            </button>
          ) : null}
          <RunUsage usage={run.usage} helpers={run.helper_usage} events={events} />
          {run.status === "cancelled" ? <span>Stopped</span> : null}
        </div>
        <div className="run-details-actions">
          {lastMessage && !active ? <MessageTime at={run.completed_at ?? run.started_at} /> : null}
          {lastMessage && !active ? <CopyMessage text={lastMessage.text} label="Copy response" /> : null}
          {/* The newest response is where the chat already is: nothing to rewind to. */}
          <CheckpointActions
            threadId={threadId}
            rewind={!latest}
            checkpoint={run.checkpoints?.find((point) => point.kind === "after_assistant")}
          />
        </div>
      </div>
    ) : null;
  return (
    <div className="coding-exchange">
      {run.input.routine_report ? (
        <RoutineReport
          threadId={threadId}
          report={run.input.routine_report}
          text={run.input.display_text ?? run.input.text}
          at={run.started_at}
          checkpoint={run.checkpoints?.find((point) => point.kind === "before_user")}
        />
      ) : (
        <UserMessage
          threadId={threadId}
          text={run.input.display_text ?? run.input.text}
          context={run.input.coding_context}
          files={files.get(run.id)}
          at={run.started_at}
          checkpoint={run.checkpoints?.find((point) => point.kind === "before_user")}
        />
      )}
      {transcript.map((item) =>
        item.kind === "steering" ? (
          <UserMessage
            key={item.id}
            threadId={threadId}
            at={item.event.created_at}
            text={String(item.event.payload.display_text ?? item.event.payload.text ?? "")}
            files={files.get(String(item.id))}
            context={item.event.payload.coding_context as Partial<TaskContext> | undefined}
            name={item.label ? "Your answer" : "Steering message"}
            label={
              item.label ??
              (applied.has(item.id)
                ? "Steering · Applied"
                : active
                  ? "Steering · Pending"
                  : "Steering · Not applied")
            }
            checkpoint={run.checkpoints?.find(
              (point) => point.kind === "before_steering" && point.event_id === item.id,
            )}
          />
        ) : item.kind === "answer" ? (
          item.text ? (
            <UserMessage
              key={item.id}
              threadId={threadId}
              text={item.text}
              label={item.label}
              at={item.at}
              name="Your answer"
            />
          ) : (
            <p key={item.id} className="agent-message text-xs text-muted-foreground">
              {item.label}
            </p>
          )
        ) : item.kind === "helper" ? (
          <div
            key={item.id}
            className="helper-started"
            role="group"
            aria-label={`Started a helper: ${item.name}`}
          >
            <div className="min-w-0">
              <p className="helper-started-title">Started a helper · {item.name}</p>
              {item.task ? <p className="helper-started-task">{item.task}</p> : null}
            </div>
            <Button
              size="sm"
              variant="outline"
              aria-label={`Open ${item.name}`}
              onClick={() => void useCoding.getState().select(item.agentId)}
            >
              Open
            </Button>
          </div>
        ) : item.kind === "compaction" ? (
          <div key={item.id} className="context-compacted" role="status">
            {item.state === "running" && active ? <LoaderCircle className="size-3 animate-spin" /> : null}
            <span>
              {item.state === "done"
                ? "Context compacted"
                : item.state === "failed"
                  ? "Compaction failed"
                  : active
                    ? "Compacting the conversation…"
                    : "Compaction stopped"}
            </span>
          </div>
        ) : item.kind === "plan" ? (
          <div className="agent-message" key={item.id}>
            <RunChecklist events={run.events} active={active} />
          </div>
        ) : (
          <div
            className={`agent-message${active || item !== lastMessage ? " agent-progress" : ""}`}
            role="group"
            aria-label="Assistant response"
            key={item.id}
          >
            {item.partial ? <p className="mb-2 text-xs text-muted-foreground">Interrupted response</p> : null}
            <div className="coding-answer">
              <CodingMarkdown streaming={active && !item.complete}>{item.text}</CodingMarkdown>
            </div>
            {item === lastMessage ? details : null}
          </div>
        ),
      )}
      <div className="agent-message">
        {active && status ? (
          <span
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
            role="status"
            aria-label="Task status"
          >
            <LoaderCircle className="size-3 animate-spin" />
            {status}
            {run.status === "queued" && (
              <Button
                size="xs"
                variant="ghost"
                aria-label="Cancel queued message"
                onClick={() => void useCoding.getState().stop(run.id)}
              >
                Cancel
              </Button>
            )}
          </span>
        ) : null}
        {run.error ? (
          <p role="alert" className="run-error">
            {run.error}
          </p>
        ) : null}
        <PauseCard run={run} threadId={threadId} chatId={threadId} />
        {active
          ? openHelperRequests(events).map((request) => (
              <HelperRequest
                key={`${request.run_id}:${request.pause.sequence}`}
                request={request}
                chatId={threadId}
              />
            ))
          : null}
        {!lastMessage ? details : null}
        {trace ? (
          <div className="coding-trace">
            <RunActivity items={activity.items} events={run.events} status={run.status} />
          </div>
        ) : null}
      </div>
    </div>
  );
});
function ConversationToolbar({
  thread,
  panel,
  onPanel,
  workbenchEnabled,
}: {
  thread: CodingThread;
  panel: boolean;
  workbenchEnabled: boolean;
  onPanel: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  return (
    <>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" />
          {renaming ? (
            <TitleEditor
              thread={thread}
              className="chat-title min-w-0 flex-1"
              onDone={() => setRenaming(false)}
            />
          ) : (
            // A click renames the chat; the toolbar around the title drags and zooms the window.
            <button
              type="button"
              className="chat-title chat-title-rename truncate"
              aria-label={`Rename ${chatTitle(thread)}`}
              onClick={() => setRenaming(true)}
            >
              {chatTitle(thread)}
            </button>
          )}
          {thread.pinned ? <Pin className="size-3 text-muted-foreground" /> : null}
        </div>
      </div>
      {workbenchEnabled ? (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={panel ? "Hide workbench" : "Show workbench"}
          title={panel ? "Hide workbench" : "Show workbench"}
          onClick={onPanel}
        >
          <PanelRight />
        </Button>
      ) : null}
    </>
  );
}
function ChatSkeleton({ label = "Loading conversation" }: { label?: string }) {
  return (
    <div
      className="chat-skeleton flex min-h-0 flex-1 overflow-hidden"
      role="status"
      aria-label={label}
      aria-busy="true"
    >
      <span className="sr-only">{label}…</span>
      <div
        aria-hidden="true"
        className="mx-auto flex w-full max-w-[780px] flex-col gap-8 px-6 py-9 sm:px-[38px] motion-safe:animate-pulse"
      >
        <div className="flex min-h-0 flex-1 flex-col gap-8 overflow-hidden">
          {["w-2/5", "w-3/5", "w-1/3"].map((width) => (
            <div className="flex shrink-0 flex-col gap-6" key={width}>
              <div className={`h-12 ${width} self-end rounded-2xl bg-muted`} />
              <div className="space-y-3">
                <div className="h-3 w-4/5 rounded bg-muted" />
                <div className="h-3 w-full rounded bg-muted" />
                <div className="h-3 w-3/5 rounded bg-muted" />
              </div>
            </div>
          ))}
        </div>
        <div className="h-28 shrink-0 rounded-2xl border border-border bg-muted/40" />
      </div>
    </div>
  );
}
function Chat({
  thread,
  onSettings,
  onModels,
  attachRef,
}: {
  thread: CodingThread;
  onSettings: () => void;
  onModels: () => void;
  attachRef: RefObject<((files: File[]) => void) | null>;
}) {
  const detail = useCoding((s) => s.details[thread.id]);
  const pending = useCoding((s) => s.pending[thread.id]);
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  // When the reader last scrolled by hand. Only that stops following the conversation's end:
  // layout changes move the view too (in WebKit a message briefly leaving clamps it upward).
  const readerScrolled = useRef(0);
  const reading = () => {
    readerScrolled.current = performance.now();
  };
  const [atBottom, setAtBottom] = useState(true);
  // A file the chat linked, previewed beside the conversation.
  const [preview, setPreview] = useState<string | null>(null);
  const closePreview = useCallback(() => setPreview(null), []);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const runs = detail?.runs;
  const hasMessages = (runs?.length ?? 0) > 0 || Boolean(pending);
  // The newest run that used the chat's desktop; its desktop stays up after it finishes.
  const desktopRun = [...(runs ?? [])]
    .reverse()
    .find(
      (run) =>
        run.status === "waiting_computer" || run.events.some((event) => event.event_type === "screenshot"),
    );
  useLayoutEffect(() => {
    const dock = composer.current;
    const viewport = scroll.current;
    if (!dock || !viewport) return;
    // Follow actual layout changes, including delayed Markdown and image layout.
    // Reading older messages and a smooth jump must not trigger competing scrolls.
    let height = -1;
    const measure = () => {
      // Resizing the window or sidebar fires this every frame; the transcript inherits the
      // variable, so write it only when the composer's height actually changed.
      if (dock.offsetHeight !== height) {
        height = dock.offsetHeight;
        // On the conversation, so the transcript and a file preview both stop above the composer.
        dock.parentElement?.style.setProperty("--composer-height", `${height}px`);
      }
      if (followBottom.current)
        viewport.scrollTo({
          top: Math.max(0, viewport.scrollHeight - viewport.clientHeight),
          behavior: "instant",
        });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(dock);
    observer.observe(viewport);
    if (content.current) observer.observe(content.current);
    // Scrolling keys reach the conversation without focusing it.
    const keys = (event: KeyboardEvent) => {
      if (
        ["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", " "].includes(event.key) &&
        !(event.target instanceof HTMLElement && event.target.closest("input, textarea, [contenteditable]"))
      )
        readerScrolled.current = performance.now();
    };
    window.addEventListener("keydown", keys);
    return () => {
      observer.disconnect();
      window.removeEventListener("keydown", keys);
    };
  }, [hasMessages]);
  // Sending asks to see the reply: follow the conversation's end again, wherever the reader was.
  const sentAt = pending?.at;
  useLayoutEffect(() => {
    const viewport = scroll.current;
    if (!sentAt || !viewport) return;
    followBottom.current = true;
    viewport.scrollTo({
      top: Math.max(0, viewport.scrollHeight - viewport.clientHeight),
      behavior: "instant",
    });
  }, [sentAt]);
  return (
    <div className="coding-chat-layout">
      <FileLinks.Provider value={setPreview}>
        <section className="coding-conversation">
          <div
            className="conversation-scroll"
            ref={scroll}
            onWheel={reading}
            onTouchMove={reading}
            onPointerDown={reading}
            onScroll={() => {
              const element = scroll.current;
              if (!element) return;
              const near = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
              // Wheel and trackpad momentum report every frame, so a short window is the reader's scroll.
              if (performance.now() - readerScrolled.current < 250) followBottom.current = near;
              else if (near) followBottom.current = true;
              else if (followBottom.current)
                element.scrollTo({
                  top: Math.max(0, element.scrollHeight - element.clientHeight),
                  behavior: "instant",
                });
              setAtBottom(followBottom.current);
            }}
          >
            {hasMessages ? (
              <div className="conversation-content" ref={content}>
                {detail?.older_cursor && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={loadingOlder}
                    onClick={async () => {
                      const viewport = scroll.current;
                      if (!viewport) return;
                      const height = viewport.scrollHeight;
                      const top = viewport.scrollTop;
                      followBottom.current = false;
                      setAtBottom(false);
                      setLoadingOlder(true);
                      await useCoding.getState().loadOlder(thread.id);
                      setLoadingOlder(false);
                      requestAnimationFrame(() => {
                        if (scroll.current === viewport)
                          viewport.scrollTop = top + viewport.scrollHeight - height;
                      });
                    }}
                  >
                    {loadingOlder ? "Loading earlier messages…" : "Load earlier messages"}
                  </Button>
                )}
                {runs?.map((run) =>
                  run.kind === "compact" ? (
                    <CompactionNotice key={run.id} run={run} />
                  ) : (
                    <RunMessage key={run.id} run={run} threadId={thread.id} latest={run === runs.at(-1)} />
                  ),
                )}
                {pending ? (
                  <UserMessage
                    threadId={thread.id}
                    text={pending.text}
                    files={pending.files}
                    context={pending.context}
                    at={pending.at}
                  />
                ) : null}
              </div>
            ) : (
              <Welcome thread={thread} />
            )}
          </div>
          <div className="composer-dock" ref={composer}>
            {!atBottom && runs?.length ? (
              <Button
                className="scroll-bottom"
                size="icon-sm"
                variant="secondary"
                aria-label="Scroll to latest"
                onClick={() => {
                  scroll.current?.scrollTo({
                    top: Math.max(0, scroll.current.scrollHeight - scroll.current.clientHeight),
                    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
                      ? "instant"
                      : "smooth",
                  });
                }}
              >
                <ArrowDown />
              </Button>
            ) : null}
            {thread.archived ? (
              <div className="archive-banner">
                This chat is archived.
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() =>
                    void useCoding
                      .getState()
                      .patch(thread.id, { archived: false })
                      .catch(() => undefined)
                  }
                >
                  Restore
                </Button>
              </div>
            ) : null}
            {desktopRun ? (
              <DesktopDock
                key={`desktop:${thread.id}`}
                run={desktopRun}
                onResume={() => useCoding.getState().load(thread.id)}
              />
            ) : null}
            <Composer
              key={thread.id}
              thread={detail ?? thread}
              onSettings={onSettings}
              onModels={onModels}
              attachRef={attachRef}
            />
          </div>
          {preview ? (
            <FilePreviewPane key={preview} threadId={thread.id} path={preview} onClose={closePreview} />
          ) : null}
        </section>
      </FileLinks.Provider>
    </div>
  );
}
function Shell() {
  useZoom({ bindShortcuts: true });
  useEffect(startUpdateChecks, []);
  // Files dropped on the chat window (toolbar and conversation) attach to its draft. Elsewhere
  // they are refused: the webview would otherwise open the file in place of the app.
  const attachRef = useRef<((files: File[]) => void) | null>(null);
  const [dropping, setDropping] = useState(false);
  useEffect(() => {
    let depth = 0;
    const files = (event: DragEvent) => event.dataTransfer?.types.includes("Files") ?? false;
    const inChat = (event: DragEvent) =>
      event.target instanceof Element && !!event.target.closest(".coding-main");
    const enter = (event: DragEvent) => {
      if (!files(event) || !attachRef.current || !inChat(event)) return;
      depth += 1;
      setDropping(true);
    };
    const leave = (event: DragEvent) => {
      if (!files(event) || !depth || !inChat(event)) return;
      depth -= 1;
      if (!depth) setDropping(false);
    };
    const over = (event: DragEvent) => {
      if (!files(event)) return;
      event.preventDefault();
      event.dataTransfer!.dropEffect = attachRef.current && inChat(event) ? "copy" : "none";
    };
    const drop = (event: DragEvent) => {
      if (!files(event)) return;
      event.preventDefault();
      depth = 0;
      setDropping(false);
      const dropped = Array.from(event.dataTransfer!.files);
      if (dropped.length && inChat(event)) attachRef.current?.(dropped);
    };
    const events = [
      ["dragenter", enter],
      ["dragleave", leave],
      ["dragover", over],
      ["drop", drop],
    ] as const;
    for (const [name, handler] of events) window.addEventListener(name, handler);
    return () => {
      for (const [name, handler] of events) window.removeEventListener(name, handler);
    };
  }, []);
  const workspace = useCoding((s) => s.workspace);
  const selected = useCoding((s) => s.selected);
  const detail = useCoding((s) => (selected ? s.details[selected] : undefined));
  const loading = useCoding((s) => s.loading);
  const error = useCoding((s) => s.error);
  const clearError = useCoding((s) => s.clearError);
  const { resolvedTheme } = useTheme();
  const { enabled: workbenchEnabled } = useWorkbenchPreference();
  const [page, setPage] = useState<Page>("chat");
  const [sidebar, setSidebar] = useState(true);
  const unreadThreads = useThreadActivity(workspace, page === "chat" ? selected : null);
  // The chat on screen gets no notifications while the window has focus.
  useEffect(() => {
    reportVisibleChat(page === "chat" ? selected : null);
  }, [page, selected]);
  // A clicked notification opens its chat, if it still exists.
  useEffect(
    () =>
      onNotificationOpen((thread) => {
        const state = useCoding.getState();
        if (!state.workspace?.threads.some((item) => item.id === thread)) return;
        setPage("chat");
        void state.select(thread);
      }),
    [],
  );
  const [panel, setPanel] = useState(false);
  const [workbenchTab, setWorkbenchTab] = useState<WorkbenchTab>("files");
  const [search, setSearch] = useState(false);
  const [projectEditor, setProjectEditor] = useState<{
    project?: CodingProject;
  } | null>(null);
  const [threadEditor, setThreadEditor] = useState<CodingThread | null>(null);
  const [modelsPage, setModelsPage] = useState(false);
  const workspaceReady = Boolean(workspace) && !loading;
  const connections = useSettings((s) => s.connections);
  // Nothing can run without a model and a project, so until both exist the window is setup.
  const onboarding =
    workspaceReady && (!workspace!.projects.length || (connections !== null && !modelSetupDone(connections)));
  useEffect(() => {
    void useCoding.getState().initialize();
    void useSettings
      .getState()
      .loadConnections()
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    // Setup's left column can be wider than the sidebar's glass.
    void syncWindowTheme(resolvedTheme === "dark", onboarding);
  }, [resolvedTheme, onboarding]);
  useEffect(() => {
    if (!workspaceReady) return;
    let stopped = false;
    let next = 0;
    let idle: number | undefined;
    let timer: number | undefined;
    const schedule = () => {
      if (stopped || next === preloadScreens.length) return;
      const preload = () => {
        if (stopped) return;
        // One chunk per idle period keeps navigation responsive while the
        // selected history loads. Importing does not mount the hidden screen.
        void preloadScreens[next++]()
          .catch(() => undefined)
          .finally(schedule);
      };
      if (window.requestIdleCallback) idle = window.requestIdleCallback(preload, { timeout: 2000 });
      else timer = window.setTimeout(preload, 100);
    };
    const frame = window.requestAnimationFrame(schedule);
    return () => {
      stopped = true;
      window.cancelAnimationFrame(frame);
      if (idle !== undefined) window.cancelIdleCallback(idle);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [workspaceReady]);
  useEffect(() => {
    function keys(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key === "k") {
        event.preventDefault();
        setSearch((value) => !value);
      }
      if (event.key === "n") {
        event.preventDefault();
        setPage("chat");
        const state = useCoding.getState();
        const selectedThread = state.workspace?.threads.find((item) => item.id === state.selected);
        const project =
          state.workspace?.projects.find((item) => item.id === selectedThread?.project_id) ??
          state.workspace?.projects[0];
        if (project) void state.newThread(project.id);
      }
      if (event.key === "b") {
        event.preventDefault();
        setSidebar((value) => !value);
      }
      if (event.key === ",") {
        event.preventDefault();
        setModelsPage(false);
        setPage("settings");
      }
    }
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, []);
  const thread = detail ?? workspace?.threads.find((item) => item.id === selected);
  const openingThread = Boolean(thread && !detail && !loading);
  const openModels = () => {
    setModelsPage(true);
    setPage("settings");
  };
  const backToChat = () => {
    const model = useSettings.getState().connections?.model;
    if (thread?.model.provider === "demo" && model && model.provider !== "demo")
      void useCoding
        .getState()
        .patch(thread.id, { model })
        .catch(() => undefined);
    setPage("chat");
  };
  if (onboarding)
    return (
      <>
        <Onboarding />
        <Toaster richColors />
      </>
    );
  return (
    <div className={`coding-shell ${page !== "settings" && !sidebar ? "sidebar-hidden" : ""}`}>
      {page === "settings" ? (
        <Suspense
          fallback={
            <div className="loading-screen">
              <LoaderCircle className="size-5 animate-spin" />
            </div>
          }
        >
          <SettingsView
            key={modelsPage ? "models" : "general"}
            initialPage={modelsPage ? "models" : "general"}
            onBack={backToChat}
          />
        </Suspense>
      ) : (
        <>
          {sidebar ? (
            <ResizableSidebar>
              <Sidebar
                onChat={() => setPage("chat")}
                onConnectors={() => setPage("connectors")}
                connectorsSelected={page === "connectors"}
                unreadThreads={unreadThreads}
                onSettings={() => {
                  setModelsPage(false);
                  setPage("settings");
                }}
                onProject={(project) => setProjectEditor({ project })}
                onToggleSidebar={() => setSidebar(false)}
                onThread={setThreadEditor}
              />
            </ResizableSidebar>
          ) : null}
          <main className="coding-main">
            {dropping ? (
              <div className="chat-drop-overlay" role="status">
                <Paperclip className="size-5" aria-hidden="true" />
                <span>Drop files to attach them to your message</span>
              </div>
            ) : null}
            <header
              className={`shell-titlebar floating-titlebar ${page === "chat" ? "chat-titlebar" : ""}`}
              aria-label="Workspace toolbar"
              data-tauri-drag-region
              onMouseDown={beginWindowDrag}
            >
              {!sidebar ? <SidebarToggle collapsed onToggle={() => setSidebar(true)} /> : null}
              {page === "connectors" ? (
                <>
                  <Plug className="size-3.5 shrink-0" aria-hidden="true" />
                  <span className="chat-title">Connectors</span>
                </>
              ) : thread ? (
                <ConversationToolbar
                  key={thread.id}
                  thread={thread}
                  panel={panel}
                  workbenchEnabled={workbenchEnabled}
                  onPanel={() => setPanel((value) => !value)}
                />
              ) : (
                <span className="chat-title">{loading ? "Wavefunction" : "New session"}</span>
              )}
            </header>
            {error ? (
              <div className="coding-error" role="alert">
                <span className="flex-1">{error}</span>
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={() =>
                    void (thread && !loading
                      ? useCoding.getState().select(thread.id, true)
                      : useCoding.getState().initialize())
                  }
                >
                  Retry
                </Button>
                {!openingThread ? (
                  <Button variant="ghost" size="icon-xs" aria-label="Dismiss error" onClick={clearError}>
                    <X />
                  </Button>
                ) : null}
              </div>
            ) : null}
            {page === "connectors" ? (
              <div className="titlebar-page min-h-0 flex-1 overflow-auto">
                <Suspense
                  fallback={
                    <div className="loading-screen">
                      <LoaderCircle className="size-5 animate-spin" />
                    </div>
                  }
                >
                  <ConnectorsView />
                </Suspense>
              </div>
            ) : loading ? (
              <ChatSkeleton label="Opening your workspace" />
            ) : openingThread ? (
              error ? (
                <div className="loading-screen">
                  <p>This chat could not load.</p>
                </div>
              ) : (
                <ChatSkeleton />
              )
            ) : thread ? (
              <Chat
                key={`${thread.id}:${thread.conversation_id}`}
                thread={thread}
                onSettings={() => setThreadEditor(thread)}
                onModels={openModels}
                attachRef={attachRef}
              />
            ) : (
              <div className="loading-screen">
                <p>Your workspace could not open.</p>
                <Button onClick={() => void useCoding.getState().initialize()}>Try again</Button>
              </div>
            )}
          </main>
          {page === "chat" && workbenchEnabled && panel && thread && detail && !loading ? (
            <ResizableSidebar side="right">
              <Suspense
                fallback={
                  <div className="loading-screen" role="status">
                    Opening workbench…
                  </div>
                }
              >
                <Workbench
                  key={`${thread.id}:${thread.conversation_id}:${thread.folder}`}
                  thread={detail ?? thread}
                  tab={workbenchTab}
                  onTab={setWorkbenchTab}
                  onClose={() => setPanel(false)}
                />
              </Suspense>
            </ResizableSidebar>
          ) : null}
        </>
      )}
      {search ? (
        <SearchDialog
          onDone={() => setSearch(false)}
          onChoose={(id) => {
            setPage("chat");
            void useCoding.getState().select(id);
          }}
        />
      ) : null}
      {projectEditor ? (
        <ProjectEditor project={projectEditor.project} onDone={() => setProjectEditor(null)} />
      ) : null}
      {threadEditor ? (
        <ThreadSettings key={threadEditor.id} thread={threadEditor} onDone={() => setThreadEditor(null)} />
      ) : null}
      <Toaster richColors />
    </div>
  );
}
export function CodingApp() {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" storageKey="harness.theme" enableSystem>
      <Shell />
    </ThemeProvider>
  );
}
