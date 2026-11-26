// Every outbound link in one place, so launch-day changes are a one-line edit.
const github = "https://github.com/stoyan-stoyanov/wavefunction";

export const site = {
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://www.wavefunction.si",
  // Says what the product is: "wavefunction" alone is a physics term in search results.
  title: "Wavefunction | Open-source coding agent for macOS and Linux",
  headline: "Fast, minimal and private. Open-source agent built for parallel work.",
  // Two lines from tablet width up, run together on phones. A non-breaking hyphen in "Open‑source",
  // so a line never splits it.
  subtitle: [
    "A desktop coding agent for macOS and Linux. Open\u2011source down to the prompt.",
    "Support for local models and ZDR. Zero telemetry.",
  ],
  // For search results and share cards, where it shows without the page around it. Google writes
  // its own snippet from the page when this is too short to say much.
  description:
    "A minimal, open-source coding agent for macOS and Linux. Keep chatting while it works on several tasks at once, with any model, including local ones.",
  github,
  // Redirects to the latest release's DMG (app/download/route.ts).
  download: "/download",
  releases: `${github}/releases/latest`,
  // The latest release's update manifest, which installed apps read too.
  updateManifest: `${github}/releases/latest/download/latest.json`,
  license: `${github}/blob/main/LICENSE`,
  issues: `${github}/issues`,
  // Open issues labelled for newcomers, the first place a contributor should look.
  goodFirstIssues: `${github}/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22`,
  discussions: `${github}/discussions`,
  testReport: `${github}/issues/new?template=test_report.yml`,
  contributing: `${github}/blob/main/CONTRIBUTING.md`,
  bugReport: `${github}/issues/new?template=bug_report.yml`,
  // A post with the homepage link, tagged so visits from it show up as their own source.
  shareOnX: `https://x.com/intent/post?text=${encodeURIComponent(
    "Wavefunction, an open-source coding agent built for parallel work",
  )}&url=${encodeURIComponent("https://www.wavefunction.si/?ref=x-share")}`,
  security: `${github}/blob/main/SECURITY.md`,
};
