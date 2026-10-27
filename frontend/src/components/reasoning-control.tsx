import { useEffect, useState } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import { isKeyProvider } from "@/lib/models";
import type { ModelSpec } from "@/lib/types";
import { useSettings } from "@/store/settings";

type Effort = NonNullable<ModelSpec["reasoning_effort"]>;
const labels: Record<Effort, string> = {
  none: "Off",
  enabled: "On",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Maximum",
  ultra: "Ultra",
};

export function ReasoningControl({
  model,
  onChange,
  disabled = false,
}: {
  model: ModelSpec;
  onChange: (effort: ModelSpec["reasoning_effort"]) => void | Promise<void>;
  disabled?: boolean;
}) {
  const [options, setOptions] = useState<Effort[] | null | undefined>(undefined);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Refresh when catalogue discovery completes after the menu opened.
  const catalogue = useSettings((state) =>
    model.provider === "codex"
      ? state.connections?.codex_models
      : model.provider === "openrouter"
        ? state.openRouterModels
        : isKeyProvider(model.provider)
          ? state.keyModels[model.provider]
          : state.connections?.ollama,
  );
  const { provider, model: name, base_url } = model;
  useEffect(() => {
    let cancelled = false;
    void api
      .modelReasoningOptions({ provider, model: name, base_url, temperature: 0.1, max_tokens: null })
      .then(({ efforts }) => {
        if (!cancelled) {
          setOptions(efforts);
          setError("");
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load reasoning options. Reopen this menu to try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [provider, name, base_url, catalogue]);

  const value = model.reasoning_effort ?? "default";
  const offered: Effort[] =
    options ?? (provider === "ollama" ? ["none", "enabled"] : ["low", "medium", "high"]);
  const unsupported = options?.length === 0;

  async function choose(next: string) {
    setSaving(true);
    setError("");
    try {
      await onChange(next === "default" ? null : (next as Effort));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save reasoning setting.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2 border-t border-hairline p-3">
      <div className="flex items-center justify-between gap-4">
        <span className="text-xs font-medium">Reasoning effort</span>
        <Select
          value={value}
          onValueChange={(next) => void choose(next)}
          disabled={disabled || saving || options === undefined || (unsupported && value === "default")}
        >
          <SelectTrigger aria-label="Reasoning effort" size="sm" className="max-w-44 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent position="popper" align="end">
            <SelectItem value="default">Provider default</SelectItem>
            {value !== "default" && !offered.includes(value) ? (
              <SelectItem value={value} disabled>
                {labels[value]} (unavailable)
              </SelectItem>
            ) : null}
            {offered.map((effort) => (
              <SelectItem key={effort} value={effort}>
                {labels[effort]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <p className="text-2xs leading-relaxed text-muted-foreground">
        {unsupported
          ? "This model does not offer a reasoning control."
          : options === null
            ? "This model does not advertise its reasoning options. Check that it supports the level you choose."
            : "Higher effort can take longer and use more tokens. Provider default keeps the model’s own setting."}
      </p>
      {error ? (
        <p role="alert" className="text-2xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
