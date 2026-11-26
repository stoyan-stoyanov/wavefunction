import type { Metadata } from "next";

// Every docs page, in sidebar order. The sitemap and the previous/next links read this list too.
export const DOCS = [
  {
    group: "Get started",
    pages: [
      { path: "/docs", label: "Overview" },
      { path: "/docs/linux", label: "Linux" },
    ],
  },
  {
    group: "Models",
    pages: [
      { path: "/docs/models", label: "Connect a model" },
      { path: "/docs/ollama", label: "Ollama" },
      { path: "/docs/lm-studio", label: "LM Studio" },
      { path: "/docs/chatgpt", label: "ChatGPT sign-in" },
    ],
  },
  {
    group: "Working with the agent",
    pages: [
      { path: "/docs/parallel-work", label: "Parallel work" },
      { path: "/docs/routines", label: "Scheduled tasks" },
      { path: "/docs/computer-use", label: "Computer use" },
      { path: "/docs/connectors-and-skills", label: "Connectors and skills" },
      { path: "/docs/designs", label: "Designs" },
    ],
  },
  {
    group: "Reference",
    pages: [{ path: "/docs/privacy", label: "Privacy and security" }],
  },
] as const;

type DocPage = (typeof DOCS)[number]["pages"][number];
export type DocPath = DocPage["path"];

export const DOC_PAGES: readonly DocPage[] = DOCS.flatMap(({ pages }): readonly DocPage[] => pages);

/** A docs page's title, search description and canonical URL. The title leads with what people
 *  search for, so it differs from the sidebar label. */
export function docMetadata(path: DocPath, title: string, description: string): Metadata {
  return {
    title: `${title} | Wavefunction docs`,
    description,
    alternates: { canonical: path },
    // A page's own openGraph replaces the root's, image included, so it names the homepage card.
    openGraph: {
      title,
      description,
      url: path,
      siteName: "Wavefunction",
      type: "article",
      images: [{ url: "/opengraph-image", width: 1200, height: 630 }],
    },
  };
}
