import Image from "next/image";
import type { ReactNode } from "react";
import { Download, Star } from "@/components/chrome";
import { DOC_PAGES, type DocPath } from "@/lib/docs";

// The building blocks of a docs page, so every guide reads and looks the same.

export function DocHeader({ title, lede, updated }: { title: string; lede: ReactNode; updated: string }) {
  return (
    <header className="border-b border-text/10 pb-8">
      <h1 className="text-balance text-[clamp(2rem,4vw,2.75rem)] font-semibold leading-[1.1] tracking-[-0.035em]">
        {title}
      </h1>
      <p className="mt-5 max-w-[60ch] text-lg leading-relaxed text-soft">{lede}</p>
      <p className="mt-5 text-sm text-faint">Updated {updated}</p>
    </header>
  );
}

export function H2({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2 id={id} className="mt-14 scroll-mt-24 text-2xl font-semibold tracking-[-0.02em]">
      <a href={`#${id}`} className="hover:text-accent">
        {children}
      </a>
    </h2>
  );
}

export function H3({ children }: { children: ReactNode }) {
  return <h3 className="mt-8 text-lg font-semibold tracking-[-0.01em]">{children}</h3>;
}

export function P({ children }: { children: ReactNode }) {
  return <p className="mt-4 max-w-[68ch] text-[16.5px] leading-7 text-text/85">{children}</p>;
}

export function List({ children, ordered = false }: { children: ReactNode; ordered?: boolean }) {
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag
      className={`mt-4 max-w-[68ch] space-y-2.5 pl-6 text-[16.5px] leading-7 text-text/85 marker:text-faint ${ordered ? "list-decimal" : "list-disc"}`}
    >
      {children}
    </Tag>
  );
}

/** Inline code, a file name or a command. */
export function C({ children }: { children: ReactNode }) {
  return <code className="rounded-md bg-panel px-1.5 py-0.5 font-mono text-[0.88em]">{children}</code>;
}

/** A label in the app, such as a setting or a button. */
export function UI({ children }: { children: ReactNode }) {
  return <strong className="font-semibold text-text">{children}</strong>;
}

export function Code({ children }: { children: string }) {
  return (
    <pre className="app-dark mt-5 max-w-[68ch] overflow-x-auto rounded-xl bg-bg px-5 py-4 font-mono text-[13.5px] leading-6 text-text ring-1 ring-text/15">
      <code>{children}</code>
    </pre>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <div className="mt-6 max-w-[68ch] rounded-xl bg-panel px-5 py-4 text-[15.5px] leading-7 text-text/85">
      {children}
    </div>
  );
}

export function A({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className="font-medium text-accent underline decoration-accent/30 underline-offset-[3px] hover:decoration-accent">
      {children}
    </a>
  );
}

/** Ends every guide: the download, then the previous and next pages. */
export function DocFooter({ path }: { path: DocPath }) {
  const index = DOC_PAGES.findIndex((page) => page.path === path);
  const previous = DOC_PAGES[index - 1];
  const next = DOC_PAGES[index + 1];
  return (
    <footer className="mt-16">
      <div className="rounded-[24px] p-6 ring-1 ring-text/10 sm:p-8">
        <p className="text-lg font-medium tracking-[-0.01em]">Try Wavefunction: free and open source.</p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Download />
          <Star place="docs" />
        </div>
      </div>
      <nav className="mt-10 grid gap-3 sm:grid-cols-2" aria-label="Previous and next">
        {previous ? (
          <a href={previous.path} className="rounded-xl px-5 py-4 ring-1 ring-text/10 transition-colors hover:bg-panel">
            <span className="text-sm text-faint">Previous</span>
            <span className="mt-1 block font-medium">{previous.label}</span>
          </a>
        ) : (
          <span />
        )}
        {next && (
          <a href={next.path} className="rounded-xl px-5 py-4 text-right ring-1 ring-text/10 transition-colors hover:bg-panel">
            <span className="text-sm text-faint">Next</span>
            <span className="mt-1 block font-medium">{next.label}</span>
          </a>
        )}
      </nav>
    </footer>
  );
}

export function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="mt-6 max-w-[68ch] overflow-x-auto rounded-xl ring-1 ring-text/10">
      <table className="w-full text-left text-[15px] leading-6">
        <thead className="bg-panel text-text">
          <tr>
            {head.map((cell) => (
              <th key={cell} className="px-4 py-2.5 font-semibold">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="text-text/85">
          {rows.map((row, index) => (
            <tr key={index} className="border-t border-text/10 align-top">
              {row.map((cell, column) => (
                <td key={column} className="px-4 py-3">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A screenshot, as wide as the text column. */
export function Figure({
  src,
  alt,
  width,
  height,
  caption,
}: {
  src: string;
  alt: string;
  width: number;
  height: number;
  caption?: string;
}) {
  return (
    <figure className="mt-8">
      <Image
        src={src}
        alt={alt}
        width={width}
        height={height}
        sizes="(min-width: 1024px) 760px, 100vw"
        className="w-full rounded-[18px] ring-1 ring-text/10"
      />
      {caption && <figcaption className="mt-3 text-sm text-faint">{caption}</figcaption>}
    </figure>
  );
}

/** A grey panel for one of the homepage's visuals. */
export function Panel({ children, center = false }: { children: ReactNode; center?: boolean }) {
  return (
    <div
      className={`mt-8 rounded-[24px] bg-panel p-3 sm:p-6 ${center ? "grid place-items-center px-4 py-10 sm:py-12" : ""}`}
    >
      {children}
    </div>
  );
}
