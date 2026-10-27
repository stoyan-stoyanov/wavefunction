import { useState } from "react";
import { ChevronDown, Plus, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { CodingThread } from "./api";
import { ProjectIcon } from "./project-appearance";
import { ProjectEditor } from "./project-editor";
import { useCoding } from "./store";
import "./welcome.css";

/** A new session's first screen. The session starts in the project of the one open before it;
 * the menu moves it to another project, or to a new one. */
export function Welcome({ thread }: { thread: CodingThread }) {
  const projects = useCoding((s) => s.workspace?.projects) ?? [];
  const patch = useCoding((s) => s.patch);
  const [creating, setCreating] = useState(false);
  const current = projects.find((project) => project.id === thread.project_id);
  // The store reports a move that fails.
  const move = async (projectId: string) => {
    if (projectId !== thread.project_id)
      await patch(thread.id, { project_id: projectId }).catch(() => undefined);
  };
  return (
    <div className="coding-welcome coding-welcome-simple">
      <h1>
        <Terminal aria-hidden="true" />
        <span>What are we building?</span>
      </h1>
      {current && !thread.parent_thread_id && !thread.archived ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="welcome-project"
              aria-label={`Project: ${current.name}`}
            >
              <ProjectIcon icon={current.icon} color={current.icon_color} />
              <span className="truncate">{current.name}</span>
              <ChevronDown />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" className="min-w-56">
            <DropdownMenuRadioGroup value={current.id} onValueChange={(id) => void move(id)}>
              {projects.map((project) => (
                <DropdownMenuRadioItem key={project.id} value={project.id}>
                  <ProjectIcon icon={project.icon} color={project.icon_color} />
                  <span className="truncate">{project.name}</span>
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setCreating(true)}>
              <Plus />
              New project
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
      {creating ? (
        <ProjectEditor onDone={() => setCreating(false)} onSaved={(project) => move(project.id)} />
      ) : null}
    </div>
  );
}
