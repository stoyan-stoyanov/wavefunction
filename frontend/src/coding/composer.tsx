import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowUp,
  ChevronDown,
  Code,
  FolderOpen,
  GitBranch,
  ListChecks,
  LoaderCircle,
  Mic,
  Paperclip,
  ShieldOff,
  Square,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ModelMenu } from "@/components/model-menu";
import { useModelOptions } from "@/components/model-picker";
import { modelKey } from "@/lib/models";
import { codingApi, isActive, type CodingThread, type GitBranches } from "./api";
import { ContextIndicator } from "./context-usage";
import { latestPause } from "./run-transcript";
import { useCoding } from "./store";
import { AttachmentPreviews } from "./attachment-preview";
import { ComposerContext } from "./composer-context";
import { WorktreeBranchPicker } from "./worktree-branch-picker";
import { contextDraftKey, emptyContext, readContextDraft } from "./context-draft";
import "./composer-ui.css";
const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));
// macOS dictation is the desktop app's; a browser tab has its own.
const canDictate = () => "__TAURI_INTERNALS__" in window;
// Managed worktrees are named `<repository>-<chat id>`; four characters of the id tell them apart.
const chatIdSuffix = /-([0-9a-f]{4})[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const repositoryLabel = (folder: string) =>
  (folder.split("/").filter(Boolean).at(-1) || folder).replace(chatIdSuffix, "-$1");

const MODES = [
  { value: "auto", label: "Code", icon: Code, hint: "Asks before commands outside the sandbox" },
  { value: "plan", label: "Plan", icon: ListChecks, hint: "Reads and explains, changes nothing" },
  {
    value: "full",
    label: "Full access",
    icon: ShieldOff,
    hint: "Runs commands outside the sandbox without asking. Ones that can't be undone still ask.",
  },
] as const;

/** The chat's mode. The choice is saved too: the next new chat starts in it. */
function ModeMenu({ thread }: { thread: CodingThread }) {
  const patch = useCoding((s) => s.patch);
  const current = MODES.find((mode) => mode.value === thread.mode) ?? MODES[0];
  async function choose(mode: CodingThread["mode"]) {
    if (mode === thread.mode) return;
    // Saved first, so the refresh after the chat's change shows it. The store reports that change failing.
    await codingApi.defaultMode(mode).catch((e: unknown) => toast.error(errorText(e)));
    await patch(thread.id, { mode }).catch(() => undefined);
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="composer-chip"
          disabled={thread.archived}
          aria-label={`Agent mode: ${current.label}`}
        >
          <current.icon />
          {current.label}
          <ChevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuRadioGroup
          value={current.value}
          onValueChange={(value) => void choose(value as CodingThread["mode"])}
        >
          {MODES.map((mode) => (
            <DropdownMenuRadioItem key={mode.value} value={mode.value} className="items-start">
              <mode.icon className="mt-0.5" />
              <span className="grid gap-0.5">
                {mode.label}
                <span className="text-xs text-muted-foreground">{mode.hint}</span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function fitMessage(input: HTMLTextAreaElement) {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

// Chats with a message held, so leaving one doesn't cancel it: it goes when the chat is open again.
const heldChats = new Set<string>();

/** Holds a message sent before the chat's history arrives. History says whether this is a new
 * chat, and so where its first message starts. If it never arrives, the message stays in the box. */
function useSendOnceLoaded(chat: string, ready: boolean, send: () => void) {
  const [waiting, setWaiting] = useState(() => heldChats.has(chat));
  const sent = useRef(false);
  useEffect(() => {
    if (!waiting || !ready || sent.current) return;
    sent.current = true;
    heldChats.delete(chat);
    send();
  });
  const hold = () => {
    sent.current = false;
    heldChats.add(chat);
    setWaiting(true);
  };
  return [waiting && !ready, hold] as const;
}

export function Composer(props: Parameters<typeof ComposerBody>[0]) {
  return (
    <ComposerBody
      key={`${props.thread.id}:${props.thread.conversation_id}:${props.thread.folder}`}
      {...props}
    />
  );
}

function ComposerBody({
  thread,
  onSettings,
  onModels,
  attachRef,
}: {
  thread: CodingThread;
  /** Set to this draft's attach function while files can be attached, for drops anywhere in the chat. */
  attachRef?: RefObject<((files: File[]) => void) | null>;
  onSettings: () => void;
  onModels: () => void;
}) {
  const detail = useCoding((s) => s.details[thread.id]);
  const sending = useCoding((s) => s.sending[thread.id]);
  const restoring = useCoding((s) => s.restoring[thread.id]);
  // Per chat, not per composer: leaving the chat and coming back keeps the upload's guard.
  const uploading = useCoding((s) => s.uploading[thread.id]);
  const setUploading = (value: boolean) =>
    useCoding.setState((s) => ({ uploading: { ...s.uploading, [thread.id]: value } }));
  const unsent = useCoding((s) => s.unsent[thread.id]);
  const patch = useCoding((s) => s.patch);
  const send = useCoding((s) => s.send);
  const stop = useCoding((s) => s.stop);
  const [draft, setDraft] = useState(() => {
    try {
      return localStorage.getItem(`harness.draft.${thread.id}`) ?? "";
    } catch {
      return "";
    }
  });
  const [context, setContext] = useState(() => readContextDraft(thread));
  useEffect(() => {
    try {
      localStorage.setItem(contextDraftKey(thread), JSON.stringify(context));
    } catch {
      /* Retain context in this session. */
    }
  }, [context, thread]);
  const [queueRunId, setQueueRunId] = useState<string | null>(null);
  // New chats start in their own worktree unless the user opts out before the first message.
  const [worktree, setWorktree] = useState(true);
  const startInWorktree = worktree && Boolean(thread.worktree_available);
  const [branches, setBranches] = useState<GitBranches | null>(null);
  const [baseRef, setBaseRef] = useState("");
  const [branchError, setBranchError] = useState<string | null>(null);
  const [branchRetry, setBranchRetry] = useState(0);
  const files = useRef<HTMLInputElement>(null);
  const message = useRef<HTMLTextAreaElement>(null);
  // macOS's own cue is a small mic by the caret, so the composer says it is listening too. It is
  // not told when dictation stops, so the cue ends with what usually stops it: Esc, sending,
  // leaving the message, or half a minute without new words.
  const [listening, setListening] = useState(false);
  const listeningTimer = useRef<number | undefined>(undefined);
  const stopListening = () => {
    window.clearTimeout(listeningTimer.current);
    setListening(false);
  };
  const keepListening = () => {
    window.clearTimeout(listeningTimer.current);
    listeningTimer.current = window.setTimeout(() => setListening(false), 30_000);
  };
  useEffect(() => () => window.clearTimeout(listeningTimer.current), []);
  useLayoutEffect(() => {
    if (message.current) fitMessage(message.current);
  }, [draft]);
  useLayoutEffect(() => {
    const input = message.current;
    if (!input) return;
    let width = input.clientWidth;
    const observer = new ResizeObserver(() => {
      if (input.clientWidth === width) return;
      width = input.clientWidth;
      fitMessage(input);
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, []);
  // A message the backend refused comes back to the box it left, unless something was written
  // there since. One refused before this composer mounted is already in the saved draft.
  const mountedWith = useRef(unsent);
  useEffect(() => {
    if (!unsent || unsent === mountedWith.current) return;
    setDraft((current) => current || unsent.text);
    setContext((current) =>
      JSON.stringify(current) === JSON.stringify(emptyContext()) && unsent.context ? unsent.context : current,
    );
  }, [unsent]);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // The box is disabled while its message is sent, which takes the keyboard from it: give it
  // back when the box opens again, unless the user has gone somewhere else meanwhile.
  const refocus = useRef(false);
  useEffect(() => {
    if (sending || !refocus.current) return;
    refocus.current = false;
    if (!document.activeElement || document.activeElement === document.body) message.current?.focus();
  }, [sending]);
  const { options, loading, specFor } = useModelOptions(thread.model);
  const activeRun = detail?.runs.find((run) => isActive(run.status) && run.status !== "queued");
  const busy = detail?.runs.some((run) => isActive(run.status));
  // A task waiting on the user takes the message as its reply, like one that is running takes
  // steering. Queued behind it, the message would wait for as long as the task kept asking.
  const waiting = activeRun?.status.startsWith("waiting") ?? false;
  const answering =
    activeRun?.status === "waiting_approval" && latestPause(activeRun.events)?.event_type === "run_questions";
  // An approval card holds the task: a message waits until it is decided, then reaches the agent.
  const deciding = activeRun?.status === "waiting_approval" && !answering;
  const steer = (activeRun?.status === "running" || waiting) && queueRunId !== activeRun?.id;
  const action = steer
    ? answering
      ? "Answer agent"
      : deciding
        ? "Send after the card"
        : waiting
          ? "Reply to agent"
          : "Steer agent"
    : busy
      ? "Queue follow-up"
      : "Send message";
  const helper = Boolean(thread.parent_thread_id);
  const newChat = detail?.runs.length === 0 && !thread.archived && !helper;
  const chooseWorktreeBase = newChat && startInWorktree && !thread.worktree;
  const baseUnavailable = chooseWorktreeBase && (!branches || !baseRef || Boolean(branchError));
  useEffect(() => {
    if (!chooseWorktreeBase) return;
    let current = true;
    codingApi
      .gitBranches(thread.id)
      .then((result) => {
        if (!current) return;
        setBranches(result);
        if (!result.head) {
          setBaseRef("");
          setBranchError("Create a commit in this repository before starting a worktree.");
          return;
        }
        const available = (ref: string | null) =>
          Boolean(ref) &&
          (result.branches.some((branch) => branch.ref === ref) || (ref === result.head && !result.current));
        // Start from what the repository has checked out (its current commit when detached);
        // another base is a choice for this chat only.
        setBaseRef((selected) => (available(selected) ? selected : (result.current ?? result.head)));
      })
      .catch((error) => {
        if (current) setBranchError(errorText(error));
      });
    return () => {
      current = false;
    };
  }, [chooseWorktreeBase, thread.id, thread.folder, branchRetry]);
  useEffect(() => {
    try {
      localStorage.setItem(`harness.draft.${thread.id}`, draft);
    } catch {
      /* Draft remains in this session. */
    }
  }, [draft, thread.id]);
  async function upload(list: FileList | File[]) {
    // While a message is being sent its attachments are being captured; a new file would race
    // into either that message or the next draft.
    if (helper || thread.archived || restoring || sending || uploading) return;
    setUploading(true);
    try {
      for (const file of Array.from(list)) await codingApi.upload(thread.id, file);
      await useCoding.getState().load(thread.id);
      toast.success("Files attached");
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setUploading(false);
    }
  }
  const canAttach = !helper && !thread.archived && !restoring && !sending && !uploading;
  useEffect(() => {
    if (!attachRef) return;
    attachRef.current = canAttach ? (list) => void upload(list) : null;
    return () => {
      attachRef.current = null;
    };
  });
  const settling = sending || restoring || uploading || baseUnavailable;
  const blocked = (!draft.trim() && !context.command) || settling || helper || thread.archived;
  const [held, hold] = useSendOnceLoaded(thread.id, Boolean(detail) && !settling, () => void submit());
  async function submit() {
    if (blocked) return;
    if (!detail) return hold();
    stopListening();
    const text = draft;
    const sentContext = context;
    // The message moves to the chat now; the store puts it back if it is refused.
    setDraft("");
    setContext(emptyContext());
    refocus.current = document.activeElement === message.current;
    try {
      const options = {
        ...(busy && !steer ? { delivery: "queue" as const } : {}),
        ...(newChat && startInWorktree ? { worktree: true } : {}),
        ...(chooseWorktreeBase ? { worktree_base: baseRef } : {}),
      };
      if (Object.keys(options).length) await send(thread.id, text, sentContext, options);
      else await send(thread.id, text, sentContext);
      if (mounted.current) setQueueRunId(null);
    } catch (e) {
      toast.error(errorText(e));
    }
  }
  return (
    <div className="composer-wrap">
      {thread.worktree_status === "deleted" ? (
        <p role="status" className="mb-3 flex items-start gap-2 text-xs leading-5 text-muted-foreground">
          <GitBranch className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>
            This worktree was deleted after inactivity.{" "}
            {helper
              ? "Its history is still available. Fork this helper to recreate its worktree and continue in a new chat."
              : "It will be recreated when you continue this chat."}
          </span>
        </p>
      ) : null}
      <div className="coding-composer">
        {chooseWorktreeBase && branchError ? (
          <div className="composer-branch-error" role="alert">
            <span>{branchError}</span>
            <button
              type="button"
              disabled={sending || restoring}
              onClick={() => {
                setBranches(null);
                setBranchError(null);
                setBranchRetry((attempt) => attempt + 1);
              }}
            >
              Retry
            </button>
          </div>
        ) : null}
        {/* While sent they belong to the message in the chat, and come back only if it is refused. */}
        {detail?.attachments.length && !sending ? (
          <AttachmentPreviews
            className="composer-attachments"
            threadId={thread.id}
            files={detail.attachments}
            onRemove={(file) =>
              void codingApi
                .removeAttachment(thread.id, file.id)
                .then(() => useCoding.getState().load(thread.id))
                .catch((e) => toast.error(errorText(e)))
            }
          />
        ) : null}
        <ComposerContext
          thread={thread}
          value={context}
          onChange={setContext}
          message={draft}
          onMessage={setDraft}
        />
        <textarea
          ref={message}
          aria-label="Message Wavefunction"
          placeholder={
            helper
              ? "This helper is managed by its parent chat"
              : thread.archived
                ? "Restore this chat to continue"
                : listening && !draft
                  ? "Listening… speak now"
                  : activeRun
                    ? steer
                      ? answering
                        ? "Answer the agent's question…"
                        : deciding
                          ? "The agent reads this once you decide the card above…"
                          : waiting
                            ? "Reply to the agent…"
                            : "Steer the current task…"
                      : "Queue a follow-up…"
                    : "Ask Wavefunction to build, fix, or explore…"
          }
          disabled={thread.archived || restoring || helper || sending}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            if (listening) keepListening();
          }}
          onBlur={() => {
            if (listening) stopListening();
          }}
          rows={1}
          onPaste={(e) => {
            const pasted = [...e.clipboardData.items]
              .filter((item) => item.kind === "file")
              .map((item) => item.getAsFile())
              .filter((f): f is File => f !== null);
            if (pasted.length) {
              e.preventDefault();
              void upload(pasted);
            }
          }}
          onKeyDown={(e) => {
            // Esc also stops macOS dictation itself; it is left to reach it.
            if (e.key === "Escape" && listening) stopListening();
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        <div className="composer-toolbar">
          {helper ? null : (
            <ContextIndicator
              runs={detail?.runs ?? []}
              busy={Boolean(activeRun)}
              onCompact={() => useCoding.getState().compact(thread.id)}
            />
          )}
          <ModelMenu
            options={options}
            loading={loading}
            selectedKey={modelKey(thread.model)}
            selectedName={thread.model.provider === "demo" ? "Offline demo" : thread.model.model}
            label="Chat model"
            title="Choose a model"
            align="start"
            variant="ghost"
            className="model-selector"
            currentModel={thread.model}
            onReasoningChange={(effort) =>
              patch(thread.id, {
                model: { ...thread.model, reasoning_effort: effort },
              })
            }
            onChange={(key) => {
              const value = specFor(key);
              if (value)
                void patch(thread.id, {
                  model: value,
                }).catch(() => undefined);
            }}
          />
          {helper ? null : <ModeMenu thread={thread} />}
          <span className="flex-1" />
          <input
            type="file"
            ref={files}
            hidden
            multiple
            onChange={(e) => {
              if (e.target.files) void upload(e.target.files);
              e.target.value = "";
            }}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Attach files"
            title="Attach files"
            disabled={uploading || helper || restoring || thread.archived}
            onClick={() => files.current?.click()}
          >
            {uploading ? <LoaderCircle className="animate-spin" /> : <Paperclip />}
          </Button>
          {canDictate() ? (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Dictate"
              aria-pressed={listening}
              className={listening ? "composer-dictating" : undefined}
              title={
                listening
                  ? "Listening: press Esc to stop"
                  : "Dictate: speak, and macOS types into the message"
              }
              disabled={thread.archived || restoring || helper || sending}
              // The message keeps focus, so what is said lands where its caret is.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                message.current?.focus();
                if (listening) return;
                void invoke("start_dictation")
                  .then(() => {
                    setListening(true);
                    keepListening();
                  })
                  .catch((e) => toast.error(errorText(e)));
              }}
            >
              <Mic />
            </Button>
          ) : null}
          {activeRun ? (
            <Button
              variant="ghost"
              size="icon-sm"
              title="Stop agent"
              aria-label="Stop agent"
              onClick={() => void stop(activeRun.id)}
            >
              <Square className="size-3.5 fill-current" />
            </Button>
          ) : null}
          <button
            className="send-button"
            aria-label={action}
            title={action}
            disabled={blocked || held}
            onClick={() => void submit()}
          >
            {sending || held ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <ArrowUp className="size-4" />
            )}
          </button>
        </div>
      </div>
      <div className="composer-footer">
        {/* Where this chat works: its folder, and for a new chat whether to start a worktree. */}
        <div className="composer-place">
          <button
            className="composer-repository"
            title={
              thread.folder ? `${thread.worktree ? "Managed worktree: " : ""}${thread.folder}` : undefined
            }
            onClick={onSettings}
          >
            <FolderOpen className="size-3" />
            <span>{thread.folder ? repositoryLabel(thread.folder) : "Choose repository"}</span>
            {thread.worktree ? <GitBranch className="size-3" /> : null}
          </button>
          {newChat && !thread.worktree ? (
            <div className="composer-worktree-controls">
              <label
                className="flex shrink-0 items-center gap-1.5"
                title={
                  thread.worktree_available
                    ? "Start this chat in its own Git worktree"
                    : "Choose a Git repository to use a worktree"
                }
              >
                <input
                  type="checkbox"
                  checked={startInWorktree}
                  disabled={!thread.worktree_available || sending || restoring}
                  onChange={(e) => {
                    setWorktree(e.target.checked);
                    if (e.target.checked) {
                      setBranches(null);
                      setBranchError(null);
                    }
                  }}
                />
                New worktree
              </label>
              {chooseWorktreeBase ? (
                <WorktreeBranchPicker
                  branches={branches}
                  value={baseRef}
                  error={Boolean(branchError)}
                  disabled={!branches || Boolean(branchError) || sending || restoring}
                  onChange={setBaseRef}
                />
              ) : null}
            </div>
          ) : null}
        </div>
        {helper ? (
          <span>
            Ask the{" "}
            <button
              className="underline"
              onClick={() => void useCoding.getState().select(thread.parent_thread_id!)}
            >
              parent chat
            </button>{" "}
            to continue this helper, or fork it.
          </span>
        ) : activeRun && (activeRun.status === "running" || waiting) ? (
          <label className="flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              checked={!steer}
              onChange={(e) => setQueueRunId(e.target.checked ? activeRun.id : null)}
            />
            Queue follow-up
          </label>
        ) : thread.model.provider === "demo" ? (
          <button className="demo-connect" onClick={onModels}>
            Connect a model to code <ArrowUp className="size-3 rotate-45" />
          </button>
        ) : (
          <span>Enter to send · Shift + Enter for newline</span>
        )}
      </div>
    </div>
  );
}
