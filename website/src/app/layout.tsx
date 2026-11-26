import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import { ConsentBanner, GoogleTag } from "@/components/analytics-consent";
import { DownloadPanel } from "@/components/download-link";
import { site } from "@/lib/site";
import "./globals.css";

const geist = Geist({ variable: "--font-geist", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  metadataBase: new URL(site.url),
  title: site.title,
  description: site.description,
  openGraph: {
    title: site.title,
    description: site.description,
    siteName: "Wavefunction",
    type: "website",
  },
  twitter: { card: "summary_large_image" },
};

export const viewport: Viewport = {
  themeColor: "#ffffff",
  colorScheme: "light",
};

// Marks visitors who can't run the app (anything but a Mac computer; iPads report a Mac with touch
// points) before the page paints, so the static HTML can show their buttons without a flash. The
// installer is macOS-only until a Linux release is published.
const DEVICE_SCRIPT =
  '(function(){try{var n=navigator,p=(n.userAgentData&&n.userAgentData.platform)||n.platform||"";if(!/mac/i.test(p)||n.maxTouchPoints>1)document.documentElement.dataset.download="none"}catch(e){}})()';

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geist.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: DEVICE_SCRIPT }} />
      </head>
      <body className="min-h-dvh font-sans">
        {children}
        <DownloadPanel />
        <ConsentBanner />
        <Analytics />
        <GoogleTag />
      </body>
    </html>
  );
}
