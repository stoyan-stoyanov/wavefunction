import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Minus, Plus } from "lucide-react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Button } from "@/components/ui/button";
import "./pdf-viewer.css";

GlobalWorkerOptions.workerSrc = workerUrl;
const ZOOMS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3];
const THUMBNAIL_SCALE = 0.18;

/** One page, drawn only while it is on screen and redrawn at the current zoom. */
function Page({
  doc,
  number,
  scale,
  root,
  onShown,
}: {
  doc: PDFDocumentProxy;
  number: number;
  scale: number;
  root: HTMLElement | null;
  onShown?: (number: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  // Measured at a scale; drawing follows the measurement, so a zoom draws each page once.
  const [size, setSize] = useState<{ width: number; height: number; scale: number } | null>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void doc.getPage(number).then((page) => {
      const viewport = page.getViewport({ scale });
      if (!cancelled) setSize({ width: viewport.width, height: viewport.height, scale });
    });
    return () => {
      cancelled = true;
    };
  }, [doc, number, scale]);
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      root,
      rootMargin: "200px 0px",
    });
    observer.observe(element);
    // The current page is the one across the middle of the view, however far it is zoomed.
    const current =
      onShown &&
      new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) onShown(number);
        },
        { root, rootMargin: "-50% 0px -50% 0px" },
      );
    current?.observe(element);
    return () => {
      observer.disconnect();
      current?.disconnect();
    };
  }, [root, number, onShown]);
  useEffect(() => {
    if (!visible && canvas.current) {
      // An off-screen page gives its bitmap back; a long PDF otherwise holds every page drawn.
      canvas.current.width = 0;
      canvas.current.height = 0;
    }
    if (!visible || !size) return;
    let task: RenderTask | null = null;
    let cancelled = false;
    void doc.getPage(number).then((page) => {
      const target = canvas.current;
      if (cancelled || !target) return;
      // Draw at the screen's pixel density so text stays sharp.
      const viewport = page.getViewport({ scale: size.scale * window.devicePixelRatio });
      target.width = viewport.width;
      target.height = viewport.height;
      task = page.render({ canvas: target, viewport });
      task.promise.catch(() => undefined);
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, number, visible, size]);
  return (
    <div
      ref={box}
      className="pdf-page"
      data-page={number}
      style={size ? { width: size.width, height: size.height } : undefined}
    >
      <canvas ref={canvas} aria-label={`Page ${number}`} />
    </div>
  );
}

/** A PDF with page thumbnails and zoom, drawn by pdf.js so it looks the same everywhere. */
export default function PdfViewer({
  load,
  name,
}: {
  load: (signal: AbortSignal) => Promise<ArrayBuffer>;
  name: string;
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [failed, setFailed] = useState("");
  // Starts fitted to the width of the page column.
  const [scale, setScale] = useState<number | null>(null);
  const [current, setCurrent] = useState(1);
  const [pages, setPages] = useState<HTMLDivElement | null>(null);
  const [thumbs, setThumbs] = useState<HTMLElement | null>(null);
  useEffect(() => {
    let cancelled = false;
    let task: PDFDocumentLoadingTask | null = null;
    const download = new AbortController();
    load(download.signal)
      .then((data) => {
        if (cancelled) return null;
        task = getDocument({ data });
        return task.promise;
      })
      .then((value) => {
        if (value && !cancelled) setDoc(value);
      })
      .catch((error) => {
        if (!cancelled) setFailed(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
      download.abort();
      // Ends the worker's copy of the document too.
      void task?.destroy();
    };
  }, [load]);
  useEffect(() => {
    if (!doc || !pages || scale !== null) return;
    void doc.getPage(1).then((page) => {
      const width = page.getViewport({ scale: 1 }).width;
      setScale(Math.min(2, Math.max(0.25, (pages.clientWidth - 32) / width)));
    });
  }, [doc, pages, scale]);
  const zoomBy = (step: number) =>
    setScale((value) => {
      const current = value ?? 1;
      const next =
        step > 0
          ? ZOOMS.find((zoom) => zoom > current + 0.01)
          : [...ZOOMS].reverse().find((zoom) => zoom < current - 0.01);
      return next ?? current;
    });
  useEffect(() => {
    // Pinch on a trackpad arrives as a wheel event with the control key held.
    if (!pages) return;
    // One pinch sends dozens of events: step a zoom level per stretch of travel, not per event.
    let travel = 0;
    const pinch = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      travel += event.deltaY;
      if (Math.abs(travel) < 40) return;
      zoomBy(travel < 0 ? 1 : -1);
      travel = 0;
    };
    pages.addEventListener("wheel", pinch, { passive: false });
    return () => pages.removeEventListener("wheel", pinch);
  }, [pages]);
  if (failed)
    return (
      <p className="file-preview-empty text-destructive" role="alert">
        This PDF could not be shown: {failed}
      </p>
    );
  if (!doc)
    return (
      <p className="file-preview-empty" role="status">
        <LoaderCircle className="size-4 animate-spin" /> Loading preview…
      </p>
    );
  const numbers = Array.from({ length: doc.numPages }, (_, index) => index + 1);
  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <span className="text-xs text-muted-foreground">
          Page {current} of {doc.numPages}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Zoom out"
            disabled={!scale || scale <= ZOOMS[0]}
            onClick={() => zoomBy(-1)}
          >
            <Minus />
          </Button>
          <span className="w-11 text-center text-xs tabular-nums" aria-label="Zoom">
            {scale ? Math.round(scale * 100) : 100}%
          </span>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Zoom in"
            disabled={!scale || scale >= ZOOMS.at(-1)!}
            onClick={() => zoomBy(1)}
          >
            <Plus />
          </Button>
        </div>
      </div>
      <div className="pdf-body">
        <nav className="pdf-thumbnails" ref={setThumbs} aria-label={`Pages of ${name}`}>
          {numbers.map((number) => (
            <button
              key={number}
              type="button"
              aria-label={`Go to page ${number}`}
              aria-current={number === current ? "page" : undefined}
              onClick={() =>
                pages?.querySelector(`[data-page="${number}"]`)?.scrollIntoView({ block: "start" })
              }
            >
              <Page doc={doc} number={number} scale={THUMBNAIL_SCALE} root={thumbs} />
              <span>{number}</span>
            </button>
          ))}
        </nav>
        <div className="pdf-pages" ref={setPages}>
          {scale !== null &&
            numbers.map((number) => (
              <Page key={number} doc={doc} number={number} scale={scale} root={pages} onShown={setCurrent} />
            ))}
        </div>
      </div>
    </div>
  );
}
