import type { ComponentProps } from "react";
import { toast } from "sonner";
import { openExternal } from "@/lib/api";

/** A link to a website, opened in the browser: the app's own window ignores target="_blank". */
export function WebLink({ href, children, ...props }: ComponentProps<"a"> & { href: string }) {
  return (
    <a
      {...props}
      href={href}
      target="_blank"
      rel="noreferrer"
      onClick={(event) => {
        event.preventDefault();
        void openExternal(href).catch(() => toast.error(`Open ${href} in your browser.`));
      }}
    >
      {children}
    </a>
  );
}
