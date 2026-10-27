import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, LoaderCircle, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { NativeSection, NativeView, ViewHeading } from "@/components/native-view";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { skillDisplayName } from "@/lib/skills";
import { codingApi, type CatalogSkill } from "@/coding/api";
import { useCoding } from "@/coding/store";

type Scope = "all" | CatalogSkill["scope"];
const SCOPES = [
  { value: "all", label: "All skills" },
  { value: "global", label: "Global" },
  { value: "project", label: "Project" },
  { value: "builtin", label: "Built-in" },
];
// Rows grouped as the General page groups settings: a raised card, hairlines between rows.
const CARD = "overflow-hidden rounded-xl bg-card";
const BODY_PLACEHOLDER = `# When to use this

Write the guidance itself here, in markdown.

## Steps

1. …`;
const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));
const day = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const moment = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
/** A skill to show after saving: a project's own, or the library's for Global. */
type Opening = { name: string; scope: CatalogSkill["scope"]; projectId?: string };
const keyOf = (skill: CatalogSkill) => `${skill.scope}:${skill.project_id ?? ""}:${skill.path ?? skill.name}`;

function scopeLabel(skill: CatalogSkill) {
  return skill.scope === "builtin"
    ? "Built-in"
    : skill.scope === "global"
      ? "Global"
      : `Project · ${skill.project_name}`;
}
/** Why a skill is or is not the one used where it applies. */
function precedence(skill: CatalogSkill) {
  if (skill.overridden_by) {
    const by =
      skill.overridden_by === "project"
        ? "another skill in this project"
        : `a ${skill.overridden_by === "global" ? "Global" : "Built-in"} skill`;
    return `Not used: ${by} with the same name takes precedence.`;
  }
  if (skill.overrides) return "Takes precedence over the Global skill with this name in this project.";
  return skill.condition ?? null;
}
function where(skill: CatalogSkill) {
  if (skill.scope === "builtin") return "Comes with Wavefunction and updates with it.";
  if (skill.source === "global") return "Saved outside Wavefunction. Edit the file to change it.";
  return skill.scope === "global"
    ? "Your Wavefunction library. Offered in every project."
    : "In the repository, shared through Git.";
}

