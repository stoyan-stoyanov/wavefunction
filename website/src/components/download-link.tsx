"use client";

import { track } from "@vercel/analytics";
import type { ReactNode } from "react";
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

// Counts each click as a Web Analytics "Download" event, so downloads trace back to a channel.
export function DownloadLink({ className, children }: { className: string; children: ReactNode }) {
  return (
    <a
      href={site.download}
      className={className}
      onClick={() => track("Download", { source: source() })}
    >
      {children}
    </a>
  );
}
