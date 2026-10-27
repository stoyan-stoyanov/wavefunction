import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/** A macOS-style segmented control: one recessed track, the active segment raised. */
export function SegmentedControl({
  value,
  options,
  label,
  onChange,
  disabled = false,
}: {
  value: string;
  options: Array<{ value: string; label: string; icon?: LucideIcon }>;
  label: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      aria-disabled={disabled || undefined}
      className={cn("inline-flex gap-0.5 rounded-lg bg-muted p-0.5", disabled && "opacity-50")}
    >
      {options.map((option) => {
        const Icon = option.icon;
        const active = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded-md px-3 text-sm transition-all focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
              active
                ? "bg-card font-medium text-foreground shadow-xs"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {Icon ? <Icon className="size-3.5" /> : null}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
