import { useState } from "react";
import { GitFork, LoaderCircle, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { isActive, type ChatCheckpoint, type CodingThread } from "./api";
import { useCoding } from "./store";

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

export function ForkDialog({
  thread,
  checkpoint,
  onClose,
}: {
  thread: CodingThread;
  checkpoint?: ChatCheckpoint;
  onClose: () => void;
}) {
  const available = Boolean(
    (thread.worktree_available || thread.worktree_status === "deleted") && (checkpoint?.available ?? true),
  );
  const [worktree, setWorktree] = useState(available);
  const [busy, setBusy] = useState(false);
  async function fork() {
    setBusy(true);
    try {
      await useCoding.getState().fork(thread.id, {
        worktree,
        ...(checkpoint ? { checkpoint_id: checkpoint.id } : {}),
      });
      onClose();
    } catch (error) {
      toast.error(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Fork chat</DialogTitle>
          <DialogDescription>
            {checkpoint
              ? "Continue from this message in a new chat."
              : "Continue this conversation in a new chat."}
          </DialogDescription>
        </DialogHeader>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={worktree}
            disabled={!available || Boolean(checkpoint) || busy}
            onChange={(event) => setWorktree(event.target.checked)}
          />
          New worktree
        </label>
        <p className="text-xs text-muted-foreground">
          {worktree
            ? checkpoint
              ? "Message forks use a new worktree so the code matches this checkpoint."
              : "The new chat will have its own copy of the code."
            : available
              ? "Both chats will use the same folder and share code changes."
              : "Worktrees require a Git repository. The new chat will use the current folder."}
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={() => void fork()} disabled={busy}>
            {busy ? <LoaderCircle className="animate-spin" /> : <GitFork />}
            Fork chat
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CheckpointActions({
  threadId,
  checkpoint,
  rewind: canRewind = true,
}: {
  threadId: string;
  checkpoint: ChatCheckpoint | undefined;
  rewind?: boolean;
}) {
  const detail = useCoding((state) => state.details[threadId]);
  const thread =
    useCoding((state) => state.workspace?.threads.find((item) => item.id === threadId)) ?? detail;
  const restoring = useCoding((state) => state.restoring[threadId]);
  const sending = useCoding((state) => state.sending[threadId]);
  const [forking, setForking] = useState(false);
  if (!checkpoint || !thread) return null;
  const active =
    thread.status !== undefined ? isActive(thread.status) : detail?.runs.some((run) => isActive(run.status));
  const unavailable = checkpoint.unavailable_reason || "Code is unavailable at this checkpoint";
  const canFork = checkpoint.available && (thread.worktree_available || thread.worktree_status === "deleted");
  async function rewind() {
    if (!checkpoint) return;
    try {
      await useCoding.getState().rewind(threadId, checkpoint.id);
      toast.success("Conversation and code restored");
    } catch (error) {
      toast.error(errorText(error));
    }
  }
  return (
    <span className="message-actions inline-flex items-center">
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Fork from this message"
        title={
          active ? "Stop the current task before forking" : canFork ? "Fork from this message" : unavailable
        }
        disabled={!canFork || active || sending || restoring}
        onClick={() => setForking(true)}
      >
        <GitFork />
      </Button>
      {canRewind ? (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label="Rewind to this message"
              title={
                thread.parent_thread_id
                  ? "Fork this helper to continue independently"
                  : checkpoint.available
                    ? "Rewind conversation and code to this message"
                    : unavailable
              }
              disabled={Boolean(thread.parent_thread_id) || !checkpoint.available || sending || restoring}
            >
              {restoring ? <LoaderCircle className="animate-spin" /> : <RotateCcw />}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Rewind conversation?</AlertDialogTitle>
              <AlertDialogDescription>
                Restore the conversation and code to{" "}
                {checkpoint.kind === "after_assistant" ? "after this response" : "before this message"}. Later
                messages and your current draft will be kept in an archived chat. An active task will be
                stopped.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction disabled={sending || restoring} onClick={() => void rewind()}>
                Rewind
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : null}
      {forking ? (
        <ForkDialog thread={thread} checkpoint={checkpoint} onClose={() => setForking(false)} />
      ) : null}
    </span>
  );
}
