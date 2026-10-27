import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ExternalLink, FolderOpen, LoaderCircle, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { codingApi, type FilePreview as Preview } from "./api";
import { FileLinks, MAX_ROWS, parseTable } from "./file-links";
import { CodingMarkdown } from "./markdown";

// pdf.js is large; load it only when a PDF is opened.
const PdfViewer = lazy(() => import("./pdf-viewer"));
const loading = (
  <p className="file-preview-empty" role="status">
    <LoaderCircle className="size-4 animate-spin" /> Loading preview…
  </p>
);
import "./file-preview.css";

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

function Body({ threadId, preview }: { threadId: string; preview: Preview }) {
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState("");
  const binary = preview.kind === "image";
  const pdf = useCallback(
    (signal: AbortSignal) => codingApi.filePreviewBytes(threadId, preview.path, signal),
    [threadId, preview.path],
  );
  const table = useMemo(
    () =>
      preview.kind === "table" && text !== null
        ? parseTable(text, preview.name.toLowerCase().endsWith(".tsv") ? "\t" : ",")
        : null,
    [preview, text],
  );
  useEffect(() => {
    if (!preview.previewable || preview.kind === "other" || preview.kind === "pdf") return;
    let cancelled = false;
    let created: string | null = null;
    // Closing the pane stops the download, not only its result.
    const download = new AbortController();
    const load = binary
      ? codingApi.filePreviewUrl(threadId, preview.path, download.signal)
      : codingApi.filePreviewText(threadId, preview.path, download.signal);
    load
      .then((value) => {
        if (cancelled) {
          if (binary) URL.revokeObjectURL(value);
          return;
        }
        if (binary) setUrl((created = value));
        else setText(value);
      })
      .catch((error) => {
        if (!cancelled) setFailed(errorText(error));
      });
    return () => {
      download.abort();
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [threadId, preview, binary]);
  if (!preview.previewable || preview.kind === "other" || failed) {
    return (
      <p className="file-preview-empty">
        {failed ||
          (preview.previewable
            ? "There is no preview for this kind of file."
            : "This file is too large to preview.")}{" "}
        Open it in another app instead.
      </p>
    );
  }
  if (preview.kind === "pdf")
    return (
      <Suspense fallback={loading}>
        <PdfViewer load={pdf} name={preview.name} />
      </Suspense>
    );
  if (binary ? !url : text === null) return loading;
  if (preview.kind === "image")
    return (
      <div className="file-preview-image">
        <img src={url!} alt={preview.name} />
      </div>
    );
  if (preview.kind === "table") {
    const rows = table!;
    const [head = [], ...body] = rows;
    return (
      <div className="file-preview-table">
        <table>
          <thead>
            <tr>
              {head.map((cell, index) => (
                <th key={index}>{cell}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.slice(0, MAX_ROWS).map((row, index) => (
              <tr key={index}>
                {row.map((cell, column) => (
                  <td key={column}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {body.length > MAX_ROWS ? (
          <p className="file-preview-note">Showing the first {MAX_ROWS} rows.</p>
        ) : null}
      </div>
    );
  }
  if (/\.(md|markdown)$/i.test(preview.name)) {
    // Links inside the file are relative to it, not to the chat, so they open nothing here.
    return (
      <div className="file-preview-markdown">
        <FileLinks.Provider value={null}>
          <CodingMarkdown>{text!}</CodingMarkdown>
        </FileLinks.Provider>
      </div>
    );
  }
  return <pre className="file-preview-text">{text}</pre>;
}

/** A file the chat linked, shown over the transcript so the composer stays visible and usable. */
export const FilePreviewPane = memo(function FilePreviewPane({
  threadId,
  path,
  onClose,
}: {
  threadId: string;
  path: string;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [failed, setFailed] = useState("");
  useEffect(() => {
    let cancelled = false;
    codingApi
      .filePreview(threadId, path)
      .then((value) => {
        if (!cancelled) setPreview(value);
      })
      .catch((error) => {
        if (!cancelled) setFailed(errorText(error));
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, path]);
  useEffect(() => {
    // Escape closes the preview, unless it is closing an open menu (Open in, or the composer's).
    const close = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !event.defaultPrevented &&
        !event.isComposing &&
        !document.querySelector('[role="menu"], [role="listbox"]')
      )
        onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  const name = preview?.name ?? path.split("/").pop() ?? path;
  const open = (target: "finder" | "app") =>
    void codingApi.openFile(threadId, preview!.path, target).catch((error) => toast.error(errorText(error)));
  return (
    // Dims the transcript only; the composer below stays visible and usable.
    <div
      className="file-preview-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside className="file-preview" aria-label={`Preview of ${name}`}>
        <header className="file-preview-header">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium" title={preview?.path ?? path}>
              {name}
            </p>
            {preview ? (
              <p className="truncate text-xs text-muted-foreground" title={preview.path}>
                {preview.path}
              </p>
            ) : null}
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" disabled={!preview}>
                Open in <ChevronDown />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => open("finder")}>
                <FolderOpen /> Finder
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!preview?.openable} onSelect={() => open("app")}>
                <ExternalLink /> Default app
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="ghost" size="icon-sm" aria-label="Close preview" onClick={onClose}>
            <X />
          </Button>
        </header>
        {failed ? (
          <p role="alert" className="file-preview-empty text-destructive">
            {failed}
          </p>
        ) : preview ? (
          <Body threadId={threadId} preview={preview} />
        ) : (
          <p className="file-preview-empty" role="status">
            <LoaderCircle className="size-4 animate-spin" /> Loading preview…
          </p>
        )}
      </aside>
    </div>
  );
});
