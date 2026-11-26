import type { Metadata } from "next";

// Every post, newest first. The blog index and the sitemap read this list.
export const POSTS = [
  {
    path: "/blog/parallel-execution",
    title: "How parallel helpers work in Wavefunction, and how we optimized them",
    description:
      "What happens when you send a second task while the first one is still running: helpers, worktrees, locks, steering and prompt caching, and the measurements behind each fix.",
    date: "2026-11-26",
    readTime: "9 min read",
  },
] as const;

export type PostPath = (typeof POSTS)[number]["path"];

export function postMetadata(path: PostPath): Metadata {
  const post = POSTS.find((entry) => entry.path === path)!;
  return {
    title: `${post.title} | Wavefunction blog`,
    description: post.description,
    alternates: { canonical: path },
    // A page's own openGraph replaces the root's, image included.
    openGraph: {
      title: post.title,
      description: post.description,
      url: path,
      siteName: "Wavefunction",
      type: "article",
      publishedTime: post.date,
      images: [{ url: "/opengraph-image", width: 1200, height: 630 }],
    },
  };
}

export function formatDate(date: string) {
  return new Date(`${date}T12:00:00Z`).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}
