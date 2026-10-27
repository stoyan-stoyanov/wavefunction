// Every outbound link in one place, so launch-day changes are a one-line edit.
const github = "https://github.com/stoyan-stoyanov/wavefunction";

export const site = {
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://www.wavefunction.si",
  headline: "Minimal by design. Built to keep up.",
  description:
    "A fast, open-source coding agent for your Mac. Keep the conversation going while it works on several tasks at once.",
  github,
  download: `${github}/releases/latest`,
  license: `${github}/blob/main/LICENSE`,
  security: `${github}/blob/main/SECURITY.md`,
};
