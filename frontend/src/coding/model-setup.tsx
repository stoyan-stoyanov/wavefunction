import { useState } from "react";
import { EyeOff, KeyRound, LoaderCircle, Plus } from "lucide-react";
import { toast } from "sonner";
import { ForgetKeyButton } from "@/components/forget-key";
import { NativeSection } from "@/components/native-view";
import { ProviderLogo } from "@/components/provider-logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import { useSettings } from "@/store/settings";
import { codingApi } from "./api";

/** Enable one OpenAI or compatible model by ID; it also becomes the default for new chats. */
export function DirectModelSection({ onEnabled }: { onEnabled?: () => void }) {
  const [provider, setProvider] = useState<"openai" | "openai-compatible">("openai");
  const [model, setModel] = useState("");
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const connections = useSettings((s) => s.connections);
  const savedKey = Boolean(connections?.secrets.openai_api_key);
  async function save() {
    if (busy || !model.trim()) return;
    setBusy(true);
    setError("");
    try {
      if (key.trim()) await api.saveSecret("openai_api_key", key.trim());
      await codingApi.configureModel({
        provider,
        model: model.trim(),
        base_url: provider === "openai-compatible" ? url.trim() : null,
        temperature: 0.1,
        max_tokens: null,
        reasoning_effort: null,
      });
      await useSettings.getState().loadConnections();
      setKey("");
      setModel("");
      toast.success("Model enabled and selected for new conversations");
      onEnabled?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <NativeSection
      className="pt-0"
      title={
        <span className="inline-flex items-center gap-3">
          <ProviderLogo provider="openai" />
          OpenAI & compatible servers
        </span>
      }
      actions={
        <span className="inline-flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {savedKey ? "API key saved" : "Not connected"}
          </span>
          {savedKey ? <ForgetKeyButton secret="openai_api_key" label="Remove key" /> : null}
        </span>
      }
    >
      {connections?.coding_models?.length ? (
        <div className="mb-4 divide-y divide-hairline overflow-hidden rounded-xl bg-card">
          {connections.coding_models.map((item) => (
            <div key={`${item.provider}::${item.model}`} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{item.model}</p>
                <p className="truncate text-xs text-muted-foreground">{item.base_url || "OpenAI API"}</p>
              </div>
              <Button
                size="xs"
                variant="secondary"
                disabled={busy}
                onClick={() =>
                  void useSettings
                    .getState()
                    .setDefaultModel(item)
                    .then(() => toast.success("Default model updated"))
                    .catch((e) => setError(String(e.message)))
                }
              >
                Use as default
              </Button>
            </div>
          ))}
        </div>
      ) : null}
      <form
        className="space-y-4 rounded-xl bg-card p-4"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>Provider</Label>
            <Select
              value={provider}
              disabled={busy}
              onValueChange={(value) => setProvider(value as "openai" | "openai-compatible")}
            >
              <SelectTrigger aria-label="Manual model provider" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="openai">OpenAI API</SelectItem>
                <SelectItem value="openai-compatible">Compatible server</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="manual-model-id">Model ID</Label>
            <Input
              id="manual-model-id"
              aria-label="Manual model ID"
              value={model}
              disabled={busy}
              required
              placeholder="Model name from your provider"
              onChange={(e) => setModel(e.target.value)}
            />
          </div>
        </div>
        {provider === "openai-compatible" ? (
          <div className="space-y-2">
            <Label htmlFor="compatible-url">API base URL</Label>
            <Input
              id="compatible-url"
              aria-label="Compatible API base URL"
              type="url"
              value={url}
              disabled={busy}
              required
              placeholder="http://127.0.0.1:1234/v1"
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
        ) : null}
        <div className="space-y-2">
          <Label htmlFor="openai-key">
            API key
            {provider === "openai-compatible" && !savedKey ? (
              <span className="font-normal text-muted-foreground"> · optional for local servers</span>
            ) : null}
          </Label>
          <div className="relative">
            <KeyRound className="absolute left-3 top-2.5 size-3.5 text-muted-foreground" />
            <Input
              id="openai-key"
              aria-label="OpenAI or compatible API key"
              type="password"
              autoComplete="off"
              value={key}
              disabled={busy}
              required={provider === "openai" && !savedKey}
              placeholder={savedKey ? "Leave blank to keep the saved key" : "sk-…"}
              onChange={(e) => setKey(e.target.value)}
              className="pl-9"
            />
          </div>
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <EyeOff className="size-3" /> Saved locally in Wavefunction’s data folder. Compatible servers
            share it.
          </p>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">
          Models must support tool calling to make code changes.
        </p>
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
        <Button
          type="submit"
          variant="secondary"
          disabled={busy || !model.trim() || (provider === "openai-compatible" && !url.trim())}
        >
          {busy ? <LoaderCircle className="animate-spin" /> : <Plus />} Enable model
        </Button>
      </form>
    </NativeSection>
  );
}
