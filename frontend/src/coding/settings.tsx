import { lazy, Suspense, useEffect, useId, useState } from "react";
import {
  ArrowLeft,
  BarChart3,
  Bell,
  BookOpen,
  Cpu,
  FolderOpen,
  Info,
  LoaderCircle,
  Monitor,
  Settings2,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { NativeView, NativeSection, ViewHeading } from "@/components/native-view";
import { GitHubSignIn } from "@/components/github-setup";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { SettingsRow } from "@/views/settings-row";
import { useSettings } from "@/store/settings";
import { api } from "@/lib/api";
import { beginWindowDrag } from "@/lib/titlebar";
import { checkForUpdate, installUpdate, restartToUpdate, useAppUpdate } from "@/lib/app-update";
import { codingApi, type CodingSnapshot } from "./api";
import { CodingMarkdown } from "./markdown";
import { useCoding } from "./store";
import { NavigationHeader, SidebarToggle } from "./navigation-header";
import { ResizableSidebar } from "./sidebar-resize";
import { useWorkbenchPreference } from "./workbench-preference";
import "./settings-ui.css";
import { ComputerSettings } from "@/views/computer-settings";
import { GeneralSettings } from "@/views/general-settings";
import { ModelsSettings } from "@/views/models-settings";
import { NotificationSettings } from "@/views/notification-settings";
import { AnalyticsSettings } from "./analytics";
import { WorktreeCleanupSettings } from "./cleanup-settings";

const SkillsView = lazy(() =>
  import("@/views/skills-view").then((module) => ({
    default: module.SkillsView,
  })),
);

type SettingsPage =
  "general" | "notifications" | "models" | "analytics" | "skills" | "access" | "computer" | "about";
const pages = [
  { id: "general", title: "General", icon: Settings2 },
  { id: "notifications", title: "Notifications", icon: Bell },
  { id: "models", title: "Models", icon: Cpu },
  { id: "analytics", title: "Analytics", icon: BarChart3 },
  { id: "skills", title: "Skills", icon: BookOpen },
  { id: "access", title: "Access", icon: FolderOpen },
  { id: "computer", title: "Computer use", icon: Monitor },
  { id: "about", title: "About", icon: Info },
] as const;

function GeneralCodingSettings() {
  const { enabled, setEnabled } = useWorkbenchPreference();
  const id = useId();
  return (
    <div className="coding-general-settings">
      <GeneralSettings />
      <div className="mx-auto w-full max-w-3xl px-8 pb-10">
        <NativeSection title="Workbench">
          <label className="coding-workbench-setting" htmlFor={id}>
            <div>
              <span className="text-base font-medium">Show workbench</span>
              <p id={`${id}-description`} className="mt-0.5 text-xs leading-5 text-muted-foreground">
                Show Files, Terminal, and Changes in the repository sidebar.
              </p>
            </div>
            <Switch
              id={id}
              aria-label="Show workbench"
              aria-describedby={`${id}-description`}
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </label>
        </NativeSection>
        <WorktreeCleanupSettings />
      </div>
    </div>
  );
}

function AccessSettings() {
  const grants = useSettings((s) => s.grants);
  const load = useSettings((s) => s.loadGrants);
  const access = useCoding((s) => s.workspace?.access ?? "network");
  const mode = useCoding((s) => s.workspace?.default_mode ?? "auto");
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void load();
  }, [load]);
  async function change(save: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await save();
      await useCoding.getState().refresh();
      toast.success(done);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <NativeView>
      <ViewHeading title="Access" />
      <NativeSection
        title="Command execution"
        description="These permissions apply to Wavefunction and the repository terminal."
      >
        <div className="divide-y divide-hairline overflow-hidden rounded-xl bg-card">
          <SettingsRow
            title="Command access"
            description="Wavefunction asks before using another folder."
            control={
              <select
                aria-label="Command access"
                disabled={busy}
                value={access}
                onChange={(e) =>
                  void change(
                    () => codingApi.access(e.target.value as CodingSnapshot["access"]),
                    "Command access updated",
                  )
                }
                className="h-8 rounded-md border border-border bg-background px-2 text-sm"
              >
                <option value="sandboxed">Sandbox · no network</option>
                <option value="network">Sandbox with network</option>
              </select>
            }
          />
          <SettingsRow
            title="Mode for new chats"
            description="Choosing a mode in a chat changes this too. Full access still asks before commands that can't be undone."
            control={
              <select
                aria-label="Mode for new chats"
                disabled={busy}
                value={mode}
                onChange={(e) =>
                  void change(
                    () => codingApi.defaultMode(e.target.value as CodingSnapshot["default_mode"]),
                    "Mode for new chats updated",
                  )
                }
                className="h-8 rounded-md border border-border bg-background px-2 text-sm"
              >
                <option value="auto">Code</option>
                <option value="plan">Plan</option>
                <option value="full">Full access</option>
              </select>
            }
          />
        </div>
        <p className="mt-2 px-1 text-xs leading-5 text-muted-foreground">
          The macOS sandbox limits access to the project and granted folders, with support for system tools
          and temporary files.
        </p>
        {grants && !grants.sandbox_ok ? (
          <p role="alert" className="mt-2 px-1 text-xs text-destructive">
            {grants.sandbox_problem || "The required filesystem sandbox is unavailable on this platform."}
          </p>
        ) : null}
      </NativeSection>
      <NativeSection
        title="GitHub"
        description="git and gh in Wavefunction's commands use your GitHub CLI sign-in when network access is on."
      >
        <div className="rounded-xl bg-card px-4 py-3.5">
          <GitHubSignIn />
        </div>
      </NativeSection>
      <NativeSection
        title="Allowed folders"
        description="Selecting a repository adds it here. You can grant or revoke additional folders."
      >
        <div className="overflow-hidden rounded-xl bg-card">
          {grants?.grants.map((grant) => (
            <div key={grant.path} className="flex items-center gap-3 border-b border-hairline px-4 py-3">
              <FolderOpen className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 break-all font-mono text-xs">{grant.path}</span>
              <span className="text-xs text-muted-foreground">
                {grant.writable ? "Read & write" : "Read"}
              </span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`Revoke ${grant.path}`}
                onClick={() =>
                  void api
                    .revokeGrant(grant.path)
                    .then(load)
                    .catch((e) => toast.error(String(e.message)))
                }
              >
                <Trash2 />
              </Button>
            </div>
          ))}
          <form
            className="flex gap-2 px-4 py-3"
            onSubmit={(e) => {
              e.preventDefault();
              void api
                .addGrant(folder, true)
                .then(() => {
                  setFolder("");
                  return load();
                })
                .catch((e) => toast.error(String(e.message)));
            }}
          >
            <input
              aria-label="Additional folder"
              className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-xs"
              placeholder="/absolute/path/to/folder"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
            />
            <Button type="submit" size="sm" variant="secondary" disabled={!folder.trim()}>
              Allow folder
            </Button>
          </form>
        </div>
      </NativeSection>
    </NativeView>
  );
}
/** Settings › About: whether a newer release is out, what it changes, and installing it. */
function Updates() {
  const update = useAppUpdate();
  if (!("__TAURI_INTERNALS__" in window) || import.meta.env.DEV) return null;
  const busy = update.status === "checking" || update.status === "installing";
  return (
    <NativeSection title="Updates">
      <div className="overflow-hidden rounded-xl bg-card">
        <SettingsRow
          title={
            update.status === "available"
              ? `Version ${update.next} is available`
              : update.status === "ready"
                ? `Version ${update.next} is installed`
                : update.status === "installing"
                  ? `Installing ${update.next}…`
                  : update.status === "current"
                    ? "Wavefunction is up to date"
                    : "Updates"
          }
          description={
            update.status === "error"
              ? `Could not check for updates: ${update.error}`
              : "Wavefunction checks for new releases on launch and a few times a day."
          }
          control={
            <Button
              size="sm"
              variant={update.status === "available" || update.status === "ready" ? "default" : "secondary"}
              disabled={busy}
              onClick={() =>
                void (update.status === "available"
                  ? installUpdate()
                  : update.status === "ready"
                    ? restartToUpdate()
                    : checkForUpdate())
              }
            >
              {update.status === "available"
                ? "Install and restart"
                : update.status === "ready"
                  ? "Restart"
                  : update.status === "checking"
                    ? "Checking…"
                    : "Check for updates"}
            </Button>
          }
        />
        {update.status === "available" && update.notes && (
          <div className="max-h-80 overflow-y-auto border-t border-hairline px-4 py-3 text-sm">
            <CodingMarkdown>{update.notes}</CodingMarkdown>
          </div>
        )}
      </div>
    </NativeSection>
  );
}

