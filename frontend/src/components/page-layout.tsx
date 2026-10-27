import type { ReactNode } from "react";

/** Shared layout for the Connectors and Skills pages. */
export function PageContent({ children }: { children: ReactNode }) {
  return (
    <div data-slot="page-content" className="mx-auto w-full max-w-6xl px-8 py-8">
      {children}
    </div>
  );
}
