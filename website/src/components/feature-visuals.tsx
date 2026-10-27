import type { CSSProperties } from "react";
import { GitHubIcon, SandboxIcon } from "@/components/icons";

export function Mark({ name, className = "size-6" }: { name: string; className?: string }) {
  return <span className={`mark ${className}`} style={{ "--mark": `url(/logos/${name}.svg)` } as CSSProperties} aria-hidden />;
}

const CONNECTORS = [
  "github", "linear", "notion", "stripe", "sentry", "vercel", "atlassian", "datadog",
  "netlify", "neon", "asana", "dropbox", "postman", "hubspot", "snowflake", "n8n",
];

export function ConnectorsVisual() {
  return (
    <div
      className="grid w-full grid-cols-4 place-items-center gap-y-9 py-2 text-text"
      style={{ maskImage: "radial-gradient(75% 85% at 50% 50%, black 40%, transparent 100%)" }}
      aria-hidden
    >
      {CONNECTORS.map((name) => (
        <Mark key={name} name={name} className="size-7 opacity-80" />
      ))}
    </div>
  );
}

/** A typical agent IDE: activity bar, explorer, tabs, editor, chat, terminal, and status bar. */
export function BusyIdeVisual() {
  const bar = "rounded-full bg-text/15";
  return (
    <div className="grid h-[230px] w-full grid-cols-[14px_64px_minmax(0,1fr)_92px] grid-rows-[12px_1fr_52px_8px] gap-1 overflow-hidden rounded-xl bg-panel-raised p-1.5 ring-1 ring-text/10" aria-hidden>
      <div className="col-span-4 flex min-w-0 gap-1">
        {[38, 52, 30, 46, 40, 34].map((width, index) => (
          <span key={index} className="h-full rounded-[3px] bg-text/10" style={{ width }} />
        ))}
      </div>
      <div className="row-span-2 flex flex-col gap-1.5 rounded-[4px] bg-white/60 p-[3px]">
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <span key={index} className="aspect-square rounded-[2px] bg-text/20" />
        ))}
      </div>
      <div className="row-span-2 space-y-1.5 rounded-[4px] bg-white/60 p-2">
        {[80, 60, 70, 50, 75, 55, 65, 45, 70, 60].map((width, index) => (
          <span key={index} className={`block h-1 ${bar}`} style={{ width: `${width}%`, marginLeft: index % 3 ? 6 : 0 }} />
        ))}
      </div>
      <div className="overflow-hidden rounded-[4px] bg-white/80 p-2.5">
        <div className="space-y-1.5">
          {[70, 85, 40, 90, 60, 75, 30, 80, 55].map((width, index) => (
            <span key={index} className={`block h-1 ${bar}`} style={{ width: `${width}%`, marginLeft: [0, 8, 8, 16, 16, 8, 0, 8, 8][index] }} />
          ))}
        </div>
      </div>
      <div className="row-span-2 flex flex-col gap-1.5 rounded-[4px] bg-white/60 p-2">
        <span className="ml-auto h-4 w-4/5 rounded-[4px] bg-text/15" />
        <span className={`block h-1 w-full ${bar}`} />
        <span className={`block h-1 w-3/4 ${bar}`} />
        <span className="h-6 w-full rounded-[4px] bg-text/10" />
        <span className={`block h-1 w-5/6 ${bar}`} />
        <span className="mt-auto h-5 w-full rounded-[4px] bg-text/15" />
      </div>
      <div className="space-y-1.5 rounded-[4px] bg-white/60 p-2">
        {[60, 85, 45].map((width, index) => (
          <span key={index} className={`block h-1 ${bar}`} style={{ width: `${width}%` }} />
        ))}
      </div>
      <div className="col-span-4 rounded-[3px] bg-text/10" />
    </div>
  );
}

