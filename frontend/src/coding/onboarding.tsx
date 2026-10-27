import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ArrowLeft, ChevronRight, LoaderCircle } from "lucide-react";
import { ProviderLogo } from "@/components/provider-logo";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import {
  hasModelProvider,
  isKeyProvider,
  isProviderConnected,
  modelProviders,
  providerCatalogue,
  realModelEnabled,
  type ProviderId,
} from "@/lib/models";
import { beginWindowDrag } from "@/lib/titlebar";
import { useSettings } from "@/store/settings";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ChatGPTSettings,
  KeyProviderSettings,
  ModelCards,
  OllamaSettings,
  OpenRouterSettings,
} from "@/views/models-settings";
import { codingApi } from "./api";
import { FolderInput } from "./folder-field";
import { DirectModelSection } from "./model-setup";
import { AppVisual, ModelsVisual, ProvidersVisual } from "./onboarding-visuals";
import { ProjectAppearance } from "./project-appearance";
import type { ProjectIconName } from "./project-icons";
import { useCoding } from "./store";
import "./onboarding.css";

type Step = "provider" | "models" | "project";
const steps: Array<{ id: Step; title: string }> = [
  { id: "provider", title: "Connect a provider" },
  { id: "models", title: "Choose models" },
  { id: "project", title: "Create a project" },
];
const order = (step: Step) => steps.findIndex(({ id }) => id === step);
type Choice = ProviderId | "openai";
const choices: Array<{ id: Choice; name: string; description: string }> = [
  ...modelProviders,
  {
    id: "openai",
    name: "OpenAI & compatible servers",
    description: "Use an OpenAI API key, LM Studio or another server with the OpenAI protocol.",
  },
];

/**
 * The whole window until the app can be used: a provider, at least one of its models, and a
 * project. Which step shows follows from what is already set up, so nothing records that
 * onboarding happened; Back only revisits an earlier step.
 */
export function Onboarding() {
  const connections = useSettings((state) => state.connections);
  const error = useSettings((state) => state.error);
  const [view, setView] = useState<Step | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  // The project as it is being filled in, previewed in the app beside the form.
  const [project, setProject] = useState<ProjectPreview>({ name: "", icon: "folder", color: null });
  if (!connections)
    return (
      <Frame>
        {error ? (
          <div className="space-y-4 text-center">
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
            <Button
              onClick={() =>
                void useSettings
                  .getState()
                  .loadConnections()
                  .catch(() => undefined)
              }
            >
              Try again
            </Button>
          </div>
        ) : (
          <LoaderCircle aria-label="Loading" className="mx-auto size-5 animate-spin text-muted-foreground" />
        )}
      </Frame>
    );
  // Signing out of every provider leaves its models enabled, so a provider is checked first.
  const reached: Step = !hasModelProvider(connections)
    ? "provider"
    : realModelEnabled(connections)
      ? "project"
      : "models";
  const step = view && order(view) < order(reached) ? view : reached;
  // The provider chosen in step one, or else the first one connected, offers its models.
  const listed = modelProviders.map(({ id }) => id).filter((id) => isProviderConnected(connections, id));
  const modelsFrom = choice && choice !== "openai" && listed.includes(choice) ? choice : listed[0];
  const toModels = () => setView(reached === "project" ? "models" : null);
  // Back to the list, not to the provider last chosen: it is connected and would move on again.
  const toProviders = () => {
    setChoice(null);
    setView("provider");
  };
  const visual =
    step === "provider" ? (
      <ProvidersVisual />
    ) : step === "models" ? (
      <ModelsVisual />
    ) : (
      <AppVisual {...project} />
    );
  return (
    <Frame step={step} visual={visual}>
      {error ? (
        <p role="alert" className="mb-4 rounded-lg border border-destructive p-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {step === "provider" ? (
        <ConnectProvider
          choice={choice}
          onChoose={(id) => {
            useSettings.getState().clearError();
            setChoice(id);
            if (id && id !== "openai" && isProviderConnected(connections, id)) toModels();
          }}
          onConnected={toModels}
          onModelEnabled={() => setView(null)}
          onContinue={hasModelProvider(connections) ? toModels : undefined}
        />
      ) : step === "models" ? (
        modelsFrom ? (
          <ChooseModels
            key={modelsFrom}
            provider={modelsFrom}
            onBack={toProviders}
            onDone={() => setView(null)}
          />
        ) : (
          <>
            <DirectModelSection onEnabled={() => setView(null)} />
            <BackButton onClick={toProviders} />
          </>
        )
      ) : (
        <CreateProject onBack={() => setView("models")} onPreview={setProject} />
      )}
    </Frame>
  );
}

