import { useContext, useState } from "react";
import { LoaderCircle, Play } from "lucide-react";
import { CodeBlock, CodeBlockCopyButton, type CustomRendererProps } from "streamdown";
import { blockCommand, RunCommand } from "./run-command";

/** A shell code block, which the chat it is in can run in a new terminal. */
export function ShellBlock({ code, language, isIncomplete }: CustomRendererProps) {
  const runner = useContext(RunCommand);
  const [starting, setStarting] = useState(false);
  // A block still streaming in is not the whole command yet.
  const command = isIncomplete ? "" : blockCommand(language, code);
  return (
    <CodeBlock code={code} language={language} isIncomplete={isIncomplete}>
      {runner && command ? (
        <button
          type="button"
          className="shell-block-run cursor-pointer p-1 text-muted-foreground transition-all hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          aria-label="Run in terminal"
          title={runner.unavailable ?? "Run in terminal"}
          disabled={Boolean(runner.unavailable) || starting}
          onClick={() => {
            setStarting(true);
            void runner.run(command).finally(() => setStarting(false));
          }}
        >
          {starting ? <LoaderCircle className="animate-spin" aria-hidden /> : <Play aria-hidden />}
        </button>
      ) : null}
      <CodeBlockCopyButton />
    </CodeBlock>
  );
}