/** Wavefunction: projects and chats, the conversation, and the composer. */
export function CleanAppVisual() {
  return (
    <div className="grid h-[230px] w-full grid-cols-[72px_minmax(0,1fr)] gap-1 rounded-xl bg-white p-1.5 ring-1 ring-text/10" aria-hidden>
      <div className="space-y-2 rounded-[4px] bg-panel-raised p-2.5">
        <span className="block h-1.5 w-4/5 rounded-full bg-text/30" />
        <span className="block h-1 w-3/5 rounded-full bg-text/15" />
        <span className="block h-3 w-full rounded-[3px] bg-accent/20" />
        <span className="block h-1 w-2/3 rounded-full bg-text/15" />
        <span className="block h-1 w-1/2 rounded-full bg-text/15" />
      </div>
      <div className="flex flex-col justify-end gap-2.5 p-3">
        <span className="ml-auto h-5 w-2/5 rounded-[6px] bg-accent/15" />
        <span className="block h-1 w-3/4 rounded-full bg-text/25" />
        <span className="block h-1 w-1/2 rounded-full bg-text/25" />
        <span className="mt-2 h-10 w-full rounded-[8px] bg-panel-raised" />
      </div>
    </div>
  );
}

export function PrivacyVisual() {
  return (
    <div className="reveal grid grid-cols-3 items-stretch gap-2 sm:gap-6">
      <div className="flex min-w-0 flex-col items-center rounded-[20px] bg-panel px-2 py-7 text-center sm:rounded-[24px] sm:px-3 sm:py-12">
        <svg viewBox="0 0 32 32" className="size-10 text-accent sm:size-16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="6" y="5" width="20" height="16" rx="2" />
          <path d="M6 21l-3 5h26l-3-5M13 26h6" />
          <path d="M12 11l-2 2 2 2m8-4 2 2-2 2m-3-5-2 6" />
        </svg>
        <h3 className="mt-5 text-[13px] font-medium tracking-[-0.02em] sm:mt-6 sm:text-lg">Local models</h3>
      </div>
      <div className="flex min-w-0 flex-col items-center rounded-[20px] bg-panel px-2 py-7 text-center sm:rounded-[24px] sm:px-3 sm:py-12">
        <SandboxIcon className="size-10 text-accent sm:size-16" />
        <h3 className="mt-5 text-[13px] font-medium tracking-[-0.02em] sm:mt-6 sm:text-lg">Your provider</h3>
      </div>
      <div className="flex min-w-0 flex-col items-center rounded-[20px] bg-panel px-2 py-7 text-center sm:rounded-[24px] sm:px-3 sm:py-12">
        <svg viewBox="0 0 32 32" className="size-10 text-accent sm:size-16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="6" y="13" width="20" height="15" rx="3" />
          <path d="M10 13V9a6 6 0 0 1 12 0v4" />
          <circle cx="16" cy="20" r="1.5" />
          <path d="M16 21.5V24" />
        </svg>
        <h3 className="mt-5 text-[13px] font-medium tracking-[-0.02em] sm:mt-6 sm:text-lg"><abbr title="Zero data retention" className="no-underline">ZDR</abbr></h3>
      </div>
    </div>
  );
}