type ProjectPreview = { name: string; icon: ProjectIconName; color: string | null };

function Frame({ step, visual, children }: { step?: Step; visual?: ReactNode; children: ReactNode }) {
  const title = steps.find(({ id }) => id === step)?.title;
  const number = step ? order(step) + 1 : null;
  return (
    <div className="onboarding-shell fixed inset-0 z-40 flex flex-col lg:flex-row">
      {/* The name, small at the top right, clear of the window controls, and what the current step
          sets up. Too narrow for two columns, the column is a bar along the top without the
          illustration. */}
      <aside
        className="onboarding-aside flex shrink-0 flex-col lg:w-2/5 lg:pb-8"
        data-tauri-drag-region
        onMouseDown={beginWindowDrag}
      >
        {/* Level with the window controls, and as far from the column's edge as they are from the
            window's: 16 window points (BUTTON_X in src-tauri/src/lib.rs), in zoomed CSS pixels. */}
        <div
          className="flex shrink-0 items-center justify-end gap-2"
          style={{
            height: "calc(var(--traffic-light-center) * 2)",
            paddingRight: "calc(16px / var(--app-zoom, 1))",
          }}
        >
          {/* The mark without its tile, in the text colour: light on dark, dark on light. */}
          <span
            aria-hidden
            className="size-5 shrink-0 bg-foreground [mask:url(/wavefunction-mark.svg)_center/contain_no-repeat]"
          />
          <h1 className="onboarding-wordmark text-base font-semibold leading-none tracking-[-0.03em]">
            Wavefunction
          </h1>
        </div>
        {visual ? (
          <div
            key={step}
            className="onboarding-fade hidden min-h-0 flex-1 items-center justify-center px-10 py-8 lg:flex"
          >
            {visual}
          </div>
        ) : null}
      </aside>
      <div className="onboarding-pane flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
        <header
          className="hidden h-10 shrink-0 lg:block"
          data-tauri-drag-region
          onMouseDown={beginWindowDrag}
        />
        {/* The title stays where it is as a step's content changes below it: choosing a provider
            swaps its list for a shorter form. */}
        <main className="mx-auto w-full max-w-xl flex-1 px-6 pb-12 pt-10 lg:pt-14">
          {title ? (
            <h2 className="mb-8 flex items-center gap-3 text-2xl font-semibold tracking-[-0.02em]">
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-foreground text-sm font-medium tracking-normal text-background">
                {number}
              </span>
              {title}
            </h2>
          ) : null}
          {children}
        </main>
      </div>
    </div>
  );
}

function BackButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  return (
    <Button variant="ghost" onClick={onClick} disabled={disabled}>
      <ArrowLeft /> Back
    </Button>
  );
}

