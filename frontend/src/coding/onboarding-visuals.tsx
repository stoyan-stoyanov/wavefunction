// Illustrations beside the setup steps, in the landing page's manner: soft cards with faint
// shadows, placeholder bars for text, one slate-blue accent, and a short animation, played once,
// of what the step sets up. Nothing in them is interactive; with reduced motion they show the end.
import { useEffect, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  ChevronRight,
  FolderOpen,
  PanelLeft,
  Paperclip,
  Plug,
  Settings2,
  SquarePen,
} from "lucide-react";
import { ProviderLogo } from "@/components/provider-logo";
import { cn } from "@/lib/utils";
import { ProjectIcon } from "./project-appearance";
import type { ProjectIconName } from "./project-icons";

const card = "rounded-[18px] bg-card shadow-[0_24px_60px_-28px_rgb(27_38_56/0.35)] ring-1 ring-border";
const accent = "text-[#486d9c] dark:text-[#8eabd2]";
const accentFill = "bg-[#486d9c]/15 dark:bg-[#8eabd2]/20";

/** The frame of an illustration that plays once and rests on its last frame, where it also
 *  starts when motion is reduced. */
function useFrame(count: number, ms: number) {
  const [still] = useState(() => Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches));
  const [frame, setFrame] = useState(still ? count - 1 : 0);
  useEffect(() => {
    if (frame >= count - 1) return;
    const timer = window.setTimeout(() => setFrame(frame + 1), ms);
    return () => window.clearTimeout(timer);
  }, [frame, count, ms]);
  return frame;
}

function Bar({ className }: { className?: string }) {
  return <span className={cn("block h-1.5 rounded-full bg-foreground/15", className)} />;
}

/** Step one: a provider picked from the list, then its API key pasted in, ready to connect. */
export function ProvidersVisual() {
  const providers = [
    { id: "openrouter" as const, name: "OpenRouter", width: "w-32" },
    { id: "vercel" as const, name: "Vercel AI Gateway", width: "w-28" },
    { id: "ollama" as const, name: "Ollama", width: "w-20" },
    { id: "codex" as const, name: "ChatGPT", width: "w-24" },
  ];
  // The list, OpenRouter clicked, its key screen, the key pasted, then Connect in reach. It
  // stops short of connecting: the key is the step people have to take.
  const frame = useFrame(5, 900);
  const selected = frame >= 1 ? 0 : -1;
  const keyScreen = frame >= 2;
  const pasted = frame >= 3;
  const ready = frame >= 4;
  return (
    // Both screens share one cell, so the card keeps its size as one fades into the other.
    <div className={cn(card, "grid w-full max-w-[380px] p-3")} aria-hidden>
      <div
        className={cn(
          "col-start-1 row-start-1 transition-opacity duration-300",
          keyScreen ? "opacity-0" : "opacity-100",
        )}
      >
        <div className="flex items-center justify-between px-2 pb-3 pt-1">
          <Bar className="w-20 bg-foreground/30" />
          <Bar className="w-10" />
        </div>
        <div className="space-y-1.5">
          {providers.map((provider, index) => (
            <div
              key={provider.id}
              className={cn(
                "flex items-center gap-3 rounded-xl px-2.5 py-2.5 ring-1 transition-colors duration-500",
                index === selected
                  ? cn(accentFill, "ring-[#486d9c]/40 dark:ring-[#8eabd2]/40")
                  : "bg-muted/60 ring-transparent",
              )}
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-card ring-1 ring-border">
                <ProviderLogo provider={provider.id} className="size-5" />
              </span>
              <span className="min-w-0 flex-1 space-y-1.5">
                <span className="block text-[13px] font-medium leading-none">{provider.name}</span>
                <Bar className={provider.width} />
              </span>
              <ChevronRight
                className={cn(
                  "size-4 shrink-0 transition-colors duration-500",
                  index === selected ? accent : "text-foreground/20",
                )}
              />
            </div>
          ))}
        </div>
      </div>
      <div
        className={cn(
          "flex flex-col px-2 pt-1 transition-opacity duration-300 col-start-1 row-start-1",
          keyScreen ? "opacity-100" : "opacity-0",
        )}
      >
        <div className="flex items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-card ring-1 ring-border">
            <ProviderLogo provider="openrouter" className="size-5" />
          </span>
          <span className="min-w-0 flex-1 space-y-1.5">
            <Bar className="w-20 bg-foreground/30" />
            <Bar className="w-28" />
          </span>
        </div>
        <span className="mt-7 block text-[12px] font-medium">API key</span>
        <div className="mt-2 flex items-center gap-2">
          <span
            className={cn(
              "flex h-9 min-w-0 flex-1 items-center rounded-lg bg-muted/60 px-3 font-mono text-[11.5px] ring-1 transition-colors duration-300",
              pasted ? "ring-[#486d9c]/40 dark:ring-[#8eabd2]/40" : "ring-border",
            )}
          >
            {pasted ? (
              <span className="onboarding-fade truncate">sk-or-v1-••••••••••••••••</span>
            ) : (
              <span className="text-muted-foreground">sk-or-v1-…</span>
            )}
          </span>
          {/* A button sketched without a label, so it can't be taken for the form's own. */}
          <span
            className={cn(
              "grid h-9 w-20 shrink-0 place-items-center rounded-lg transition-colors duration-300",
              ready ? "bg-[#486d9c] dark:bg-[#8eabd2]" : "bg-muted",
            )}
          >
            <Bar className={cn("w-10", ready && "bg-white/60 dark:bg-background/50")} />
          </span>
        </div>
        <div className="mt-4 space-y-2">
          <Bar className="w-11/12" />
          <Bar className="w-2/3" />
        </div>
      </div>
    </div>
  );
}

