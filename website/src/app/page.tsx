import type { Metadata } from "next";
import Image from "next/image";
import type { ReactNode } from "react";
import {
  BusyIdeVisual,
  CleanAppVisual,
  ConnectorsVisual,
  ContributeVisual,
  Mark,
  PrivacyVisual,
  RepositoryVisual,
  RoutinesVisual,
  SetupVisual,
} from "@/components/feature-visuals";
import { ComputerVisual } from "@/components/computer-visual";
import { HeroVideo } from "@/components/hero-video";
import { buttonShape, Container, Download, Footer, Nav, Star } from "@/components/chrome";
import { StarLink } from "@/components/download-link";
import { BranchIcon, FlaskIcon, GitHubIcon, MegaphoneIcon, StarIcon } from "@/components/icons";
import { Timeline } from "@/components/timeline";
import { WaveField } from "@/components/wave-field";
import { site } from "@/lib/site";

export const ensureStatic = "navigation";

// Here rather than in the layout, so the 404 page doesn't claim the homepage as its canonical URL.
export const metadata: Metadata = { alternates: { canonical: "/" } };

// Links the name to the product for search engines. Google also needs a real rating or review
// before it shows this as a rich result, so don't add one until there is something to cite.
const APPLICATION_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Wavefunction",
  description: site.description,
  url: site.url,
  downloadUrl: `${site.url}${site.download}`,
  applicationCategory: "DeveloperApplication",
  operatingSystem: "macOS, Linux",
  license: site.license,
  sameAs: site.github,
  offers: { "@type": "Offer", price: 0, priceCurrency: "USD" },
};

// Google shows this name above the result instead of the bare domain.
const WEBSITE_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: "Wavefunction",
  url: site.url,
};

// Fade the hero waves into the page below and soften them behind the headline.
const HERO_MASK = {
  maskImage:
    "linear-gradient(to bottom, black 40%, transparent 92%), linear-gradient(to right, rgb(0 0 0 / 0.35), black 55%)",
  maskComposite: "intersect",
  WebkitMaskImage:
    "linear-gradient(to bottom, black 40%, transparent 92%), linear-gradient(to right, rgb(0 0 0 / 0.35), black 55%)",
  WebkitMaskComposite: "source-in",
} as const;

export default function Home() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify([WEBSITE_JSON_LD, APPLICATION_JSON_LD]).replace(/</g, "\\u003c"),
        }}
      />
      <Nav logoHref="#top" />
      <main>
        <Hero />
        <WhyWavefunction />
        <ParallelWorkflows />
        <Minimal />
        <ComputerUse />
        <Routines />
        <Providers />
        <Privacy />
        <OpenSource />
        <Stats />
        <Extras />
        <Designs />
        <Contribute />
        <FAQ />
        <Closing />
      </main>
      <Footer logoHref="#top" />
    </>
  );
}

function Card({
  visual,
  title,
  children,
}: {
  visual: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="reveal flex flex-col justify-between gap-12 overflow-hidden rounded-[28px] bg-panel p-7 sm:p-10">
      <div className="flex min-h-[200px] items-center justify-center">
        {visual}
      </div>
      <div className="max-w-[40ch]">
        <h3 className="text-2xl font-medium tracking-[-0.025em]">{title}</h3>
        <p className="mt-3 text-[17px] leading-relaxed text-soft">{children}</p>
      </div>
    </div>
  );
}

function Screenshot({
  src,
  alt,
  width,
  height,
}: {
  src: string;
  alt: string;
  width: number;
  height: number;
}) {
  return (
    <Image
      src={src}
      alt={alt}
      width={width}
      height={height}
      sizes="(min-width: 1200px) 1136px, 100vw"
      className="reveal w-full rounded-[20px] sm:rounded-[28px]"
    />
  );
}

