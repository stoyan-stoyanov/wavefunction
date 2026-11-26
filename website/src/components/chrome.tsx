import Image from "next/image";
import type { ReactNode } from "react";
import { CookieSettings } from "@/components/analytics-consent";
import { DownloadLink, StarLink } from "@/components/download-link";
import { AppleIcon, GitHubIcon } from "@/components/icons";
import { site } from "@/lib/site";

// The parts every page shares: the page width, the header, the footer and the main buttons.

export function Container({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mx-auto w-full max-w-[1200px] px-5 sm:px-8 ${className}`}>
      {children}
    </div>
  );
}

function Logo({ href }: { href: string }) {
  return (
    <a
      href={href}
      className="flex items-center gap-2.5 text-base font-semibold tracking-[-0.01em] sm:gap-3 sm:text-[17px]"
    >
      <Image
        src="/wavefunction-mark.svg"
        alt=""
        width={350}
        height={331}
        className="h-auto w-9 shrink-0 sm:w-10"
        priority
      />
      Wavefunction
    </a>
  );
}

export function buttonShape(size: "sm" | "lg") {
  return size === "lg"
    ? "h-12 gap-2.5 rounded-xl px-5 text-[15px]"
    : "h-9 gap-2 rounded-[10px] px-3.5 text-sm";
}

// On a Mac, Download is the main button and Star sits beside it. On devices the app doesn't run
// on (the `no-download` variant), Star takes the main style and comes first, and the header drops
// Download.
export function Download({ size = "lg" }: { size?: "sm" | "lg" }) {
  return (
    <DownloadLink
      className={`inline-flex items-center bg-text font-medium text-white transition-colors hover:bg-text/85 no-download:bg-panel no-download:text-text no-download:hover:bg-panel-raised ${size === "sm" ? "no-download:hidden" : ""} ${buttonShape(size)}`}
    >
      <AppleIcon />
      {size === "lg" ? "Download for macOS" : "Download"}
    </DownloadLink>
  );
}

export function Star({ size = "lg", place }: { size?: "sm" | "lg"; place: string }) {
  return (
    <StarLink
      place={place}
      className={`inline-flex items-center bg-panel font-medium transition-colors hover:bg-panel-raised no-download:order-first no-download:bg-text no-download:text-white no-download:hover:bg-text/85 ${buttonShape(size)}`}
    >
      <GitHubIcon />
      {size === "lg" ? "Star on GitHub" : "Star"}
    </StarLink>
  );
}

// The section links point at the homepage, so they work from the docs too; on the homepage they
// only scroll.
// The homepage sections only show from laptop width, where there's room for all six.
const SECTIONS = [
  { href: "/#why", label: "Why Wavefunction", wide: true },
  { href: "/#how", label: "How it works", wide: true },
  { href: "/#models", label: "Models", wide: true },
  { href: "/#privacy", label: "Privacy", wide: true },
  { href: "/docs", label: "Docs", wide: false },
  { href: "/blog", label: "Blog", wide: false },
];

export function Nav({ logoHref = "/" }: { logoHref?: string }) {
  return (
    <header className="sticky top-0 z-40 bg-bg/85 backdrop-blur-sm">
      <Container className="flex h-16 items-center gap-3 sm:gap-8">
        <Logo href={logoHref} />
        <nav
          className="ml-auto hidden items-center gap-7 text-sm text-soft md:flex"
          aria-label="Sections"
        >
          {SECTIONS.map(({ href, label, wide }) => (
            <a
              key={href}
              href={href}
              className={`whitespace-nowrap transition-colors hover:text-text ${wide ? "hidden lg:inline" : ""}`}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2 md:ml-0">
          <Star size="sm" place="header" />
          <Download size="sm" />
        </div>
      </Container>
    </header>
  );
}

export function Footer({ logoHref = "/" }: { logoHref?: string }) {
  return (
    <footer className="pb-12 pt-6 text-sm text-soft">
      <Container className="flex flex-col items-start justify-between gap-6 sm:flex-row sm:items-center">
        <Logo href={logoHref} />
        <nav className="flex flex-wrap gap-x-6 gap-y-2" aria-label="Project">
          <a href="/docs" className="transition-colors hover:text-text">
            Docs
          </a>
          <a href="/blog" className="transition-colors hover:text-text">
            Blog
          </a>
          <a href={site.github} className="transition-colors hover:text-text">
            GitHub
          </a>
          <a href={site.license} className="transition-colors hover:text-text">
            MIT License
          </a>
          <a href={site.security} className="transition-colors hover:text-text">
            Security
          </a>
          <CookieSettings className="transition-colors hover:text-text" />
        </nav>
      </Container>
    </footer>
  );
}
