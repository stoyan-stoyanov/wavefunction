// Every outbound link in one place, so launch-day changes are a one-line edit.
const github = "https://github.com/stoyan-stoyanov/wavefunction";

export const site = {
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://www.wavefunction.si",
  // Says what the product is: "wavefunction" alone is a physics term in search results.
  title: "Wavefunction | Open-source coding agent for Mac",
  headline: "Minimal by design. Open source down to the prompt.",
  subtitle: "A coding agent for your Mac. Keep talking while it works on several tasks at once.",
  // For search results and share cards, where it shows without the page around it. Google writes
  // its own snippet from the page when this is too short to say much.
  description:
    "A minimal, open-source coding agent for your Mac. Keep talking while it works on several tasks at once, with any model, including local ones.",
  github,
  // Redirects to the latest release's DMG (app/download/route.ts).
  download: "/download",
  releases: `${github}/releases/latest`,
  // The latest release's update manifest, which installed apps read too.
  updateManifest: `${github}/releases/latest/download/latest.json`,
  license: `${github}/blob/main/LICENSE`,
  security: `${github}/blob/main/SECURITY.md`,
};
