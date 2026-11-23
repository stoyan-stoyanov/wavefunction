import { Minus, Square, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useDesign } from "@/lib/design";
import { replaceWindowControls, windowAction } from "@/lib/titlebar";
import "./window-controls.css";

/**
 * Minimize, maximize and close, drawn by the app in place of the native buttons for a design whose
 * manifest asks for them. The design styles them; `data-window-controls` on the root lets it make
 * room for them in the toolbars.
 */
export function WindowControls() {
  const wanted = useDesign((state) => state.active.windowControls === "theme");
  const [shown, setShown] = useState(false);
  useEffect(() => {
    let current = true;
    void replaceWindowControls(wanted).then((replaced) => {
      if (!current) return;
      setShown(replaced);
      if (replaced) document.documentElement.dataset.windowControls = "theme";
      else delete document.documentElement.dataset.windowControls;
    });
    return () => {
      current = false;
    };
  }, [wanted]);
  if (!shown) return null;
  return (
    <div className="window-controls" role="group" aria-label="Window">
      <button
        type="button"
        className="window-control"
        data-control="minimize"
        aria-label="Minimize"
        onClick={() => windowAction("minimize")}
      >
        <Minus aria-hidden="true" />
      </button>
      <button
        type="button"
        className="window-control"
        data-control="maximize"
        aria-label="Maximize"
        onClick={() => windowAction("toggleMaximize")}
      >
        <Square aria-hidden="true" />
      </button>
      <button
        type="button"
        className="window-control"
        data-control="close"
        aria-label="Close"
        onClick={() => windowAction("close")}
      >
        <X aria-hidden="true" />
      </button>
    </div>
  );
}
