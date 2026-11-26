<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

`next dev` can keep serving an old `globals.css`, and old `/_next/image` copies of a replaced file. If a change doesn't show, stop the server, delete `.next/dev` and start it again.

Web Analytics is on through `<Analytics />` in the root layout. On the Pro plan every page view is a billed event, and UTM breakdowns need the Web Analytics Plus add-on. Instead, links we post carry a `?ref=` tag, and each Download click sends a `Download` event whose `source` is that tag, else the referring site, else `direct`; each Star on GitHub click sends a `Star` event with the same `source` and the button's `place` (`components/download-link.tsx`).

Google Analytics (tag `GTM-55MJR4N3`, in the same Google account as the Search Console domain property) sets cookies, so `components/analytics-consent.tsx` loads it only after consent for visitors whose time zone is in the EU, EEA, UK or Switzerland, and right away for everyone else. Nothing about it is in the static HTML.

The docs live in `app/docs`, one folder per guide, and `lib/docs.ts` lists them for the sidebar, the sitemap and the previous/next links, so a new guide needs an entry there. A page's own `openGraph` replaces the root's, image included, so `docMetadata` names `/opengraph-image` itself.
