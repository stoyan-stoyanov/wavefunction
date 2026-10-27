import { cacheLife } from "next/cache";
import { site } from "@/lib/site";

/** Starts the download of the latest release's DMG, or opens the release page if it can't be found. */
export async function GET() {
  return Response.redirect((await latestDmg()) ?? site.releases, 302);
}

// Asked of GitHub at most every few minutes, so a new release reaches the button without a deploy.
async function latestDmg() {
  "use cache";
  cacheLife("minutes");
  try {
    const response = await fetch(site.latestReleaseApi, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "wavefunction.si" },
    });
    if (!response.ok) return null;
    const { assets } = (await response.json()) as {
      assets: { name: string; browser_download_url: string }[];
    };
    return assets.find((asset) => asset.name.endsWith(".dmg"))?.browser_download_url ?? null;
  } catch {
    return null;
  }
}
