import { useEffect, useRef, useState } from "react";
import { BookOpen, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { codingApi, type CodingThread, type DiscoveredSkill, type TaskContext, type Workflow } from "./api";

export function ComposerContext({
  thread,
  value,
  onChange,
  message,
  onMessage,
}: {
  thread: CodingThread;
  value: TaskContext;
  onChange: (next: TaskContext) => void;
  message: string;
  onMessage: (next: string) => void;
}) {
  const [manualMenu, setMenu] = useState<"files" | "commands" | null>(null);
  const [query, setQuery] = useState("");
  const [loadedFiles, setFiles] = useState<string[]>([]);
  const [loadedSkills, setSkills] = useState<DiscoveredSkill[]>([]);
  const [loadedWorkflow, setWorkflow] = useState<Workflow | null>(null);
  const [loadedKey, setLoadedKey] = useState("");
  const [selection, setSelection] = useState<{
    path: string;
    version: string;
    lines: number;
    text: string;
  } | null>(null);
  const [start, setStart] = useState(1);
  const [end, setEnd] = useState(1);
  const [savedError, setError] = useState("");
  const [selecting, setLoading] = useState(false);
  const [suppressed, setSuppressed] = useState("");
  const sequence = useRef(0);
  const fileToken = /(?:^|\s)@([\w./-]*)$/.exec(message);
  const slashToken = /^\/([\w-]*)(?:\s+[\s\S]*)?$/.exec(message);
  const suppressedCommand = /^\/([\w-]+)(?:\s+[\s\S]*)?$/.exec(suppressed)?.[1];
  const automaticMenu =
    thread.archived || message === suppressed || (suppressedCommand && slashToken?.[1] === suppressedCommand)
      ? null
      : slashToken
        ? "commands"
        : fileToken && thread.folder
          ? "files"
          : null;
  const menu = thread.archived ? null : (automaticMenu ?? manualMenu);
  const fileQuery = automaticMenu === "files" ? fileToken![1] : query;
  const pickerKey = JSON.stringify([thread.id, thread.folder, menu, fileQuery]);
  const ready = loadedKey === pickerKey;
  const files = ready ? loadedFiles : [];
  const skills = ready ? loadedSkills : [];
  const workflow = ready ? loadedWorkflow : null;
  const error = ready ? savedError : "";
  const loading = selecting || Boolean(menu && !ready);
  useEffect(() => {
    const owner = ++sequence.current;
    if (!menu) return;
    const promise =
      menu === "files"
        ? codingApi.contextFiles(thread.id, fileQuery).then((result) => {
            if (owner === sequence.current) setFiles(result.files);
          })
        : Promise.all([codingApi.workflow(thread.id), codingApi.skills(thread.id)]).then(
            ([commands, inventory]) => {
              if (owner === sequence.current) {
                setWorkflow(commands);
                setSkills(inventory.skills.filter((skill) => skill.active));
              }
            },
          );
    void promise
      .then(() => {
        if (owner === sequence.current) {
          setLoadedKey(pickerKey);
          setError("");
        }
      })
      .catch((reason) => {
        if (owner === sequence.current) {
          setLoadedKey(pickerKey);
          setFiles([]);
          setSkills([]);
          setWorkflow(null);
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (owner === sequence.current) setLoading(false);
      });
    return () => {
      sequence.current = owner + 1000;
    };
  }, [thread.id, thread.folder, menu, fileQuery, pickerKey]);
  const commandQuery = slashToken?.[1].toLowerCase() ?? "";
  const showFiles = "files lines repository context".includes(commandQuery);
  const prompts =
    workflow?.prompts.filter((prompt) =>
      `${prompt.name} ${prompt.description}`.toLowerCase().includes(commandQuery),
    ) ?? [];
  const skillCommands = skills.filter((skill) =>
    `${skill.name} ${skill.description}`.toLowerCase().includes(commandQuery),
  );
  function commandArguments() {
    return message.replace(/^\/[\w-]*(?:\s+|$)/, "");
  }
  function openFiles() {
    const next = commandArguments();
    setSuppressed(next);
    onMessage(next);
    setSelection(null);
    setQuery("");
    setMenu("files");
  }
  function invokeSkill(name: string) {
    onChange({
      ...value,
      skills: Array.from(new Set([...value.skills, name])),
      command: null,
    });
    const argumentsText = commandArguments();
    const instruction = workflow?.prompts.some((prompt) => prompt.name === name)
      ? `Use the ${name} skill for this repository.`
      : `/${name}`;
    const next = `${instruction}${argumentsText ? ` ${argumentsText}` : ""}`;
    setSuppressed(next);
    onMessage(next);
    setMenu(null);
  }
  function invokePrompt(name: string) {
    const next = commandArguments();
    setSuppressed(next);
    onMessage(next);
    onChange({ ...value, command: name });
    setMenu(null);
  }
  async function select(path: string) {
    const owner = ++sequence.current;
    setError("");
    setLoading(true);
    try {
      const content = await codingApi.file(thread.id, path, thread.folder ?? undefined);
      if (owner !== sequence.current) return;
      const lines = content.text.split(/\r?\n/).length - (content.text.endsWith("\n") ? 1 : 0) || 1;
      setSelection({
        path,
        version: content.version,
        lines,
        text: content.text,
      });
      setStart(1);
      setEnd(lines);
    } catch (reason) {
      if (owner === sequence.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (owner === sequence.current) setLoading(false);
    }
  }
  function attach() {
    if (!selection || start < 1 || end < start || end > selection.lines) {
      setError("Choose lines within this file.");
      return;
    }
    onChange({
      ...value,
      files: [
        ...value.files.filter((f) => f.path !== selection.path),
        {
          path: selection.path,
          version: selection.version,
          start_line: start,
          end_line: end,
        },
      ],
    });
    const next = message.replace(/@[\w./-]*$/, `@${selection.path}:${start}-${end}`);
    setSuppressed(next);
    onMessage(next);
    setMenu(null);
    setSelection(null);
  }
  return (
    <div className="composer-context">
      <div className="context-chips">
        {value.files.map((file) => (
          <span key={file.path}>
            {file.path}:{file.start_line}–{file.end_line ?? "end"}
            <button
              aria-label={`Remove context ${file.path}`}
              onClick={() =>
                onChange({
                  ...value,
                  files: value.files.filter((f) => f.path !== file.path),
                })
              }
            >
              <X size={12} />
            </button>
          </span>
        ))}
        {value.skills.map((name) => (
          <span key={name}>
            <BookOpen size={12} />
            {name}
            <button
              aria-label={`Remove skill ${name}`}
              onClick={() =>
                onChange({
                  ...value,
                  skills: value.skills.filter((s) => s !== name),
                })
              }
            >
              <X size={12} />
            </button>
          </span>
        ))}
        {value.command && (
          <span>
            /{value.command}
            <button aria-label="Remove prompt command" onClick={() => onChange({ ...value, command: null })}>
              <X size={12} />
            </button>
          </span>
        )}
      </div>
      {menu && (
        <div className="context-picker">
          <div className="context-picker-title">
            <strong>{menu === "files" ? "Attach repository context" : "Skills and prompt commands"}</strong>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label="Close context picker"
              onClick={() => {
                setSuppressed(message);
                setMenu(null);
              }}
            >
              <X />
            </Button>
          </div>
          {error && (
            <p className="workbench-error" role="alert">
              {error}
            </p>
          )}
          {loading && <p className="review-note">Loading…</p>}
          {menu === "files" &&
            (selection ? (
              <>
                <p>{selection.path}</p>
                <div className="context-range">
                  <label>
                    From line
                    <Input
                      aria-label="Context start line"
                      type="number"
                      min={1}
                      max={selection.lines}
                      value={start}
                      onChange={(e) => setStart(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    To line
                    <Input
                      aria-label="Context end line"
                      type="number"
                      min={start}
                      max={selection.lines}
                      value={end}
                      onChange={(e) => setEnd(Number(e.target.value))}
                    />
                  </label>
                  <Button size="sm" onClick={attach}>
                    Attach lines
                  </Button>
                </div>
                <pre className="context-preview">
                  {selection.text
                    .split("\n")
                    .slice(Math.max(0, start - 1), Math.min(end, start + 12))
                    .join("\n")}
                </pre>
              </>
            ) : (
              <>
                <Input
                  aria-label="Search context files"
                  placeholder="Search files…"
                  value={query}
                  onChange={(e) => {
                    setSuppressed(message);
                    setMenu("files");
                    setQuery(e.target.value);
                  }}
                />
                <div className="context-options">
                  {files.map((path) => (
                    <button key={path} onClick={() => void select(path)}>
                      {path}
                    </button>
                  ))}
                </div>
              </>
            ))}
          {menu === "commands" && (
            <div className="context-options">
              {showFiles && (
                <button aria-label="/files · Files & lines" disabled={!thread.folder} onClick={openFiles}>
                  <strong>/files</strong>
                  <small>
                    {thread.folder
                      ? "Files & lines · Attach repository context"
                      : "Choose a repository to attach files and lines"}
                  </small>
                </button>
              )}
              {prompts.map((prompt) => (
                <button
                  key={prompt.name}
                  aria-label={`/${prompt.name} · Prompt · ${prompt.description}`}
                  onClick={() => invokePrompt(prompt.name)}
                >
                  <strong>/{prompt.name}</strong>
                  <small>Prompt · {prompt.description}</small>
                </button>
              ))}
              {skillCommands.map((skill) => (
                <button
                  key={`skill:${skill.name}`}
                  aria-label={`/${skill.name} · Skill · ${skill.description}`}
                  onClick={() => invokeSkill(skill.name)}
                >
                  <strong>/{skill.name}</strong>
                  <small>Skill · {skill.description}</small>
                </button>
              ))}
              {!loading && !error && !showFiles && prompts.length === 0 && skillCommands.length === 0 && (
                <p className="review-note">No matching skills or commands.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
