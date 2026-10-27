import { useEffect, useId, useState } from "react";
import {
  ChevronRight,
  FileCode2,
  Files,
  Folder,
  GitBranch,
  GitCompareArrows,
  LoaderCircle,
  RefreshCw,
  Save,
  Terminal,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { codingApi, type CodingThread, type TreeEntry } from "./api";
import { TerminalView } from "./terminal";
import { ChangesView } from "./changes";
import { beginWindowDrag } from "@/lib/titlebar";
import "./workbench-ui.css";

export type WorkbenchTab = "files" | "changes" | "terminal";

const toolTabs = [
  { id: "files", title: "Files", icon: Files },
  { id: "terminal", title: "Terminal", icon: Terminal },
  { id: "changes", title: "Changes", icon: GitCompareArrows },
] as const;

function Directory({
  threadId,
  path = "",
  onFile,
}: {
  threadId: string;
  path?: string;
  onFile: (path: string) => void;
}) {
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    void codingApi
      .tree(threadId, path)
      .then((data) => {
        if (alive) setEntries(data);
      })
      .catch((e) => {
        if (alive) setError(String(e.message));
      });
    return () => {
      alive = false;
    };
  }, [threadId, path]);
  return (
    <div className={path ? "ml-3 border-l border-border pl-1" : ""}>
      {error ? <p className="p-3 text-xs text-destructive">{error}</p> : null}
      {entries.map((entry) => (
        <div key={entry.path}>
          <button
            className="workbench-file"
            onClick={() =>
              entry.directory ? setOpen({ ...open, [entry.path]: !open[entry.path] }) : onFile(entry.path)
            }
          >
            {entry.directory ? (
              <>
                <ChevronRight className={`size-3 ${open[entry.path] ? "rotate-90" : ""}`} />
                <Folder className="size-3.5 text-muted-foreground" />
              </>
            ) : (
              <FileCode2 className="ml-4 size-3.5 text-muted-foreground" />
            )}
            <span className="truncate">{entry.name}</span>
          </button>
          {entry.directory && open[entry.path] ? (
            <Directory threadId={threadId} path={entry.path} onFile={onFile} />
          ) : null}
        </div>
      ))}
    </div>
  );
}
function Editor({
  threadId,
  path,
  folder,
  onClose,
}: {
  threadId: string;
  path: string;
  folder: string;
  onClose: () => void;
}) {
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [version, setVersion] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [revision, setRevision] = useState(0);
  const draftKey = `harness.file.${threadId}.${folder}.${path}`;
  useEffect(() => {
    let alive = true;
    void codingApi
      .file(threadId, path, folder)
      .then((file) => {
        if (alive) {
          let draft: {
            text: string;
            original: string;
            version: string;
          } | null = null;
          try {
            const value = JSON.parse(localStorage.getItem(draftKey) || "null");
            if (
              value &&
              typeof value.text === "string" &&
              typeof value.original === "string" &&
              typeof value.version === "string"
            )
              draft = value;
          } catch {
            /* A private or full storage area still allows editing. */
          }
          setContent(draft?.text ?? file.text);
          setOriginal(draft?.original ?? file.text);
          setVersion(draft?.version ?? file.version);
          setError(
            draft && draft.version !== file.version
              ? "This file changed on disk. Your draft is preserved. Copy it or reload the file before saving."
              : "",
          );
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [threadId, path, folder, draftKey, revision]);
  useEffect(() => {
    if (!version) return;
    try {
      if (content === original) localStorage.removeItem(draftKey);
      else localStorage.setItem(draftKey, JSON.stringify({ text: content, original, version }));
    } catch {
      /* The draft stays available in this editor session. */
    }
  }, [content, original, version, draftKey]);
  async function save() {
    if (!version || saving || content === original) return;
    setSaving(true);
    try {
      const saved = await codingApi.save(threadId, path, content, version, folder);
      setVersion(saved.version);
      setOriginal(content);
      setError("");
      toast.success("File saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-y border-border px-3 py-2 text-xs">
        <FileCode2 className="size-3.5" />
        <span className="min-w-0 flex-1 truncate">
          {path}
          {content !== original ? " •" : ""}
        </span>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="Discard draft and reload file"
          title="Discard draft and reload file"
          disabled={saving}
          onClick={() => {
            try {
              localStorage.removeItem(draftKey);
            } catch {
              /* Private browser. */
            }
            setVersion("");
            setRevision(revision + 1);
          }}
        >
          <RefreshCw />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="Save file"
          disabled={!version || content === original || saving}
          onClick={() => void save()}
        >
          {saving ? <LoaderCircle className="animate-spin" /> : <Save />}
        </Button>
        <Button size="icon-xs" variant="ghost" aria-label="Close file" onClick={onClose}>
          <X />
        </Button>
      </div>
      {error ? (
        <p role="alert" className="p-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <textarea
        className="code-editor"
        aria-label={`Edit ${path}`}
        spellCheck={false}
        value={content}
        disabled={!version}
        onChange={(e) => setContent(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "s") {
            e.preventDefault();
            void save();
          }
        }}
      />
    </div>
  );
}
export function Workbench({
  thread,
  tab,
  onTab,
  onClose,
}: {
  thread: CodingThread;
  tab?: WorkbenchTab;
  onTab?: (tab: WorkbenchTab) => void;
  onClose?: () => void;
}) {
  const [localTab, setLocalTab] = useState<WorkbenchTab>("files");
  const activeTab = tab ?? localTab;
  const tabId = useId();
  const [file, setFile] = useState("");
  const [revision, setRevision] = useState(0);
  function selectTab(value: WorkbenchTab) {
    if (tab === undefined) setLocalTab(value);
    onTab?.(value);
  }
  return (
    <aside className="coding-workbench" aria-label="Repository workbench">
      <div
        className="workbench-toolbar flex shrink-0 items-center gap-1 border-b border-border px-2"
        data-tauri-drag-region
        onMouseDown={beginWindowDrag}
      >
        <div className="workbench-tabs" role="tablist" aria-label="Repository tools">
          {toolTabs.map((item, index) => (
            <button
              type="button"
              key={item.id}
              id={`${tabId}-${item.id}-tab`}
              role="tab"
              aria-selected={activeTab === item.id}
              aria-controls={`${tabId}-${item.id}-panel`}
              tabIndex={activeTab === item.id ? 0 : -1}
              onClick={() => selectTab(item.id)}
              onKeyDown={(event) => {
                let next = index;
                if (event.key === "ArrowRight") next = (index + 1) % toolTabs.length;
                else if (event.key === "ArrowLeft") next = (index + toolTabs.length - 1) % toolTabs.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = toolTabs.length - 1;
                else return;
                event.preventDefault();
                selectTab(toolTabs[next].id);
                const buttons =
                  event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=tab]");
                buttons?.[next]?.focus();
              }}
              className={`workbench-tab ${activeTab === item.id ? "selected" : ""}`}
            >
              <item.icon className="size-4" aria-hidden="true" />
              <span>{item.title}</span>
            </button>
          ))}
        </div>
        {onClose ? (
          <Button
            size="icon-xs"
            variant="ghost"
            className="workbench-overlay-close"
            aria-label="Close workbench"
            onClick={onClose}
          >
            <X />
          </Button>
        ) : null}
      </div>
      <div
        className="workbench-content"
        role="tabpanel"
        id={`${tabId}-${activeTab}-panel`}
        aria-labelledby={`${tabId}-${activeTab}-tab`}
      >
        {thread.worktree_status === "deleted" ? (
          <section className="m-5 rounded-2xl bg-muted px-5 py-7 text-center" aria-label="Deleted worktree">
            <GitBranch className="mx-auto mb-3 size-7 text-muted-foreground" aria-hidden="true" />
            <h3 className="text-sm font-semibold">Worktree deleted</h3>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              This inactive worktree was deleted to free disk space. Your chat history and Git branch are
              kept.
              {thread.parent_thread_id
                ? " Fork this helper to recreate its worktree and continue in a new chat."
                : " Continue this chat to recreate the worktree and access its files, changes, and terminals."}
            </p>
          </section>
        ) : thread.folder ? (
          activeTab === "files" ? (
            <>
              <div className="flex items-center justify-between px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground">
                <span>Repository</span>
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label="Refresh files"
                  onClick={() => setRevision(revision + 1)}
                >
                  <RefreshCw />
                </Button>
              </div>
              <div className={`overflow-auto ${file ? "max-h-[35%]" : "flex-1"}`}>
                <Directory key={revision} threadId={thread.id} onFile={setFile} />
              </div>
              {file ? (
                <Editor
                  key={file}
                  threadId={thread.id}
                  path={file}
                  folder={thread.folder}
                  onClose={() => setFile("")}
                />
              ) : null}
            </>
          ) : activeTab === "changes" ? (
            <ChangesView thread={thread} />
          ) : (
            <TerminalView thread={thread} />
          )
        ) : (
          <div className="p-6 text-sm text-muted-foreground">
            Choose a repository folder in the conversation to browse files, review changes, and run commands.
          </div>
        )}
      </div>
    </aside>
  );
}