export default function CodingSettings({
  initialPage = "general",
  onBack,
}: {
  initialPage?: SettingsPage;
  onBack: () => void;
}) {
  const [page, setPage] = useState<SettingsPage>(initialPage);
  const [sidebarVisible, setSidebarVisible] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const update = useAppUpdate();
  const connections = useSettings((s) => s.connections);
  const models = useSettings((s) => s.openRouterModels);
  const loading = useSettings((s) => s.modelOptionsLoading);
  const load = useSettings((s) => s.loadConnections);
  const error = useSettings((s) => s.error);
  const clearError = useSettings((s) => s.clearError);
  useEffect(() => {
    void load();
  }, [load]);
  return (
    <>
      {sidebarVisible ? (
        <ResizableSidebar>
          <aside
            className="coding-sidebar coding-settings-sidebar"
            aria-label="Settings sidebar"
            data-tauri-drag-region
            onMouseDown={beginWindowDrag}
          >
            <NavigationHeader onToggle={() => setSidebarVisible(false)} />
            <div className="coding-settings-back">
              <button type="button" onClick={onBack}>
                <ArrowLeft className="size-4" />
                Back to chat
              </button>
            </div>
            <nav className="coding-settings-nav" aria-label="Settings pages">
              {pages.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={page === item.id ? "selected" : ""}
                  aria-current={page === item.id ? "page" : undefined}
                  onClick={() => {
                    clearError();
                    setPage(item.id);
                  }}
                >
                  <item.icon className="size-4" />
                  {item.title}
                </button>
              ))}
            </nav>
          </aside>
        </ResizableSidebar>
      ) : null}
      <main className={`coding-main coding-settings-main ${sidebarVisible ? "" : "sidebar-hidden"}`}>
        <header
          className="shell-titlebar floating-titlebar"
          aria-label="Settings toolbar"
          data-tauri-drag-region
          onMouseDown={beginWindowDrag}
        >
          {!sidebarVisible ? <SidebarToggle collapsed onToggle={() => setSidebarVisible(true)} /> : null}
          <Settings2 className="size-4" />
          <span className="font-medium text-foreground">Settings</span>
        </header>
        <section
          className="coding-settings-content"
          aria-label={`${pages.find((item) => item.id === page)?.title} settings`}
        >
          {error ? (
            <div
              className="mx-5 mt-5 rounded-lg border border-destructive p-3 text-xs text-destructive"
              role="alert"
            >
              {error}
            </div>
          ) : null}
          {page === "general" ? (
            <GeneralCodingSettings />
          ) : page === "notifications" ? (
            <NotificationSettings />
          ) : page === "models" ? (
            <>
              <ModelsSettings
                connections={connections}
                models={models}
                loading={loading}
                savingKey={saving}
                apiKey={apiKey}
                onApiKeyChange={setApiKey}
                onConnect={() => {
                  setSaving(true);
                  void useSettings
                    .getState()
                    .saveOpenRouterKey(apiKey.trim())
                    .then(() => {
                      setApiKey("");
                      toast.success("Provider connected");
                    })
                    .catch(() => undefined)
                    .finally(() => setSaving(false));
                }}
              />
            </>
          ) : page === "analytics" ? (
            <AnalyticsSettings />
          ) : page === "access" ? (
            <AccessSettings />
          ) : page === "computer" ? (
            <ComputerSettings />
          ) : page === "skills" ? (
            <Suspense
              fallback={
                <div className="coding-settings-loading" role="status">
                  <LoaderCircle className="size-4 animate-spin" />
                  Loading settings…
                </div>
              }
            >
              <SkillsView />
            </Suspense>
          ) : (
            <NativeView>
              <ViewHeading title="About" />
              <NativeSection
                title="Wavefunction"
                description={`Version ${update.version ?? __APP_VERSION__}`}
              >
                <p className="text-sm leading-7 text-muted-foreground">
                  A local desktop coding agent with independent chats, projects, model providers, skills,
                  connectors, and computer use. Built with Tauri, React, FastAPI, and LangGraph. Your chats
                  and credentials stay in this application's own workspace. Model requests and connected tools
                  reach the services you select.
                </p>
                <p className="mt-4 text-xs text-muted-foreground">
                  MIT licensed. The license and third-party notices are included.
                </p>
              </NativeSection>
              <Updates />
            </NativeView>
          )}
        </section>
        {saving ? (
          <span className="sr-only">
            <LoaderCircle />
            Saving provider
          </span>
        ) : null}
      </main>
    </>
  );
}