function Hero() {
  return (
    <section id="top" className="relative -mt-16 overflow-x-clip bg-bg pt-16">
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-[720px] sm:h-[840px]"
        style={HERO_MASK}
        aria-hidden
      >
        <WaveField className="h-full w-full" center={0.37} intensity={1} />
      </div>
      <Container className="relative flex flex-col items-center pt-14 text-center sm:pt-24">
        <h1 className="enter max-w-[19ch] text-balance text-[clamp(2.3rem,6.4vw,5.3rem)] font-semibold leading-[1.02] tracking-[-0.045em] [animation-delay:80ms]">
          {/* One sentence per line, so phones never break mid-phrase. */}
          {site.headline.split(/(?<=\.) /).map((line) => (
            <span key={line} className="block">
              {line}{" "}
            </span>
          ))}
        </h1>
        <p className="enter mt-6 max-w-[48rem] text-lg leading-relaxed text-soft [animation-delay:160ms] sm:text-xl">
          {site.subtitle.map((line) => (
            <span key={line} className="sm:block">
              {line}{" "}
            </span>
          ))}
        </p>
        <div className="enter mt-10 flex flex-wrap justify-center gap-3 [animation-delay:240ms]">
          <Download />
          <Star place="hero" />
        </div>
        <p className="enter mt-5 text-sm text-faint [animation-delay:300ms]">
          Free and MIT licensed. Use local models, your own API keys or a
          ChatGPT sign-in.
        </p>
      </Container>
      <figure className="hero-desktop relative mt-6 overflow-hidden sm:mt-0">
        <HeroVideo />
      </figure>
    </section>
  );
}

function WhyWavefunction() {
  return (
    <section id="why" className="scroll-mt-16 pt-32 sm:pt-44">
      <Container>
        <h2 className="reveal text-[clamp(2rem,4.4vw,3.5rem)] font-medium leading-[1.08] tracking-[-0.035em]">
          Why Wavefunction
        </h2>
        <ol className="mt-12 grid gap-10 md:grid-cols-3 md:gap-12">
          {[
            {
              title: "Agent apps keep growing",
              description:
                "Coding agents are turning into super apps, adding more with every release. Wavefunction goes the other way: less is more, simpler is better, and building comes first.",
            },
            {
              title: "Free to choose",
              description:
                "Pick any model and switch whenever you like. Run models locally or require zero data retention. You’re never tied to one provider’s plans or limits.",
            },
            {
              title: "Open source, on the desktop",
              description:
                "Most open-source agents shine in the terminal. Wavefunction is built to be a great desktop app: minimal, considered and fast, and open source down to the prompt.",
            },
          ].map(({ title, description }, index) => (
            <li key={title} className="reveal border-t border-text/15 pt-6">
              <span className="font-mono text-xs text-faint" aria-hidden>
                0{index + 1}
              </span>
              <h3 className="mt-5 text-xl font-medium tracking-[-0.02em]">
                {title}
              </h3>
              <p className="mt-3 max-w-[35ch] text-[16px] leading-relaxed text-soft">
                {description}
              </p>
            </li>
          ))}
        </ol>
      </Container>
    </section>
  );
}

function ParallelWorkflows() {
  return (
    <section id="how" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal max-w-[24ch] text-[clamp(2rem,4.4vw,3.5rem)] font-semibold leading-[1.08] tracking-[-0.035em]">
          Optimized for parallel workflows
        </h2>
        <p className="reveal mt-7 max-w-[40rem] text-lg leading-relaxed text-soft sm:text-xl">
          Keep the conversation going. Give it a task, then another, and
          another. Wavefunction splits the work across subagents, steers them as
          your plans change, and brings their work together.
        </p>
        <div className="reveal mt-16 rounded-[28px] bg-panel p-3 sm:p-8 lg:p-10">
          <Timeline />
        </div>
        <ul className="mt-14 grid gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
          {[
            {
              title: "The conversation keeps flowing",
              description:
                "Send the next task while the last one runs. Each new piece of work that can run on its own gets a subagent, and nothing waits its turn.",
            },
            {
              title: "Parallel without collisions",
              description:
                "Every chat starts on its own branch, and subagents that could touch the same files each get a worktree. Wavefunction reviews their changes and brings them together.",
            },
            {
              title: "Steer while it works",
              description:
                "Change your mind mid-task. Your message reaches the subagent already on it, so it adjusts without starting over.",
            },
            {
              title: "Fork or rewind",
              description:
                "Start a new chat from any message, or put the code and the conversation back to how they were.",
            },
          ].map(({ title, description }) => (
            <li key={title} className="reveal border-t border-text/15 pt-6">
              <h3 className="text-[17px] font-medium">{title}</h3>
              <p className="mt-3 text-[16px] leading-relaxed text-soft">
                {description}
              </p>
            </li>
          ))}
        </ul>
      </Container>
    </section>
  );
}

