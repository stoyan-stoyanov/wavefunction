import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import { site } from "@/lib/site";

export const alt = `Wavefunction, coding agent for Mac: "${site.headline}" above the app window.`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const logo = await readFile(join(process.cwd(), "public/wavefunction-mark.svg"));
const mark = `data:image/svg+xml;base64,${logo.toString("base64")}`;
const poster = await readFile(join(process.cwd(), "public/wavefunction-hero-poster.jpg"));
const app = `data:image/jpeg;base64,${poster.toString("base64")}`;

export default async function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          paddingTop: 56,
          background: "#ffffff",
          color: "#1b2638",
          position: "relative",
        }}
      >
        {/* The hero frame, scaled so the app window starts below the headline and runs off the bottom. */}
        <img src={app} width={1500} height={813} alt="" style={{ position: "absolute", left: -150, top: 150 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 28 }}>
          <img src={mark} width={46} height={43.5} alt="" />
          Wavefunction
          {/* The headline doesn't say what the app is, and on a share card nothing else does. */}
          <div style={{ width: 1.5, height: 28, background: "#c4ccd8", margin: "0 6px" }} />
          <div style={{ color: "#586578" }}>Coding agent for Mac</div>
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            marginTop: 26,
            fontSize: 56,
            fontWeight: 600,
            lineHeight: 1.05,
            letterSpacing: -2,
          }}
        >
          {/* One sentence per line, so "open source" never splits. */}
          {site.headline.split(/(?<=\.) /).map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      </div>
    ),
    size,
  );
}
