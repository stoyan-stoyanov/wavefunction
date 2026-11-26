import type { ReactNode } from "react";
import { Download, Star } from "@/components/chrome";
import { formatDate } from "@/lib/blog";

// The parts of a blog post that the docs don't have. Paragraphs, lists, code and notes come from
// components/docs.tsx, so a post reads like the guides.

export function PostHeader({ title, dek, date, readTime }: { title: string; dek: string; date: string; readTime: string }) {
  return (
    <header className="border-b border-text/10 pb-8">
      <p className="text-sm text-faint">
        <a href="/blog" className="hover:text-text">
          Blog
        </a>{" "}
        · {formatDate(date)} · {readTime}
      </p>
      <h1 className="mt-4 text-balance text-[clamp(2.1rem,5vw,3.1rem)] font-semibold leading-[1.08] tracking-[-0.04em]">
        {title}
      </h1>
      <p className="mt-5 text-xl leading-relaxed text-soft">{dek}</p>
    </header>
  );
}

/** A figure that isn't a screenshot: a diagram or a chart, with its number and caption. */
export function Diagram({ caption, children }: { caption: ReactNode; children: ReactNode }) {
  return (
    <figure className="mt-10">
      <div className="rounded-[24px] bg-panel p-4 sm:p-7">{children}</div>
      <figcaption className="mt-3 max-w-[68ch] text-sm leading-6 text-faint">{caption}</figcaption>
    </figure>
  );
}

/** A before-and-after measurement: the change is the point, so it's a number, not a chart. */
export function Stat({ label, before, after, note }: { label: string; before: string; after: string; note: string }) {
  return (
    <div className="rounded-[20px] bg-bg p-5 ring-1 ring-text/10">
      <p className="text-sm font-medium text-soft">{label}</p>
      <p className="mt-3 flex flex-wrap items-baseline gap-x-2 tabular-nums">
        <span className="text-lg text-faint line-through decoration-text/30">{before}</span>
        <span className="text-faint" aria-hidden>
          →
        </span>
        <span className="text-3xl font-semibold tracking-[-0.03em]">{after}</span>
      </p>
      <p className="mt-2 text-sm leading-6 text-faint">{note}</p>
    </div>
  );
}

export function PostFooter() {
  return (
    <footer className="mt-16 rounded-[24px] p-6 ring-1 ring-text/10 sm:p-8">
      <p className="text-lg font-medium tracking-[-0.01em]">Try Wavefunction: free and open source.</p>
      <div className="mt-5 flex flex-wrap gap-3">
        <Download />
        <Star place="blog" />
      </div>
    </footer>
  );
}
