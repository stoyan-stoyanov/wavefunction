import type { ReactNode } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

export function NativeView({
  children,
  className,
  contentClassName,
}: {
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <div className={cn("native-view h-full min-h-0 bg-background", className)}>
      <ScrollArea className="h-full">
        {/* One width and padding for every settings page. */}
        <div className={cn("mx-auto w-full max-w-3xl px-8 py-10", contentClassName)}>{children}</div>
      </ScrollArea>
    </div>
  );
}

export function ViewHeading({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="mb-8 flex items-start justify-between gap-6">
      <h2 className="text-xl font-semibold tracking-[-0.015em]">{title}</h2>
      {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
    </div>
  );
}

export function NativeSection({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    // Grouped like an iOS settings list: heading, then a raised card. No rules.
    <section className={cn("pb-2 pt-7 first:pt-0", className)}>
      <div className="mb-3 flex items-start justify-between gap-6">
        <div>
          <h3 className="text-base font-semibold">{title}</h3>
          {description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}
