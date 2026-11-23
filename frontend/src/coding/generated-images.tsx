/** Images the agent's image tools saved, shown in the transcript where they were made.
 *
 * Kept to this one component so a general media preview can replace it. A file in the chat's
 * folder opens in the chat's file preview; a chat without a folder keeps its images as
 * attachments, which open here at full size.
 */

import { useContext, useEffect, useState } from "react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { codingApi } from "./api";
import { FileLinks } from "./file-links";
import type { GeneratedImage } from "./run-transcript";
import "./generated-images.css";

const nameOf = (image: GeneratedImage) =>
  "path" in image ? (image.path.split("/").pop() ?? image.path) : image.attachment;

/** A blob URL for an image, revoked when it is no longer shown. */
function useImage(load: (signal: AbortSignal) => Promise<string>, key: string) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const download = new AbortController();
    let created: string | null = null;
    load(download.signal)
      .then((value) => {
        if (download.signal.aborted) URL.revokeObjectURL(value);
        else setUrl((created = value));
      })
      .catch(() => !download.signal.aborted && setFailed(true));
    return () => {
      download.abort();
      if (created) URL.revokeObjectURL(created);
    };
    // `key` names what `load` fetches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { url, failed };
}

function Thumbnail({ threadId, image }: { threadId: string; image: GeneratedImage }) {
  const { url, failed } = useImage(
    (signal) =>
      "path" in image
        ? codingApi.filePreviewUrl(threadId, image.path, signal)
        : codingApi.attachmentPreview(threadId, image.id),
    `${threadId}:${"path" in image ? image.path : image.id}`,
  );
  if (failed) return <span className="generated-image-missing">No longer available</span>;
  if (!url) return <span className="generated-image-loading" aria-hidden="true" />;
  return <img src={url} alt={nameOf(image)} />;
}

function FullImage({ threadId, image }: { threadId: string; image: { attachment: string; id: string } }) {
  const { url, failed } = useImage(
    (signal) => codingApi.attachmentImage(threadId, image.id, signal),
    `${threadId}:${image.id}`,
  );
  return (
    <div className="generated-image-full">
      {url ? (
        <img src={url} alt={image.attachment} />
      ) : (
        <p className="text-sm text-muted-foreground" role="status">
          {failed ? "This image is no longer available." : "Loading image…"}
        </p>
      )}
    </div>
  );
}

export function GeneratedImages({ threadId, images }: { threadId: string; images: GeneratedImage[] }) {
  const openFile = useContext(FileLinks);
  const [viewing, setViewing] = useState<{ attachment: string; id: string } | null>(null);
  return (
    <>
      <ul className="generated-images" aria-label="Generated images">
        {images.map((image) => (
          <li key={"path" in image ? image.path : image.id}>
            <button
              type="button"
              title={nameOf(image)}
              aria-label={`Open ${nameOf(image)}`}
              onClick={() => ("path" in image ? openFile?.(image.path) : setViewing(image))}
            >
              <Thumbnail threadId={threadId} image={image} />
            </button>
          </li>
        ))}
      </ul>
      <Dialog open={viewing !== null} onOpenChange={(open) => !open && setViewing(null)}>
        <DialogContent className="sm:max-w-4xl">
          <DialogTitle className="truncate pr-8">{viewing?.attachment}</DialogTitle>
          {viewing ? <FullImage threadId={threadId} image={viewing} /> : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
