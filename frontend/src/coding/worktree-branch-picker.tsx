import { useMemo, useState } from "react";
import { ChevronDown, GitBranch } from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { GitBranches } from "./api";

const preferredBranches = ["main", "develop", "master", "development", "dev", "trunk"];

export function WorktreeBranchPicker({
  branches,
  value,
  disabled,
  error,
  onChange,
}: {
  branches: GitBranches | null;
  value: string;
  disabled?: boolean;
  error: boolean;
  onChange: (ref: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const choices = useMemo(() => {
    const items = (branches?.branches ?? [])
      .map((branch) => {
        const remote = branch.ref.startsWith("refs/remotes/");
        const name = remote
          ? branch.ref.slice("refs/remotes/".length).split("/").slice(1).join("/")
          : branch.ref.slice("refs/heads/".length);
        const priority = preferredBranches.indexOf(name);
        return {
          ...branch,
          kind: remote ? "Remote" : "Local",
          group: priority < 0 ? (remote ? 3 : 2) : remote ? 1 : 0,
          priority,
        };
      })
      .sort(
        (a, b) =>
          a.group - b.group ||
          a.priority - b.priority ||
          a.name.localeCompare(b.name) ||
          a.ref.localeCompare(b.ref),
      );
    if (branches?.head && !branches.current) {
      items.unshift({
        name: `Current commit (${branches.head.slice(0, 7)})`,
        ref: branches.head,
        kind: "Commit",
        group: -1,
        priority: -1,
      });
    }
    return items;
  }, [branches]);
  const matches = useMemo(() => {
    const search = query.trim().toLowerCase();
    return choices.filter(
      (branch) => branch.name.toLowerCase().includes(search) || branch.ref.toLowerCase().includes(search),
    );
  }, [choices, query]);
  const label = error
    ? "Branches unavailable"
    : !branches
      ? "Loading branches…"
      : choices.find((branch) => branch.ref === value)?.name || "Choose branch";

  return (
    <Popover
      open={open && !disabled}
      onOpenChange={(next) => {
        if (disabled) return;
        setOpen(next);
        if (next) setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className="composer-base-branch"
          role="combobox"
          aria-label="Base branch"
          aria-expanded={open && !disabled}
          title={`Base branch: ${label}`}
          disabled={disabled}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" && !disabled) {
              event.preventDefault();
              setQuery("");
              setOpen(true);
            }
          }}
        >
          <GitBranch className="size-3 shrink-0" />
          <span>{label}</span>
          <ChevronDown className="size-3 shrink-0" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="composer-branch-menu" align="end">
        <Command label="Search branches" shouldFilter={false} defaultValue={value}>
          <CommandInput
            aria-label="Search branches"
            placeholder="Search branches…"
            value={query}
            onValueChange={setQuery}
          />
          <CommandList label="Branches">
            <CommandEmpty>No branches match your search.</CommandEmpty>
            <CommandGroup>
              {matches.slice(0, 100).map((branch) => (
                <CommandItem
                  key={branch.ref}
                  value={branch.ref}
                  title={branch.ref}
                  aria-label={`${branch.name} ${branch.kind}`}
                  className="composer-branch-option"
                  data-checked={branch.ref === value}
                  onSelect={() => {
                    if (disabled) return;
                    onChange(branch.ref);
                    setOpen(false);
                  }}
                >
                  <span className="composer-branch-name">{branch.name}</span>
                  <span className="composer-branch-kind">{branch.kind}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
          {matches.length > 100 ? (
            <p className="composer-branch-limit">
              Showing 100 of {matches.length} branches. Refine your search.
            </p>
          ) : null}
        </Command>
      </PopoverContent>
    </Popover>
  );
}
