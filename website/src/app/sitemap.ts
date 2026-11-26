import type { MetadataRoute } from "next";
import { POSTS } from "@/lib/blog";
import { DOC_PAGES } from "@/lib/docs";
import { site } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: site.url },
    ...DOC_PAGES.map(({ path }) => ({ url: `${site.url}${path}` })),
    { url: `${site.url}/blog` },
    ...POSTS.map(({ path, date }) => ({ url: `${site.url}${path}`, lastModified: date })),
  ];
}
