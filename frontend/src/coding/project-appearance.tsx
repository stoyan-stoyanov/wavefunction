import { Pipette } from "lucide-react";
import { projectIcons, projectColors, type ProjectIconName } from "./project-icons";
import "./project-appearance.css";

export function ProjectIcon({
  icon = "folder",
  color,
  className,
}: {
  icon?: string;
  color?: string | null;
  className?: string;
}) {
  const Icon = projectIcons.find((choice) => choice.name === icon)?.component ?? projectIcons[0].component;
  return <Icon className={className} style={color ? { color } : undefined} aria-hidden="true" />;
}

export function ProjectAppearance({
  icon,
  color,
  onIcon,
  onColor,
}: {
  icon: ProjectIconName;
  color: string | null;
  onIcon: (icon: ProjectIconName) => void;
  onColor: (color: string | null) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const gridId = useId();
  const selected = projectIcons.find((choice) => choice.name === icon) ?? projectIcons[0];
  const custom = color !== null && !projectColors.some((choice) => choice.value === color.toLowerCase());
  const firstRow = projectIcons.slice(0, 8);
  const choices = expanded
    ? projectIcons
    : firstRow.some((choice) => choice.name === selected.name)
      ? firstRow
      : [...firstRow.slice(0, 7), selected];
  return (
    <div className="project-appearance">
      <fieldset>
        <legend>Project icon</legend>
        <div className="project-icon-grid" id={gridId}>
          {choices.map((choice) => (
            <button
              key={choice.name}
              type="button"
              aria-label={choice.label}
              aria-pressed={icon === choice.name}
              title={choice.label}
              onClick={() => onIcon(choice.name)}
            >
              <choice.component style={color ? { color } : undefined} aria-hidden="true" />
            </button>
          ))}
        </div>
        <button
          type="button"
          className="project-icons-toggle"
          aria-expanded={expanded}
          aria-controls={gridId}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      </fieldset>
      <fieldset>
        <legend>Icon color</legend>
        <div className="project-color-grid">
          <button
            type="button"
            aria-label="Default color"
            title="Default color"
            aria-pressed={color === null}
            onClick={() => onColor(null)}
          >
            <span className="project-default-color" />
          </button>
          {projectColors.map((choice) => (
            <button
              key={choice.value}
              type="button"
              aria-label={choice.name}
              title={choice.name}
              aria-pressed={color?.toLowerCase() === choice.value}
              onClick={() => onColor(choice.value)}
            >
              <span style={{ background: choice.value }} />
            </button>
          ))}
          <label className="project-custom-color" title="Custom icon color" data-selected={custom}>
            <Pipette style={custom ? { color: color ?? undefined } : undefined} aria-hidden="true" />
            <input
              type="color"
              aria-label="Custom icon color"
              value={color ?? "#5896ed"}
              onChange={(event) => onColor(event.target.value)}
            />
          </label>
        </div>
      </fieldset>
    </div>
  );
}
import { useId, useState } from "react";
