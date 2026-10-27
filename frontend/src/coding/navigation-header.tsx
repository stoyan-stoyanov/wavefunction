import { PanelLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

export function SidebarToggle({
  onToggle,
  collapsed = false,
}: {
  onToggle: () => void;
  collapsed?: boolean;
}) {
  return (
    <Button
      className="sidebar-toggle"
      variant="ghost"
      size="icon-sm"
      aria-label={collapsed ? "Show sidebar" : "Hide sidebar"}
      title={collapsed ? "Show sidebar" : "Hide sidebar"}
      onClick={onToggle}
    >
      <PanelLeft />
    </Button>
  );
}

export function NavigationHeader({ onToggle }: { onToggle?: () => void }) {
  return (
    <div className="navigation-header">
      <div className="navigation-topbar" data-tauri-drag-region>
        {onToggle ? <SidebarToggle onToggle={onToggle} /> : null}
      </div>
    </div>
  );
}