/** Step two: the composer's model menu opening and a model being chosen. */
export function ModelsVisual() {
  // Placeholder bars, not model names, so no one looks for these models in their own list.
  const models = [
    { name: "w-24", detail: "w-14" },
    { name: "w-16", detail: "w-16" },
    { name: "w-20", detail: "w-12" },
  ];
  // Closed on the third model, open on it, move to the first, choose it and close.
  const frame = useFrame(4, 800);
  const open = frame === 1 || frame === 2;
  const highlighted = frame === 1 ? 2 : 0;
  const current = frame >= 3 ? 0 : 2;
  return (
    // The menu opens upward from the model selector, over the composer, as the real one does;
    // the top padding keeps room for it.
    <div className="w-full max-w-[420px] pt-20" aria-hidden>
      <div className={cn(card, "p-4")}>
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <FolderOpen className="size-3.5" />
          <Bar className="w-20" />
        </div>
        <div className="mt-4 space-y-2">
          <Bar className="w-11/12 bg-foreground/20" />
          <Bar className="w-3/5 bg-foreground/20" />
        </div>
        <div className="mt-6 flex items-center justify-end gap-2.5">
          <div className="relative">
            <div
              className={cn(
                card,
                "absolute bottom-full right-0 z-10 mb-2 w-60 origin-bottom-right p-1.5 transition-[opacity,scale] duration-300",
                open ? "scale-100 opacity-100" : "scale-95 opacity-0",
              )}
            >
              <div className="px-2.5 pb-2 pt-1.5">
                <Bar className="w-16 bg-foreground/25" />
              </div>
              {models.map((model, index) => (
                <div
                  key={model.name}
                  className={cn(
                    "rounded-lg px-2.5 py-2 transition-colors duration-200",
                    highlighted === index && cn(accentFill),
                  )}
                >
                  <Bar
                    className={cn(
                      "h-2 transition-colors duration-200",
                      model.name,
                      highlighted === index ? "bg-[#486d9c]/60 dark:bg-[#8eabd2]/60" : "bg-foreground/30",
                    )}
                  />
                  <Bar className={cn("mt-1.5", model.detail)} />
                </div>
              ))}
            </div>
            <span
              className={cn(
                "flex h-6 items-center gap-1.5 rounded-full px-2.5 transition-colors duration-300",
                open ? cn(accentFill, accent) : "bg-muted text-foreground",
              )}
            >
              <Bar
                className={cn(
                  "h-2 transition-[width] duration-300",
                  models[current].name,
                  open ? "bg-[#486d9c]/60 dark:bg-[#8eabd2]/60" : "bg-foreground/30",
                )}
              />
              <ChevronDown className="size-3" />
            </span>
          </div>
          <Paperclip className="size-3.5 text-muted-foreground" />
          <span className="grid size-7 place-items-center rounded-full bg-foreground text-background">
            <ArrowUp className="size-3.5" />
          </span>
        </div>
      </div>
    </div>
  );
}

