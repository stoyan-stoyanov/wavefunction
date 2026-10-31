// Every outbound link in one place, so launch-day changes are a one-line edit.
const github = "https://github.com/stoyan-stoyanov/wavefunction";

export const site = {
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://www.wavefunction.si",
  // Says what the product is: "wavefunction" alone is a physics term in search results.
  title: "Wavefunction: open-source coding agent for Mac",
  headline: "Minimal by design. Built to keep up.",
  description:
    "A fast, open-source coding agent for your Mac. It works on several tasks at once, while you keep the conversation going.",
  github,
  // Redirects to the latest release's DMG (app/download/route.ts).
  download: "/download",
  releases: `${github}/releases/latest`,
  // The latest release's update manifest, which installed apps read too.
  updateManifest: `${github}/releases/latest/download/latest.json`,
  license: `${github}/blob/main/LICENSE`,
  security: `${github}/blob/main/SECURITY.md`,
};
