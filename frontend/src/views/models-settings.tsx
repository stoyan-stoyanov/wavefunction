import { ModelMenu } from "@/components/model-menu";
import { useModelOptions } from "@/components/model-picker";
import { NativeSection, NativeView, ViewHeading } from "@/components/native-view";
import { ProviderLogo } from "@/components/provider-logo";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import { ForgetKeyButton, KeyStatus } from "@/components/forget-key";
import {
  defaultModelConnected,
  codexModelKey,
  hasModelProvider,
  isKeyProvider,
  isProviderConnected,
  keyProviders,
  modelProviders,
  providerCatalogue,
  realModelEnabled,
  type KeyProviderId,
  type ModelChoice,
  type ProviderId,
} from "@/lib/models";
import type { Connections, ModelSpec, OpenRouterModel } from "@/lib/types";
import { DirectModelSection } from "@/coding/model-setup";
import { cn } from "@/lib/utils";
import { useSettings } from "@/store/settings";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  CircleAlert,
  Cpu,
  EyeOff,
  Info,
  KeyRound,
  LoaderCircle,
  Plus,
  Search,
} from "lucide-react";
import { Popover as PopoverPrimitive } from "radix-ui";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { SettingsRow } from "./settings-row";
function contextLabel(contextLength: number): string {
  if (!contextLength) return "";
  if (contextLength >= 1000000) return `${(contextLength / 1000000).toFixed(1)}M`;
  if (contextLength >= 1000) return `${Math.round(contextLength / 1000)}K`;
  return `${contextLength}`;
}
function sameModels(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return left.every((item) => rightSet.has(item));
}
/** A searchable list of one provider's models, each with a checkbox. */
function ModelChecklist({
  label,
  models,
  isChecked,
  onToggle,
  disabled = false,
  className,
}: {
  label: string;
  models: ModelChoice[];
  isChecked: (id: string) => boolean;
  onToggle: (id: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <Command className={cn("min-h-0 flex-1", className)}>
      <CommandInput aria-label={`Search ${label} models`} placeholder={`Search ${label} models…`} />
      <CommandList className="max-h-80">
        <CommandEmpty>No matching models.</CommandEmpty>
        <CommandGroup heading="Available models">
          {models.map((model) => {
            const enabled = isChecked(model.id);
            const context = contextLabel(model.context_length ?? 0);
            return (
              <CommandItem
                key={model.id}
                value={`${model.name} ${model.id}`}
                role="menuitemcheckbox"
                aria-checked={enabled}
                aria-label={`${enabled ? "Disable" : "Enable"} ${model.name}`}
                disabled={disabled || (Boolean(model.unavailable) && !enabled)}
                onSelect={() => onToggle(model.id)}
                className="items-start py-2.5"
              >
                <div
                  className={cn(
                    "mt-0.5 grid size-4 shrink-0 place-items-center rounded border",
                    enabled
                      ? "border-foreground bg-foreground text-background"
                      : "border-input bg-background",
                  )}
                >
                  {enabled ? <Check className="size-3" /> : null}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="truncate text-sm font-medium">{model.name}</span>
                    {context ? (
                      <span className="shrink-0 text-2xs text-muted-foreground">{context} context</span>
                    ) : null}
                  </div>
                  <p className="truncate font-mono text-2xs text-muted-foreground">
                    {model.unavailable ?? model.id}
                  </p>
                </div>
              </CommandItem>
            );
          })}
        </CommandGroup>
      </CommandList>
    </Command>
  );
}
/** Every model a provider offers as a card, with search and filters above, for choosing several. */
export function ModelCards({
  label,
  models,
  isChecked,
  onToggle,
  disabled = false,
}: {
  label: string;
  models: ModelChoice[];
  isChecked: (id: string) => boolean;
  onToggle: (id: string) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [vendor, setVendor] = useState("all");
  const [selectedOnly, setSelectedOnly] = useState(false);
  // Gateways name models `vendor/model`; their vendors are the useful filter.
  const vendors = useMemo(
    () =>
      [
        ...new Set(models.flatMap((model) => (model.id.includes("/") ? [model.id.split("/")[0]] : []))),
      ].sort(),
    [models],
  );
  const words = query.trim().toLowerCase();
  const shown = models.filter(
    (model) =>
      (!words || `${model.name} ${model.id}`.toLowerCase().includes(words)) &&
      (vendor === "all" || model.id.startsWith(`${vendor}/`)) &&
      (!selectedOnly || isChecked(model.id)),
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label={`Search ${label} models`}
            placeholder={`Search ${label} models…`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="pl-8"
          />
        </div>
        {vendors.length > 1 ? (
          <Select value={vendor} onValueChange={setVendor}>
            <SelectTrigger aria-label="Model provider" className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All providers</SelectItem>
              {vendors.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
        <Button
          type="button"
          variant={selectedOnly ? "secondary" : "ghost"}
          aria-pressed={selectedOnly}
          onClick={() => setSelectedOnly((value) => !value)}
        >
          Selected
        </Button>
      </div>
      <div className="grid gap-2">
        {shown.map((model) => {
          const checked = isChecked(model.id);
          const context = contextLabel(model.context_length ?? 0);
          return (
            <button
              key={model.id}
              type="button"
              role="checkbox"
              aria-checked={checked}
              aria-label={`${checked ? "Disable" : "Enable"} ${model.name}`}
              disabled={disabled || (Boolean(model.unavailable) && !checked)}
              onClick={() => onToggle(model.id)}
              className={cn(
                "flex w-full items-center gap-3 rounded-xl bg-card px-4 py-3 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                checked && "ring-1 ring-foreground/30",
              )}
            >
              <span
                className={cn(
                  "grid size-4 shrink-0 place-items-center rounded border",
                  checked ? "border-foreground bg-foreground text-background" : "border-input bg-background",
                )}
              >
                {checked ? <Check className="size-3" /> : null}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{model.name}</span>
                <span className="block truncate font-mono text-2xs text-muted-foreground">
                  {model.unavailable ?? model.id}
                </span>
              </span>
              {context ? (
                <span className="shrink-0 text-xs text-muted-foreground">{context} context</span>
              ) : null}
            </button>
          );
        })}
        {!shown.length ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No matching models.</p>
        ) : null}
      </div>
    </div>
  );
}
/** A provider's whole catalogue as cards; each click enables or disables a model at once. */
function ProviderModelCards({
  provider,
  label,
  models,
}: {
  provider: "openrouter" | KeyProviderId;
  label: string;
  models: ModelChoice[];
}) {
  const enabled = useSettings((state) => state.connections?.enabled_models) ?? [];
  const saving = useSettings((state) => state.modelPreferencesSaving);
  const saveEnabledModels = useSettings((state) => state.saveEnabledModels);
  const key = (id: string) => `${provider}::${id}`;
  return (
    <section className="mt-8 space-y-3">
      <div>
        <h3 className="text-base font-semibold">Models</h3>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          Enabled models appear in the model selector of every chat.
        </p>
      </div>
      <ModelCards
        label={label}
        models={models}
        isChecked={(id) => enabled.includes(key(id))}
        onToggle={(id) => {
          const next = enabled.includes(key(id))
            ? enabled.filter((item) => item !== key(id))
            : [...enabled.filter((item) => !item.startsWith("demo::")), key(id)];
          // The banner above carries a failure and the store restores what was saved.
          void saveEnabledModels(next).catch(() => undefined);
        }}
        disabled={saving}
      />
    </section>
  );
}
function ModelChooser({
  provider,
  label,
  models,
  disabled,
  disabledLabel,
  loading = false,
  footer,
}: {
  provider: ProviderId;
  label: string;
  models: ModelChoice[];
  /** No key, or nobody signed in: the list is shown but cannot be changed. */
  disabled: boolean;
  disabledLabel: string;
  loading?: boolean;
  /** Anything the provider needs under the list — ChatGPT's add-a-model-by-id field. */
  footer?: ReactNode;
}) {
  const connections = useSettings((state) => state.connections);
  const saveEnabledModels = useSettings((state) => state.saveEnabledModels);
  const saving = useSettings((state) => state.modelPreferencesSaving);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[] | null>(null);
  const saved = useMemo(() => connections?.enabled_models ?? [], [connections?.enabled_models]);
  const visible = draft ?? saved;
  const enabledSet = useMemo(() => new Set(visible), [visible]);
  const keyFor = (id: string) => `${provider}::${id}`;
  const enabledHere = models.filter((model) => enabledSet.has(keyFor(model.id)));
  const changed = draft !== null && !sameModels(draft, saved);
  const triggerLabel = loading
    ? "Loading models…"
    : enabledHere.length === 0
      ? "Choose models"
      : enabledHere.length === 1
        ? enabledHere[0].name
        : `${enabledHere.length} models enabled`;
  function toggle(key: string) {
    if (saving) return;
    setDraft((current) => {
      const base = current ?? saved;
      return base.includes(key) ? base.filter((item) => item !== key) : [...base, key];
    });
  }
  async function save() {
    if (draft === null) return;
    try {
      const hasRealModel = draft.some((key) => !key.startsWith("demo::"));
      await saveEnabledModels(hasRealModel ? draft.filter((key) => !key.startsWith("demo::")) : draft);
      setOpen(false);
      setDraft(null);
    } catch {
      // The banner above carries the reason and the store restores what was saved.
    }
  }
  return (
    <div className="space-y-2 border-t border-hairline p-4">
      <Label>Models</Label>
      <PopoverPrimitive.Root
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          setDraft(next ? [...saved] : null);
        }}
      >
        <PopoverPrimitive.Trigger asChild>
          <Button
            type="button"
            variant="outline"
            aria-label={`Choose enabled ${label} models`}
            className="w-full justify-between font-normal"
            disabled={disabled || loading || saving}
          >
            <span className="truncate">{disabled ? disabledLabel : triggerLabel}</span>
            <ChevronsUpDown className="size-3.5 text-muted-foreground" />
          </Button>
        </PopoverPrimitive.Trigger>
        <PopoverPrimitive.Portal>
          {/* Held to the room the window has, so the list scrolls and the save button below it
            stays on screen: in a shorter window it opened past the bottom edge, out of reach. */}
          <PopoverPrimitive.Content
            align="start"
            sideOffset={6}
            collisionPadding={8}
            className="z-50 flex max-h-(--radix-popover-content-available-height) w-[460px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-xl bg-popover p-0 text-popover-foreground shadow-lg outline-none"
          >
            <ModelChecklist
              label={label}
              models={models}
              isChecked={(id) => enabledSet.has(keyFor(id))}
              onToggle={(id) => toggle(keyFor(id))}
              disabled={saving}
            />
            <div className="flex shrink-0 items-center justify-between gap-3 border-t border-hairline px-3 py-2.5">
              <span className="text-xs text-muted-foreground">{enabledHere.length} enabled</span>
              <Button type="button" size="sm" disabled={!changed || saving} onClick={() => void save()}>
                {saving ? "Saving…" : "Save enabled models"}
              </Button>
            </div>
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      </PopoverPrimitive.Root>
      {footer}
      <p className="text-xs text-muted-foreground">
        Enabled models appear in the model selector of every chat.
      </p>
    </div>
  );
}
export function ModelsSettings({
  connections,
  models,
  loading,
  savingKey,
  apiKey,
  onApiKeyChange,
  onConnect,
}: {
  connections: Connections | null;
  models: OpenRouterModel[];
  loading: boolean;
  savingKey: boolean;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  onConnect: () => void;
}) {
  const [flow, setFlow] = useState<
    | "choose"
    | {
        // "openai" is the OpenAI API and compatible servers, set up one model at a time.
        provider: ProviderId | "openai";
        adding: boolean;
        asDefault: boolean;
      }
    | null
  >(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const clearError = useSettings((state) => state.clearError);
  const action = useSettings((state) => state.action);
  if (!connections)
    return (
      <NativeView>
        <ViewHeading title="Models" />
        <p role="status">Loading providers…</p>
      </NativeView>
    );
  const openAI = {
    id: "openai" as const,
    name: "OpenAI & compatible servers",
    description: "Use an OpenAI API key, LM Studio or another server with the OpenAI protocol.",
  };
  const openAIConnected = connections.secrets.openai_api_key || Boolean(connections.coding_models?.length);
  const connected = [
    ...modelProviders.filter(({ id }) => isProviderConnected(connections, id)),
    ...(openAIConnected ? [openAI] : []),
  ];
  const available = [
    ...modelProviders.filter(({ id }) => !isProviderConnected(connections, id)),
    ...(openAIConnected ? [] : [openAI]),
  ];
  const defaultConnected = defaultModelConnected(connections);
  const ready = realModelEnabled(connections) && defaultConnected;
  const hasProvider = hasModelProvider(connections);
  const selected = typeof flow === "object" && flow !== null ? flow : null;
  const selectingModel =
    selected?.adding && selected.provider !== "openai" && isProviderConnected(connections, selected.provider);
  const selectedName = [...modelProviders, openAI].find(({ id }) => id === selected?.provider)?.name;
  const heading =
    flow === "choose"
      ? "Add a provider"
      : selectingModel
        ? selected?.asDefault
          ? "Choose your default model"
          : "Choose a model"
        : selected
          ? `${selected.adding ? "Connect" : "Manage"} ${selectedName}`
          : "Models";
  function back() {
    clearError();
    onApiKeyChange("");
    setFlow(null);
  }
  return (
    <NativeView>
      <ViewHeading
        title={heading}
        actions={
          flow ? (
            <Button variant="ghost" onClick={back} disabled={Boolean(action)}>
              <ArrowLeft /> Back to models
            </Button>
          ) : hasProvider && available.length ? (
            <Button variant="secondary" onClick={() => setFlow("choose")}>
              <Plus /> Add provider
            </Button>
          ) : undefined
        }
      />
      {flow === "choose" ? (
        <>
          <p className="mb-6 text-sm text-muted-foreground">
            Choose how you want to connect. You can add another provider later.
          </p>
          <div className="divide-y divide-hairline overflow-hidden rounded-xl bg-card">
            {available.map((provider) => (
              <button
                key={provider.id}
                type="button"
                aria-label={provider.name}
                className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                onClick={() => {
                  clearError();
                  setFlow({
                    provider: provider.id,
                    adding: true,
                    asDefault: !ready,
                  });
                }}
              >
                <ProviderLogo provider={provider.id} className="size-6" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{provider.name}</span>
                  <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                    {provider.description}
                  </span>
                </span>
                <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
              </button>
            ))}
          </div>
        </>
      ) : selected ? (
        selectingModel && selected.provider !== "openai" ? (
          <ProviderModelSetup provider={selected.provider} asDefault={selected.asDefault} onDone={back} />
        ) : (
          <>
            {selected.adding && selected.provider !== "openai" ? (
              <p className="mb-5 text-sm text-muted-foreground">
                Connect your account, then choose{" "}
                {selected.asDefault ? "your default coding model" : "a model for your conversations"}.
              </p>
            ) : null}
            {selected.provider === "codex" ? <ChatGPTSettings setup={selected.adding} /> : null}
            {selected.provider === "openrouter" ? (
              <OpenRouterSettings
                connections={connections}
                models={models}
                loading={loading}
                savingKey={savingKey}
                apiKey={apiKey}
                onApiKeyChange={onApiKeyChange}
                onConnect={onConnect}
                setup={selected.adding}
              />
            ) : null}
            {isKeyProvider(selected.provider) ? (
              <KeyProviderSettings provider={selected.provider} setup={selected.adding} />
            ) : null}
            {selected.provider === "ollama" ? <OllamaSettings setup={selected.adding} /> : null}
            {selected.provider === "openai" ? <DirectModelSection /> : null}
          </>
        )
      ) : !hasProvider ? (
        <div className="rounded-xl bg-card p-6">
          <Cpu className="mb-5 size-7 text-muted-foreground" />
          <h3 className="text-lg font-medium">Connect your first model provider</h3>
          <p className="mb-6 mt-2 max-w-md text-sm leading-6 text-muted-foreground">
            Connect an account you already use, or run local models with Ollama.
          </p>
          <Button onClick={() => setFlow("choose")}>
            <Plus /> Add provider
          </Button>
        </div>
      ) : (
        <>
          {!ready ? (
            <p className="mb-5 text-sm text-muted-foreground">
              Your provider is connected. Choose a default model to finish setup.
            </p>
          ) : null}
          {/* Shown before either gateway is connected, so a first chat can already require it. */}
          <ZeroDataRetentionSettings connections={connections} />
          {realModelEnabled(connections) ? <ModelRoles connections={connections} /> : null}
          <NativeSection
            title="Your providers"
            description={"Manage your connections and the models available in conversations."}
          >
            <div className="divide-y divide-hairline overflow-hidden rounded-xl bg-card">
              {connected.map(({ id, name }) => (
                <SettingsRow
                  key={id}
                  title={
                    <span className="flex items-center gap-2.5">
                      <ProviderLogo provider={id} className="size-5" />
                      {name}
                    </span>
                  }
                  description={
                    id === "codex"
                      ? connections.codex.email || "Signed in"
                      : id === "ollama"
                        ? connections.ollama!.base_url
                        : id === "openai"
                          ? `${connections.coding_models?.length ?? 0} models`
                          : "API key saved"
                  }
                  control={
                    <div className="flex gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        aria-label={`Manage ${name}`}
                        onClick={() => {
                          clearError();
                          setFlow({
                            provider: id,
                            adding: false,
                            asDefault: false,
                          });
                        }}
                      >
                        Manage
                      </Button>
                      {!ready && id !== "openai" ? (
                        <Button
                          size="sm"
                          aria-label={`Set up ${name}`}
                          onClick={() => {
                            clearError();
                            setFlow({
                              provider: id,
                              adding: true,
                              asDefault: true,
                            });
                          }}
                        >
                          {"Choose default model"}
                        </Button>
                      ) : null}
                    </div>
                  }
                />
              ))}
            </div>
          </NativeSection>

          {ready ? (
            <div className="mt-8">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSearchOpen(!searchOpen)}
                className="-ml-2.5 text-muted-foreground"
                aria-expanded={searchOpen}
              >
                <ChevronDown
                  aria-hidden
                  className={cn("transition-transform", !searchOpen && "-rotate-90")}
                />
                Web search settings
              </Button>
              {searchOpen ? <WebSearchSettings /> : null}
            </div>
          ) : null}
        </>
      )}
    </NativeView>
  );
}
function ProviderModelSetup({
  provider,
  asDefault,
  onDone,
}: {
  provider: ProviderId;
  asDefault: boolean;
  onDone: () => void;
}) {
  const connections = useSettings((state) => state.connections)!;
  const openRouterModels = useSettings((state) => state.openRouterModels);
  const keyModels = useSettings((state) => state.keyModels);
  const loadOpenRouterModels = useSettings((state) => state.loadOpenRouterModels);
  const loadKeyModels = useSettings((state) => state.loadKeyModels);
  const loadConnections = useSettings((state) => state.loadConnections);
  const saveEnabledModels = useSettings((state) => state.saveEnabledModels);
  const setDefaultModel = useSettings((state) => state.setDefaultModel);
  const [selection, setSelection] = useState("");
  const [custom, setCustom] = useState("");
  const [operation, setOperation] = useState<"save" | "refresh" | null>(null);
  const action = useSettings((state) => state.action);
  const catalogueLoading = useSettings((state) => state.modelOptionsLoading);
  const busy = operation !== null;
  const loadingModels =
    operation === "refresh" ||
    (provider === "openrouter" && catalogueLoading) ||
    (isKeyProvider(provider) && action === `saving-${provider}`);
  const [error, setError] = useState("");
  const catalogue = providerCatalogue(provider, connections, openRouterModels, keyModels);
  const id = custom.trim() || (catalogue.some((model) => model.id === selection) ? selection : "");
  const options = catalogue.map((model) => ({
    key: model.id,
    provider,
    model: model.id,
    name: model.name,
    detail: model.id,
  }));
  async function finish() {
    if (!id || busy) return;
    setOperation("save");
    setError("");
    try {
      const enabled = useSettings.getState().connections?.enabled_models ?? [];
      await saveEnabledModels([
        ...new Set([...enabled.filter((key) => !key.startsWith("demo::")), `${provider}::${id}`]),
      ]);
      if (asDefault)
        await setDefaultModel({
          provider,
          model: id,
          base_url: null,
          temperature: 0.1,
          max_tokens: null,
          reasoning_effort: null,
        });
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save this model. Try again.");
    } finally {
      setOperation(null);
    }
  }
  async function refresh() {
    setOperation("refresh");
    setError("");
    try {
      if (provider === "openrouter") await loadOpenRouterModels();
      else if (isKeyProvider(provider)) await loadKeyModels(provider);
      else if (provider === "ollama" && connections.ollama) {
        await api.connectOllama(connections.ollama.base_url, connections.ollama.num_ctx);
        await loadConnections();
      } else await loadConnections();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not refresh models.");
    } finally {
      setOperation(null);
    }
  }
  return (
    <div className="space-y-4 rounded-xl bg-card p-4">
      <p className="text-sm leading-6 text-muted-foreground">
        {"New conversations use this default. You can choose another model in each conversation."}
      </p>
      <ModelMenu
        availableModels
        options={options}
        selectedKey={selection}
        selectedName={catalogue.find((model) => model.id === selection)?.name ?? "Choose a model"}
        disabled={busy || loadingModels}
        loading={loadingModels}
        onChange={(value) => {
          setSelection(value);
          setCustom("");
        }}
        label={"Default model"}
        title="Available models"
        className="w-full"
      />
      {!catalogue.length && !loadingModels ? (
        <p role="status" className="text-sm text-muted-foreground">
          {provider === "ollama"
            ? "No models with tool support are available. Download one in Ollama, then refresh."
            : "No models loaded yet. Refresh to try again."}
        </p>
      ) : null}
      {provider === "codex" ? (
        <details className="text-sm text-muted-foreground">
          <summary className="cursor-pointer">Use another model ID</summary>
          <Input
            className="mt-3"
            aria-label="ChatGPT model ID"
            value={custom}
            onChange={(event) => setCustom(event.target.value)}
            placeholder="Enter a model ID your account supports"
            disabled={busy}
          />
        </details>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="flex items-center gap-3">
        <Button disabled={!id || busy || loadingModels} onClick={() => void finish()}>
          {operation === "save" ? "Saving…" : asDefault ? "Use this default model" : "Add model"}
        </Button>
        <Button variant="ghost" disabled={busy || loadingModels} onClick={() => void refresh()}>
          {loadingModels ? "Loading models…" : "Refresh models"}
        </Button>
      </div>
    </div>
  );
}
export function OpenRouterSettings({
  connections,
  models,
  loading,
  savingKey,
  apiKey,
  onApiKeyChange,
  onConnect,
  setup = false,
}: {
  setup?: boolean;
  connections: Connections | null;
  models: OpenRouterModel[];
  loading: boolean;
  savingKey: boolean;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  onConnect: () => void;
}) {
  const hasKey = Boolean(connections?.secrets.openrouter_api_key);
  return (
    <NativeSection
      className="pt-0"
      title={
        <span className="inline-flex items-center gap-3">
          <ProviderLogo provider="openrouter" />
          OpenRouter
        </span>
      }
      actions={<KeyStatus secret="openrouter_api_key" connected={hasKey} />}
    >
      <div className="overflow-hidden rounded-xl bg-card">
        <form
          className="space-y-2 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            onConnect();
          }}
        >
          <Label htmlFor="openrouter-key">API key</Label>
          <div className="flex gap-2">
            <div className="relative min-w-0 flex-1">
              <KeyRound className="absolute left-3 top-2.5 size-3.5 text-muted-foreground" />
              <Input
                id="openrouter-key"
                type="password"
                value={apiKey}
                onChange={(event) => onApiKeyChange(event.target.value)}
                placeholder={hasKey ? "Replace saved key" : "sk-or-v1-…"}
                className="pl-9"
              />
            </div>
            <Button type="submit" variant="secondary" disabled={!apiKey.trim() || savingKey}>
              {savingKey ? "Saving…" : hasKey ? "Update key" : "Connect"}
            </Button>
          </div>
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <EyeOff className="size-3" /> Saved locally in Agent/.wavefunction.env in Wavefunction’s data
            folder.
          </p>
        </form>
      </div>
      {!setup && hasKey ? (
        loading && !models.length ? (
          <p role="status" className="mt-8 text-sm text-muted-foreground">
            Loading models…
          </p>
        ) : (
          <ProviderModelCards provider="openrouter" label="OpenRouter" models={models} />
        )
      ) : null}
    </NativeSection>
  );
}
export function ModelRoles({ connections }: { connections: Connections }) {
  const setDefaultModel = useSettings((state) => state.setDefaultModel);
  return (
    <NativeSection
      title={"Default model"}
      description={"Used for new conversations. Switch models at any time in the conversation."}
    >
      <RoleModelMenu
        current={connections.model}
        connected={defaultModelConnected(connections)}
        label={"Default model"}
        onChange={setDefaultModel}
      />
    </NativeSection>
  );
}
function RoleModelMenu({
  current,
  connected,
  label,
  onChange,
}: {
  current: ModelSpec;
  /** Whether `current`'s provider can run it. */
  connected: boolean;
  label: string;
  onChange: (model: ModelSpec) => Promise<void>;
}) {
  const { options, selectedKey, selectedName, loading, specFor } = useModelOptions(current);
  // A default whose provider was removed stays saved for when it is back, but it is not the
  // default meanwhile: unlike a chat, which keeps its own model, the menu asks for another.
  return (
    <ModelMenu
      currentModel={connected ? current : undefined}
      onReasoningChange={(effort) => onChange({ ...current, reasoning_effort: effort ?? null })}
      options={connected ? options : options.filter((option) => option.key !== selectedKey)}
      loading={loading}
      selectedKey={connected ? selectedKey : ""}
      selectedName={connected ? selectedName : "Choose a model"}
      onChange={(key) => {
        const spec = specFor(key);
        if (spec) void onChange(spec).catch(() => undefined);
      }}
      label={label}
      title={label}
      align="end"
      className="max-w-56"
    />
  );
}
export function ChatGPTSettings({ setup = false }: { setup?: boolean }) {
  const connections = useSettings((state) => state.connections);
  const loadConnections = useSettings((state) => state.loadConnections);
  const startCodexLogin = useSettings((state) => state.startCodexLogin);
  const cancelCodexLogin = useSettings((state) => state.cancelCodexLogin);
  const signOutCodex = useSettings((state) => state.signOutCodex);
  const saveEnabledModels = useSettings((state) => state.saveEnabledModels);
  const action = useSettings((state) => state.action);
  const [customModel, setCustomModel] = useState("");
  const status = connections?.codex;
  const seeded = useMemo(() => connections?.codex_models ?? [], [connections?.codex_models]);
  const enabled = useMemo(() => new Set(connections?.enabled_models ?? []), [connections?.enabled_models]);
  // A model someone typed because the subscription serves it and this app does not know it.
  // Listed alongside the seeded ones so it can be switched off the same way it was added.
  const models = useMemo(() => {
    const known = new Set(seeded.map((model) => model.id));
    const typed = [...enabled]
      .filter((key) => key.startsWith("codex::"))
      .map((key) => key.slice("codex::".length))
      .filter((id) => id && !known.has(id))
      .map((id) => ({ id, name: id, description: "Added by you" }));
    return [...seeded, ...typed];
  }, [enabled, seeded]);
  const signingIn = Boolean(status?.signing_in);
  // The sign-in finishes on a redirect to a listener in the backend, not in the request that
  // started it, so the only way to see it land is to ask. Polling stops the moment it does.
  useEffect(() => {
    if (!signingIn) return;
    const timer = window.setInterval(() => void loadConnections(), 1500);
    return () => window.clearInterval(timer);
  }, [signingIn, loadConnections]);
  // Adding enables it: an id someone typed and then had to go and tick would be a step with
  // nothing behind it.
  async function addCustomModel() {
    const id = customModel.trim();
    if (!id || enabled.has(codexModelKey(id))) {
      setCustomModel("");
      return;
    }
    await saveEnabledModels(
      [...enabled].filter((key) => !key.startsWith("demo::")).concat(codexModelKey(id)),
    );
    setCustomModel("");
  }
  return (
    <NativeSection
      className="pt-0"
      title={
        <span className="inline-flex items-center gap-3">
          <ProviderLogo provider="codex" />
          ChatGPT
        </span>
      }
      description="Use your own ChatGPT Plus or Pro subscription instead of an API key."
      actions={
        <span className={cn("text-xs", status?.connected ? "text-success" : "text-muted-foreground")}>
          {status?.connected ? "Signed in" : "Not signed in"}
        </span>
      }
    >
      <div className="overflow-hidden rounded-xl bg-card">
        <div className="space-y-3 p-4">
          {status?.connected ? (
            <div className="flex items-center gap-2">
              <Check className="size-3.5 shrink-0 text-success" />
              <span className="min-w-0 flex-1 truncate text-sm">
                {status.email || "Signed in to ChatGPT"}
              </span>
              <Button type="button" variant="ghost" size="xs" onClick={() => void signOutCodex()}>
                Sign out
              </Button>
            </div>
          ) : signingIn ? (
            <div className="flex items-center gap-2">
              <LoaderCircle className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
              <span className="min-w-0 flex-1 text-sm text-muted-foreground">
                Finish signing in in your browser.
              </span>
              <Button type="button" variant="ghost" size="xs" onClick={() => void cancelCodexLogin()}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              type="button"
              variant="secondary"
              disabled={action === "signing-in-codex"}
              onClick={() =>
                void startCodexLogin().catch(() => {
                  // The error banner above carries the reason; a port already in use is the
                  // one people actually hit.
                })
              }
            >
              {action === "signing-in-codex" ? "Opening browser…" : "Sign in with ChatGPT"}
            </Button>
          )}

          {status?.error ? (
            <p className="flex items-start gap-1.5 text-xs text-destructive">
              <CircleAlert className="mt-0.5 size-3 shrink-0" /> {status.error}
            </p>
          ) : null}

          <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <EyeOff className="mt-0.5 size-3 shrink-0" /> The sign-in is saved in Wavefunction’s local
            application data.
          </p>
        </div>

        {!setup ? (
          <ModelChooser
            provider="codex"
            label="ChatGPT"
            models={models}
            disabled={!status?.connected}
            disabledLabel="Sign in first"
            footer={
              <form
                className="flex gap-2 pt-1"
                onSubmit={(event) => {
                  event.preventDefault();
                  void addCustomModel();
                }}
              >
                <Input
                  value={customModel}
                  onChange={(event) => setCustomModel(event.target.value)}
                  placeholder="Another model id, e.g. gpt-5.7-codex"
                  aria-label="Add a ChatGPT model by id"
                  disabled={!status?.connected}
                />
                <Button
                  type="submit"
                  variant="secondary"
                  disabled={!status?.connected || !customModel.trim()}
                >
                  <Plus /> Add
                </Button>
              </form>
            }
          />
        ) : null}

        <div className="border-t border-hairline p-4">
          <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <Info className="mt-0.5 size-3 shrink-0" />
            <span>
              This signs in the way the Codex CLI does, which OpenAI has not published for other apps — it can
              stop working.
            </span>
          </p>
        </div>
      </div>
    </NativeSection>
  );
}
export function OllamaSettings({ setup = false }: { setup?: boolean }) {
  const connections = useSettings((state) => state.connections);
  const saved = connections?.ollama;
  const [address, setAddress] = useState<string | null>(null);
  const [context, setContext] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const baseUrl = address ?? saved?.base_url ?? "http://127.0.0.1:11434";
  const numCtx = context ?? saved?.num_ctx ?? 32768;
  const models: ModelChoice[] = (saved?.models ?? []).map((model) => ({
    ...model,
    unavailable: model.capabilities.includes("tools") ? undefined : model.description,
  }));
  for (const key of connections?.enabled_models ?? []) {
    if (key.startsWith("ollama::") && !models.some((model) => `ollama::${model.id}` === key)) {
      const id = key.slice("ollama::".length);
      models.push({
        id,
        name: id,
        unavailable: "No longer installed on this server",
      });
    }
  }
  const changed = Boolean(saved && (baseUrl !== saved.base_url || numCtx !== saved.num_ctx));
  async function connect() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.connectOllama(baseUrl, numCtx);
      useSettings.setState((state) => ({
        connections: state.connections ? { ...state.connections, ollama: result } : state.connections,
      }));
      setAddress(result.base_url);
      setContext(result.num_ctx);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not connect to Ollama.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <NativeSection
      className="pt-0"
      title={
        <span className="inline-flex items-center gap-3">
          <ProviderLogo provider="ollama" />
          Ollama
        </span>
      }
      actions={
        <span className="text-xs text-muted-foreground">
          {error ? "Connection failed" : saved?.connected ? "Connected" : "Not connected"}
        </span>
      }
    >
      <div className="overflow-hidden rounded-xl bg-card">
        <form
          className="space-y-3 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <Label htmlFor="ollama-address">Server address</Label>
          <div className="flex gap-2">
            <Input
              id="ollama-address"
              value={baseUrl}
              onChange={(event) => setAddress(event.target.value)}
              disabled={busy}
              placeholder="http://127.0.0.1:11434"
            />
            <Button type="submit" variant="secondary" disabled={busy || !baseUrl.trim()}>
              {busy ? "Connecting…" : !saved ? "Connect" : changed ? "Save and connect" : "Refresh models"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Start Ollama and download a model in Ollama, then connect here. Refresh after adding models.
          </p>
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">Context size</summary>
            <div className="mt-3 space-y-2">
              <Label htmlFor="ollama-context">Context window (tokens)</Label>
              <Input
                id="ollama-context"
                type="number"
                min={1}
                step={1}
                value={numCtx}
                disabled={busy}
                onChange={(event) => setContext(Number(event.target.value))}
              />
              <p>
                More context lets the agent handle longer tasks but uses more memory. Lower this if your
                computer struggles.
              </p>
            </div>
          </details>
          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          {saved && !saved.models.length ? (
            <p className="text-xs text-muted-foreground">
              No models installed. Download a model in Ollama, then refresh.
            </p>
          ) : null}
        </form>
        {!setup ? (
          <ModelChooser
            provider="ollama"
            label="Ollama"
            models={models}
            disabled={!saved || changed || Boolean(error)}
            disabledLabel={changed ? "Save the server changes first" : "Connect to Ollama first"}
            loading={busy}
            footer={
              <p className="px-4 pb-3 text-xs text-muted-foreground">
                The agent needs a model that supports tool calling. Other models appear unavailable.
              </p>
            }
          />
        ) : null}
      </div>
    </NativeSection>
  );
}
/** A provider connected with an API key alone: Vercel AI Gateway, Anthropic or Gemini. */
export function KeyProviderSettings({
  provider,
  setup = false,
}: {
  provider: KeyProviderId;
  setup?: boolean;
}) {
  const { name, secret, variable, placeholder, label } = keyProviders[provider];
  const connections = useSettings((state) => state.connections);
  const models = useSettings((state) => state.keyModels[provider]);
  const loadKeyModels = useSettings((state) => state.loadKeyModels);
  const saveProviderKey = useSettings((state) => state.saveProviderKey);
  const action = useSettings((state) => state.action);
  const [key, setKey] = useState("");
  useEffect(() => {
    void loadKeyModels(provider);
  }, [loadKeyModels, provider]);
  const connected = Boolean(connections?.secrets[secret]);
  const saving = action === `saving-${provider}`;
  return (
    <NativeSection
      className="pt-0"
      title={
        <span className="inline-flex items-center gap-3">
          <ProviderLogo provider={provider} />
          {name}
        </span>
      }
      actions={<KeyStatus secret={secret} connected={connected} />}
    >
      <div className="overflow-hidden rounded-xl bg-card">
        <form
          className="space-y-2 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (key.trim()) void saveProviderKey(provider, key.trim()).then(() => setKey(""));
          }}
        >
          <Label htmlFor={`${provider}-key`}>API key</Label>
          <div className="flex gap-2">
            <div className="relative min-w-0 flex-1">
              <KeyRound className="absolute left-3 top-2.5 size-3.5 text-muted-foreground" />
              <Input
                id={`${provider}-key`}
                type="password"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder={connected ? "Replace saved key" : placeholder}
                className="pl-9"
              />
            </div>
            <Button type="submit" variant="secondary" disabled={!key.trim() || saving}>
              {saving ? "Saving…" : connected ? "Update key" : "Connect"}
            </Button>
          </div>
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <EyeOff className="size-3" /> Saved locally as {variable} in Wavefunction’s local application
            data.
          </p>
        </form>
      </div>
      {!setup && connected ? <ProviderModelCards provider={provider} label={label} models={models} /> : null}
    </NativeSection>
  );
}
function ZeroDataRetentionSettings({ connections }: { connections: Connections }) {
  const setZeroDataRetention = useSettings((state) => state.setZeroDataRetention);
  const saving = useSettings((state) => state.action === "saving-zdr");
  return (
    <NativeSection className="pt-0" title="Privacy">
      <div className="overflow-hidden rounded-xl bg-card">
        <SettingsRow
          title="Require zero data retention"
          description="OpenRouter and Vercel AI Gateway send chats only to providers that keep no prompts or responses, and a model with none stops with an error. Other providers set this on your account with them. Applies from your next message."
          control={
            <Switch
              aria-label="Require zero data retention"
              checked={Boolean(connections.require_zdr)}
              disabled={saving}
              onCheckedChange={(checked) => void setZeroDataRetention(checked)}
            />
          }
        />
      </div>
    </NativeSection>
  );
}

function WebSearchSettings() {
  const connections = useSettings((state) => state.connections);
  const saveSearchKey = useSettings((state) => state.saveSearchKey);
  const action = useSettings((state) => state.action);
  const [key, setKey] = useState("");
  const hasTavily = Boolean(connections?.secrets.tavily_api_key);
  // OpenRouter and a ChatGPT sign-in search for their own models; Tavily searches for any model.
  const available =
    hasTavily || Boolean(connections?.secrets.openrouter_api_key || connections?.codex.connected);
  async function connect() {
    const value = key.trim();
    if (!value) return;
    try {
      await saveSearchKey(value);
      setKey("");
      toast.success("Search key saved");
    } catch {
      // The persistent error banner carries the details.
    }
  }
  return (
    <NativeSection
      title="Web search"
      description="Chats on an OpenRouter or ChatGPT model search the web through that provider. Nothing to set up."
      actions={
        <span className={cn("text-xs", available ? "text-success" : "text-muted-foreground")}>
          {available ? "Available" : "Not set up"}
        </span>
      }
    >
      <div className="overflow-hidden rounded-xl bg-card">
        <form
          className="space-y-2 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <Label htmlFor="tavily-key">Tavily API key (optional)</Label>
          <div className="flex gap-2">
            <div className="relative min-w-0 flex-1">
              <KeyRound className="absolute left-3 top-2.5 size-3.5 text-muted-foreground" />
              <Input
                id="tavily-key"
                type="password"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder={hasTavily ? "Replace saved key" : "tvly-…"}
                className="pl-9"
              />
            </div>
            <Button type="submit" variant="secondary" disabled={!key.trim() || action === "saving-search"}>
              {action === "saving-search" ? "Saving…" : hasTavily ? "Update key" : "Save"}
            </Button>
            {hasTavily ? <ForgetKeyButton secret="tavily_api_key" label="Remove key" /> : null}
          </div>
          <p className="text-xs text-muted-foreground">
            Only needed for chats whose model runs somewhere other than OpenRouter or ChatGPT.
          </p>
        </form>
      </div>
    </NativeSection>
  );
}
