import { useCallback, useState } from "react";
import type { CodingThread, TerminalSession } from "./api";
import { useCommandRunner } from "./run-command";
import { useCoding } from "./store";
import type { WorkbenchTab } from "./workbench";
import { useWorkbenchPreference } from "./workbench-preference";

/** The workbench beside the chat on screen: whether it shows, its tab, and the terminals the chat's commands open. */
export function useWorkbenchPanel(thread: CodingThread | undefined) {
  const { enabled } = useWorkbenchPreference();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<WorkbenchTab>("files");
  const [terminal, setTerminal] = useState<string | null>(null);
  const showTerminal = useCallback((session: TerminalSession) => {
    // The terminal is the chat's either way; it shows only while that chat is on screen.
    if (useCoding.getState().selected !== session.thread_id) return;
    setTerminal(session.id);
    setTab("terminal");
    setOpen(true);
  }, []);
  // Commands run only where the workbench can show their terminal.
  const commands = useCommandRunner(thread, enabled ? showTerminal : undefined);
  return { enabled, open, setOpen, commands, tabs: { tab, onTab: setTab, terminal } };
}
