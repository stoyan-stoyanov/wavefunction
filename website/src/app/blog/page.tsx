import type { Metadata } from "next";
import { formatDate, POSTS } from "@/lib/blog";

export const metadata: Metadata = {
  title: "Blog | Wavefunction",
  description: "How Wavefunction, the open-source coding agent, is built: the engine, the app and how we measure them.",
  alternates: { canonical: "/blog" },
};

export default function Page() {
  return (
    <div className="mx-auto max-w-[760px]">
      <h1 className="text-[clamp(2.25rem,5vw,3.25rem)] font-semibold leading-[1.05] tracking-[-0.04em]">Blog</h1>
      <p className="mt-5 text-lg leading-relaxed text-soft">
        How Wavefunction is built: the engine, the app and how we measure them.
      </p>
      <ul className="mt-12 border-t border-text/10">
        {POSTS.map((post) => (
          <li key={post.path} className="border-b border-text/10">
            <a href={post.path} className="group block py-8">
              <p className="text-sm text-faint">
                {formatDate(post.date)} · {post.readTime}
              </p>
              <h2 className="mt-2 text-2xl font-semibold tracking-[-0.02em] group-hover:text-accent">{post.title}</h2>
              <p className="mt-3 leading-relaxed text-soft">{post.description}</p>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
