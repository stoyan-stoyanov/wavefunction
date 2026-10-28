// Every outbound link in one place, so launch-day changes are a one-line edit.
const github = "https://github.com/stoyan-stoyanov/wavefunction";

export const site = {
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://www.wavefunction.si",
  headline: "Minimal by design. Built to keep up.",
  description:
    "A fast, open-source coding agent for your Mac. It works on several tasks at once, while you keep the conversation going.",
  github,
  // Redirects to the latest release's DMG (app/download/route.ts).
  download: "/download",
  releases: `${github}/releases/latest`,
  latestReleaseApi: "https://api.github.com/repos/stoyan-stoyanov/wavefunction/releases/latest",
  license: `${github}/blob/main/LICENSE`,
  security: `${github}/blob/main/SECURITY.md`,
};
