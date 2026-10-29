import { cacheLife } from "next/cache";
import { site } from "@/lib/site";

/** Starts the download of the latest release's DMG, or opens the release page if it can't be found. */
export async function GET() {
  return Response.redirect((await latestDmg()) ?? site.releases, 302);
}

// Read at most every few minutes, so a new release reaches the button without a deploy. The
// update manifest comes from GitHub's download servers, unlike the REST API, which allows
// 60 unauthenticated requests an hour per address and so fails from shared hosting.
async function latestDmg() {
  "use cache";
  cacheLife("minutes");
  try {
    const response = await fetch(site.updateManifest);
    if (!response.ok) return null;
    const { version } = (await response.json()) as { version: string };
    return `${site.github}/releases/download/v${version}/Wavefunction-${version}-macos-arm64.dmg`;
  } catch {
    return null;
  }
}