function ComputerUse() {
  return (
    <section id="computer-use" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal max-w-[24ch] text-[clamp(2rem,4.4vw,3.5rem)] font-semibold leading-[1.08] tracking-[-0.035em]">
          Computer use that leaves your computer alone
        </h2>
        <p className="reveal mt-7 max-w-[40rem] text-lg leading-relaxed text-soft sm:text-xl">
          Other agents take over your mouse, keyboard and screen to get things
          done. Wavefunction gets a desktop of its own, so you keep working
          while it clicks. Watch it, or take control at any time.
        </p>
        <div className="mt-16 grid gap-14 lg:grid-cols-[1.25fr_0.75fr] lg:items-center lg:gap-16">
          <ComputerVisual />
          <ul className="grid gap-x-10 gap-y-10 md:grid-cols-3 lg:grid-cols-1 lg:gap-y-8">
            {[
              {
                title: "Your computer stays yours",
                description:
                  "It never moves your pointer, types on your keyboard or records your screen. Its desktop is a local Docker container with its own browser and files, and a file reaches your computer only when the agent hands it over, to a folder you allowed.",
              },
              {
                title: "Secure sign-ins",
                description:
                  "When a site needs your login, you sign in privately. The agent pauses and never sees what you type, and the chat stays signed in for next time.",
              },
              {
                title: "Files in and out",
                description:
                  "Downloads come to your computer in the folder you choose, and files you add are ready to upload.",
              },
            ].map(({ title, description }) => (
              <li key={title} className="reveal border-t border-text/15 pt-6">
                <h3 className="text-[17px] font-medium">{title}</h3>
                <p className="mt-3 text-[16px] leading-relaxed text-soft">
                  {description}
                </p>
              </li>
            ))}
          </ul>
        </div>
        <p className="reveal mt-10 text-sm text-faint">
          Needs Docker Desktop and a model that can read images. macOS only for now.
        </p>
      </Container>
    </section>
  );
}

