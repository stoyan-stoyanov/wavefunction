import { useEffect, useState } from "react";
import { NativeSection } from "@/components/native-view";
import { ProviderLogo } from "@/components/provider-logo";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { ModelChoice } from "@/lib/models";
import type { Connections } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { ModelCards } from "./models-settings";
import { SettingsRow } from "./settings-row";

/** The providers with image models, and the key each needs. */
const imageProviders = [
  { id: "gemini", name: "Google Gemini", secret: "gemini_api_key" },
  { id: "openai", name: "OpenAI", secret: "openai_api_key" },
  { id: "openrouter", name: "OpenRouter", secret: "openrouter_api_key" },
  { id: "vercel", name: "Vercel AI Gateway", secret: "vercel_api_key" },
] as const;
type ImageProvider = (typeof imageProviders)[number];

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Image models for the agent's generate_image and edit_image tools, per connected provider. */
export function ImageModelsSettings({ connections }: { connections: Connections }) {
  const [open, setOpen] = useState<ImageProvider["id"] | null>(null);
  const enabled = connections.enabled_image_models ?? [];
  const connected = imageProviders.filter((provider) => connections.secrets[provider.secret]);
  if (!connected.length) return null;
  return (
    <NativeSection
      title="Image models"
      description="Turn on image models to let the agent create and edit images in chats. The first one turned on is the default."
    >
      <div className="divide-y divide-hairline overflow-hidden rounded-xl bg-card">
        {connected.map((provider) => {
          const count = enabled.filter((key) => key.startsWith(`${provider.id}::`)).length;
          return (
            <div key={provider.id}>
              <SettingsRow
                title={
                  <span className="flex items-center gap-2.5">
                    <ProviderLogo provider={provider.id} className="size-5" />
                    {provider.name}
                  </span>
                }
                description={count ? `${count} turned on` : "None turned on"}
                control={
                  <Button
                    variant="secondary"
                    size="sm"
                    aria-expanded={open === provider.id}
                    aria-label={`Choose ${provider.name} image models`}
                    onClick={() => setOpen(open === provider.id ? null : provider.id)}
                  >
                    {open === provider.id ? "Done" : "Choose"}
                  </Button>
                }
              />
              {open === provider.id ? <ImageModelPicker provider={provider} enabled={enabled} /> : null}
            </div>
          );
        })}
      </div>
    </NativeSection>
  );
}

/** One provider's image models; each click turns one on or off at once. */
function ImageModelPicker({ provider, enabled }: { provider: ImageProvider; enabled: string[] }) {
  const [models, setModels] = useState<ModelChoice[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api
      .listImageModels(provider.id)
      .then((found) => !cancelled && setModels(found))
      .catch((cause) => !cancelled && setError(errorText(cause)));
    return () => {
      cancelled = true;
    };
  }, [provider.id]);
  const key = (id: string) => `${provider.id}::${id}`;
  async function toggle(id: string) {
    const next = enabled.includes(key(id))
      ? enabled.filter((item) => item !== key(id))
      : [...enabled, key(id)];
    setSaving(true);
    setError("");
    try {
      const { models: saved } = await api.updateImageModels(next);
      const current = useSettings.getState().connections;
      if (current) useSettings.setState({ connections: { ...current, enabled_image_models: saved } });
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="border-t border-hairline p-4">
      {error ? (
        <p role="alert" className="mb-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {models ? (
        <ModelCards
          label={`${provider.name} image`}
          models={models}
          isChecked={(id) => enabled.includes(key(id))}
          onToggle={(id) => void toggle(id)}
          disabled={saving}
        />
      ) : error ? null : (
        <p role="status" className="text-sm text-muted-foreground">
          Loading image models…
        </p>
      )}
    </div>
  );
}
