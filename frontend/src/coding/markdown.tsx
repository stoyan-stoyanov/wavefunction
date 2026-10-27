import {
  Children,
  createContext,
  isValidElement,
  memo,
  useContext,
  useState,
  type ComponentProps,
} from "react";
import {
  defaultRehypePlugins,
  Streamdown,
  type AnimateOptions,
  type Components,
  type ExtraProps,
} from "streamdown";
import { toast } from "sonner";
import { openExternal } from "@/lib/api";
import { FileLinks, linkedFile } from "./file-links";
import "./markdown.css";

const VisibleRows = createContext(10);
function ChatTable({ children, node }: ComponentProps<"table"> & ExtraProps) {
  const [visible, setVisible] = useState(10);
  const body = node?.children.find((child) => child.type === "element" && child.tagName === "tbody");
  const total =
    body?.type === "element"
      ? body.children.filter((child) => child.type === "element" && child.tagName === "tr").length
      : 0;
  return (
    <div className="chat-table">
      <div className="chat-table-scroll" tabIndex={0} role="region" aria-label="Table">
        <VisibleRows.Provider value={visible}>
          <table>{children}</table>
        </VisibleRows.Provider>
      </div>
      {total > 10 ? (
        <div className="chat-table-pagination">
          <span>
            {Math.min(visible, total)} of {total} rows
          </span>
          {visible < total ? (
            <button type="button" onClick={() => setVisible((value) => value + 10)}>
              Show more
            </button>
          ) : null}
          {visible > 10 ? (
            <button type="button" onClick={() => setVisible(10)}>
              Show less
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
function ChatTableBody({ children }: ComponentProps<"tbody"> & ExtraProps) {
  const visible = useContext(VisibleRows);
  return <tbody>{Children.toArray(children).filter(isValidElement).slice(0, visible)}</tbody>;
}
function ChatLink({ href, children, title, ...props }: ComponentProps<"a"> & ExtraProps) {
  const openFile = useContext(FileLinks);
  const external = !!href && /^(https?:|mailto:)/i.test(href);
  const file = linkedFile((props as { "data-file-href"?: string })["data-file-href"] ?? href);
  const open = () => {
    if (href)
      void openExternal(href).catch((error: unknown) =>
        toast.error(error instanceof Error ? error.message : "Could not open link"),
      );
  };
  return (
    <a
      className="text-primary underline underline-offset-4"
      href={href}
      title={title}
      target={external ? "_blank" : undefined}
      rel={external ? "noopener noreferrer" : undefined}
      onClick={(event) => {
        if (external) {
          event.preventDefault();
          open();
        } else if (file) {
          // Never navigate the app's own window to a file.
          event.preventDefault();
          openFile?.(file);
        }
      }}
      onAuxClick={(event) => {
        if (external && event.button === 1) {
          event.preventDefault();
          open();
        }
      }}
    >
      {children}
    </a>
  );
}
const components: Components = {
  a: ChatLink,
  table: ChatTable,
  tbody: ChatTableBody,
  thead: ({ children }) => <thead>{children}</thead>,
  tr: ({ children }) => <tr>{children}</tr>,
  th: ({ children, style }) => <th style={style}>{children}</th>,
  td: ({ children, style }) => <td style={style}>{children}</td>,
};
const wordFade: AnimateOptions = {
  animation: "fadeIn",
  duration: 180,
  sep: "word",
  stagger: 5,
  maxBacklogMs: 100,
};
const linkSafety = { enabled: false };
type HastNode = {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};
/**
 * Link hardening blocks a relative path such as out/report.pdf and rewrites ./out/report.pdf to
 * /out/report.pdf. Keep the path the agent wrote for the file preview; the anchor only needs a fragment.
 */
function relativeFileLinks() {
  const visit = (node: HastNode) => {
    const href = node.tagName === "a" ? node.properties?.href : undefined;
    if (typeof href === "string" && href && !/^([a-z][a-z0-9+.-]*:|[#/?])/i.test(href)) {
      node.properties!.dataFileHref = href;
      node.properties!.href = "#file";
    }
    node.children?.forEach(visit);
  };
  return visit;
}
const rehypePlugins = Object.entries(defaultRehypePlugins).flatMap(([name, plugin]) =>
  name === "harden" ? [relativeFileLinks, plugin] : [plugin],
);
const controls = { table: false, code: { copy: true, download: false } };
export const CodingMarkdown = memo(function CodingMarkdown({
  children,
  streaming = false,
}: {
  children: string;
  streaming?: boolean;
}) {
  return (
    <Streamdown
      className="coding-markdown"
      components={components}
      isAnimating={streaming}
      animated={streaming ? wordFade : false}
      linkSafety={linkSafety}
      rehypePlugins={rehypePlugins}
      controls={controls}
      tableMaxHeight={0}
    >
      {children}
    </Streamdown>
  );
});
