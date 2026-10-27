import type { ReactNode } from "react";
import { FolderOpen } from "lucide-react";
import { toast } from "sonner";
import { open } from "@tauri-apps/plugin-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));
async function chooseFolder(current: string, setValue: (value: string) => void) {
  // The folder picker is the desktop app's; a browser can only take a typed path.
  if (!("__TAURI_INTERNALS__" in window)) {
    toast.info("Choosing a folder needs the desktop app. Type its path instead.");
    return;
  }
  const value = await open({
    directory: true,
    multiple: false,
    defaultPath: current || undefined,
  });
  if (typeof value === "string") setValue(value);
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="coding-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export function FolderField({
  value,
  onChange,
  required = false,
}: {
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <Field label="Repository folder">
      <div className="coding-folder-field">
        <input
          aria-label="Repository folder"
          required={required}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="/path/to/your/project"
        />
        <Button
          type="button"
          variant="secondary"
          size="icon-sm"
          aria-label="Choose folder"
          onClick={() => void chooseFolder(value, onChange).catch((e) => toast.error(errorText(e)))}
        >
          <FolderOpen />
        </Button>
      </div>
    </Field>
  );
}

/** The same folder choice with the settings pages' label and input. */
export function FolderInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <div className="space-y-2">
      <Label htmlFor="project-folder">Repository folder</Label>
      <div className="flex gap-2">
        <Input
          id="project-folder"
          aria-label="Repository folder"
          required
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="/path/to/your/project"
        />
        <Button
          type="button"
          variant="secondary"
          size="icon"
          aria-label="Choose folder"
          onClick={() => void chooseFolder(value, onChange).catch((e) => toast.error(errorText(e)))}
        >
          <FolderOpen />
        </Button>
      </div>
    </div>
  );
}
