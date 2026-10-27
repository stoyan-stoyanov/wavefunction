# Wavefunction website

The landing page, a static Next.js 16 app deployed on Vercel.

```sh
npm install
npm run dev
```

Outbound links (GitHub, download) live in `src/lib/site.ts`. Open Graph URLs use the production domain, `https://www.wavefunction.si`; set `NEXT_PUBLIC_SITE_URL` to use another.

The light hero blends into `public/wavefunction-desktop.png`, the supplied desktop screenshot. Next.js serves responsive, optimized versions through its Image component. The open-source section links directly to GitHub.

The page and social preview use the selected script W mark at `public/wavefunction-mark.svg`, copied from `assets/branding/wavefunction-mark.svg` in the repository root. Site favicons use the same mark on a transparent background.

## Deploy on Vercel

Import the repository and set **Root Directory** to `website`. Vercel detects Next.js; no other settings are needed.
