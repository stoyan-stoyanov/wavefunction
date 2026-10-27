import { parseDiff, type DiffLine } from "./diff-parser";

export type DiffLocation = { side: "before" | "after"; line: number | null };
export function DiffView({
  diff,
  split,
  onComment,
}: {
  diff: string;
  split: boolean;
  onComment?: (location: DiffLocation) => void;
}) {
  const lines = parseDiff(diff);
  const gutter = (line: number | null, side: DiffLocation["side"]) =>
    line === null ? (
      <span className="diff-number" />
    ) : onComment ? (
      <button
        className="diff-number"
        aria-label={`Comment on ${side} line ${line}`}
        onClick={() => onComment({ side, line })}
      >
        {line}
      </button>
    ) : (
      <span className="diff-number">{line}</span>
    );
  if (!split)
    return (
      <div className="review-diff inline-diff" aria-label="Inline diff">
        {lines.map((line, i) => (
          <div key={i} className={`diff-row ${line.kind}`}>
            {gutter(line.before, "before")}
            {gutter(line.after, "after")}
            <span className="diff-sign">{line.kind === "add" ? "+" : line.kind === "delete" ? "−" : ""}</span>
            <code>{line.text || " "}</code>
          </div>
        ))}
      </div>
    );
  const rows: Array<{ before?: DiffLine; after?: DiffLine; meta?: DiffLine }> = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.kind === "delete" || line.kind === "add") {
      const removed: DiffLine[] = [],
        added: DiffLine[] = [];
      while (i < lines.length && lines[i].kind === "delete") removed.push(lines[i++]);
      while (i < lines.length && lines[i].kind === "add") added.push(lines[i++]);
      i--;
      for (let n = 0; n < Math.max(removed.length, added.length); n++)
        rows.push({ before: removed[n], after: added[n] });
    } else if (line.kind === "context") rows.push({ before: line, after: line });
    else rows.push({ meta: line });
  }
  return (
    <div className="review-diff split-diff" aria-label="Split diff">
      <div className="split-labels">
        <span>Before</span>
        <span>After</span>
      </div>
      {rows.map((row, i) =>
        row.meta ? (
          <div className={`diff-row ${row.meta.kind}`} key={i}>
            <code>{row.meta.text}</code>
          </div>
        ) : (
          <div className="split-row" key={i}>
            <div className={`diff-half ${row.before?.kind ?? "empty"}`}>
              {gutter(row.before?.before ?? null, "before")}
              <code>{row.before?.text || " "}</code>
            </div>
            <div className={`diff-half ${row.after?.kind ?? "empty"}`}>
              {gutter(row.after?.after ?? null, "after")}
              <code>{row.after?.text || " "}</code>
            </div>
          </div>
        ),
      )}
    </div>
  );
}