/** Step three: the app, sketched, with the new project first in a sidebar laid out like the real one. */
export function AppVisual({
  name,
  icon,
  color,
}: {
  name: string;
  icon: ProjectIconName;
  color: string | null;
}) {
  // The first chat's message appears, then its reply fills in.
  const frame = useFrame(5, 600);
  const replied = Math.max(0, frame - 1);
  const others = [
    { icon: "server", width: "w-16" },
    { icon: "app", width: "w-20" },
  ];
  return (
    <div
      className={cn(
        card,
        "grid h-[340px] w-full max-w-[540px] grid-cols-[160px_minmax(0,1fr)] overflow-hidden",
      )}
      aria-hidden
    >
      <div className="flex min-w-0 flex-col border-r border-border bg-muted/50 px-2.5 py-3 text-[11px]">
        <div className="flex items-center gap-1 px-1">
          <span className="size-2 rounded-full bg-[#ff5f57]" />
          <span className="size-2 rounded-full bg-[#febc2e]" />
          <span className="size-2 rounded-full bg-[#28c840]" />
          <PanelLeft className="ml-auto size-3 text-muted-foreground" />
        </div>
        <div className="mt-4 space-y-2.5 px-1">
          <span className="flex items-center gap-2">
            <SquarePen className="size-3.5 text-muted-foreground" />
            <Bar className="w-16" />
          </span>
          <span className="flex items-center gap-2">
            <Plug className="size-3.5 text-muted-foreground" />
            <Bar className="w-14" />
          </span>
        </div>
        <span className="mt-5 px-1 text-[10.5px] font-semibold text-muted-foreground">Projects</span>
        <div className="mt-2 space-y-1">
          <span className="flex min-w-0 items-center gap-1.5 px-0.5 py-1">
            <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
            <ProjectIcon icon={icon} color={color} className="size-3.5 shrink-0" />
            <span className="truncate font-medium">{name || "Your project"}</span>
          </span>
          <span className="ml-4 flex h-6 items-center rounded-md bg-foreground/[0.08] px-2">
            <Bar className="w-16 bg-foreground/25" />
          </span>
          {others.map((other) => (
            <span key={other.icon} className="flex min-w-0 items-center gap-1.5 px-0.5 py-1.5">
              <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
              <ProjectIcon icon={other.icon} className="size-3.5 shrink-0 text-muted-foreground" />
              <Bar className={other.width} />
            </span>
          ))}
        </div>
        <span className="mt-auto flex items-center gap-2 px-1">
          <Settings2 className="size-3.5 text-muted-foreground" />
          <Bar className="w-12" />
        </span>
      </div>
      <div className="flex min-w-0 flex-col justify-end gap-3 p-4">
        <span
          className={cn(
            "ml-auto h-6 w-2/5 rounded-[10px] transition-opacity duration-300",
            accentFill,
            frame === 0 ? "opacity-0" : "opacity-100",
          )}
        />
        <div className="space-y-2">
          {["w-11/12", "w-4/5", "w-2/3"].map((width, index) => (
            <span
              key={width}
              className={cn(
                "block h-1.5 rounded-full bg-foreground/15 transition-opacity duration-300",
                width,
                replied > index ? "opacity-100" : "opacity-0",
              )}
            />
          ))}
        </div>
        <div className="mt-2 rounded-[12px] bg-muted/60 px-3 py-2.5">
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <FolderOpen className="size-3" />
            <span className="truncate">{name || "Your project"}</span>
          </span>
          <div className="mt-2.5 flex items-center justify-between">
            <Bar className="w-1/2" />
            <span className="grid size-5 place-items-center rounded-full bg-foreground/80 text-background">
              <ArrowUp className="size-3" />
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
