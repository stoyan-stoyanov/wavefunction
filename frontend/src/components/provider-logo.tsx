import { Cpu } from "lucide-react";

import type { ModelSpec } from "@/lib/types";
import { cn } from "@/lib/utils";

const logos: Partial<Record<ModelSpec["provider"], string>> = {
  codex: "/providers/openai.svg",
  openai: "/providers/openai.svg",
  openrouter: "/providers/openrouter.svg",
  vercel: "/connectors/vercel.svg",
  anthropic: "/providers/anthropic.svg",
  gemini: "/providers/gemini.svg",
  ollama: "/providers/ollama.svg",
};

/** Bundled monochrome brand marks, readable in both application themes. */
export function ProviderLogo({
  provider,
  className,
}: {
  provider: ModelSpec["provider"];
  className?: string;
}) {
  const src = logos[provider];
  return src ? (
    <img
      src={src}
      alt=""
      aria-hidden
      draggable={false}
      className={cn("size-6 shrink-0 object-contain dark:invert", className)}
    />
  ) : (
    <Cpu aria-hidden className={cn("size-6 shrink-0 text-muted-foreground", className)} />
  );
}
