// Every outbound link in one place, so launch-day changes are a one-line edit.
const github = "https://github.com/stoyan-stoyanov/wavefunction";

export const site = {
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://www.wavefunction.si",
  // Says what the product is: "wavefunction" alone is a physics term in search results.
  title: "Wavefunction: open-source coding agent for Mac",
  headline: "Minimal by design. Open source down to the prompt.",
  description:
    "A coding agent for your Mac. Keep talking while it works on several tasks at once.",
  github,
  // Redirects to the latest release's DMG (app/download/route.ts).
  download: "/download",
  releases: `${github}/releases/latest`,
  // The latest release's update manifest, which installed apps read too.
  updateManifest: `${github}/releases/latest/download/latest.json`,
  license: `${github}/blob/main/LICENSE`,
  security: `${github}/blob/main/SECURITY.md`,
};
