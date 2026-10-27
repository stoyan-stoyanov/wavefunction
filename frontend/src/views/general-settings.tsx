import { NativeSection, NativeView, ViewHeading } from "@/components/native-view";
import { SegmentedControl } from "@/components/segmented-control";
import { Button } from "@/components/ui/button";
import { useZoom } from "@/lib/zoom";
import { readSidebarTransparency, setSidebarTransparency } from "@/lib/sidebar-transparency";
import { useId, useState } from "react";
import { Minus, Monitor, Moon, Plus, RotateCcw, Sun, type LucideIcon } from "lucide-react";
import { useTheme } from "next-themes";

const themeOptions: Array<{ value: string; label: string; icon: LucideIcon }> = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

import { SettingsRow } from "./settings-row";

export function GeneralSettings() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const appearance = resolvedTheme === "dark" ? "dark" : "light";
  const transparencyId = useId();
  const [transparency, setTransparency] = useState(() => ({
    light: readSidebarTransparency("light"),
    dark: readSidebarTransparency("dark"),
  }));
  const { zoom, zoomIn, zoomOut, resetZoom, canZoomIn, canZoomOut, canResetZoom } = useZoom();

  return (
    <NativeView>
      <ViewHeading title="General" />
      <NativeSection title="Appearance" description="Applies immediately across every view.">
        <div className="overflow-hidden rounded-xl bg-card">
          <SettingsRow
            title="Theme"
            description="System follows your macOS appearance setting."
            control={
              <SegmentedControl
                label="Theme"
                value={theme ?? "system"}
                options={themeOptions}
                onChange={setTheme}
              />
            }
          />
          <SettingsRow
            className="border-t border-hairline"
            title={<label htmlFor={transparencyId}>Sidebar transparency</label>}
            description="Saved separately for light and dark themes."
            control={
              <div className="flex items-center gap-3">
                <input
                  id={transparencyId}
                  type="range"
                  min={0}
                  max={100}
                  step={1}
                  value={transparency[appearance]}
                  aria-valuetext={`${transparency[appearance]}% transparent`}
                  className="w-32 accent-primary"
                  onChange={(event) => {
                    const value = Number(event.currentTarget.value);
                    setSidebarTransparency(appearance, value);
                    setTransparency((previous) => ({ ...previous, [appearance]: value }));
                  }}
                />
                <span aria-hidden="true" className="w-10 text-right font-mono text-sm tabular-nums">
                  {transparency[appearance]}%
                </span>
              </div>
            }
          />
          <SettingsRow
            className="border-t border-hairline"
            title="Zoom"
            description="Scales the whole window. Also on ⌘+, ⌘- and ⌘0."
            control={
              <div className="flex items-center gap-1">
                <Button
                  variant="secondary"
                  size="icon-sm"
                  aria-label="Zoom out"
                  onClick={zoomOut}
                  disabled={!canZoomOut}
                >
                  <Minus />
                </Button>
                <span className="w-14 text-center font-mono text-sm tabular-nums">
                  {Math.round(zoom * 100)}%
                </span>
                <Button
                  variant="secondary"
                  size="icon-sm"
                  aria-label="Zoom in"
                  onClick={zoomIn}
                  disabled={!canZoomIn}
                >
                  <Plus />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Reset zoom"
                  onClick={resetZoom}
                  disabled={!canResetZoom}
                  className="ml-1"
                >
                  <RotateCcw />
                </Button>
              </div>
            }
          />
        </div>
      </NativeSection>
    </NativeView>
  );
}
