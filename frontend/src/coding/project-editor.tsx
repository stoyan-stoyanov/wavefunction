import { useState, type FormEvent } from "react";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { codingApi, type CodingProject } from "./api";
import { Field, FolderField } from "./folder-field";
import { ProjectAppearance } from "./project-appearance";
import type { ProjectIconName } from "./project-icons";
import { useCoding } from "./store";

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Creates a project, or edits one. `onSaved` hears which project was saved. */
export function ProjectEditor({
  project,
  onDone,
  onSaved,
}: {
  project?: CodingProject;
  onDone: () => void;
  onSaved?: (project: CodingProject) => void | Promise<void>;
}) {
  const [name, setName] = useState(project?.name ?? "");
  const [folder, setFolder] = useState(project?.folder ?? "");
  const [icon, setIcon] = useState<ProjectIconName>(project?.icon ?? "folder");
  const [iconColor, setIconColor] = useState<string | null>(project?.icon_color ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const folderRequired = !project || Boolean(project.folder);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || (folderRequired && !folder.trim()) || busy) return;
    setBusy(true);
    try {
      const saved = await codingApi.project(
        {
          name,
          folder: folder.trim() || null,
          icon,
          icon_color: iconColor,
        },
        project?.id,
      );
      await useCoding.getState().refresh();
      await onSaved?.(saved);
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value) onDone();
      }}
    >
      <DialogContent className="project-editor sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{project ? "Project settings" : "New project"}</DialogTitle>
          <DialogDescription>A collection of chats sharing a repository.</DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => void submit(e)} className="space-y-4">
          <Field label="Project name">
            <input
              autoFocus
              required
              maxLength={100}
              aria-label="Project name"
              placeholder="My project"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <FolderField value={folder} onChange={setFolder} required={folderRequired} />
          <ProjectAppearance icon={icon} color={iconColor} onIcon={setIcon} onColor={setIconColor} />
          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onDone}>
              Cancel
            </Button>
            <Button disabled={busy || !name.trim() || (folderRequired && !folder.trim())} type="submit">
              {busy ? <LoaderCircle className="animate-spin" /> : null}
              {project ? "Save changes" : "Create Project"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