function ConnectProvider({
  choice,
  onChoose,
  onConnected,
  onModelEnabled,
  onContinue,
}: {
  choice: Choice | null;
  onChoose: (choice: Choice | null) => void;
  onConnected: () => void;
  onModelEnabled: () => void;
  onContinue?: () => void;
}) {
  const connections = useSettings((state) => state.connections)!;
  const openRouterModels = useSettings((state) => state.openRouterModels);
  const loadingModels = useSettings((state) => state.modelOptionsLoading);
  const [apiKey, setApiKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const connected = choice && choice !== "openai" && isProviderConnected(connections, choice);
  // Signing in finishes in the browser and a key is checked by the server, so the step moves on
  // when the connection shows up rather than when a button is pressed.
  useEffect(() => {
    if (connected) onConnected();
  }, [connected, onConnected]);
  if (choice && !connected)
    return (
      // The shared provider screens bring Settings' cards; here they sit on the window, with
      // their content lined up with the text around it.
      <div className="space-y-4 [&_.bg-card]:overflow-visible [&_.bg-card]:bg-transparent [&_.bg-card>*]:px-0 [&_.bg-card>:first-child]:pt-0">
        {choice === "codex" ? <ChatGPTSettings setup /> : null}
        {choice === "openrouter" ? (
          <OpenRouterSettings
            setup
            connections={connections}
            models={openRouterModels}
            loading={loadingModels}
            savingKey={savingKey}
            apiKey={apiKey}
            onApiKeyChange={setApiKey}
            onConnect={() => {
              setSavingKey(true);
              void useSettings
                .getState()
                .saveOpenRouterKey(apiKey.trim())
                .then(() => setApiKey(""))
                .catch(() => undefined)
                .finally(() => setSavingKey(false));
            }}
          />
        ) : null}
        {isKeyProvider(choice) ? <KeyProviderSettings provider={choice} setup /> : null}
        {choice === "ollama" ? <OllamaSettings setup /> : null}
        {choice === "openai" ? <DirectModelSection onEnabled={onModelEnabled} /> : null}
        <BackButton onClick={() => onChoose(null)} />
      </div>
    );
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Choose your model provider. You can add more providers later in Settings.
      </p>
      <div className="flex flex-col gap-2">
        {choices.map((item) => {
          const ready = item.id !== "openai" && isProviderConnected(connections, item.id);
          return (
            <button
              key={item.id}
              type="button"
              aria-label={item.name}
              className="flex w-full items-center gap-3 rounded-xl bg-card px-4 py-3.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => onChoose(item.id)}
            >
              <ProviderLogo provider={item.id} className="size-6" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{item.name}</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">{item.description}</span>
              </span>
              {ready ? <span className="text-xs text-success">Connected</span> : null}
              <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            </button>
          );
        })}
      </div>
      {onContinue ? (
        <div className="flex justify-end">
          <Button onClick={onContinue}>Continue</Button>
        </div>
      ) : null}
    </div>
  );
}

