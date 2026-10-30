import { useState } from "react";
import { ChevronDown } from "lucide-react";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorEmpty,
  ModelSelectorGroup,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorName,
  ModelSelectorTrigger,
} from "@/components/ai-elements/model-selector";
import type { ModelOption } from "@/components/model-picker";
import { ReasoningControl } from "@/components/reasoning-control";
import { ProviderLogo } from "@/components/provider-logo";
import type { ModelSpec } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * One list of the enabled models, with the chosen one marked.
 *
 * The same menu picks a chat's model in the composer and the default model in Settings, so
 * their headings and rows cannot drift apart.
 */
export function ModelMenu({
  options,
  loading = false,
  disabled = false,
  selectedKey,
  selectedName,
  onChange,
  label,
  title,
  align = "start",
  variant = "secondary",
  className,
  currentModel,
  onReasoningChange,
}: {
  options: ModelOption[];
  loading?: boolean;
  disabled?: boolean;
  selectedKey: string;
  selectedName: string;
  onChange: (key: string) => void;
  /** What the choice is for, read to screen readers: "Chat model", "Default model". */
  label: string;
  /** The heading of the open list. */
  title: string;
  align?: "start" | "end";
  variant?: "secondary" | "ghost";
  /** Sizing for the trigger; the menu itself is the same everywhere. */
  className?: string;
  currentModel?: ModelSpec;
  onReasoningChange?: (effort: ModelSpec["reasoning_effort"]) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <ModelSelector
      open={open && !disabled}
      onOpenChange={(value) => {
        if (!disabled) setOpen(value);
      }}
    >
      <ModelSelectorTrigger asChild>
        <Button
          type="button"
          disabled={disabled}
          variant={variant}
          size="sm"
          className={cn("min-w-0 justify-between gap-1.5", className)}
          aria-label={`${label}: ${selectedName}`}
        >
          <span className="truncate">{loading ? "Loading models…" : selectedName}</span>
          <ChevronDown className="size-3.5 shrink-0" />
        </Button>
      </ModelSelectorTrigger>
      <ModelSelectorContent
        title={title}
        align={align}
        className="min-w-80"
        footer={
          currentModel && onReasoningChange ? (
            <ReasoningControl
              key={`${currentModel.provider}:${currentModel.model}:${currentModel.base_url}`}
              model={currentModel}
              onChange={onReasoningChange}
              disabled={disabled}
            />
          ) : null
        }
      >
        <ModelSelectorInput placeholder="Search enabled models…" />
        <ModelSelectorList className="max-h-80">
          <ModelSelectorEmpty>
            {options.length ? "No model matches your search." : "Enable models in Settings to add them here."}
          </ModelSelectorEmpty>
          <ModelSelectorGroup heading="Enabled models">
            {options.map((option) => (
              <ModelSelectorItem
                key={option.key}
                value={`${option.name} ${option.model} ${option.detail}`}
                data-checked={option.key === selectedKey}
                onSelect={() => {
                  if (disabled) return;
                  onChange(option.key);
                  setOpen(false);
                }}
              >
                <ProviderLogo provider={option.provider} className="size-4" />
                <div className="min-w-0 flex-1">
                  <ModelSelectorName>{option.name}</ModelSelectorName>
                  <p className="truncate text-2xs text-muted-foreground">{option.detail}</p>
                </div>
              </ModelSelectorItem>
            ))}
          </ModelSelectorGroup>
        </ModelSelectorList>
      </ModelSelectorContent>
    </ModelSelector>
  );
}