export function RepositoryVisual() {
  return (
    <figure className="reveal min-w-0 overflow-hidden rounded-xl border border-[#d1d9e0] bg-white text-[#1f2328] shadow-[0_24px_80px_-36px_rgb(27_38_56/0.22)]" role="img" aria-label="GitHub repository preview showing Wavefunction's source folders, README, and MIT license.">
      <div aria-hidden>
        <div className="border-b border-[#d1d9e0] bg-[#f6f8fa] px-4 pt-5 sm:px-6">
          <div className="flex min-w-0 items-center gap-3 text-sm">
            <GitHubIcon className="size-7 shrink-0" />
            <span className="hidden text-[#59636e] sm:inline">stoyan-stoyanov</span>
            <span className="hidden text-[#59636e] sm:inline">/</span>
            <span className="truncate font-semibold">wavefunction</span>
          </div>
          <div className="mt-5 flex gap-5 text-xs sm:gap-7 sm:text-sm">
            <span className="flex items-center gap-2 border-b-2 border-[#fd8c73] pb-3 font-semibold">
              <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m5 4-4 4 4 4m6-8 4 4-4 4" /></svg>
              Code
            </span>
            <span className="flex items-center gap-2 pb-3">
              <svg viewBox="0 0 16 16" className="hidden size-4 sm:block" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8" cy="8" r="6" /><circle cx="8" cy="8" r="1" fill="currentColor" stroke="none" /></svg>
              Issues
            </span>
            <span className="flex items-center gap-2 pb-3">
              <svg viewBox="0 0 16 16" className="hidden size-4 sm:block" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="4" cy="3" r="1.5" /><circle cx="4" cy="13" r="1.5" /><circle cx="12" cy="13" r="1.5" /><path d="M4 4.5v7M12 11.5V6a3 3 0 0 0-3-3H8m2-2L8 3l2 2" /></svg>
              Pull requests
            </span>
          </div>
        </div>
        <div className="grid gap-7 p-4 sm:p-6 md:grid-cols-[minmax(0,1fr)_180px]">
          <div className="min-w-0">
            <div className="mb-4 flex items-center justify-between gap-3 text-xs">
              <span className="flex items-center gap-2 rounded-md border border-[#d1d9e0] bg-[#f6f8fa] px-3 py-1.5 font-medium">
                <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.4"><circle cx="4" cy="3" r="1.5" /><circle cx="4" cy="13" r="1.5" /><circle cx="12" cy="3" r="1.5" /><path d="M4 4.5v7m8-7V6a3 3 0 0 1-3 3H4" /></svg>
                main
                <span className="ml-2 text-[10px]">▾</span>
              </span>
              <span className="flex items-center gap-2 rounded-md border border-[#1f2328]/15 bg-[#1f883d] px-3 py-1.5 font-semibold text-white">
                <span className="font-mono">&lt;&gt;</span> Code <span className="text-[10px]">▾</span>
              </span>
            </div>
            <div className="overflow-hidden rounded-md border border-[#d1d9e0]">
              <div className="flex items-center gap-2.5 border-b border-[#d1d9e0] bg-[#f6f8fa] px-3 py-3 text-xs">
                <GitHubIcon className="size-5" />
                <span className="font-semibold">wavefunction</span>
                <span className="ml-auto h-1.5 w-12 rounded-full bg-[#d1d9e0]" />
              </div>
              <ul className="divide-y divide-[#d1d9e0]/70 text-[13px]">
                {[
                  { name: ".agents", folder: true },
                  { name: "backend", folder: true },
                  { name: "frontend", folder: true },
                  { name: "skills", folder: true },
                  { name: "src-tauri", folder: true },
                  { name: "AGENTS.md", folder: false },
                  { name: "LICENSE", folder: false },
                  { name: "README.md", folder: false },
                ].map(({ name, folder }, index) => (
                  <li key={name} className="flex items-center gap-2.5 px-3 py-2.5">
                    <svg viewBox="0 0 16 16" className={`size-4 shrink-0 ${folder ? "text-[#54aeff]" : "text-[#59636e]"}`} fill={folder ? "currentColor" : "none"} stroke={folder ? "none" : "currentColor"} strokeWidth="1.2">
                      {folder ? <path d="M1.5 2h4l1.6 2h7.4c.8 0 1.5.7 1.5 1.5v7c0 .8-.7 1.5-1.5 1.5h-13C.7 14 0 13.3 0 12.5v-9C0 2.7.7 2 1.5 2Z" /> : <path d="M3.5 1.5h6l3 3v10h-9zM9.5 1.5v3h3" />}
                    </svg>
                    <span className="min-w-0 flex-1 truncate">{name}</span>
                    <span className="hidden h-1.5 w-1/4 rounded-full bg-[#d1d9e0]/60 sm:block" style={{ width: `${[28, 36, 24, 32][index % 4]}%` }} />
                    <span className="ml-6 hidden h-1.5 w-10 rounded-full bg-[#d1d9e0]/50 sm:block" />
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="hidden border-l border-[#d1d9e0]/70 pl-6 md:block">
            <div className="mb-5 text-sm font-semibold">About</div>
            <div className="flex items-center gap-2 text-xs text-[#59636e]">
              <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.2"><path d="M8 2v12m-4 0h8M2 5h12M4 5 1.5 10h5L4 5Zm8 0L9.5 10h5L12 5Z" /></svg>
              MIT license
            </div>
            <div className="mt-4 flex items-center gap-2 text-xs text-[#59636e]">
              <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.2"><path d="M8 3v11M1 2h4a3 3 0 0 1 3 3 3 3 0 0 1 3-3h4v10h-4a3 3 0 0 0-3 2 3 3 0 0 0-3-2H1Z" /></svg>
              Readme
            </div>
          </div>
        </div>
      </div>
    </figure>
  );
}

// Illustrative daily usage, in thousands of tokens; the fourteen days total 1.28M.
const EXAMPLE_TOKENS = [52, 68, 84, 60, 102, 78, 96, 114, 72, 88, 126, 104, 92, 144];

export function StatsVisual() {
  return (
    <figure className="reveal min-w-0 rounded-[28px] bg-panel p-7 sm:p-10">
      <figcaption className="flex items-center justify-between gap-4">
        <span className="text-[17px] font-medium">Usage</span>
        <span className="text-xs text-faint">Example data</span>
      </figcaption>
      <dl className="mt-9 grid grid-cols-2 gap-5">
        <div>
          <dt className="text-sm text-soft">Total tokens</dt>
          <dd className="mt-2 text-[clamp(1.75rem,3.5vw,2.75rem)] font-medium leading-none tracking-[-0.04em] tabular-nums">1.28M</dd>
        </div>
        <div>
          <dt className="text-sm text-soft">Reported cost</dt>
          <dd className="mt-2 text-[clamp(1.75rem,3.5vw,2.75rem)] font-medium leading-none tracking-[-0.04em] tabular-nums">$6.42</dd>
        </div>
      </dl>
      <div className="mt-8 flex items-center justify-between gap-4 border-t border-text/10 pt-5 text-sm">
        <span className="text-soft">Cached input</span>
        <span className="font-medium tabular-nums">812K tokens</span>
      </div>
      <div className="mt-8" aria-hidden>
        <div className="flex h-32 items-end gap-1.5 sm:gap-2">
          {EXAMPLE_TOKENS.map((tokens, index) => (
            <span
              key={index}
              className="flex-1 rounded-t-[4px] bg-accent/40"
              style={{ height: `${(tokens / 144) * 100}%` }}
            />
          ))}
        </div>
        <div className="mt-3 flex justify-between gap-4 text-xs text-faint">
          <span>14 days ago</span>
          <span>Today</span>
        </div>
      </div>
      <p className="sr-only">An example of daily token usage over fourteen days, totaling 1.28 million tokens.</p>
    </figure>
  );
}

const SETUP = [
  { depth: 0, name: "acme-dashboard/" },
  { depth: 1, name: "AGENTS.md", read: true },
  { depth: 1, name: ".agents/skills/" },
  { depth: 2, name: "release/SKILL.md", read: true },
  { depth: 2, name: "migrations/SKILL.md", read: true },
  { depth: 1, name: "src/" },
  { depth: 1, name: "package.json" },
];

/** A repository as the agent sees it: its AGENTS.md and skills are picked up as they are. */
export function SetupVisual() {
  return (
    <div className="w-full max-w-[320px] rounded-xl bg-white px-4 py-3.5 font-mono text-[12px] leading-7 shadow-[0_16px_40px_-20px_rgb(27_38_56/0.18)]" aria-hidden>
      {SETUP.map((row) => (
        <div key={row.name} className="flex items-center" style={{ paddingLeft: row.depth * 16 }}>
          <span className={row.read ? "text-text" : "text-soft"}>{row.name}</span>
          {row.read && (
            <svg viewBox="0 0 16 16" className="ml-auto size-3.5 text-done" aria-hidden>
              <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </div>
      ))}
    </div>
  );
}
