export interface DiffLine {
  kind: "add" | "delete" | "context" | "hunk" | "meta";
  text: string;
  before: number | null;
  after: number | null;
}
export function parseDiff(diff: string): DiffLine[] {
  let before = 0,
    after = 0,
    // Lines the current hunk still holds, from its header. A deleted line reading "-- note" is
    // "--- note" in the diff, so a line is a file header only once the hunk is complete.
    oldLeft = 0,
    newLeft = 0;
  return diff
    .split("\n")
    .filter((line, i, lines) => line !== "" || i < lines.length - 1)
    .map((line) => {
      const hunk = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line);
      if (hunk) {
        before = Number(hunk[1]);
        after = Number(hunk[3]);
        oldLeft = hunk[2] === undefined ? 1 : Number(hunk[2]);
        newLeft = hunk[4] === undefined ? 1 : Number(hunk[4]);
        return { kind: "hunk", text: line, before: null, after: null };
      }
      if ((oldLeft <= 0 && newLeft <= 0) || line.startsWith("\\"))
        return { kind: "meta", text: line, before: null, after: null };
      if (line.startsWith("+")) {
        newLeft--;
        return {
          kind: "add",
          text: line.slice(1),
          before: null,
          after: after++,
        };
      }
      if (line.startsWith("-")) {
        oldLeft--;
        return {
          kind: "delete",
          text: line.slice(1),
          before: before++,
          after: null,
        };
      }
      if (line.startsWith(" ")) {
        oldLeft--;
        newLeft--;
        return {
          kind: "context",
          text: line.slice(1),
          before: before++,
          after: after++,
        };
      }
      return { kind: "meta", text: line, before: null, after: null };
    });
}
