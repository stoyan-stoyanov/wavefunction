import { useEffect, useState } from "react";
import { ArrowLeft, ChevronRight, FileCode2, LoaderCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { codingApi, type CodingThread, type GitPatch, type GitState } from "./api";
import { DiffView } from "./diff";
import { useCoding } from "./store";
import "./changes.css";

const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

function FileDiff({ threadId, path, snapshot }: { threadId: string; path: string; snapshot: GitState }) {
  const [result, setResult] = useState<{
    snapshot: GitState;
    patch?: GitPatch;
    error?: string;
  } | null>(null);
  useEffect(() => {
    let current = true;
    void codingApi
      .gitDiff(threadId, path, false, true)
      .then((patch) => {
        if (current) setResult({ snapshot, patch });
      })
      .catch((reason: unknown) => {
        if (current) setResult({ snapshot, error: errorText(reason) });
      });
    return () => {
      current = false;
    };
  }, [threadId, path, snapshot]);
  const current = result?.snapshot === snapshot ? result : null;
  const patch = current?.patch;
  const binary = patch && /^(?:Binary files .* differ|GIT binary patch)$/m.test(patch.diff);
  return (
    <div className="repository-change-preview" role="region" aria-label={`Changes in ${path}`}>
      {!current ? (
        <p className="repository-changes-message" role="status">
          <LoaderCircle className="size-4 animate-spin" /> Loading diff…
        </p>
      ) : current.error ? (
        <p className="workbench-error" role="alert">
          {current.error}
        </p>
      ) : patch ? (
        <>
          {patch.warning ? <p className="repository-changes-message">{patch.warning}</p> : null}
          {binary ? (
            <p className="repository-changes-message">Binary file changed. A text diff is not available.</p>
          ) : patch.diff ? (
            <DiffView diff={patch.diff} split={false} />
          ) : !patch.warning ? (
            <p className="repository-changes-message">No text changes remain for this file.</p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function ChangesView({ thread }: { thread: CodingThread }) {
  return <ThreadChanges key={`${thread.id}:${thread.conversation_id}:${thread.folder}`} thread={thread} />;
}

function ThreadChanges({ thread }: { thread: CodingThread }) {
  const [result, setResult] = useState<{
    revision: number;
    activity: string;
    snapshot: GitState | null;
    error: string;
  } | null>(null);
  const [revision, setRevision] = useState(0);
  const [path, setPath] = useState<string | null>(null);
  const activity = useCoding(
    (state) =>
      state.details[thread.id]?.runs
        .map(
          (run) =>
            `${run.id}:${run.status}:${
              run.events.filter(
                (event) => event.event_type === "tool_completed" || event.event_type === "tool_failed",
              ).length
            }`,
        )
        .join(";") ?? "",
  );
  useEffect(() => {
    let current = true;
    void codingApi
      .gitState(thread.id)
      .then((next) => {
        if (!current) return;
        setResult({ revision, activity, snapshot: next, error: "" });
        setPath((selected) => (next.files.some((file) => file.path === selected) ? selected : null));
      })
      .catch((reason: unknown) => {
        if (current)
          setResult((previous) => ({
            revision,
            activity,
            snapshot: previous?.snapshot ?? null,
            error: errorText(reason),
          }));
      });
    return () => {
      current = false;
    };
  }, [thread.id, revision, activity]);
  const snapshot = result?.snapshot;
  const loading = result?.revision !== revision || result?.activity !== activity;
  const error = loading ? "" : result?.error;
  const files = [...new Map(snapshot?.files.map((file) => [file.path, file])).values()];
  return (
    <div className="repository-changes">
      <div className="repository-changes-toolbar">
        {path ? (
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label="Back to changed files"
            title="Back to changed files"
            onClick={() => setPath(null)}
          >
            <ArrowLeft />
          </Button>
        ) : null}
        <span title={path ?? undefined}>{path ?? "Changed files"}</span>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="Refresh changes"
          title="Refresh changes"
          disabled={loading}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw className={loading ? "animate-spin" : ""} />
        </Button>
      </div>
      {error ? (
        <p className="workbench-error" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="repository-changes-message" role="status">
          {snapshot ? "Refreshing changes…" : "Loading changed files…"}
        </p>
      ) : null}
      {path && snapshot ? (
        <FileDiff key={path} threadId={thread.id} path={path} snapshot={snapshot} />
      ) : snapshot ? (
        files.length ? (
          <ul className="repository-change-files" aria-label="Changed files">
            {files.map((file) => (
              <li key={file.path}>
                <button
                  type="button"
                  aria-label={`View changes in ${file.path}`}
                  onClick={() => setPath(file.path)}
                >
                  <FileCode2 className="size-3.5" aria-hidden="true" />
                  <span>
                    <span>{file.path}</span>
                    {file.original_path ? <small>From {file.original_path}</small> : null}
                  </span>
                  {file.conflict ? <small>Conflict</small> : null}
                  <ChevronRight className="size-3" aria-hidden="true" />
                </button>
              </li>
            ))}
            {snapshot.total_files > snapshot.files.length ? (
              <li className="repository-changes-message">
                Showing {snapshot.files.length.toLocaleString()} of {snapshot.total_files.toLocaleString()}{" "}
                changes. Commit them, or add generated folders to .gitignore, to see the rest.
              </li>
            ) : null}
          </ul>
        ) : !loading && !error ? (
          <p className="repository-changes-message">No uncommitted changes.</p>
        ) : null
      ) : null}
    </div>
  );
}
