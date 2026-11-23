import { useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Globe, LoaderCircle, Play, Server } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { openExternal } from "@/lib/api";
import { codingApi, type LinkPreview } from "./api";
import { FileLinks, LinkedFilesThread, linkedFile } from "./file-links";
import "./link-previews.css";

/** Opens the files a chat's transcript links, and reads them from that chat's folders. */
export function ChatFileLinks({
  threadId,
  open,
  children,
}: {
  threadId: string;
  open: (path: string) => void;
  children: ReactNode;
}) {
  return (
    <FileLinks.Provider value={open}>
      <LinkedFilesThread.Provider value={threadId}>{children}</LinkedFilesThread.Provider>
    </FileLinks.Provider>
  );
}

/** A picture or video a reply names: a file in the chat's folders, or a web address. */
export type Media = { path: string } | { url: string };

const IMAGE = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const VIDEO = /\.(mp4|webm|mov|m4v)$/i;
// Past these, cards are noise; every link still shows in the text.
const MAX_CARDS = 3;
const MAX_IMAGES = 8;
const MARKDOWN_LINK =
  /(!?)\[(?:[^\]\\\n]|\\.)*\]\(\s*<?([^\s<>()]+(?:\([^\s<>()]*\)[^\s<>()]*)*)>?(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)/g;
const BARE_URL = /<?https?:\/\/[^\s<>"'`]+/gi;

/** The web pages, pictures and videos a reply links, in order. Code is quoted rather than shared,
 * so its links are skipped, and so are images the Markdown already shows in place. */
function replyLinks(markdown: string) {
  let fenced = false;
  const prose = markdown
    .split("\n")
    .map((line) => {
      if (/^ {0,3}(```|~~~)/.test(line)) fenced = !fenced;
      else if (!fenced) return line.replace(/`[^`]*`/g, (code) => " ".repeat(code.length));
      return " ".repeat(line.length);
    })
    .join("\n");
  const found: [number, string][] = [];
  // Markdown links first, blanked so the addresses inside them aren't read twice.
  const rest = prose.replace(MARKDOWN_LINK, (match: string, image: string, href: string, at: number) => {
    if (!image) found.push([at, href]);
    return " ".repeat(match.length);
  });
  for (const match of rest.matchAll(BARE_URL)) {
    let href = match[0].replace(/^</, "").replace(/[>.,;:!?*_~]+$/, "");
    // A closing parenthesis belongs to the address only when it opened one.
    while (href.endsWith(")") && href.split("(").length < href.split(")").length) href = href.slice(0, -1);
    found.push([match.index, href]);
  }
  const pages: string[] = [];
  const images: Media[] = [];
  const videos: Media[] = [];
  for (const href of new Set(found.sort((a, b) => a[0] - b[0]).map(([, href]) => href))) {
    if (/^https?:\/\//i.test(href)) {
      let path: string;
      try {
        path = new URL(href).pathname;
      } catch {
        continue;
      }
      if (IMAGE.test(path)) images.push({ url: href });
      else if (VIDEO.test(path)) videos.push({ url: href });
      else pages.push(href);
    } else {
      // Any other file opens in the chat's file preview from the link itself.
      const path = linkedFile(href);
      if (path && IMAGE.test(path)) images.push({ path });
      else if (path && VIDEO.test(path)) videos.push({ path });
    }
  }
  return {
    pages: pages.slice(0, MAX_CARDS),
    images: images.slice(0, MAX_IMAGES),
    videos: videos.slice(0, MAX_CARDS),
  };
}

function mediaName(media: Media) {
  const address = "path" in media ? media.path : media.url.replace(/[?#].*$/, "");
  const name = address.replace(/\/+$/, "").split("/").pop() || address;
  try {
    return decodeURIComponent(name);
  } catch {
    return name;
  }
}

/** A blob URL for `media` once `load` is set; the engine reads it, from the chat's folders or the
 * web. Revoked when the media changes or the component goes. */
function useMediaUrl(media: Media, load = true) {
  const threadId = useContext(LinkedFilesThread);
  const path = "path" in media ? media.path : null;
  const url = "url" in media ? media.url : null;
  const key = `${threadId}\n${path}\n${url}`;
  const [state, setState] = useState<{ key: string; url?: string; failed?: boolean }>({ key });
  useEffect(() => {
    if (!load || (path !== null && threadId === null)) return;
    const download = new AbortController();
    let created: string | null = null;
    (path !== null
      ? codingApi.filePreviewUrl(threadId!, path, download.signal)
      : codingApi.linkMedia(url!, download.signal)
    )
      .then((value) => {
        if (download.signal.aborted) URL.revokeObjectURL(value);
        else setState({ key, url: (created = value) });
      })
      .catch(() => {
        if (!download.signal.aborted) setState({ key, failed: true });
      });
    return () => {
      download.abort();
      if (created) URL.revokeObjectURL(created);
    };
  }, [key, load, path, threadId, url]);
  // A file needs a chat to be read from.
  if (path !== null && threadId === null) return { key, failed: true };
  return state.key === key ? state : { key };
}

/** A picture as a thumbnail that opens larger: a file in the chat's file preview, anything else
 * in a viewer. Renders `fallback` if the picture can't be read. */
export function ImageThumbnail({
  media,
  alt,
  fallback = null,
}: {
  media: Media;
  alt?: string;
  fallback?: ReactNode;
}) {
  const openFile = useContext(FileLinks);
  const { url, failed } = useMediaUrl(media);
  const [viewing, setViewing] = useState(false);
  const name = alt || mediaName(media);
  const inPreview = "path" in media && openFile ? () => openFile(media.path) : null;
  if (failed) return fallback;
  return (
    <>
      <button
        type="button"
        className="media-thumbnail"
        aria-label={`View ${name}`}
        title={name}
        disabled={!url}
        onClick={() => (inPreview ? inPreview() : setViewing(true))}
      >
        {url ? <img src={url} alt={name} /> : <span className="media-thumbnail-placeholder" aria-hidden />}
      </button>
      {inPreview ? null : (
        <Dialog open={viewing && !!url} onOpenChange={setViewing}>
          <DialogContent className="media-viewer sm:max-w-[min(92vw,1200px)]">
            <DialogTitle className="truncate pr-8">{name}</DialogTitle>
            <img src={url} alt={name} />
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

/** A video as a poster that plays inline when clicked; nothing downloads before that. */
export function VideoPlayer({ media }: { media: Media }) {
  const [play, setPlay] = useState(false);
  const { url, failed } = useMediaUrl(media, play);
  const name = mediaName(media);
  if (url) return <video className="media-video" src={url} controls autoPlay playsInline aria-label={name} />;
  return (
    <button
      type="button"
      className="media-video-poster"
      aria-label={`Play ${name}`}
      disabled={play}
      onClick={() => setPlay(true)}
    >
      <span className="media-video-play" aria-hidden>
        {play && !failed ? <LoaderCircle className="size-5 animate-spin" /> : <Play className="size-5" />}
      </span>
      <span className="media-video-name">{failed ? `Couldn't load ${name}` : name}</span>
    </button>
  );
}

/** "localhost:5173" for a server on this machine: it gets a simple card, and nothing is fetched. */
function localServer(url: string) {
  try {
    const { hostname, host } = new URL(url);
    return ["localhost", "127.0.0.1", "[::1]", "0.0.0.0"].includes(hostname) ||
      hostname.endsWith(".localhost")
      ? host
      : null;
  } catch {
    return null;
  }
}

function CardPicture({
  url,
  className,
  fallback = null,
}: {
  url: string;
  className: string;
  fallback?: ReactNode;
}) {
  const { url: blob } = useMediaUrl({ url });
  return blob ? <img className={className} src={blob} alt="" /> : fallback;
}

function CardLink({ url, label, children }: { url: string; label: string; children: ReactNode }) {
  const open = () =>
    void openExternal(url).catch((error: unknown) =>
      toast.error(error instanceof Error ? error.message : "Could not open link"),
    );
  return (
    <a
      className="link-card"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      title={url}
      onClick={(event) => {
        event.preventDefault();
        open();
      }}
      onAuxClick={(event) => {
        if (event.button === 1) {
          event.preventDefault();
          open();
        }
      }}
    >
      {children}
    </a>
  );
}

/** A web link's card, as the engine read it from the page. A link without a card shows nothing
 * here: it is already a link in the text. */
export function LinkCard({ url }: { url: string }) {
  const local = localServer(url);
  const [card, setCard] = useState<LinkPreview | null>(null);
  useEffect(() => {
    if (local) return;
    let cancelled = false;
    codingApi
      .linkPreview(url)
      .then((value) => {
        if (!cancelled) setCard(value);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [url, local]);
  if (local) {
    const path = url.slice(url.indexOf(local) + local.length);
    return (
      <CardLink url={url} label={`Open ${local}`}>
        <span className="link-card-text">
          <span className="link-card-site">
            <Server className="size-3.5" aria-hidden /> Local server
          </span>
          <span className="link-card-title">
            {local}
            {path.length > 1 ? path : ""}
          </span>
        </span>
      </CardLink>
    );
  }
  if (card?.kind === "image") return <ImageThumbnail media={{ url }} />;
  if (card?.kind === "video") return <VideoPlayer media={{ url }} />;
  if (card?.kind !== "page") return null;
  return (
    <CardLink url={url} label={`Open ${card.title}`}>
      {card.image ? <CardPicture url={card.image} className="link-card-image" /> : null}
      <span className="link-card-text">
        <span className="link-card-site">
          {card.icon ? (
            <CardPicture
              url={card.icon}
              className="link-card-icon"
              fallback={<Globe className="size-3.5" aria-hidden />}
            />
          ) : (
            <Globe className="size-3.5" aria-hidden />
          )}
          <span className="truncate">{card.site_name}</span>
        </span>
        <span className="link-card-title">{card.title}</span>
        {card.description ? <span className="link-card-description">{card.description}</span> : null}
      </span>
    </CardLink>
  );
}

const mediaKey = (media: Media) => ("path" in media ? `file:${media.path}` : media.url);

/** Pictures, players and cards for what a finished reply links, below its text. */
export function ReplyPreviews({ text }: { text: string }) {
  const { pages, images, videos } = useMemo(() => replyLinks(text), [text]);
  if (!pages.length && !images.length && !videos.length) return null;
  return (
    <div className="reply-previews">
      {images.length ? (
        <div className="reply-previews-images">
          {images.map((media) => (
            <ImageThumbnail key={mediaKey(media)} media={media} />
          ))}
        </div>
      ) : null}
      {videos.map((media) => (
        <VideoPlayer key={mediaKey(media)} media={media} />
      ))}
      {pages.map((url) => (
        <LinkCard key={url} url={url} />
      ))}
    </div>
  );
}
