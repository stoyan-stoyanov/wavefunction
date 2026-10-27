import { useEffect, useState } from "react";
import { FileText, X } from "lucide-react";
import type { ProjectFile } from "@/lib/types";
import { codingApi } from "./api";

/** Attached files in the order they were added; removable only while still a draft. */
export function AttachmentPreviews({
  threadId,
  files,
  onRemove,
  className,
}: {
  threadId: string;
  files: ProjectFile[];
  onRemove?: (file: ProjectFile) => void;
  className: string;
}) {
  return (
    <ul className={className} aria-label="Attached files">
      {files.map((file) => (
        <AttachmentPreview
          key={file.id}
          threadId={threadId}
          file={file}
          onRemove={onRemove ? () => onRemove(file) : undefined}
        />
      ))}
    </ul>
  );
}

/** One attached file: an image shows its thumbnail, anything else its type and size. */
function AttachmentPreview({
  threadId,
  file,
  onRemove,
}: {
  threadId: string;
  file: ProjectFile;
  onRemove?: () => void;
}) {
  const image = file.media_type?.startsWith("image/") ?? false;
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!image) return;
    let current: string | null = null;
    let cancelled = false;
    codingApi
      .attachmentPreview(threadId, file.id)
      .then((blob) => {
        if (cancelled) URL.revokeObjectURL(blob);
        else setUrl((current = blob));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      if (current) URL.revokeObjectURL(current);
    };
  }, [image, threadId, file.id]);
  const extension = file.name.includes(".") ? file.name.split(".").pop()!.toUpperCase() : "FILE";
  return (
    <li className={image ? "composer-attachment image" : "composer-attachment"} title={file.name}>
      {image ? (
        url ? (
          <img src={url} alt={file.name} />
        ) : (
          <span className="composer-attachment-placeholder" aria-hidden="true" />
        )
      ) : (
        <>
          <span className="composer-attachment-icon" aria-hidden="true">
            <FileText className="size-4" />
          </span>
          <span className="composer-attachment-text">
            <span className="composer-attachment-name">{file.name}</span>
            <span className="composer-attachment-meta">
              {extension}
              {file.size_bytes ? ` · ${fileSize(file.size_bytes)}` : ""}
            </span>
          </span>
        </>
      )}
      {onRemove ? (
        <button className="composer-attachment-remove" aria-label={`Remove ${file.name}`} onClick={onRemove}>
          <X className="size-3" />
        </button>
      ) : null}
    </li>
  );
}

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
