import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function SettingsRow({
  title,
  description,
  control,
  className,
}: {
  title: ReactNode;
  description: string;
  control: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-6 px-4 py-3.5", className)}>
      <div className="min-w-0">
        <p className="text-base font-medium">{title}</p>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}
