import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import { site } from "@/lib/site";

export const alt = `Wavefunction: ${site.headline}`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const logo = await readFile(join(process.cwd(), "public/wavefunction-mark.svg"));
const source = `data:image/svg+xml;base64,${logo.toString("base64")}`;

export default async function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 80,
          background: "radial-gradient(70% 90% at 85% 10%, #e2eaf5 0%, #ffffff 70%)",
          color: "#1b2638",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 34 }}>
          <img src={source} width={64} height={60.5} alt="" />
          Wavefunction
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div style={{ fontSize: 62, fontWeight: 600, lineHeight: 1.05, letterSpacing: -2, maxWidth: 1000 }}>
            {site.headline}
          </div>
          <div style={{ fontSize: 30, color: "#586578" }}>An open-source Mac app with parallel subagents and local models.</div>
        </div>
      </div>
    ),
    size,
  );
}