function SkillRow({ skill, onOpen }: { skill: CatalogSkill; onOpen: () => void }) {
  const note = precedence(skill);
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Open skill ${skill.name} (${scopeLabel(skill)})`}
      className="flex w-full items-center gap-6 border-b border-hairline px-4 py-3.5 text-left transition-colors last:border-b-0 hover:bg-accent/40"
    >
      <span className="min-w-0 flex-1">
        <span
          className={`block truncate text-base font-medium${skill.overridden_by ? " text-muted-foreground" : ""}`}
        >
          {skillDisplayName(skill.name)}
        </span>
        <span className="mt-0.5 block truncate text-xs leading-5 text-muted-foreground">
          {skill.description}
        </span>
        {note ? <span className="block text-xs leading-5 text-muted-foreground">{note}</span> : null}
      </span>
      <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
        {skill.overridden_by ? (
          <span className="rounded-md bg-muted px-1.5 py-0.5">Overridden</span>
        ) : skill.modified_at ? (
          <>Edited {day.format(new Date(skill.modified_at))}</>
        ) : null}
        <ChevronRight className="size-4" />
      </span>
    </button>
  );
}

function SkillForm({
  skill,
  projects,
  onSaved,
  onCancel,
}: {
  skill?: CatalogSkill;
  projects: Array<{ id: string; name: string; folder: string | null }>;
  onSaved: (name: string, scope: CatalogSkill["scope"], projectId?: string) => void;
  onCancel: () => void;
}) {
  const editing = Boolean(skill);
  const [name, setName] = useState("");
  const [description, setDescription] = useState(skill?.description ?? "");
  const [body, setBody] = useState(skill?.body ?? "");
  const [target, setTarget] = useState("global");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  // As the backend names it: hyphenated lowercase, at most 64 characters.
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/, "");
  const project = projects.find((item) => item.id === target);
  const destination = project
    ? `${project.folder}/.agents/skills/${slug || "<name>"}/SKILL.md`
    : `Your Wavefunction library · ${slug || "<name>"}`;
  const changed = !skill || description !== skill.description || body !== skill.body;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem("");
    try {
      if (skill?.scope === "project") {
        await codingApi.updateProjectSkill(skill.project_id!, {
          path: skill.path!,
          digest: skill.digest!,
          description,
          body,
        });
        onSaved(skill.name, "project", skill.project_id);
      } else if (skill) {
        await api.updateSkill(skill.name, { description, body });
        onSaved(skill.name, "global");
      } else if (project) {
        const created = await codingApi.createProjectSkill(project.id, { name, description, body });
        onSaved(created.name, "project", project.id);
      } else {
        const created = await api.createSkill({ name, description, body });
        onSaved(created.name, "global");
      }
    } catch (error) {
      setProblem(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      onSubmit={submit}
      className={`${CARD} space-y-4 px-4 py-4`}
      aria-label={editing ? "Edit skill" : "New skill"}
    >
      {editing ? null : (
        <>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Name</span>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Code review checklist"
              aria-label="Skill name"
              className="h-9 bg-background text-sm dark:bg-background"
              autoFocus
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Save to</span>
            <select
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              aria-label="Save to"
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              <option value="global">Global · every project</option>
              {projects
                .filter((item) => item.folder)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    Project · {item.name}
                  </option>
                ))}
            </select>
            <span className="block truncate font-mono text-xs text-muted-foreground" title={destination}>
              {destination}
            </span>
          </label>
        </>
      )}
      <label className="block space-y-1.5">
        <span className="text-sm font-medium">When to use it</span>
        <Textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={2}
          placeholder="What it covers and when to read it, e.g. What to check before approving a change. Use when asked to review code."
          aria-label="Skill description"
          className="bg-background text-sm dark:bg-background"
        />
        <span className="block text-xs leading-5 text-muted-foreground">
          The agent sees only the name and this description until it decides to read the rest.
        </span>
      </label>
      <label className="block space-y-1.5">
        <span className="text-sm font-medium">Instructions</span>
        <Textarea
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={14}
          placeholder={BODY_PLACEHOLDER}
          aria-label="Skill instructions"
          className="bg-background font-mono text-xs dark:bg-background"
        />
      </label>
      {problem ? (
        <p role="alert" className="text-xs text-destructive">
          {problem}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={busy || !description.trim() || (!editing && !slug) || !changed}
        >
          {busy ? <LoaderCircle className="animate-spin" /> : null}
          {editing ? "Save" : "Create skill"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function SkillDetail({
  skill,
  skills,
  projects,
  onBack,
  onChanged,
}: {
  skill: CatalogSkill;
  skills: CatalogSkill[];
  projects: Array<{ id: string; name: string; folder: string | null }>;
  onBack: () => void;
  onChanged: (open?: Opening) => void;
}) {
  const [busy, setBusy] = useState(false);
  const note = precedence(skill);
  const customised = skills.find(
    (item) => item.scope === "global" && item.source === "library" && item.name === skill.name,
  );
  const editable = skill.editable && skill.source !== "global";
  async function customise() {
    setBusy(true);
    try {
      await api.createSkill({ name: skill.name, description: skill.description, body: skill.body });
      onChanged({ name: skill.name, scope: "global" });
    } catch (error) {
      toast.error(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    try {
      if (skill.scope === "project")
        await codingApi.deleteProjectSkill(skill.project_id!, skill.path!, skill.digest!);
      else await api.deleteSkill(skill.name);
      onChanged();
    } catch (error) {
      toast.error(errorText(error));
      setBusy(false);
    }
  }
  const rows: Array<[string, string]> = [
    ["Scope", scopeLabel(skill)],
    ["Name", skill.name],
  ];
  if (skill.path) rows.push(["Location", skill.path]);
  if (skill.created_at) rows.push(["Created", moment.format(new Date(skill.created_at))]);
  if (skill.modified_at) rows.push(["Last edited", moment.format(new Date(skill.modified_at))]);
  if (skill.version) rows.push(["Version", String(skill.version)]);
  return (
    <>
      <Button variant="ghost" size="sm" onClick={onBack} className="-ml-2 mb-4">
        <ChevronLeft /> All skills
      </Button>
      <ViewHeading title={skillDisplayName(skill.name)} />
      <NativeSection title="Details" description={`${where(skill)}${note ? ` ${note}` : ""}`}>
        <dl className={CARD}>
          {rows.map(([label, value]) => (
            <div
              key={label}
              className="flex items-center justify-between gap-6 border-b border-hairline px-4 py-3 last:border-b-0"
            >
              <dt className="text-sm font-medium">{label}</dt>
              <dd
                className={`min-w-0 truncate text-right text-xs text-muted-foreground${label === "Location" || label === "Name" ? " font-mono" : ""}`}
                title={value}
              >
                {value}
              </dd>
            </div>
          ))}
        </dl>
      </NativeSection>
      <NativeSection title="Instructions">
        {editable ? (
          <SkillForm
            key={`${skill.digest}:${skill.version}`}
            skill={skill}
            projects={projects}
            onSaved={(name, scope, projectId) => onChanged({ name, scope, projectId })}
            onCancel={onBack}
          />
        ) : (
          <div className={`${CARD} space-y-3 px-4 py-4`}>
            <p className="text-sm">{skill.description}</p>
            <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground">
              {skill.body}
            </pre>
          </div>
        )}
      </NativeSection>
      {skill.scope === "builtin" && !skill.condition ? (
        <NativeSection title="Customise">
          <div className={`${CARD} flex items-center justify-between gap-6 px-4 py-3.5`}>
            <p className="text-xs leading-5 text-muted-foreground">
              {customised
                ? "You have a Global copy of this skill, which is used instead."
                : "Copy it to your Global skills to change it. Your copy is used instead."}
            </p>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() =>
                customised ? onChanged({ name: skill.name, scope: "global" }) : void customise()
              }
            >
              {customised ? "Open your copy" : "Customise"}
            </Button>
          </div>
        </NativeSection>
      ) : null}
      {editable ? (
        <NativeSection title="Delete">
          <div className={`${CARD} flex items-center justify-between gap-6 px-4 py-3.5`}>
            <p className="text-xs leading-5 text-muted-foreground">
              {skill.scope === "project"
                ? "Deletes the SKILL.md file from the repository."
                : "Stops offering it to new tasks. Running tasks keep their copy."}
            </p>
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive"
              disabled={busy}
              onClick={() => void remove()}
            >
              <Trash2 /> Delete
            </Button>
          </div>
        </NativeSection>
      ) : null}
    </>
  );
}

export function SkillsView() {
  const workspaceProjects = useCoding((state) => state.workspace?.projects);
  const projects = useMemo(() => workspaceProjects ?? [], [workspaceProjects]);
  const [skills, setSkills] = useState<CatalogSkill[] | null>(null);
  const [failed, setFailed] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [scope, setScope] = useState<Scope>("all");
  const [query, setQuery] = useState("");
  const [opened, setOpened] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const show = useCallback((catalog: { skills: CatalogSkill[]; warnings: string[] }, open?: Opening) => {
    setSkills(catalog.skills);
    setWarnings(catalog.warnings);
    setFailed("");
    const match =
      open &&
      catalog.skills.find(
        (item) =>
          item.name === open.name &&
          item.scope === open.scope &&
          (open.scope !== "global" || item.source === "library") &&
          (!open.projectId || item.project_id === open.projectId),
      );
    setOpened(match ? keyOf(match) : null);
  }, []);
  const load = useCallback(
    (open?: Opening) =>
      codingApi
        .skillCatalog()
        .then((catalog) => show(catalog, open))
        .catch((error) => {
          // Leave a detail that may be stale (just deleted or changed) for the error and Retry.
          setOpened(null);
          setFailed(errorText(error));
        }),
    [show],
  );
  useEffect(() => {
    let alive = true;
    codingApi
      .skillCatalog()
      .then((catalog) => {
        if (alive) show(catalog);
      })
      .catch((error) => {
        if (alive) setFailed(errorText(error));
      });
    return () => {
      alive = false;
    };
  }, [show]);
  const visible = useMemo(
    () =>
      (skills ?? []).filter(
        (skill) =>
          (scope === "all" || skill.scope === scope) &&
          `${skill.name} ${skill.description} ${skill.project_name ?? ""}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [skills, scope, query],
  );
  const current = skills?.find((skill) => keyOf(skill) === opened) ?? null;
  const groups = useMemo(() => {
    const out: Array<[string, CatalogSkill[]]> = [
      ["Global", visible.filter((skill) => skill.scope === "global")],
    ];
    for (const project of projects)
      out.push([
        `Project · ${project.name}`,
        visible.filter((skill) => skill.scope === "project" && skill.project_id === project.id),
      ]);
    out.push(["Built-in", visible.filter((skill) => skill.scope === "builtin")]);
    return out.filter(([, items]) => items.length);
  }, [visible, projects]);
  if (current) {
    return (
      <NativeView>
        <SkillDetail
          skill={current}
          skills={skills ?? []}
          projects={projects}
          // Back reloads, so reopening a skill that changed elsewhere reads its current file.
          onBack={() => {
            setOpened(null);
            void load();
          }}
          onChanged={(open) => void load(open)}
        />
      </NativeView>
    );
  }
  if (creating) {
    return (
      <NativeView>
        <Button variant="ghost" size="sm" onClick={() => setCreating(false)} className="-ml-2 mb-4">
          <ChevronLeft /> All skills
        </Button>
        <ViewHeading title="New skill" />
        <NativeSection
          title="Skill"
          description="The agent sees the name and description, and reads the instructions when the skill applies."
        >
          <SkillForm
            projects={projects}
            onCancel={() => setCreating(false)}
            onSaved={(name, scope, projectId) => {
              setCreating(false);
              void load({ name, scope, projectId });
            }}
          />
        </NativeSection>
      </NativeView>
    );
  }
  return (
    <NativeView>
      <ViewHeading
        title="Skills"
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus /> New skill
          </Button>
        }
      />
      <p className="-mt-4 text-xs leading-5 text-muted-foreground">
        Guidance the agent reads when it applies. Project skills live in a repository&apos;s .agents/skills
        folder and take precedence over Global ones, which take precedence over Built-in. You can also ask the
        agent to create or update a skill.
      </p>
      {/* Search and scope filter laid out as on the Connectors page. */}
      <div className="mt-6 mb-8 flex flex-wrap gap-3">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search skills"
            aria-label="Search skills"
            className="pl-9"
          />
        </div>
        <div className="relative">
          <select
            aria-label="Filter by scope"
            value={scope}
            onChange={(event) => setScope(event.target.value as Scope)}
            className="h-8 appearance-none rounded-lg border border-input bg-transparent py-1 pl-3 pr-9 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
          >
            {SCOPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <ChevronDown
            aria-hidden
            className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
        </div>
      </div>
      {failed ? (
        <div role="alert" className={`${CARD} flex items-center justify-between gap-6 px-4 py-3.5`}>
          <span className="text-xs leading-5 text-destructive">Skills could not be loaded: {failed}</span>
          <Button size="sm" variant="secondary" onClick={() => void load()}>
            Retry
          </Button>
        </div>
      ) : skills === null ? (
        <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          <LoaderCircle className="size-3.5 animate-spin" /> Loading skills…
        </p>
      ) : groups.length ? (
        <div aria-label="Skills">
          {groups.map(([title, items]) => (
            <NativeSection key={title} title={title}>
              <div className={CARD}>
                {items.map((skill) => (
                  <SkillRow key={keyOf(skill)} skill={skill} onOpen={() => setOpened(keyOf(skill))} />
                ))}
              </div>
            </NativeSection>
          ))}
        </div>
      ) : (
        <p className={`${CARD} px-4 py-8 text-center text-xs text-muted-foreground`}>
          {query || scope !== "all" ? "No skills match." : "No skills yet."}
        </p>
      )}
      {warnings.length ? (
        <details className="mt-4 text-xs text-muted-foreground">
          <summary>
            {warnings.length} skill {warnings.length === 1 ? "file" : "files"} could not be read
          </summary>
          <ul className="mt-2 space-y-1 font-mono">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </NativeView>
  );
}
