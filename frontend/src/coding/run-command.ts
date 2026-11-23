import { createContext, useMemo } from "react";
import { toast } from "sonner";
import { codingApi, type CodingThread, type TerminalSession } from "./api";

/** Runs a command from the chat in a new terminal of that chat. Null where no terminal can show it. */
export type CommandRunner = {
  run: (command: string) => Promise<void>;
  /** Why the chat can't run commands right now. */
  unavailable: string | null;
};
export const RunCommand = createContext<CommandRunner | null>(null);
/** Commands waiting for the terminal opened to run them, by terminal: its screen types them when ready. */
export const waitingCommands = new Map<string, string>();
/** Where the Terminal tab remembers which of a chat's terminals is selected. */
export const selectedTerminalKey = (threadId: string) => `harness.terminal.local.${threadId}`;

/** Fences a terminal can run as written. */
const scripts = ["bash", "sh", "zsh", "shell"];
/** Fences that show a session: `$ ` prompts before commands, and the commands' output. */
const sessions = ["console", "shell-session", "shellsession", "sh-session"];
export const shellLanguages = [...scripts, ...sessions];

/** What a shell block runs: the block as written, or a session's prompted commands without their output. */
export function blockCommand(language: string, code: string) {
  const lines = code.split("\n");
  if (!sessions.includes(language) || !lines.some((line) => line.startsWith("$ "))) return code.trim();
  const commands: string[] = [];
  let continued = false;
  for (const line of lines) {
    // Output lines are skipped; a command ending in a backslash goes on in the next line, which may
    // show the shell's `> ` continuation prompt.
    if (!continued && !line.startsWith("$ ")) continue;
    const command: string = continued ? line.replace(/^> /, "") : line.slice(2);
    commands.push(command);
    continued = command.endsWith("\\");
  }
  return commands.join("\n").trim();
}

/** The terminal's tab name: the command's first line, within the 80 characters a title allows. */
function terminalTitle(command: string) {
  const line = Array.from(command.split("\n")[0].trim());
  return line.length > 80 ? `${line.slice(0, 79).join("")}…` : line.join("");
}

export function useCommandRunner(
  thread: CodingThread | undefined,
  onOpen: ((session: TerminalSession) => void) | undefined,
): CommandRunner | null {
  const { id, archived, mode, folder } = thread ?? {};
  return useMemo(() => {
    if (!id || !onOpen) return null;
    return {
      unavailable: archived
        ? "Restore this chat to run commands."
        : mode === "plan"
          ? "Switch out of Plan mode to run commands."
          : !folder
            ? "Choose a folder for this chat to run commands."
            : null,
      run: async (command) => {
        try {
          // The terminal belongs to the chat the command was shared in, wherever the user is by now.
          const session = await codingApi.createTerminal(id, terminalTitle(command));
          waitingCommands.set(session.id, command);
          localStorage.setItem(selectedTerminalKey(id), session.id);
          onOpen(session);
        } catch (error) {
          toast.error(error instanceof Error ? error.message : "Could not run the command");
        }
      },
    };
  }, [id, archived, mode, folder, onOpen]);
}