function Routines() {
  return (
    <section id="routines" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal max-w-[24ch] text-[clamp(2rem,4.4vw,3.5rem)] font-semibold leading-[1.08] tracking-[-0.035em]">
          Scheduled tasks that remember the last run
        </h2>
        <p className="reveal mt-7 max-w-[40rem] text-lg leading-relaxed text-soft sm:text-xl">
          Ask a chat to do something every few minutes, every morning or once
          next week. Wavefunction turns it into a routine: a chat of its own
          that gets the same instruction on schedule.
        </p>
        <div className="mt-16 grid gap-14 lg:grid-cols-[1.25fr_0.75fr] lg:items-center lg:gap-16">
          <div className="reveal grid place-items-center rounded-[28px] bg-panel px-4 py-12 sm:px-8 sm:py-16">
            <RoutinesVisual />
          </div>
          <ul className="grid gap-x-10 gap-y-10 md:grid-cols-3 lg:grid-cols-1 lg:gap-y-8">
            {[
              {
                title: "Set up in plain words",
                description:
                  "Say “every weekday at 9, review yesterday’s merged pull requests.” The routine gets the model and access of the chat you asked, and you can change its instruction or schedule in its settings.",
              },
              {
                title: "Picks up where it left off",
                description:
                  "Every run lands in the same chat, so it knows what it found last time.",
              },
              {
                title: "Reports where you want",
                description:
                  "Send each finished run to another chat, which picks it up from there.",
              },
            ].map(({ title, description }) => (
              <li key={title} className="reveal border-t border-text/15 pt-6">
                <h3 className="text-[17px] font-medium">{title}</h3>
                <p className="mt-3 text-[16px] leading-relaxed text-soft">
                  {description}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </Container>
    </section>
  );
}

// The providers the app's model settings offer, and how each one connects.
const PROVIDERS = [
  { logo: "openai", name: "ChatGPT", connect: "Sign in with Plus or Pro" },
  { logo: "anthropic", name: "Anthropic", connect: "API key" },
  { logo: "gemini", name: "Google Gemini", connect: "API key" },
  { logo: "openai", name: "OpenAI", connect: "API key" },
  { logo: "openrouter", name: "OpenRouter", connect: "API key" },
  { logo: "vercel", name: "Vercel AI Gateway", connect: "API key" },
  { logo: "ollama", name: "Ollama", connect: "On your computer or your server" },
  { logo: null, name: "OpenAI-compatible", connect: "LM Studio and other servers" },
];

function Providers() {
  return (
    <section id="models" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal max-w-[24ch] text-[clamp(2rem,4.4vw,3.5rem)] font-semibold leading-[1.08] tracking-[-0.035em]">
          Bring your own model
        </h2>
        <p className="reveal mt-7 max-w-[40rem] text-lg leading-relaxed text-soft sm:text-xl">
          Sign in with ChatGPT, use your own API keys, or run models on your
          own computer. Pick a model for each chat and switch whenever you like.
        </p>
        <div className="mt-12 sm:mt-16">
          <Screenshot
            src="/wavefunction-providers.jpg"
            alt="Wavefunction setup, step 1: connect a provider, from ChatGPT, Anthropic, Google Gemini, OpenRouter, Vercel AI Gateway, Ollama or an OpenAI-compatible server."
            width={2841}
            height={1856}
          />
        </div>
        <ul className="reveal mt-2 grid grid-cols-2 gap-2 sm:mt-4 sm:gap-4 lg:grid-cols-4">
          {PROVIDERS.map(({ logo, name, connect }) => (
            <li
              key={name}
              className="flex min-w-0 flex-col gap-8 rounded-[20px] bg-panel p-5 sm:rounded-[24px] sm:p-7"
            >
              {logo ? (
                <Mark name={logo} className="size-7 text-text" />
              ) : (
                <svg
                  viewBox="0 0 24 24"
                  className="size-7 text-text"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  aria-hidden
                >
                  <rect x="3" y="4" width="18" height="7" rx="2" />
                  <rect x="3" y="13" width="18" height="7" rx="2" />
                  <path d="M7 7.5h.01M7 16.5h.01" strokeWidth="2.5" />
                </svg>
              )}
              <div className="min-w-0">
                <h3 className="text-[15px] font-medium sm:text-[17px]">
                  {name}
                </h3>
                <p className="mt-1 text-sm text-soft">{connect}</p>
              </div>
            </li>
          ))}
        </ul>
      </Container>
    </section>
  );
}

function Privacy() {
  return (
    <section id="privacy" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal text-balance text-[clamp(2.5rem,6vw,4.75rem)] font-semibold leading-none tracking-[-0.045em]">
          Secure and private
        </h2>
        <p className="reveal mt-7 max-w-[40ch] text-lg leading-relaxed text-soft sm:text-xl">
          Zero telemetry: Wavefunction sends no usage data, analytics or crash
          reports. Use local models or require zero data retention from your
          provider.
        </p>
        <div className="mt-12 sm:mt-16">
          <PrivacyVisual />
        </div>
      </Container>
    </section>
  );
}

function Minimal() {
  return (
    <section id="minimal" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal text-[clamp(2.5rem,6vw,4.75rem)] font-semibold leading-none tracking-[-0.045em]">
          Less is more
        </h2>
        <p className="reveal mt-7 max-w-[28ch] text-lg leading-relaxed text-soft sm:text-xl">
          One agent. A conversation. More room to focus on your code.
        </p>
        <div className="mt-14 grid gap-4 md:grid-cols-2 sm:mt-16">
          <figure className="reveal flex min-w-0 flex-col gap-6 rounded-[28px] bg-panel p-6 sm:p-9">
            <BusyIdeVisual />
            <figcaption className="text-[15.5px] text-soft">
              Other apps
              <span className="sr-only">
                : an interface crowded with tabs, sidebars, editor panels, and a
                terminal.
              </span>
            </figcaption>
          </figure>
          <figure className="reveal flex min-w-0 flex-col gap-6 rounded-[28px] bg-panel p-6 sm:p-9">
            <CleanAppVisual />
            <figcaption className="text-[15.5px] font-medium text-text">
              Wavefunction
              <span className="sr-only">
                : a simple project sidebar with room for the conversation and
                composer.
              </span>
            </figcaption>
          </figure>
        </div>
        <ul className="mt-14 grid gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
          {[
            {
              title: "Fewer sidebars. Fewer tabs.",
              description:
                "Simple, clean, minimal interface. The conversation stays at the center.",
            },
            {
              title: "Your browser, always",
              description:
                "No built-in browser. Links open in your default browser, with the setup you already use.",
            },
            {
              title: "Just a coding agent",
              description:
                "No dots, bots, built-in apps of all kinds, or a million extra features. Just the tools to get things done.",
            },
            {
              title: "Computer use in a container",
              description:
                "Your own computer stays under your control. When the agent needs a desktop, it uses its own, in a local Docker container.",
            },
          ].map(({ title, description }) => (
            <li key={title} className="reveal border-t border-text/15 pt-6">
              <h3 className="text-[17px] font-medium">{title}</h3>
              <p className="mt-3 text-[16px] leading-relaxed text-soft">
                {description}
              </p>
            </li>
          ))}
        </ul>
      </Container>
    </section>
  );
}

function OpenSource() {
  return (
    <section id="open-source" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal text-balance text-[clamp(2rem,4.4vw,3.5rem)] font-medium leading-[1.08] tracking-[-0.035em]">
          Open source, down to the prompt
        </h2>
        <p className="reveal mt-6 max-w-[34rem] text-lg leading-relaxed text-soft sm:text-xl">
          Interested in contributing? We welcome new contributors.
        </p>
        <div className="reveal mt-8">
          <Star place="open-source" />
        </div>
        <div className="mt-14 sm:mt-20">
          <RepositoryVisual />
        </div>
      </Container>
    </section>
  );
}

function Extras() {
  return (
    <section id="features" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal text-[clamp(2rem,4.4vw,3.5rem)] font-medium leading-[1.08] tracking-[-0.035em]">
          Connectors and skills
        </h2>
        <div className="mt-12 sm:mt-16">
          <Screenshot
            src="/wavefunction-connectors.jpg"
            alt="The connector catalog in Wavefunction: Gmail, Outlook, Google, Microsoft 365, Airtable, Asana, Atlassian and more."
            width={2841}
            height={1855}
          />
        </div>
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card visual={<ConnectorsVisual />} title="MCP connectors">
            70+ ready-made connectors, plus any MCP server you add.
          </Card>
          <Card visual={<SetupVisual />} title="Skills and instructions">
            It follows your AGENTS.md and loads SKILL.md skills from .agents,
            .codex and .opencode folders.
          </Card>
        </div>
      </Container>
    </section>
  );
}

function Designs() {
  return (
    <section id="designs" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal max-w-[24ch] text-[clamp(2rem,4.4vw,3.5rem)] font-semibold leading-[1.08] tracking-[-0.035em]">
          Make it look the way you like
        </h2>
        <p className="reveal mt-7 max-w-[40rem] text-lg leading-relaxed text-soft sm:text-xl">
          Designs restyle the whole app. Pick a built-in one, like Nostalgia
          or Retro, or ask the agent to make one for you.
        </p>
        <div className="mt-12 grid gap-4 sm:mt-16 sm:grid-cols-2 lg:gap-5">
          {[
            {
              src: "/wavefunction-design-retro.jpg",
              alt: "The Retro design: pixel type, pixel icons and pixel art of a desk on the new-session screen.",
            },
            {
              src: "/wavefunction-design-nostalgia.jpg",
              alt: "The Nostalgia design: a glass title bar, an Explorer-style sidebar and classic controls.",
            },
            {
              src: "/wavefunction-design-night-light.jpg",
              alt: "Night Light, a design the agent made in a chat: warm charcoal colours and a sleeping moon on the new-session screen.",
            },
          ].map(({ src, alt }, index) => (
            <Image
              key={src}
              src={src}
              alt={alt}
              width={1920}
              height={1440}
              sizes={
                index === 0
                  ? "(min-width: 1200px) 1152px, 100vw"
                  : "(min-width: 1200px) 568px, (min-width: 640px) 50vw, 100vw"
              }
              className={`reveal w-full rounded-[20px] sm:rounded-[24px] ${index === 0 ? "sm:col-span-2" : ""}`}
            />
          ))}
        </div>
      </Container>
    </section>
  );
}

function Stats() {
  return (
    <section id="stats" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <h2 className="reveal max-w-[18ch] text-[clamp(2.5rem,6vw,4.75rem)] font-semibold leading-none tracking-[-0.045em]">
          Track every token and every dollar
        </h2>
        <p className="reveal mt-7 max-w-[40rem] text-lg leading-relaxed text-soft sm:text-xl">
          Track token usage, cache usage, and provider-reported costs in one
          place. See what you use, what you spend, and how it changes over time.
        </p>
        <div className="mt-12 sm:mt-16">
          <Screenshot
            src="/wavefunction-analytics.jpg"
            alt="Settings, Analytics: token totals, runs, working time, reported cost and daily activity."
            width={1480}
            height={966}
          />
        </div>
      </Container>
    </section>
  );
}

function FAQ() {
  const questions = [
    {
      question: "Is Wavefunction free?",
      answer:
        "Yes. Wavefunction is free and open source under the MIT license. You bring your own models or provider account; any model usage charges come from your provider.",
    },
    {
      question: "Which models can I use?",
      answer:
        "Sign in with ChatGPT, use an API key from OpenAI, Anthropic, Google Gemini, OpenRouter or Vercel AI Gateway, or run models locally through Ollama, LM Studio or another OpenAI-compatible server. Choose the model that fits each conversation.",
    },
    {
      question: "Is it truly private?",
      answer:
        "Yes. Wavefunction collects nothing: no telemetry, no usage data, no analytics and no crash reports. Your conversations and settings stay on your computer. What leaves it goes only to the models, connectors and websites you use, and to GitHub to check for updates. Use a local model through Ollama or LM Studio to keep inference on your computer too, or a provider with zero data retention.",
    },
    {
      question: "How does parallel work happen?",
      answer:
        "Describe what you want in one conversation. Wavefunction can split work across subagents, take follow-up instructions while they run, and bring their work back together. You can keep talking as the work moves forward.",
    },
    {
      question: "Can I use my existing tools and instructions?",
      answer:
        "Yes. Connect your tools through the built-in connector catalogue or add an MCP server. Wavefunction also reads your AGENTS.md instructions and loads SKILL.md skills, so it can work with the setup you already use.",
    },
    {
      question: "Which platforms are supported?",
      answer:
        "Wavefunction runs on macOS: download the app, connect a model, and start a conversation. Linux support is experimental: build it from source with the steps in the README.",
    },
  ];

  return (
    <section id="faq" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container className="grid gap-12 lg:grid-cols-[0.7fr_1.3fr] lg:gap-24">
        <h2 className="reveal max-w-[12ch] text-[clamp(2rem,4.4vw,3.5rem)] font-medium leading-[1.08] tracking-[-0.035em]">
          Frequently asked questions
        </h2>
        <div className="reveal min-w-0 border-t border-text/15">
          {questions.map(({ question, answer }) => (
            <details key={question} className="group border-b border-text/15">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-6 text-[17px] font-medium transition-colors hover:text-accent sm:text-lg [&::-webkit-details-marker]:hidden">
                {question}
                <svg
                  viewBox="0 0 20 20"
                  className="size-5 shrink-0 text-faint"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  aria-hidden
                >
                  <path d="M4 10h12" />
                  <path
                    d="M10 4v12"
                    className="origin-center transition-transform group-open:scale-y-0 motion-reduce:transition-none"
                  />
                </svg>
              </summary>
              <p className="max-w-[58ch] pb-7 pr-6 text-[16px] leading-relaxed text-soft">
                {answer}
              </p>
            </details>
          ))}
        </div>
      </Container>
    </section>
  );
}

const LINK = "font-medium text-accent transition-colors hover:text-text";

function Contribute() {
  return (
    <section id="contribute" className="scroll-mt-16 pt-44 sm:pt-64">
      <Container>
        <div className="grid gap-12 lg:grid-cols-[0.85fr_1.15fr] lg:items-center lg:gap-16">
          <div>
            <h2 className="reveal max-w-[16ch] text-[clamp(2rem,4.4vw,3.5rem)] font-semibold leading-[1.08] tracking-[-0.035em]">
              Contribute and support
            </h2>
            <p className="reveal mt-7 max-w-[34rem] text-lg leading-relaxed text-soft sm:text-xl">
              Wavefunction is built in the open. Run it from source in a few
              minutes, with no account or model needed, and ask anything in{" "}
              <a href={site.discussions} className="text-accent underline decoration-accent/30 underline-offset-[3px] hover:decoration-accent">
                Discussions
              </a>
              .
            </p>
            <div className="reveal mt-9 flex flex-wrap gap-3">
              <StarLink
                place="contribute"
                className={`inline-flex items-center bg-text font-medium text-white transition-colors hover:bg-text/85 ${buttonShape("lg")}`}
              >
                <GitHubIcon />
                Star on GitHub
              </StarLink>
              <a
                href={site.contributing}
                className={`inline-flex items-center bg-panel font-medium transition-colors hover:bg-panel-raised ${buttonShape("lg")}`}
              >
                Contributing guide
              </a>
            </div>
          </div>
          <div className="reveal min-w-0 rounded-[28px] bg-panel p-4 sm:p-8 lg:p-10">
            <ContributeVisual />
          </div>
        </div>
        <ul className="mt-12 grid gap-3 sm:mt-16 sm:grid-cols-2 sm:gap-4 lg:grid-cols-4">
          {[
            {
              icon: <BranchIcon className="size-7" />,
              title: "Fix or build something",
              description:
                "Start with an issue labelled good first issue, and read the contributing guide for how to send a change.",
              links: (
                <>
                  <a href={site.goodFirstIssues} className={LINK}>
                    Good first issues
                  </a>
                  <a href={site.contributing} className={LINK}>
                    Contributing guide
                  </a>
                </>
              ),
            },
            {
              icon: <FlaskIcon className="size-7" />,
              title: "Test it on your setup",
              description:
                "Run it on your Linux distribution or macOS version, or with your local models in Ollama or LM Studio, and tell us what breaks.",
              links: (
                <>
                  <a href={site.testReport} className={LINK}>
                    Send a test report
                  </a>
                  <a href={site.bugReport} className={LINK}>
                    Report a bug
                  </a>
                </>
              ),
            },
            {
              icon: <StarIcon className="size-7" />,
              title: "Star it on GitHub",
              description: "Stars help other developers find Wavefunction.",
              links: (
                <StarLink place="contribute-tile" className={LINK}>
                  Star on GitHub
                </StarLink>
              ),
            },
            {
              icon: <MegaphoneIcon className="size-7" />,
              title: "Spread the word",
              description:
                "Tell a friend, or share it where developers talk about coding agents.",
              links: (
                <a href={site.shareOnX} className={LINK}>
                  Share on X
                </a>
              ),
            },
          ].map(({ icon, title, description, links }) => (
            <li
              key={title}
              className="reveal flex flex-col rounded-[20px] bg-panel p-6 sm:rounded-[24px] sm:p-7"
            >
              <span className="text-text">{icon}</span>
              <h3 className="mt-6 text-[17px] font-medium">{title}</h3>
              <p className="mt-3 flex-1 text-[16px] leading-relaxed text-soft">
                {description}
              </p>
              <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-[15px]">
                {links}
              </div>
            </li>
          ))}
        </ul>
      </Container>
    </section>
  );
}

function Closing() {
  return (
    <section className="relative mt-44 overflow-x-clip pb-32 pt-24 sm:mt-64 sm:pb-44">
      <div
        className="absolute inset-0 [mask-image:linear-gradient(to_bottom,transparent,black_35%,black_70%,transparent)]"
        aria-hidden
      >
        <WaveField
          className="h-full w-full opacity-60"
          center={0.5}
          lines={26}
          intensity={1}
        />
      </div>
      <Container className="relative flex flex-col items-center text-center">
        <Image
          src="/wavefunction-mark.svg"
          alt=""
          width={350}
          height={331}
          className="reveal h-auto w-24"
        />
        <h2 className="reveal mt-10 text-[clamp(2.5rem,6vw,4.75rem)] font-semibold leading-none tracking-[-0.045em]">
          Try Wavefunction today
        </h2>
        <p className="reveal mt-6 text-lg text-soft sm:text-xl">
          Free and open source for macOS and Linux.
        </p>
        <div className="reveal mt-10 flex flex-wrap justify-center gap-3">
          <Download />
          <Star place="closing" />
        </div>
      </Container>
    </section>
  );
}

