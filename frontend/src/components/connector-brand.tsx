import { Mail, Plug } from "lucide-react";
import logos from "@/lib/connector-logos.json";
import { connectorLogoId } from "@/lib/connector-presentation";
import { cn } from "@/lib/utils";

export function ConnectorLogo({ id, className }: { id: string; className?: string }) {
  const key = connectorLogoId(id);
  const logo = (logos as Record<string, { file: string; monochrome: boolean; color?: string }>)[key];
  const color = key === "email" ? "#2563EB" : (logo?.color ?? "#52525B");
  const mask = logo ? `url(/connectors/${logo.file}) center / contain no-repeat` : undefined;
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-white p-1 ring-1 ring-black/5",
        className,
      )}
      style={{ color }}
    >
      {key === "microsoft-workspace" ? (
        <svg viewBox="0 0 24 24" className="size-full">
          <path fill="#f25022" d="M1 1h10v10H1z" />
          <path fill="#7fba00" d="M13 1h10v10H13z" />
          <path fill="#00a4ef" d="M1 13h10v10H1z" />
          <path fill="#ffb900" d="M13 13h10v10H13z" />
        </svg>
      ) : key === "email" ? (
        <Mail className="size-full" />
      ) : !logo ? (
        <Plug className="size-full" />
      ) : logo.monochrome ? (
        <span className="size-full" style={{ backgroundColor: color, mask, WebkitMask: mask }} />
      ) : (
        <img src={`/connectors/${logo.file}`} alt="" draggable={false} className="size-full object-contain" />
      )}
    </span>
  );
}

export function LoginMethodTag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-md border border-hairline bg-muted/60 px-2 py-0.5 text-2xs font-medium text-muted-foreground">
      {children}
    </span>
  );
}

export function InstalledTag() {
  return (
    <span className="ml-auto inline-flex shrink-0 items-center rounded-md bg-emerald-500/10 px-2 py-0.5 text-2xs font-medium text-emerald-700 dark:text-emerald-400">
      Installed
    </span>
  );
}
