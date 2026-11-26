"use client";

import { usePathname } from "next/navigation";
import { DOCS } from "@/lib/docs";

function Links({ current }: { current: string }) {
  return (
    <div className="space-y-7">
      {DOCS.map(({ group, pages }) => (
        <div key={group}>
          <p className="px-3 text-xs font-semibold uppercase tracking-[0.08em] text-text">{group}</p>
          <ul className="mt-2 space-y-0.5">
            {pages.map(({ path, label }) => (
              <li key={path}>
                <a
                  href={path}
                  aria-current={path === current ? "page" : undefined}
                  className="block rounded-lg px-3 py-1.5 text-[15px] text-soft transition-colors hover:bg-panel hover:text-text aria-[current=page]:bg-panel aria-[current=page]:font-medium aria-[current=page]:text-text"
                >
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** The docs' table of contents: a column beside the page on wide screens, a menu above it on
 *  narrow ones. */
export function DocsSidebar() {
  const current = usePathname();
  return (
    <>
      <nav aria-label="Docs" className="sticky top-24 hidden max-h-[calc(100dvh-7rem)] overflow-y-auto pb-10 lg:block">
        <Links current={current} />
      </nav>
      <details className="group mb-8 rounded-xl bg-panel lg:hidden">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-[15px] font-medium [&::-webkit-details-marker]:hidden">
          Docs menu
          <svg viewBox="0 0 20 20" className="size-4 text-faint transition-transform group-open:rotate-180" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden>
            <path d="M5 8l5 5 5-5" />
          </svg>
        </summary>
        <nav aria-label="Docs" className="px-1 pb-4">
          <Links current={current} />
        </nav>
      </details>
    </>
  );
}