function ChooseModels({
  provider,
  onBack,
  onDone,
}: {
  provider: ProviderId;
  onBack: () => void;
  onDone: () => void;
}) {
  const connections = useSettings((state) => state.connections)!;
  const openRouterModels = useSettings((state) => state.openRouterModels);
  const keyModels = useSettings((state) => state.keyModels);
  const catalogueLoading = useSettings((state) => state.modelOptionsLoading);
  const prefix = `${provider}::`;
  // In the order they were picked: the first is the default for new chats.
  const [picked, setPicked] = useState(() =>
    connections.enabled_models.filter((key) => key.startsWith(prefix)).map((key) => key.slice(prefix.length)),
  );
  const [busy, setBusy] = useState<"save" | "refresh" | null>(null);
  const catalogue = providerCatalogue(provider, connections, openRouterModels, keyModels);
  const name = modelProviders.find(({ id }) => id === provider)!.name;
  const loading = busy === "refresh" || (provider === "openrouter" && catalogueLoading);
  useEffect(() => {
    const settings = useSettings.getState();
    if (provider === "openrouter" && !settings.openRouterModels.length) void settings.loadOpenRouterModels();
    if (isKeyProvider(provider) && !settings.keyModels[provider].length)
      void settings.loadKeyModels(provider);
  }, [provider]);
  async function refresh() {
    setBusy("refresh");
    const settings = useSettings.getState();
    try {
      if (provider === "openrouter") await settings.loadOpenRouterModels();
      else if (isKeyProvider(provider)) await settings.loadKeyModels(provider);
      else if (provider === "ollama" && connections.ollama) {
        await api.connectOllama(connections.ollama.base_url, connections.ollama.num_ctx);
        await settings.loadConnections();
      } else await settings.loadConnections();
    } catch {
      // The banner above carries the reason.
    } finally {
      setBusy(null);
    }
  }
  async function save() {
    if (!picked.length || busy) return;
    setBusy("save");
    const settings = useSettings.getState();
    try {
      const others = connections.enabled_models.filter(
        (key) => !key.startsWith("demo::") && !key.startsWith(prefix),
      );
      await settings.saveEnabledModels([...others, ...picked.map((id) => prefix + id)]);
      await settings.setDefaultModel({
        provider,
        model: picked[0],
        base_url: null,
        temperature: 0.1,
        max_tokens: null,
        reasoning_effort: null,
      });
      onDone();
    } catch {
      // The banner above carries the reason and the store restores what was saved.
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Pick the {name} models you want in chats. You can change them later in Settings.
      </p>
      {catalogue.length ? (
        <ModelCards
          label={name}
          models={catalogue}
          isChecked={(id) => picked.includes(id)}
          onToggle={(id) =>
            setPicked((current) =>
              current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
            )
          }
          disabled={busy !== null}
        />
      ) : (
        <p role="status" className="py-4 text-sm text-muted-foreground">
          {loading
            ? "Loading models…"
            : provider === "ollama"
              ? "No models with tool support are installed. Download one in Ollama, then refresh."
              : "No models loaded yet. Refresh to try again."}
        </p>
      )}
      {/* Stays in reach at the bottom of the window however long the list is. */}
      <div className="sticky bottom-0 -mx-6 flex items-center gap-2 border-t border-hairline bg-background px-6 py-3">
        <BackButton onClick={onBack} disabled={busy !== null} />
        <Button variant="ghost" disabled={busy !== null} onClick={() => void refresh()}>
          {busy === "refresh" ? "Loading models…" : "Refresh models"}
        </Button>
        <Button className="ml-auto" disabled={!picked.length || busy !== null} onClick={() => void save()}>
          {busy === "save" ? <LoaderCircle className="animate-spin" /> : null}
          Continue
        </Button>
      </div>
    </div>
  );
}

function CreateProject({
  onBack,
  onPreview,
}: {
  onBack: () => void;
  onPreview: (project: ProjectPreview) => void;
}) {
  const [folder, setFolder] = useState("");
  const [name, setName] = useState<string | null>(null);
  const [icon, setIcon] = useState<ProjectIconName>("folder");
  const [color, setColor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Named after its folder until a name is typed.
  const projectName = name ?? folder.trim().replace(/\/+$/, "").split("/").pop() ?? "";
  useEffect(() => onPreview({ name: projectName, icon, color }), [onPreview, projectName, icon, color]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim() || !folder.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const project = await codingApi.project({
        name: projectName.trim(),
        folder: folder.trim(),
        icon,
        icon_color: color,
      });
      // Creating its first chat also refreshes the workspace, which ends onboarding.
      await useCoding.getState().newThread(project.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBusy(false);
    }
  }
  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-4">
      <p className="text-sm text-muted-foreground">
        A project is a folder you work in, usually a code repository. Every chat you start in the project
        works in that folder.
      </p>
      <div className="space-y-4">
        <FolderInput value={folder} onChange={setFolder} />
        <div className="space-y-2">
          <Label htmlFor="project-name">Project name</Label>
          <Input
            id="project-name"
            required
            maxLength={100}
            aria-label="Project name"
            placeholder="My project"
            value={projectName}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <ProjectAppearance icon={icon} color={color} onIcon={setIcon} onColor={setColor} />
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
        <BackButton onClick={onBack} disabled={busy} />
        <Button className="ml-auto" type="submit" disabled={busy || !projectName.trim() || !folder.trim()}>
          {busy ? <LoaderCircle className="animate-spin" /> : null}
          Create project
        </Button>
      </div>
    </form>
  );
}
