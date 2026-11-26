"use client";

import { track } from "@vercel/analytics";
import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import { GitHubIcon } from "@/components/icons";
import { site } from "@/lib/site";

// Where this visitor came from: the `ref` tag on links we post (wavefunction.si/?ref=x-nov5),
// else the site that sent them. Hash links keep both, since the page never reloads.
function source() {
  const ref = new URLSearchParams(window.location.search).get("ref");
  if (ref) return ref;
  if (!document.referrer) return "direct";
  const host = new URL(document.referrer).hostname.replace(/^www\./, "");
  return host === window.location.hostname.replace(/^www\./, "") ? "direct" : host;
}

// How many Download clicks there have been, shared by every Download button. Each one opens the
// install steps, even after the visitor closed them.
let downloads = 0;
const listeners = new Set<() => void>();
function countDownload() {
  downloads += 1;
  listeners.forEach((listener) => listener());
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Counts each click as a "Download" event in Web Analytics, and in Google Analytics when it's loaded,
// so downloads trace back to a channel. The installer downloads in the background, so the page stays
// and shows the install steps.
export function DownloadLink({ className, children }: { className: string; children: ReactNode }) {
  return (
    <a
      href={site.download}
      className={className}
      onClick={() => {
        const from = source();
        track("Download", { source: from });
        window.gtag?.("event", "download", { source: from });
        countDownload();
      }}
    >
      {children}
    </a>
  );
}

// Counts each click as a "Star" event, with the same source as downloads and which button it was.
export function StarLink({ className, place, children }: { className: string; place: string; children: ReactNode }) {
  return (
    <a
      href={site.github}
      className={className}
      onClick={() => {
        const from = source();
        track("Star", { source: from, place });
        window.gtag?.("event", "star", { source: from, place });
      }}
    >
      {children}
    </a>
  );
}

/** The install steps, and a star button, shown while the installer downloads. */
export function DownloadPanel() {
  const count = useSyncExternalStore(subscribe, () => downloads, () => 0);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (count > 0 && !dialog.current?.open) dialog.current?.showModal();
  }, [count]);
  return (
    <dialog
      ref={dialog}
      aria-labelledby="download-panel-title"
      // A click on the backdrop lands on the dialog itself.
      onClick={(event) => event.target === event.currentTarget && event.currentTarget.close()}
      className="m-auto w-[calc(100%-2rem)] max-w-[26rem] rounded-2xl bg-bg p-0 text-text shadow-xl ring-1 ring-text/10 backdrop:bg-text/30"
    >
      <div className="p-6 sm:p-7">
        <h2 id="download-panel-title" className="text-xl font-semibold tracking-[-0.02em]">
          Your download has started
        </h2>
        <ol className="mt-4 list-decimal space-y-1.5 pl-5 text-[15px] leading-relaxed text-soft">
          <li>Open the Wavefunction DMG in your Downloads.</li>
          <li>Drag Wavefunction to Applications.</li>
          <li>Open it and connect a model.</li>
        </ol>
        <p className="mt-6 border-t border-text/10 pt-6 text-[15px] leading-relaxed text-soft">
          Stars help other developers find Wavefunction.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <StarLink
            place="after-download"
            className="inline-flex h-10 items-center gap-2 rounded-[10px] bg-text px-4 text-[15px] font-medium text-white transition-colors hover:bg-text/85"
          >
            <GitHubIcon />
            Star on GitHub
          </StarLink>
          <button
            type="button"
            onClick={() => dialog.current?.close()}
            className="h-10 rounded-[10px] bg-panel px-4 text-[15px] font-medium transition-colors hover:bg-panel-raised"
          >
            Close
          </button>
        </div>
      </div>
    </dialog>
  );
}
